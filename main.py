import sys
import io
import re
import os
import json
import argparse

if sys.stdout.encoding != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

from core.entity_resolver import EntityResolver
from connectors.pubmed import PubMedConnector
from connectors.pubmed_validator import PMIDValidator
from connectors.patents import PatentConnector
from connectors.regulatory_comex import RegulatoryComexConnector
from core.score_engine import (
    ScoreEngine, MODEL_VERSION, SCI_BASELINE_HISTORY_DAYS, PROVISIONAL_WEIGHTS_DISCLAIMER,
    get_display_jurisdictions
)
from core.sanity_checks import check_cross_country_trade_divergence, TradeDataSanityError
from core.database import DatabaseManager, SCHEMA_VERSION
from core.llm_analysis import LLMAnalysisEngine
from core.predictive_ranking import PredictiveRankingEngine, TIER_INSUFFICIENT_DATA
from reports.pdf_generator import PDFReportGenerator

LANGUAGES = ["PT-BR", "PT-PT", "ES"]

# Mercado-alvo (--market) -> idioma do relatório e jurisdição regulatória
# exibida (core.score_engine.get_display_jurisdictions) - único ponto de
# verdade que decide qual PDF é de fato gerado numa execução com --market.
MARKET_TO_LANG = {"BR": "PT-BR", "PT": "PT-PT", "ES": "ES"}
LANG_TO_TARGET_MARKET = {lang: market for market, lang in MARKET_TO_LANG.items()}

# Países declarantes da UE cujos dados de comércio exterior (Eurostat Comext/
# TARIC, connectors/trade_eurostat.py) são cruzados pela trava de sanidade
# (core/sanity_checks.py) antes de qualquer PDF ser gerado - independente de
# qual mercado foi pedido via --market, a checagem cobre sempre os dois.
EU_SANITY_CHECK_REPORTER_LANGS = {"PT": "PT-PT", "ES": "ES"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Actives Predict - Pipeline do PhytoDemand Report")
    parser.add_argument(
        "--market", choices=sorted(MARKET_TO_LANG.keys()), default=None,
        help="Restringe a execução a um único mercado-alvo (gera só o PDF desse idioma). Sem esta flag, gera PT-BR/PT-PT/ES (modo legado)."
    )
    parser.add_argument(
        "--include-mock-jurisdictions", action="store_true", default=False,
        help="Inclui jurisdições mock/ilustrativas (ex.: FDA) na Matriz Regulatória exibida - só tem efeito com VANGUARD_TEST_ENV=1; nunca em builds de exportação final."
    )
    return parser.parse_args()


def resolve_search_query(asset: dict) -> str:
    """Nome botânico em Latim (melhor para PubMed/patentes) > CAS > INCI (inglês) > primeiro alias."""
    identifiers = asset.get("botanical_or_cas", [])
    botanical = next((i for i in identifiers if not re.match(r'^\d{2,7}-\d{2}-\d$', i)), None)
    return botanical or (identifiers[0] if identifiers else None) or asset.get("inci_name") or asset.get("aliases", [asset["canonical_name"]])[0]


def main():
    args = parse_args()
    target_market = args.market
    languages_to_generate = [MARKET_TO_LANG[target_market]] if target_market else LANGUAGES
    include_mock_jurisdictions = args.include_mock_jurisdictions and os.environ.get("VANGUARD_TEST_ENV") == "1"
    if args.include_mock_jurisdictions and not include_mock_jurisdictions:
        print("[!] --include-mock-jurisdictions ignorado: requer VANGUARD_TEST_ENV=1 (nunca ativo em builds de exportação final).")

    print("=" * 70)
    print("       ACTIVES PREDICT - PIPELINE DE INTELIGÊNCIA (PhytoDemand Report)")
    if target_market:
        print(f"       Execução restrita ao mercado-alvo: {target_market} ({MARKET_TO_LANG[target_market]})")
    print("=" * 70)

    # 1. Carregar Taxonomia de Ativos (catálogo global, incl. Ácidos Cosmecêuticos)
    taxonomy_file = "data/taxonomy/ativos_mvp.json"
    resolver = EntityResolver(taxonomy_path=taxonomy_file)
    assets = resolver.assets
    print(f"\n[1] Taxonomia carregada: {len(assets)} ativo(s) configurado(s) - Schema v{SCHEMA_VERSION}.")

    # 2. Inicializar Conectores, Engine de Score, Banco, Ranking Preditivo, LLM e Relatórios
    pubmed_conn = PubMedConnector(resolver=resolver)
    pmid_validator = PMIDValidator(pubmed_conn)
    patent_conn = PatentConnector(resolver=resolver)
    comex_conn = RegulatoryComexConnector(resolver=resolver)

    score_engine = ScoreEngine()
    db_manager = DatabaseManager()
    ranking_engine = PredictiveRankingEngine()
    llm_engine = LLMAnalysisEngine()
    pdf_generator = PDFReportGenerator()

    # 3. Abre a execução auditável do pipeline (pipeline_runs)
    run_id = db_manager.start_run(
        domain_code="DERMOCOSMETICS",
        product_code="PHYTODEMAND_REPORT",
        model_version=MODEL_VERSION,
        schema_version=SCHEMA_VERSION
    )
    print(f"[2] Execução iniciada - Run ID: {run_id}")

    try:
        # 3a. Fase 0: Trava de Sanidade - divergência de comércio exterior
        #     entre países declarantes da UE (PT vs ES, Eurostat Comext/
        #     TARIC via connectors/trade_eurostat.py). Roda sobre o catálogo
        #     completo, independente de --market, e BLOQUEIA a geração de
        #     qualquer PDF se os dados de comércio de dois países
        #     divergirem para menos de 90% dos ativos comparados
        #     (core/sanity_checks.py).
        print("\n" + "=" * 70)
        print("[FASE 0] TRAVA DE SANIDADE - COMÉRCIO EXTERIOR POR PAÍS DECLARANTE (PT vs ES)")
        print("=" * 70)
        trade_by_country = {country: {} for country in EU_SANITY_CHECK_REPORTER_LANGS}
        for asset in assets:
            hs_code = asset.get("hs_codes", [None])[0]
            for country, lang_key in EU_SANITY_CHECK_REPORTER_LANGS.items():
                trade_by_country[country][asset["asset_id"]] = comex_conn.fetch_import_volume_mock(
                    hs_code, asset_id=asset["asset_id"], lang=lang_key
                )
        sanity_report = check_cross_country_trade_divergence(trade_by_country)
        for pair, info in sanity_report["pairs"].items():
            divergent_pct = 1 - info["identical_fraction"]
            print(
                f"  {pair}: {divergent_pct:.1%} dos {info['total_assets_compared']} ativos com dados de "
                f"comércio DIVERGENTES ({len(info['identical_assets'])} idênticos - limite tolerado: 10%)."
            )
        print("  [OK] Sanidade confirmada: dados de comércio de ES e PT são divergentes e específicos por país.")

        # 4. Fase 1: coletar evidências e calcular scores para TODOS os ativos do catálogo
        #    (sem chamadas de LLM ainda - só os 8 selecionados no ranking preditivo
        #    passam pela síntese via IA, evitando custo/tempo desnecessário).
        print("\n" + "=" * 70)
        print(f"[FASE 1] COLETA E SCORING DOS {len(assets)} ATIVOS DO CATÁLOGO")
        print("=" * 70)
        print(f"[Ressalva] {PROVISIONAL_WEIGHTS_DISCLAIMER}")

        all_evaluations = []
        period_start = None
        period_end = None
        # Rastreiam o primeiro caso real de SUCESSO e de BLOQUEIO da validação
        # determinística de PMIDs (connectors/pubmed_validator.py) encontrados
        # organicamente durante a coleta do catálogo completo - usados na
        # demonstração da Fase 1b logo após este laço.
        _first_pmid_success = None
        _first_pmid_rejection = None
        for asset in assets:
            asset_id = asset["asset_id"]
            canonical_name = asset["canonical_name"]
            exclusions = asset.get("exclusions", [])
            search_query = resolve_search_query(asset)

            # PubMed: aplica as exclusões do ativo diretamente na query, restringe à janela de 15
            # dias (data de publicação), recebe PMIDs + query_hash. Estes são os PMIDs de
            # "novidade" exibidos no relatório - só os que passam pela validação determinística
            # obrigatória pré-relatório (connectors/pubmed_validator.py: existe no NCBI E a
            # entidade do ativo é confirmada no título/resumo) chegam ao PDF. Qualquer PMID
            # rejeitado é descartado nesta camada, nunca chega à síntese da LLM.
            pubmed_search = pubmed_conn.search_articles(search_query, exclusions=exclusions, max_results=5)
            verified_pmids, rejected_pmids = pmid_validator.validate_batch(pubmed_search["pmids"], canonical_name)
            if rejected_pmids:
                print(f"    [PMID] {asset_id} - {len(rejected_pmids)} PMID(s) rejeitado(s) na validação NCBI: {[r['pmid'] for r in rejected_pmids]}")
                if _first_pmid_rejection is None:
                    _first_pmid_rejection = {"asset_id": asset_id, "canonical_name": canonical_name, **rejected_pmids[0]}
            if verified_pmids:
                # Trilha de auditoria explícita: todo PMID que chega ao relatório passou
                # por aqui - sem esta linha, só as rejeições ficavam visíveis no console,
                # dando a falsa impressão de que os PMIDs aceitos não tinham sido checados.
                print(f"    [PMID] {asset_id} - {len(verified_pmids)} PMID(s) validado(s) e aceito(s) na NCBI: {verified_pmids}")
                if _first_pmid_success is None:
                    _first_pmid_success = {"asset_id": asset_id, "canonical_name": canonical_name, "pmid": verified_pmids[0]}
            if period_start is None:
                period_start = pubmed_search["date_range"]["start"]
                period_end = pubmed_search["date_range"]["end"]

            # PubMed (Tração): segunda busca sobre a janela histórica móvel de 12 meses
            # (TRACTION_WINDOW_DAYS) - usada exclusivamente para calcular Tração Científica
            # (core/score_engine.py), nunca para exibir PMIDs de novidade no relatório.
            pubmed_traction_search = pubmed_conn.search_articles(
                search_query, exclusions=exclusions, max_results=10, days=pubmed_conn.TRACTION_WINDOW_DAYS
            )
            pubmed_traction_results = [pubmed_conn.fetch_article_details(pmid) for pmid in pubmed_traction_search["pmids"]]

            # PubMed (Linha de Base Histórica): janela de 36 meses ESTRITAMENTE
            # ANTERIOR aos 12 meses de análise (sem sobreposição), usada só para
            # o componente [G] de calculate_scientific_traction_breakdown
            # (core/score_engine.py). max_results=0 -> só a contagem bruta do
            # PubMed, sem efetch/Entity Resolution (custo de API mínimo).
            pubmed_baseline_search = pubmed_conn.search_articles(
                search_query, exclusions=exclusions, max_results=0,
                days=pubmed_conn.TRACTION_WINDOW_DAYS + SCI_BASELINE_HISTORY_DAYS,
                end_days_ago=pubmed_conn.TRACTION_WINDOW_DAYS
            )

            # Patentes: aplica exclusões, restringe à janela de 15 dias (publication_date), deduplica
            # por família de patentes, recebe resultados + query_hash
            patent_search = patent_conn.fetch_patents_mock(search_query, exclusions=exclusions)
            patent_results = [patent_conn.process_patent(p) for p in patent_search["results"]]

            # Patentes (Tração): mesma janela histórica móvel de 12 meses, para Tração Industrial.
            # Patentes estão sujeitas à defasagem legal entre depósito e publicação - a janela de
            # 12 meses é mais representativa da proteção industrial real do que a de 15 dias.
            patent_traction_search = patent_conn.fetch_patents_mock(
                search_query, exclusions=exclusions, days=patent_conn.TRACTION_WINDOW_DAYS
            )
            patent_traction_results = [patent_conn.process_patent(p) for p in patent_traction_search["results"]]

            # Regulatório/Comex: dossiê consolidado - Alertas Regulatórios e Sinais Comerciais/Comex + query_hash.
            # Jurisdição baseline PT-BR (Anvisa) usada para o dossiê comercial (R_trade permanece
            # regional - não é um "pior caso" cross-jurisdição); os 8 ativos selecionados têm seus
            # dados regulatórios/comerciais recalculados por idioma na Fase 3.
            hs_code = asset.get("hs_codes", [None])[0]
            dossier = comex_conn.get_asset_dossier(asset_id, hs_code=hs_code, lang="PT-BR")
            commercial_signals = dossier["sinais_comerciais_comex"]

            # Matriz Regulatória cross-jurisdição (Anvisa/UE/FDA) - o Alerta Regulatório usado
            # na árvore de precedência (core/predictive_ranking.py) passa a refletir o "Máximo
            # de severidade regulatória observado entre as jurisdições monitoradas", não só a
            # jurisdição local do idioma do relatório.
            regulatory_matrix = comex_conn.get_regulatory_matrix(asset_id)
            regulatory_alerts = regulatory_matrix["max_severity_status"]

            query_hashes = [
                pubmed_search["query_hash"], pubmed_traction_search["query_hash"],
                pubmed_baseline_search["query_hash"],
                patent_search["query_hash"], patent_traction_search["query_hash"],
                dossier["query_hash"]
            ]

            # Tração Científica calculada sobre o modelo de 4 componentes V/G/A/Q
            # (core/score_engine.py calculate_scientific_traction_breakdown) - [G] usa
            # baseline_36m_count (contagem bruta na janela de 36 meses estritamente
            # anterior aos 12 meses de análise, sem sobreposição).
            assessment = score_engine.generate_assessment(
                pubmed_data=pubmed_traction_results,
                patent_data=patent_traction_results,
                regulatory_alerts=regulatory_alerts,
                commercial_signals=commercial_signals,
                query_hashes=query_hashes,
                baseline_36m_count=pubmed_baseline_search["count"]
            )

            # Persistência auditável: avaliação do ativo + fontes de evidência (evaluation_evidence_sources)
            evaluation_id = db_manager.save_evaluation(
                run_id, asset_id, canonical_name, assessment, domain_code="DERMOCOSMETICS"
            )
            db_manager.save_evidence_source(
                evaluation_id, source_type="PUBMED", query_hash=pubmed_search["query_hash"],
                raw_response_summary=json.dumps(pubmed_search["raw_metadata"], ensure_ascii=False),
                items_found=pubmed_search["count"]
            )
            db_manager.save_evidence_source(
                evaluation_id, source_type="PUBMED_TRACTION_12M", query_hash=pubmed_traction_search["query_hash"],
                raw_response_summary=json.dumps(pubmed_traction_search["raw_metadata"], ensure_ascii=False),
                items_found=pubmed_traction_search["count"]
            )
            db_manager.save_evidence_source(
                evaluation_id, source_type="PUBMED_BASELINE_36M", query_hash=pubmed_baseline_search["query_hash"],
                raw_response_summary=json.dumps(pubmed_baseline_search["raw_metadata"], ensure_ascii=False),
                items_found=pubmed_baseline_search["count"]
            )
            db_manager.save_evidence_source(
                evaluation_id, source_type="PATENTS", query_hash=patent_search["query_hash"],
                raw_response_summary=json.dumps({
                    "total_found": patent_search["total_found"],
                    "total_after_dedup": patent_search["total_after_dedup"],
                    "duplicate_families_collapsed": patent_search["duplicate_families_collapsed"]
                }, ensure_ascii=False),
                items_found=patent_search["total_after_dedup"]
            )
            db_manager.save_evidence_source(
                evaluation_id, source_type="PATENTS_TRACTION_12M", query_hash=patent_traction_search["query_hash"],
                raw_response_summary=json.dumps({
                    "total_found": patent_traction_search["total_found"],
                    "total_after_dedup": patent_traction_search["total_after_dedup"],
                    "duplicate_families_collapsed": patent_traction_search["duplicate_families_collapsed"]
                }, ensure_ascii=False),
                items_found=patent_traction_search["total_after_dedup"]
            )
            db_manager.save_evidence_source(
                evaluation_id, source_type="REGULATORY_COMEX", query_hash=dossier["query_hash"],
                raw_response_summary=json.dumps({
                    "alertas_regulatorios": regulatory_alerts,
                    "sinais_comerciais_comex": commercial_signals
                }, ensure_ascii=False),
                items_found=1
            )

            print(
                f"  {asset_id} {canonical_name:<24} "
                f"Cientifica={assessment['tracao_cientifica']:<7} "
                f"Industrial={assessment['tracao_industrial']:<7} "
                f"Risco={assessment['risco_oferta']:<12} "
                f"Confianca={assessment['confianca_sinal']}"
            )

            all_evaluations.append({
                "asset_id": asset_id,
                "canonical_name": canonical_name,
                "tracao_cientifica": assessment["tracao_cientifica"],
                "tracao_cientifica_componentes": assessment["tracao_cientifica_componentes"],
                "tracao_industrial": assessment["tracao_industrial"],
                "risco_oferta": assessment["risco_oferta"],
                "alerta_regulatorio": assessment["alerta_regulatorio"],
                "sinal_comercial_comex": assessment["sinal_comercial_comex"],
                "confianca_sinal": assessment["confianca_sinal"],
                "evidencias_verificadas": assessment["evidencias_verificadas"],
                "nivel_evidencia_maximo": assessment["nivel_evidencia_maximo"],
                "pubmed_raw_count_12m": pubmed_traction_search["count"],
                "tem_exclusoes": bool(exclusions),
                "pmids": verified_pmids,
                "patent_ids": [p["patent_id"] for p in patent_search["results"]],
                "hs_code": hs_code,
                "pubmed_baseline_count": pubmed_baseline_search["count"],
                "_pubmed_traction_results": pubmed_traction_results,
                "_patent_traction_results": patent_traction_results
            })

        # 4b. Fase 1b: resumo da Validação Determinística de PMIDs via NCBI
        #     E-utilities (connectors/pubmed_validator.py) - demonstra, com
        #     dados reais coletados no catálogo completo acima, um caso de
        #     SUCESSO (PMID real e topicamente relevante confirmado no NCBI)
        #     e um caso de BLOQUEIO (PMID rejeitado por não existir ou não
        #     confirmar a entidade do ativo). Se nenhuma rejeição orgânica
        #     ocorreu nesta execução, roda um caso controlado com um PMID
        #     deliberadamente fora de contexto para garantir a demonstração.
        print("\n" + "=" * 70)
        print("[FASE 1b] VALIDAÇÃO DETERMINÍSTICA DE PMIDs (NCBI E-utilities) - RESUMO")
        print("=" * 70)
        if _first_pmid_success:
            print(
                f"  [OK] SUCESSO: PMID {_first_pmid_success['pmid']} de '{_first_pmid_success['canonical_name']}' "
                f"({_first_pmid_success['asset_id']}) confirmado no NCBI (existe + entidade presente no título/resumo)."
            )
        else:
            print("  [!] Nenhum PMID passou na validação nesta execução (todos os candidatos retornados pelo PubMed hoje foram rejeitados).")

        if _first_pmid_rejection:
            print(
                f"  [OK] BLOQUEIO: PMID {_first_pmid_rejection['pmid']} de '{_first_pmid_rejection['canonical_name']}' "
                f"({_first_pmid_rejection['asset_id']}) removido -> {_first_pmid_rejection['reason']}"
            )
        else:
            _fallback_asset = assets[0]
            _valid_fallback, _rejected_fallback = pmid_validator.validate_batch(["1"], _fallback_asset["canonical_name"])
            print(
                f"  [OK] BLOQUEIO (caso controlado): PMID inventado/fora de contexto '1' testado contra "
                f"'{_fallback_asset['canonical_name']}' -> {_rejected_fallback[0]['reason']}"
            )

        # 5. Fase 2: filtro preditivo - seleciona exatamente 8 ativos, categorizados
        print("\n" + "=" * 70)
        print("[FASE 2] RANKING PREDITIVO - SELECIONANDO 8 ATIVOS")
        print("=" * 70)
        selected = ranking_engine.select_predictive_assets(all_evaluations)
        for s in selected:
            print(f"  {s['asset_id']} {s['canonical_name']:<24} → {s['predictive_category']}")

        # Classificação do catálogo COMPLETO pela árvore de precedência (auditoria interna -
        # não afeta o relatório, que só exibe os 8 selecionados acima).
        print("\n  --- Matriz de Classificação (catálogo completo, árvore de precedência) ---")
        tier_counts = {}
        for e in all_evaluations:
            tier = ranking_engine.classify_precedence_tier(e)
            tier_counts[tier] = tier_counts.get(tier, 0) + 1
        for tier, count in tier_counts.items():
            print(f"    {tier}: {count} ativo(s)")

        # Relatório de Cobertura: causa raiz dos ativos em "Dados Insuficientes/Não
        # Classificado" (auditoria interna - não entra no PDF).
        coverage_report = ranking_engine.generate_coverage_report(all_evaluations)
        if coverage_report:
            print(f"\n  --- Relatório de Cobertura ({len(coverage_report)} ativo(s) em {TIER_INSUFFICIENT_DATA}) ---")
            for row in coverage_report:
                print(f"    {row['asset_id']} {row['canonical_name']:<24} -> {row['causa_raiz']}")

        # 6. Fase 3: síntese via LLM (Claude Sonnet 5) apenas para os 8 selecionados,
        #    com dados regulatórios, Risco de Oferta e recomendações recalculados
        #    conforme a jurisdição de cada idioma (Anvisa/PT-BR; INFARMED+CosIng-ECHA/
        #    PT-PT; AEMPS+CosIng-ECHA/ES) - a Tração Científica/Industrial não muda por
        #    jurisdição (é baseada em evidência, não em regulação).
        print("\n" + "=" * 70)
        print("[FASE 3] SÍNTESE VIA LLM (CLAUDE SONNET 5) - 8 ATIVOS SELECIONADOS")
        print("=" * 70)

        evaluations_by_lang = {lang: [] for lang in languages_to_generate}
        regional_authorities = {}

        for item in selected:
            canonical_name = item["canonical_name"]
            asset_id = item["asset_id"]
            print(f"\n🔍 Sintetizando: {canonical_name} ({asset_id}) [{item['predictive_category']}]")

            evidence_summary = llm_engine.summarize_evidence(
                canonical_name, item["_pubmed_traction_results"], item["_patent_traction_results"]
            )
            print(f"   🤖 Resumo: {evidence_summary[:90]}...")

            # Validação determinística pré-relatório dos números de patente
            # citáveis deste ativo (equivalente ao gate de PMIDs, aplicado a
            # patentes via Google Patents - connectors/patents.py.validate_patent_batch).
            # Qualquer patent_id que não exista de fato ou cuja página não
            # confirme a entidade do ativo é removido antes de chegar à LLM
            # ou ao PDF.
            _valid_patent_ids, _rejected_patents = patent_conn.validate_patent_batch(item["patent_ids"], canonical_name)
            if _rejected_patents:
                print(f"   [PAT] {asset_id} - {len(_rejected_patents)} patente(s) rejeitada(s) na validação: {[r['patent_id'] for r in _rejected_patents]}")
            item["patent_ids"] = _valid_patent_ids

            # Trava determinística pós-LLM (core/llm_analysis.py): tier real do ativo
            # (não o predictive_category exibido, que pode vir do preenchimento de
            # último recurso - ver core/predictive_ranking.py) OU confiança baixa
            # bloqueiam qualquer recomendação executiva de compra/priorização,
            # independente do idioma.
            is_insufficient_data = (
                ranking_engine.classify_precedence_tier(item) == TIER_INSUFFICIENT_DATA
                or item["confianca_sinal"] == "BAIXA"
            )
            if is_insufficient_data:
                print(f"   🔒 Trava pós-LLM: {canonical_name} rebaixado para DADOS INSUFICIENTES (tier/confiança abaixo do mínimo) - LLM não é chamada, recomendação executiva suspensa.")

            # Matriz Regulatória cross-jurisdição (Anvisa/UE/FDA) - mesma lógica da Fase 1,
            # não depende do idioma do relatório (calculada uma vez por ativo).
            item_regulatory_matrix = comex_conn.get_regulatory_matrix(asset_id)

            for lang in languages_to_generate:
                # Dossiê regulatório/comex específico da jurisdição do idioma do relatório -
                # usado para a narrativa regional da LLM (generate_recommendations), não para
                # o score da tabela, que usa o pior caso cross-jurisdição (ver abaixo).
                jurisdiction_dossier = comex_conn.get_asset_dossier(asset_id, hs_code=item["hs_code"], lang=lang)
                if lang not in regional_authorities:
                    regional_authorities[lang] = {
                        "regulatory_body": jurisdiction_dossier["regulatory_body"],
                        "trade_source": jurisdiction_dossier["trade_source"]
                    }

                jurisdiction_assessment = score_engine.generate_assessment(
                    pubmed_data=item["_pubmed_traction_results"],
                    patent_data=item["_patent_traction_results"],
                    regulatory_alerts=item_regulatory_matrix["max_severity_status"],
                    commercial_signals=jurisdiction_dossier["sinais_comerciais_comex"],
                    query_hashes=[jurisdiction_dossier["query_hash"]],
                    baseline_36m_count=item["pubmed_baseline_count"]
                )

                assessment_view = {
                    "tracao_cientifica": jurisdiction_assessment["tracao_cientifica"],
                    "tracao_industrial": jurisdiction_assessment["tracao_industrial"],
                    "risco_oferta": jurisdiction_assessment["risco_oferta"],
                    "confianca_sinal": jurisdiction_assessment["confianca_sinal"]
                }
                # Recomendações de alta confiança só para evidência de Nível 2/3
                # (core/entity_resolver.py) - Nível 1/insuficiente força um
                # framing deliberadamente conservador na LLM (ver
                # core/llm_analysis.py generate_recommendations).
                high_confidence = jurisdiction_assessment["nivel_evidencia_maximo"] >= 2
                recs = llm_engine.generate_recommendations(
                    canonical_name, assessment_view,
                    regulatory_alerts=jurisdiction_dossier["alertas_regulatorios"],
                    commercial_signals=jurisdiction_dossier["sinais_comerciais_comex"],
                    lang=lang,
                    pmids=item["pmids"],
                    patent_ids=item["patent_ids"],
                    high_confidence=high_confidence,
                    is_insufficient_data=is_insufficient_data
                )

                evaluations_by_lang[lang].append({
                    "asset_id": asset_id,
                    "canonical_name": canonical_name,
                    "predictive_category": item["predictive_category"],
                    "scientific_traction": jurisdiction_assessment["tracao_cientifica"],
                    "industrial_traction": jurisdiction_assessment["tracao_industrial"],
                    "supply_risk": jurisdiction_assessment["risco_oferta"],
                    "confidence_level": jurisdiction_assessment["confianca_sinal"],
                    "inovacao_pd": recs["inovacao_pd"],
                    "compras_procurement": recs["compras_procurement"],
                    "pmids": item["pmids"],
                    "patent_ids": item["patent_ids"]
                })
            print(f"   🤖 Recomendações geradas para {len(languages_to_generate)} idioma(s) (dados regulatórios por jurisdição).")

        # 7. Gerar o PhytoDemand Report em PDF apenas para o(s) mercado(s)
        #    pedido(s) (--market), com o bloco de Rastreabilidade e Auditoria
        #    (Run ID, Schema/Model Version, Período de Análise, PMIDs e
        #    registros de patentes das evidências coletadas) e a Matriz
        #    Regulatória filtrada estritamente pelo target_market de cada
        #    idioma (core.score_engine.get_display_jurisdictions) - nunca a
        #    lista cheia de jurisdições monitoradas internamente.
        print("\n" + "=" * 70)
        print(f"📄 GERANDO PHYTODEMAND REPORT EM PDF ({', '.join(languages_to_generate)})")
        print("=" * 70)
        for lang in languages_to_generate:
            authorities = regional_authorities.get(lang, {})
            lang_target_market = LANG_TO_TARGET_MARKET[lang]
            jurisdictions_monitored = get_display_jurisdictions(lang_target_market, include_mock=include_mock_jurisdictions)
            print(f"  [{lang}] Matriz Regulatória filtrada para mercado-alvo '{lang_target_market}': {jurisdictions_monitored}")
            pdf_generator.generate_report(
                evaluations_by_lang[lang], lang=lang,
                run_id=run_id, schema_version=SCHEMA_VERSION, model_version=MODEL_VERSION,
                period_start=period_start, period_end=period_end,
                regulatory_body=authorities.get("regulatory_body"),
                trade_source=authorities.get("trade_source"),
                regulatory_matrix={"jurisdictions_monitored": jurisdictions_monitored}
            )

        db_manager.complete_run(run_id, status="COMPLETED")
        print(f"\n✅ Execução do Pipeline Concluída com Sucesso! Run ID: {run_id}")

    except TradeDataSanityError as e:
        db_manager.complete_run(run_id, status="FAILED")
        print(f"\n🚫 TRAVA DE SANIDADE BLOQUEOU A GERAÇÃO DO PDF - Run ID {run_id} marcado como FAILED.")
        print(f"   {e}")
        raise
    except Exception:
        db_manager.complete_run(run_id, status="FAILED")
        print(f"\n❌ Execução do Pipeline falhou - Run ID {run_id} marcado como FAILED.")
        raise


if __name__ == "__main__":
    main()
