import sys
import io
import re
import json

if sys.stdout.encoding != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

from core.entity_resolver import EntityResolver
from connectors.pubmed import PubMedConnector
from connectors.patents import PatentConnector
from connectors.regulatory_comex import RegulatoryComexConnector
from core.score_engine import ScoreEngine, MODEL_VERSION
from core.database import DatabaseManager, SCHEMA_VERSION
from core.llm_analysis import LLMAnalysisEngine
from core.predictive_ranking import PredictiveRankingEngine
from reports.pdf_generator import PDFReportGenerator

LANGUAGES = ["PT-BR", "PT-PT", "ES"]


def resolve_search_query(asset: dict) -> str:
    """Nome botânico em Latim (melhor para PubMed/patentes) > CAS > INCI (inglês) > primeiro alias."""
    identifiers = asset.get("botanical_or_cas", [])
    botanical = next((i for i in identifiers if not re.match(r'^\d{2,7}-\d{2}-\d$', i)), None)
    return botanical or (identifiers[0] if identifiers else None) or asset.get("inci_name") or asset.get("aliases", [asset["canonical_name"]])[0]


def main():
    print("=" * 70)
    print("       PLATAFORMA VANGUARD DATA - PIPELINE DE INTELIGÊNCIA")
    print("=" * 70)

    # 1. Carregar Taxonomia de Ativos (catálogo global, incl. Ácidos Cosmecêuticos)
    taxonomy_file = "data/taxonomy/ativos_mvp.json"
    resolver = EntityResolver(taxonomy_path=taxonomy_file)
    assets = resolver.assets
    print(f"\n[1] Taxonomia carregada: {len(assets)} ativo(s) configurado(s) - Schema v{SCHEMA_VERSION}.")

    # 2. Inicializar Conectores, Engine de Score, Banco, Ranking Preditivo, LLM e Relatórios
    pubmed_conn = PubMedConnector(resolver=resolver)
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
        # 4. Fase 1: coletar evidências e calcular scores para TODOS os ativos do catálogo
        #    (sem chamadas de LLM ainda - só os 8 selecionados no ranking preditivo
        #    passam pela síntese via IA, evitando custo/tempo desnecessário).
        print("\n" + "=" * 70)
        print(f"[FASE 1] COLETA E SCORING DOS {len(assets)} ATIVOS DO CATÁLOGO")
        print("=" * 70)

        all_evaluations = []
        period_start = None
        period_end = None
        for asset in assets:
            asset_id = asset["asset_id"]
            canonical_name = asset["canonical_name"]
            exclusions = asset.get("exclusions", [])
            search_query = resolve_search_query(asset)

            # PubMed: aplica as exclusões do ativo diretamente na query, restringe à janela de 15
            # dias (data de publicação), recebe PMIDs + query_hash
            pubmed_search = pubmed_conn.search_articles(search_query, exclusions=exclusions, max_results=5)
            pubmed_results = [pubmed_conn.fetch_article_details(pmid) for pmid in pubmed_search["pmids"]]
            if period_start is None:
                period_start = pubmed_search["date_range"]["start"]
                period_end = pubmed_search["date_range"]["end"]

            # Patentes: aplica exclusões, restringe à janela de 15 dias (publication_date), deduplica
            # por família de patentes, recebe resultados + query_hash
            patent_search = patent_conn.fetch_patents_mock(search_query, exclusions=exclusions)
            patent_results = [patent_conn.process_patent(p) for p in patent_search["results"]]

            # Regulatório/Comex: dossiê consolidado - Alertas Regulatórios e Sinais Comerciais/Comex + query_hash.
            # Jurisdição baseline PT-BR (Anvisa) usada para a coleta e o ranking preditivo; os 8
            # ativos selecionados têm seus dados regulatórios recalculados por idioma na Fase 3.
            hs_code = asset.get("hs_codes", [None])[0]
            dossier = comex_conn.get_asset_dossier(asset_id, hs_code=hs_code, lang="PT-BR")
            regulatory_alerts = dossier["alertas_regulatorios"]
            commercial_signals = dossier["sinais_comerciais_comex"]

            query_hashes = [pubmed_search["query_hash"], patent_search["query_hash"], dossier["query_hash"]]

            assessment = score_engine.generate_assessment(
                pubmed_data=pubmed_results,
                patent_data=patent_results,
                regulatory_alerts=regulatory_alerts,
                commercial_signals=commercial_signals,
                query_hashes=query_hashes
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
                evaluation_id, source_type="PATENTS", query_hash=patent_search["query_hash"],
                raw_response_summary=json.dumps({
                    "total_found": patent_search["total_found"],
                    "total_after_dedup": patent_search["total_after_dedup"],
                    "duplicate_families_collapsed": patent_search["duplicate_families_collapsed"]
                }, ensure_ascii=False),
                items_found=patent_search["total_after_dedup"]
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
                "tracao_industrial": assessment["tracao_industrial"],
                "risco_oferta": assessment["risco_oferta"],
                "confianca_sinal": assessment["confianca_sinal"],
                "pmids": pubmed_search["pmids"],
                "patent_ids": [p["patent_id"] for p in patent_search["results"]],
                "hs_code": hs_code,
                "_pubmed_results": pubmed_results,
                "_patent_results": patent_results
            })

        # 5. Fase 2: filtro preditivo - seleciona exatamente 8 ativos, categorizados
        print("\n" + "=" * 70)
        print("[FASE 2] RANKING PREDITIVO - SELECIONANDO 8 ATIVOS")
        print("=" * 70)
        selected = ranking_engine.select_predictive_assets(all_evaluations)
        for s in selected:
            print(f"  {s['asset_id']} {s['canonical_name']:<24} → {s['predictive_category']}")

        # 6. Fase 3: síntese via LLM (Claude Sonnet 5) apenas para os 8 selecionados,
        #    com dados regulatórios, Risco de Oferta e recomendações recalculados
        #    conforme a jurisdição de cada idioma (Anvisa/PT-BR; INFARMED+CosIng-ECHA/
        #    PT-PT; AEMPS+CosIng-ECHA/ES) - a Tração Científica/Industrial não muda por
        #    jurisdição (é baseada em evidência, não em regulação).
        print("\n" + "=" * 70)
        print("[FASE 3] SÍNTESE VIA LLM (CLAUDE SONNET 5) - 8 ATIVOS SELECIONADOS")
        print("=" * 70)

        evaluations_by_lang = {lang: [] for lang in LANGUAGES}
        regional_authorities = {}

        for item in selected:
            canonical_name = item["canonical_name"]
            asset_id = item["asset_id"]
            print(f"\n🔍 Sintetizando: {canonical_name} ({asset_id}) [{item['predictive_category']}]")

            evidence_summary = llm_engine.summarize_evidence(canonical_name, item["_pubmed_results"], item["_patent_results"])
            print(f"   🤖 Resumo: {evidence_summary[:90]}...")

            for lang in LANGUAGES:
                # Dossiê regulatório/comex específico da jurisdição do idioma do relatório
                jurisdiction_dossier = comex_conn.get_asset_dossier(asset_id, hs_code=item["hs_code"], lang=lang)
                if lang not in regional_authorities:
                    regional_authorities[lang] = {
                        "regulatory_body": jurisdiction_dossier["regulatory_body"],
                        "trade_source": jurisdiction_dossier["trade_source"]
                    }

                jurisdiction_assessment = score_engine.generate_assessment(
                    pubmed_data=item["_pubmed_results"],
                    patent_data=item["_patent_results"],
                    regulatory_alerts=jurisdiction_dossier["alertas_regulatorios"],
                    commercial_signals=jurisdiction_dossier["sinais_comerciais_comex"],
                    query_hashes=[jurisdiction_dossier["query_hash"]]
                )

                assessment_view = {
                    "tracao_cientifica": jurisdiction_assessment["tracao_cientifica"],
                    "tracao_industrial": jurisdiction_assessment["tracao_industrial"],
                    "risco_oferta": jurisdiction_assessment["risco_oferta"],
                    "confianca_sinal": jurisdiction_assessment["confianca_sinal"]
                }
                recs = llm_engine.generate_recommendations(
                    canonical_name, assessment_view,
                    regulatory_alerts=jurisdiction_dossier["alertas_regulatorios"],
                    commercial_signals=jurisdiction_dossier["sinais_comerciais_comex"],
                    lang=lang
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
            print(f"   🤖 Recomendações geradas para {len(LANGUAGES)} idiomas (dados regulatórios por jurisdição).")

        # 7. Gerar o PhytoDemand Report em PDF (8 ativos preditivos, 3 idiomas), com o
        #    bloco de Rastreabilidade e Auditoria (Run ID, Schema/Model Version, Período de
        #    Análise, PMIDs e registros de patentes das evidências coletadas)
        print("\n" + "=" * 70)
        print("📄 GERANDO PHYTODEMAND REPORT EM PDF (PT-BR, PT-PT, ES)")
        print("=" * 70)
        for lang in LANGUAGES:
            authorities = regional_authorities.get(lang, {})
            pdf_generator.generate_report(
                evaluations_by_lang[lang], lang=lang,
                run_id=run_id, schema_version=SCHEMA_VERSION, model_version=MODEL_VERSION,
                period_start=period_start, period_end=period_end,
                regulatory_body=authorities.get("regulatory_body"),
                trade_source=authorities.get("trade_source")
            )

        db_manager.complete_run(run_id, status="COMPLETED")
        print(f"\n✅ Execução do Pipeline Concluída com Sucesso! Run ID: {run_id}")

    except Exception:
        db_manager.complete_run(run_id, status="FAILED")
        print(f"\n❌ Execução do Pipeline falhou - Run ID {run_id} marcado como FAILED.")
        raise


if __name__ == "__main__":
    main()
