"""
Coleta de dados para a recalibração de Tração Industrial (Opção B, log +
N_ref) e da regra de tier "Estrela Emergente" (piso mínimo em AMBOS os
componentes, não soma). Roda o catálogo COMPLETO (~35 ativos) UMA VEZ com os
conectores reais (PubMed/NCBI, EPO OPS, validação Google Patents) e salva,
por ativo, a evidência bruta (Tração Científica já calculada, contagem de
patentes VALIDADAS na janela de 12 meses, nº de titulares distintos) junto
com o resultado da fórmula/regra ATUAIS (antes da correção).

Por quê UMA coleta só, não duas: a fórmula nova de Tração Industrial e a
regra nova de tier operam sobre a MESMA evidência bruta (contagem de
patentes validadas, Tração Científica) - só a fórmula que transforma essa
evidência em score muda. Rodar a coleta ao vivo duas vezes (antes/depois)
mistura o efeito da mudança de fórmula com a flutuação natural dos
resultados do PubMed/EPO OPS entre duas execuções (novos artigos/patentes
publicados no intervalo) - reaplicar a fórmula nova sobre os MESMOS dados
brutos desta coleta isola exatamente o efeito da correção, que é o que
importa para o comparativo antes/depois.

Uso: python -m scripts.tier_recalibration_collect
Saída: data/cache/tier_recalibration_raw.json (git-ignorado - dado
transitório de calibração, não fonte).
"""
import sys
import io
import json

if sys.stdout.encoding != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

from dotenv import load_dotenv
load_dotenv()

from core.entity_resolver import EntityResolver
from connectors.pubmed import PubMedConnector
from connectors.patents import PatentConnector
from connectors.regulatory_comex import RegulatoryComexConnector
from core.score_engine import ScoreEngine, SCI_BASELINE_HISTORY_DAYS
from core.predictive_ranking import PredictiveRankingEngine
from main import resolve_search_query

OUTPUT_PATH = "data/cache/tier_recalibration_raw.json"


def collect_asset(asset, pubmed_conn, patent_conn, comex_conn, score_engine):
    asset_id = asset["asset_id"]
    canonical_name = asset["canonical_name"]
    exclusions = asset.get("exclusions", [])
    search_query = resolve_search_query(asset)
    hs_code = asset.get("hs_codes", [None])[0]

    pubmed_traction_search = pubmed_conn.search_articles(
        search_query, exclusions=exclusions, max_results=10, days=pubmed_conn.TRACTION_WINDOW_DAYS
    )
    pubmed_traction_results = [pubmed_conn.fetch_article_details(pmid) for pmid in pubmed_traction_search["pmids"]]
    pubmed_baseline_search = pubmed_conn.search_articles(
        search_query, exclusions=exclusions, max_results=0,
        days=pubmed_conn.TRACTION_WINDOW_DAYS + SCI_BASELINE_HISTORY_DAYS, end_days_ago=pubmed_conn.TRACTION_WINDOW_DAYS
    )
    sci = score_engine.calculate_scientific_traction(pubmed_traction_results, baseline_36m_count=pubmed_baseline_search["count"])

    # Só a janela de TRAÇÃO (12 meses) importa aqui - é a única que alimenta
    # calculate_industrial_traction(); a janela de novidade (15 dias) só
    # afeta a citação exibida no relatório (patent_ids), não o score.
    patent_traction_search = patent_conn.fetch_patents(search_query, exclusions=exclusions, days=patent_conn.TRACTION_WINDOW_DAYS)
    valid_traction_ids, _ = patent_conn.validate_patent_batch(
        [p["patent_id"] for p in patent_traction_search["results"]], canonical_name
    )
    valid_set = set(valid_traction_ids)
    patent_traction_results = [
        patent_conn.process_patent(p) for p in patent_traction_search["results"] if p["patent_id"] in valid_set
    ]
    validated_count = len(patent_traction_results)
    distinct_assignees = len(set(p.get("assignee") for p in patent_traction_results if p.get("assignee")))
    old_ind = score_engine.calculate_industrial_traction(patent_traction_results)  # fórmula ATUAL (não modificada ainda)

    dossier = comex_conn.get_asset_dossier(asset_id, hs_code=hs_code, lang="PT-BR")
    regulatory_matrix = comex_conn.get_regulatory_matrix(asset_id)
    regulatory_alerts = regulatory_matrix["max_severity_status"]
    commercial_signals = dossier["sinais_comerciais_comex"]
    evidencias_verificadas = score_engine.calculate_verified_evidence_count(pubmed_traction_results, patent_traction_results)

    old_eval = {
        "alerta_regulatorio": score_engine.calculate_regulatory_alert_level(regulatory_alerts),
        "sinal_comercial_comex": score_engine.calculate_commercial_signal_level(commercial_signals),
        "evidencias_verificadas": evidencias_verificadas,
        "tracao_cientifica": f"{sci}/10",
        "tracao_industrial": f"{old_ind}/10",
    }
    old_tier = PredictiveRankingEngine.classify_precedence_tier(old_eval)

    return {
        "asset_id": asset_id,
        "canonical_name": canonical_name,
        "sci": sci,
        "validated_patent_count": validated_count,
        "distinct_assignees": distinct_assignees,
        "alerta_regulatorio": old_eval["alerta_regulatorio"],
        "sinal_comercial_comex": old_eval["sinal_comercial_comex"],
        "evidencias_verificadas": evidencias_verificadas,
        "old_tracao_industrial": old_ind,
        "old_tier": old_tier,
    }


if __name__ == "__main__":
    resolver = EntityResolver(taxonomy_path="data/taxonomy/ativos_mvp.json")
    pubmed_conn = PubMedConnector(resolver=resolver)
    patent_conn = PatentConnector(resolver=resolver)
    comex_conn = RegulatoryComexConnector(resolver=resolver)
    score_engine = ScoreEngine()

    results = []
    for i, asset in enumerate(resolver.assets, 1):
        print(f"[{i}/{len(resolver.assets)}] {asset['asset_id']} {asset['canonical_name']}...", flush=True)
        try:
            r = collect_asset(asset, pubmed_conn, patent_conn, comex_conn, score_engine)
            print(f"  sci={r['sci']} validated_patents={r['validated_patent_count']} "
                  f"distinct_assignees={r['distinct_assignees']} old_ind={r['old_tracao_industrial']} old_tier={r['old_tier']}", flush=True)
        except Exception as e:
            print(f"  ERRO: {e}", flush=True)
            r = {"asset_id": asset["asset_id"], "canonical_name": asset["canonical_name"], "error": str(e)}
        results.append(r)

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\nSalvo em {OUTPUT_PATH} ({len(results)} ativos)")
