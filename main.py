import sys
import io

if sys.stdout.encoding != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

from core.entity_resolver import EntityResolver
from connectors.pubmed import PubMedConnector
from connectors.patents import PatentConnector
from connectors.regulatory_comex import RegulatoryComexConnector
from core.score_engine import ScoreEngine
from core.database import DatabaseManager
from core.llm_analysis import LLMAnalysisEngine
from core.predictive_ranking import PredictiveRankingEngine
from reports.pdf_generator import PDFReportGenerator

LANGUAGES = ["PT-BR", "PT-PT", "ES"]


def resolve_search_query(asset: dict) -> str:
    """Botanical_name (Latin, melhor para PubMed/patentes) > INCI (inglês) > primeiro alias."""
    return asset.get("botanical_name") or asset.get("inci_name") or asset.get("aliases", [asset["canonical_name"]])[0]


def main():
    print("=" * 70)
    print("       PLATAFORMA VANGUARD DATA - PIPELINE DE INTELIGÊNCIA")
    print("=" * 70)

    # 1. Carregar Taxonomia de Ativos (catálogo global de 30)
    taxonomy_file = "data/taxonomy/ativos_mvp.json"
    resolver = EntityResolver(taxonomy_path=taxonomy_file)
    assets = resolver.assets
    print(f"\n[1] Taxonomia carregada: {len(assets)} ativo(s) configurado(s).")

    # 2. Inicializar Conectores, Engine de Score, Banco, Ranking Preditivo, LLM e Relatórios
    pubmed_conn = PubMedConnector(resolver=resolver)
    patent_conn = PatentConnector(resolver=resolver)
    comex_conn = RegulatoryComexConnector(resolver=resolver)

    score_engine = ScoreEngine()
    db_manager = DatabaseManager()
    ranking_engine = PredictiveRankingEngine()
    llm_engine = LLMAnalysisEngine()
    pdf_generator = PDFReportGenerator()

    # 3. Fase 1: coletar evidências e calcular scores para TODOS os 30 ativos
    #    (sem chamadas de LLM ainda - só os 8 selecionados no ranking preditivo
    #    passam pela síntese via IA, evitando custo/tempo desnecessário).
    print("\n" + "=" * 70)
    print("[FASE 1] COLETA E SCORING DOS 30 ATIVOS DO CATÁLOGO")
    print("=" * 70)

    all_evaluations = []
    for asset in assets:
        asset_id = asset["asset_id"]
        canonical_name = asset["canonical_name"]
        search_query = resolve_search_query(asset)

        pmids = pubmed_conn.search_articles(search_query, max_results=5)
        pubmed_results = [pubmed_conn.fetch_article_details(p) for p in pmids]

        raw_patents = patent_conn.fetch_patents_mock(search_query)
        patent_results = [patent_conn.process_patent(p) for p in raw_patents]

        reg_data = comex_conn.fetch_regulatory_status(asset_id)
        hs_code = asset.get("hs_codes", [None])[0]
        trade_data = comex_conn.fetch_import_volume_mock(hs_code, asset_id=asset_id)

        assessment = score_engine.generate_assessment(
            pubmed_data=pubmed_results,
            patent_data=patent_results,
            reg_data=reg_data,
            trade_data=trade_data
        )

        db_manager.save_evaluation(asset_id, canonical_name, assessment)

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
            "_pubmed_results": pubmed_results,
            "_patent_results": patent_results
        })

    # 4. Fase 2: filtro preditivo - seleciona exatamente 8 ativos, categorizados
    print("\n" + "=" * 70)
    print("[FASE 2] RANKING PREDITIVO - SELECIONANDO 8 ATIVOS")
    print("=" * 70)
    selected = ranking_engine.select_predictive_assets(all_evaluations)
    for s in selected:
        print(f"  {s['asset_id']} {s['canonical_name']:<24} → {s['predictive_category']}")

    # 5. Fase 3: síntese via LLM (Claude Sonnet 5) apenas para os 8 selecionados,
    #    com recomendações localizadas por idioma (Anvisa / INFARMED / AEMPS).
    print("\n" + "=" * 70)
    print("[FASE 3] SÍNTESE VIA LLM (CLAUDE SONNET 5) - 8 ATIVOS SELECIONADOS")
    print("=" * 70)

    evaluations_by_lang = {lang: [] for lang in LANGUAGES}

    for item in selected:
        canonical_name = item["canonical_name"]
        print(f"\n🔍 Sintetizando: {canonical_name} ({item['asset_id']}) [{item['predictive_category']}]")

        assessment_view = {
            "tracao_cientifica": item["tracao_cientifica"],
            "tracao_industrial": item["tracao_industrial"],
            "risco_oferta": item["risco_oferta"],
            "confianca_sinal": item["confianca_sinal"]
        }

        evidence_summary = llm_engine.summarize_evidence(canonical_name, item["_pubmed_results"], item["_patent_results"])
        print(f"   🤖 Resumo: {evidence_summary[:90]}...")

        for lang in LANGUAGES:
            recs = llm_engine.generate_recommendations(canonical_name, assessment_view, lang=lang)
            evaluations_by_lang[lang].append({
                "asset_id": item["asset_id"],
                "canonical_name": canonical_name,
                "predictive_category": item["predictive_category"],
                "scientific_traction": item["tracao_cientifica"],
                "industrial_traction": item["tracao_industrial"],
                "supply_risk": item["risco_oferta"],
                "confidence_level": item["confianca_sinal"],
                "inovacao_pd": recs["inovacao_pd"],
                "compras_procurement": recs["compras_procurement"]
            })
        print(f"   🤖 Recomendações geradas para {len(LANGUAGES)} idiomas.")

    # 6. Gerar Relatórios Executivos em PDF (8 ativos preditivos, 3 idiomas)
    print("\n" + "=" * 70)
    print("📄 GERANDO RELATÓRIOS EXECUTIVOS EM PDF (PT-BR, PT-PT, ES)")
    print("=" * 70)
    for lang in LANGUAGES:
        pdf_generator.generate_report(evaluations_by_lang[lang], lang=lang)

    print("\n✅ Execução do Pipeline Concluída com Sucesso!")


if __name__ == "__main__":
    main()
