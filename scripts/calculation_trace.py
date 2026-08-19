"""
Ferramenta de auditoria: gera um rastro de cálculo (calculation trace) para
um ou mais ativos, chamando literalmente as mesmas funções de produção
usadas por main.py (core.score_engine.ScoreEngine, connectors.pubmed,
connectors.patents, connectors.regulatory_comex) com dados coletados
AO VIVO (NCBI E-utilities real, Google Patents real para validação) -
nunca reimplementa a fórmula em paralelo. O objetivo é permitir que
qualquer pessoa confirme, número por número, que o score final exibido
no PDF deriva de fato da evidência bruta coletada, e não de um valor
hardcoded ou desacoplado dos dados reais.

Uso: python scripts/calculation_trace.py AT-009 AT-015
"""
import sys
import io
import json

if sys.stdout.encoding != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

from core.entity_resolver import EntityResolver
from connectors.pubmed import PubMedConnector
from connectors.patents import PatentConnector
from connectors.regulatory_comex import RegulatoryComexConnector
from core.score_engine import ScoreEngine, SCI_BASELINE_HISTORY_DAYS
from core.formatting import format_usd_estimate
from main import resolve_search_query

LANG = "PT-BR"


def trace_asset(asset_id: str, resolver, pubmed_conn, patent_conn, comex_conn, score_engine):
    asset = next(a for a in resolver.assets if a["asset_id"] == asset_id)
    canonical_name = asset["canonical_name"]
    exclusions = asset.get("exclusions", [])
    search_query = resolve_search_query(asset)
    hs_code = asset.get("hs_codes", [None])[0]

    print(f"\n{'='*78}")
    print(f"RASTRO DE CÁLCULO - {canonical_name} ({asset_id})")
    print(f"{'='*78}")
    print(f"Query de busca (PubMed/Patentes): {search_query!r}  |  Exclusões: {exclusions}")
    print(f"HS Code (comércio exterior): {hs_code}")

    # ---- 1. PubMed (janela de tração, 12 meses) - ENTRADA REAL DE T_c ----
    print("\n--- [1] PubMed - janela de Tração Científica (12 meses, dados AO VIVO da NCBI) ---")
    traction_search = pubmed_conn.search_articles(search_query, exclusions=exclusions, max_results=10, days=pubmed_conn.TRACTION_WINDOW_DAYS)
    print(f"Query exata enviada ao NCBI: {traction_search['query']}")
    print(f"Janela: {traction_search['date_range']['start']} a {traction_search['date_range']['end']}")
    print(f"Contagem total reportada pelo NCBI (esearch 'count'): {traction_search['count']}")
    print(f"PMIDs candidatos retornados: {traction_search['pmids']}")

    traction_results = []
    for pmid in traction_search["pmids"]:
        details = pubmed_conn.fetch_article_details(pmid)
        traction_results.append(details)
        link = f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
        status = "VERIFICADO (entra no cálculo de T_c)" if details.get("verified") else "REJEITADO (não entra no cálculo)"
        print(f"  PMID {pmid} -> {status}")
        print(f"    Link: {link}")
        print(f"    Título (NCBI, bruto): {details.get('title', '')[:140]}")
        if details.get("entity_match"):
            em = details["entity_match"]
            print(f"    Entity Resolution: match_type={em.get('match_type')} confidence_score={em.get('confidence_score')} relevance_level={em.get('relevance_level')}")

    verified_count = sum(1 for d in traction_results if d.get("entity_match"))
    print(f"\nTotal de PMIDs VERIFICADOS que entram na fórmula de T_c: {verified_count}")

    # ---- 2. PubMed (linha de base histórica, 36 meses) - ENTRADA REAL de [G] ----
    print("\n--- [2] PubMed - linha de base histórica (36 meses, componente [G], dados AO VIVO) ---")
    baseline_search = pubmed_conn.search_articles(
        search_query, exclusions=exclusions, max_results=0,
        days=pubmed_conn.TRACTION_WINDOW_DAYS + SCI_BASELINE_HISTORY_DAYS,
        end_days_ago=pubmed_conn.TRACTION_WINDOW_DAYS
    )
    print(f"Janela: {baseline_search['date_range']['start']} a {baseline_search['date_range']['end']}")
    print(f"Contagem bruta reportada pelo NCBI (esearch 'count', sem efetch): {baseline_search['count']}")

    # ---- 3. Patentes (janela de tração, 12 meses) - ENTRADA de T_i (BASE MOCK) ----
    print("\n--- [3] Patentes - janela de Tração Industrial (12 meses) ---")
    print("ATENÇÃO: connectors/patents.py NÃO consulta uma API real de patentes - fetch_patents_mock()")
    print("retorna uma base de dados FABRICADA/ilustrativa embutida no código (_mock_database()).")
    patent_traction_search = patent_conn.fetch_patents_mock(search_query, exclusions=exclusions, days=patent_conn.TRACTION_WINDOW_DAYS)
    print(f"Registros mock retornados (após dedup por família): {patent_traction_search['total_after_dedup']}")

    patent_traction_results = []
    for raw in patent_traction_search["results"]:
        processed = patent_conn.process_patent(raw)
        patent_traction_results.append(processed)
        pid = processed["patent_id"]
        gp_link = f"https://patents.google.com/patent/{pid}/en"
        print(f"  Patente {pid} (mock, assignee={processed.get('assignee')})")
        print(f"    Título (mock, usado para Entity Resolution local): {processed.get('title')}")
        em = processed.get("entity_match")
        print(f"    Entity Resolution LOCAL (contra o próprio título mock): {'match' if em else 'sem match'}"
              + (f" (confidence_score={em.get('confidence_score')})" if em else ""))
        # Validação real ao vivo contra o Google Patents - desde a correção de
        # 2026-08-19 (ver METHODOLOGY.md), este resultado É o que decide se a
        # patente entra ou não na fórmula de T_i (não é mais só cosmético).
        live = patent_conn.validate_patent(pid, canonical_name)
        print(f"    Validação AO VIVO no Google Patents ({gp_link}): valid={live['valid']}"
              + (f" -> {live['reason']}" if not live["valid"] else " (existe e confirma a entidade)"))

    valid_patent_ids_live = {
        p["patent_id"] for p in patent_traction_results
        if patent_conn.validate_patent(p["patent_id"], canonical_name)["valid"]
    }
    n_patents_mock_total = len(patent_traction_results)
    n_patents_feeding_score = len(valid_patent_ids_live)
    # CORRIGIDO (2026-08-19): T_i agora é calculada só com as patentes que sobrevivem
    # à validação ao vivo - filtra ANTES de chamar generate_assessment(), igual ao
    # main.py em produção (ver METHODOLOGY.md, "Limitação conhecida" - histórico).
    patent_traction_results = [p for p in patent_traction_results if p["patent_id"] in valid_patent_ids_live]
    print(f"\nPatentes mock retornadas pela busca: {n_patents_mock_total} | Validadas ao vivo (entram em T_i): {n_patents_feeding_score}")

    # ---- 4. Comércio Exterior (MOCK, nunca uma API real neste ambiente) ----
    print("\n--- [4] Comércio Exterior / Regulatório (usado em Risco de Oferta e Confiança) ---")
    dossier = comex_conn.get_asset_dossier(asset_id, hs_code=hs_code, lang=LANG)
    commercial = dossier["sinais_comerciais_comex"]
    print(f"Fonte declarada: {dossier['trade_source']}")
    print("ATENÇÃO: sem credenciais de API configuradas (.env) - este valor é MOCK determinístico")
    print("(seed SHA256 de asset_id+região), NÃO uma chamada real à Comex Stat/Eurostat.")
    print(f"Volume anual BRUTO (exato, antes do arredondamento): USD {commercial.get('volume_usd_annual')}")
    print(f"Faixa exibida no relatório (core.formatting.format_usd_estimate): {format_usd_estimate(commercial.get('volume_usd_annual'), lang=LANG)}")
    print(f"Fornecedores mapeados (suppliers_count): {commercial.get('suppliers_count')}")
    print(f"Tendência (trend): {commercial.get('trend')}")

    regulatory_matrix = comex_conn.get_regulatory_matrix(asset_id)
    regulatory_alerts = regulatory_matrix["max_severity_status"]
    print(f"Alerta Regulatório (pior caso entre jurisdições monitoradas): {regulatory_alerts.get('restriction_level')} "
          f"(fonte: {regulatory_matrix['max_severity_source']})")

    # ---- 5. CHAMADA REAL À FUNÇÃO DE PRODUÇÃO - nada reimplementado ----
    print("\n--- [5] Score final: chamada literal a ScoreEngine.generate_assessment() (produção) ---")
    assessment = score_engine.generate_assessment(
        pubmed_data=traction_results,
        patent_data=patent_traction_results,
        regulatory_alerts=regulatory_alerts,
        commercial_signals=commercial,
        query_hashes=[],
        baseline_36m_count=baseline_search["count"]
    )
    breakdown = assessment["tracao_cientifica_componentes"]
    print(f"Componentes V/G/A/Q (calculate_scientific_traction_breakdown): {breakdown['components']}")
    print(f"Pesos aplicados: {breakdown['weights']}")
    print(f"  V = min(10, soma(confidence_score dos {verified_count} PMIDs verificados) * 2.2)")
    print(f"  G = 5 + 5*clamp((taxa_atual - taxa_base)/taxa_base, -1, 1) | taxa_atual={verified_count}/365, taxa_base={baseline_search['count']}/{SCI_BASELINE_HISTORY_DAYS}")
    print(f"  A = 10 * (nº matches com relevance_level>=2 / {verified_count if verified_count else 1})")
    print(f"  Q = 10 * (média dos confidence_score dos {verified_count} PMIDs verificados)")
    print(f"TRAÇÃO CIENTÍFICA FINAL: {assessment['tracao_cientifica']}")
    print(f"  = {breakdown['weights']['V']}*{breakdown['components']['V']} + {breakdown['weights']['G']}*{breakdown['components']['G']}"
          f" + {breakdown['weights']['A']}*{breakdown['components']['A']} + {breakdown['weights']['Q']}*{breakdown['components']['Q']}")
    print(f"\nTRAÇÃO INDUSTRIAL FINAL: {assessment['tracao_industrial']}"
          f"  (derivada de {n_patents_feeding_score} patente(s) validada(s) ao vivo no Google Patents - ver [3] acima)")
    print(f"\nRISCO DE OFERTA: {assessment['risco_oferta']}  (Alerta Regulatório={assessment['alerta_regulatorio']}, Sinal Comercial={assessment['sinal_comercial_comex']})")
    print(f"CONFIANÇA DO SINAL: {assessment['confianca_sinal']}")
    print(f"Evidências verificadas (PubMed verificado + patentes com match local): {assessment['evidencias_verificadas']}")

    return {
        "asset_id": asset_id, "canonical_name": canonical_name,
        "pmids_verified": [d["pmid"] for d in traction_results if d.get("entity_match")],
        "baseline_36m_count": baseline_search["count"],
        "patents_feeding_score": [p["patent_id"] for p in patent_traction_results],
        "patents_live_valid_count": n_patents_feeding_score,
        "commercial_raw": commercial,
        "assessment": assessment
    }


if __name__ == "__main__":
    asset_ids = sys.argv[1:] or ["AT-009", "AT-015"]

    resolver = EntityResolver(taxonomy_path="data/taxonomy/ativos_mvp.json")
    pubmed_conn = PubMedConnector(resolver=resolver)
    patent_conn = PatentConnector(resolver=resolver)
    comex_conn = RegulatoryComexConnector(resolver=resolver)
    score_engine = ScoreEngine()

    results = []
    for asset_id in asset_ids:
        results.append(trace_asset(asset_id, resolver, pubmed_conn, patent_conn, comex_conn, score_engine))

    print(f"\n\n{'='*78}\nRESUMO (JSON bruto dos assessments retornados por generate_assessment)\n{'='*78}")
    print(json.dumps([r["assessment"] for r in results], ensure_ascii=False, indent=2))
