"""
Gera um PDF de AUDITORIA (não o PhytoDemand Report principal - ver nota
abaixo) para UM ativo específico, usando exatamente os mesmos componentes de
produção do pipeline (conectores reais, ScoreEngine, LLMAnalysisEngine,
PDFReportGenerator) - sem fabricar nenhum dado.

MOTIVO DE EXISTIR: o ativo Bakuchiol (AT-001) não é selecionado
organicamente para a tabela de 8 ativos do PhytoDemand Report principal
(core/predictive_ranking.py `select_predictive_assets`), porque não tem
evidência científica/industrial verificada suficiente (fica no tier "Dados
Insuficientes/Não Classificado" - isso É o comportamento correto e
esperado, não um bug). Para permitir a um auditor confirmar diretamente,
num PDF real gerado pelo pipeline, que o PMID fabricado 42596530 continua
sendo rejeitado e que o ativo cai corretamente para "dados insuficientes"
em vez de citar essa referência inválida, este script contorna
DELIBERADAMENTE só a etapa de seleção dos 8 (que é um filtro de
priorização de negócio, não uma etapa de validação) - toda coleta de
evidência, validação de PMID/patente, cálculo de score e geração de
recomendação usa os componentes reais de produção, inalterados.

O PDF gerado é salvo em reports/output/audit_single_asset/ (diretório
separado do PhytoDemand Report principal) e claramente rotulado como
relatório de auditoria de ativo único - não deve ser confundido com o
PhytoDemand Report de 8 ativos.

Uso: python scripts/audit_report_single_asset.py AT-001
"""
import sys
import io

if sys.stdout.encoding != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

from core.entity_resolver import EntityResolver
from connectors.pubmed import PubMedConnector
from connectors.pubmed_validator import PMIDValidator
from connectors.patents import PatentConnector
from connectors.regulatory_comex import RegulatoryComexConnector
from core.score_engine import ScoreEngine, SCI_BASELINE_HISTORY_DAYS
from core.database import DatabaseManager
from core.llm_analysis import LLMAnalysisEngine
from core.predictive_ranking import PredictiveRankingEngine, TIER_INSUFFICIENT_DATA
from reports.pdf_generator import PDFReportGenerator
from main import resolve_search_query

LANG = "PT-BR"


def main():
    asset_id = sys.argv[1] if len(sys.argv) > 1 else "AT-001"

    resolver = EntityResolver(taxonomy_path="data/taxonomy/ativos_mvp.json")
    pubmed_conn = PubMedConnector(resolver=resolver)
    pmid_validator = PMIDValidator(pubmed_conn)
    patent_conn = PatentConnector(resolver=resolver)
    comex_conn = RegulatoryComexConnector(resolver=resolver)
    score_engine = ScoreEngine()
    ranking_engine = PredictiveRankingEngine()
    llm_engine = LLMAnalysisEngine()
    db_manager = DatabaseManager()
    pdf_generator = PDFReportGenerator(output_dir="reports/output/audit_single_asset")

    asset = next(a for a in resolver.assets if a["asset_id"] == asset_id)
    canonical_name = asset["canonical_name"]
    exclusions = asset.get("exclusions", [])
    search_query = resolve_search_query(asset)
    hs_code = asset.get("hs_codes", [None])[0]

    run_id = db_manager.start_run(domain_code="DERMOCOSMETICS", product_code="PHYTODEMAND_REPORT",
                                   model_version="2.3.0", schema_version="2.0.0")
    print(f"AUDITORIA DE ATIVO ÚNICO - {canonical_name} ({asset_id}) - Run ID: {run_id}")

    # ---- Coleta idêntica à Fase 1 de main.py ----
    pubmed_search = pubmed_conn.search_articles(search_query, exclusions=exclusions, max_results=5)
    verified_pmids, rejected_pmids = pmid_validator.validate_batch(pubmed_search["pmids"], canonical_name)
    print(f"PMIDs candidatos (15d, novidade): {pubmed_search['pmids']}")
    print(f"PMIDs validados/aceitos: {verified_pmids}")
    for r in rejected_pmids:
        print(f"PMID REJEITADO: {r['pmid']} -> {r['reason']}")

    period_start, period_end = pubmed_search["date_range"]["start"], pubmed_search["date_range"]["end"]

    pubmed_traction_search = pubmed_conn.search_articles(search_query, exclusions=exclusions, max_results=10, days=pubmed_conn.TRACTION_WINDOW_DAYS)
    pubmed_traction_results = [pubmed_conn.fetch_article_details(pmid) for pmid in pubmed_traction_search["pmids"]]

    pubmed_baseline_search = pubmed_conn.search_articles(
        search_query, exclusions=exclusions, max_results=0,
        days=pubmed_conn.TRACTION_WINDOW_DAYS + SCI_BASELINE_HISTORY_DAYS, end_days_ago=pubmed_conn.TRACTION_WINDOW_DAYS
    )

    patent_search = patent_conn.fetch_patents(search_query, exclusions=exclusions)
    patent_traction_search = patent_conn.fetch_patents(search_query, exclusions=exclusions, days=patent_conn.TRACTION_WINDOW_DAYS)
    # Tração Industrial só conta patentes validadas ao vivo (Google Patents) - mesma
    # correção aplicada em main.py Fase 1 (ver METHODOLOGY.md).
    valid_traction_ids, _ = patent_conn.validate_patent_batch(
        [p["patent_id"] for p in patent_traction_search["results"]], canonical_name
    )
    valid_traction_ids_set = set(valid_traction_ids)
    patent_traction_results = [
        patent_conn.process_patent(p) for p in patent_traction_search["results"]
        if p["patent_id"] in valid_traction_ids_set
    ]

    dossier = comex_conn.get_asset_dossier(asset_id, hs_code=hs_code, lang=LANG)
    regulatory_matrix = comex_conn.get_regulatory_matrix(asset_id)
    regulatory_alerts = regulatory_matrix["max_severity_status"]

    assessment = score_engine.generate_assessment(
        pubmed_data=pubmed_traction_results, patent_data=patent_traction_results,
        regulatory_alerts=regulatory_alerts, commercial_signals=dossier["sinais_comerciais_comex"],
        query_hashes=[], baseline_36m_count=pubmed_baseline_search["count"]
    )
    print(f"Assessment real: {assessment['tracao_cientifica']} / {assessment['tracao_industrial']} / {assessment['confianca_sinal']}")

    # PMIDs/patentes citados no PDF de auditoria: união entre a novidade de 15
    # dias (verified_pmids/valid_patent_ids, mais abaixo) e a evidência real
    # que efetivamente entrou na fórmula de T_c/T_i (janela de 12 meses) -
    # MESMA correção aplicada em main.py (achado de auditoria 2026-08-20: este
    # script tinha o mesmo bug, citando só a novidade de 15 dias e por isso
    # podendo declarar "nenhuma evidência disponível" para um ativo cujo score
    # foi calculado a partir de evidência real da janela de 12 meses - ver
    # comentário longo em main.py, "PubMed (Tração)", para a causa raiz
    # completa). extract_verified_pmids()/extract_traction_patent_ids()
    # (core/score_engine.py) são a MESMA fonte usada pela própria fórmula
    # internamente - nunca uma extração reimplementada à parte aqui, para as
    # duas nunca mais poderem divergir silenciosamente.
    traction_pmids = score_engine.extract_verified_pmids(pubmed_traction_results)
    traction_patent_ids = score_engine.extract_traction_patent_ids(patent_traction_results)

    evaluation = {
        "asset_id": asset_id, "canonical_name": canonical_name,
        "tracao_cientifica": assessment["tracao_cientifica"], "tracao_industrial": assessment["tracao_industrial"],
        "risco_oferta": assessment["risco_oferta"], "alerta_regulatorio": assessment["alerta_regulatorio"],
        "sinal_comercial_comex": assessment["sinal_comercial_comex"], "confianca_sinal": assessment["confianca_sinal"],
        "evidencias_verificadas": assessment["evidencias_verificadas"], "nivel_evidencia_maximo": assessment["nivel_evidencia_maximo"],
        "predictive_category": "Auditoria - Ativo Único (fora da seleção Top-8)"
    }

    # ---- Validação de patentes (mesma etapa da Fase 3 de main.py) ----
    patent_ids_candidates = [p["patent_id"] for p in patent_search["results"]]
    valid_patent_ids, rejected_patents = patent_conn.validate_patent_batch(patent_ids_candidates, canonical_name)
    print(f"Patentes candidatas: {patent_ids_candidates} | Validadas: {valid_patent_ids}")

    # União final (mesma lógica de main.py 'cited_pmids'/'cited_patent_ids'):
    # novidade de 15 dias + evidência real da janela de 12 meses que entrou na
    # fórmula. dict.fromkeys() deduplica preservando a ordem.
    cited_pmids = list(dict.fromkeys(verified_pmids + traction_pmids))
    cited_patent_ids = list(dict.fromkeys(valid_patent_ids + traction_patent_ids))
    print(f"Evidência citável no relatório de auditoria (união 15d + tração 12m): {cited_pmids} {cited_patent_ids}")

    # ---- Trava pós-LLM real (mesma condição de main.py Fase 3) ----
    is_insufficient_data = (
        ranking_engine.classify_precedence_tier(evaluation) == TIER_INSUFFICIENT_DATA
        or evaluation["confianca_sinal"] == "BAIXA"
    )
    print(f"Tier de precedência: {ranking_engine.classify_precedence_tier(evaluation)} | is_insufficient_data={is_insufficient_data}")

    recs = llm_engine.generate_recommendations(
        canonical_name, assessment, regulatory_alerts=dossier["alertas_regulatorios"],
        commercial_signals=dossier["sinais_comerciais_comex"], lang=LANG,
        pmids=cited_pmids, patent_ids=cited_patent_ids,
        high_confidence=assessment["nivel_evidencia_maximo"] >= 2, is_insufficient_data=is_insufficient_data
    )
    print(f"Recomendação Inovação & P&D: {recs['inovacao_pd']}")
    print(f"Recomendação Compras & Procurement: {recs['compras_procurement']}")

    row = {**evaluation, "scientific_traction": assessment["tracao_cientifica"], "industrial_traction": assessment["tracao_industrial"],
           "supply_risk": assessment["risco_oferta"], "confidence_level": assessment["confianca_sinal"],
           "regulatory_source": regulatory_alerts.get("source"), "regulatory_last_verified": regulatory_alerts.get("last_verified"),
           "inovacao_pd": recs["inovacao_pd"], "compras_procurement": recs["compras_procurement"],
           "pmids": cited_pmids, "patent_ids": cited_patent_ids}

    from core.score_engine import get_display_jurisdictions
    pdf_path = pdf_generator.generate_report(
        [row], lang=LANG, run_id=run_id, schema_version="2.0.0", model_version="2.3.0",
        period_start=period_start, period_end=period_end,
        regulatory_body=dossier["regulatory_body"], trade_source=dossier["trade_source"],
        regulatory_matrix={"jurisdictions_monitored": get_display_jurisdictions("BR")}
    )
    db_manager.complete_run(run_id, status="COMPLETED")
    print(f"\nPDF DE AUDITORIA GERADO: {pdf_path}")


if __name__ == "__main__":
    main()
