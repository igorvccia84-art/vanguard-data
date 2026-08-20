"""
Relatório antes/depois da recalibração de Tração Industrial (log + N_ref,
core/score_engine.py INDUSTRIAL_TRACTION_N_REF) e da regra de tier "Estrela
Emergente" (piso mínimo em AMBOS os componentes, core/predictive_ranking.py
MIN_SCI_FOR_EMERGING_STAR/MIN_IND_FOR_EMERGING_STAR).

Não faz nenhuma chamada de rede: recalcula a fórmula/regra NOVAS sobre a
MESMA evidência bruta coletada por scripts/tier_recalibration_collect.py
(data/cache/tier_recalibration_raw.json) - isola o efeito da correção,
sem misturar com a flutuação natural do PubMed/EPO OPS entre execuções.

Uso: python -m scripts.tier_recalibration_report
"""
import sys
import io
import json

if sys.stdout.encoding != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

from core.score_engine import ScoreEngine
from core.predictive_ranking import PredictiveRankingEngine

INPUT_PATH = "data/cache/tier_recalibration_raw.json"

if __name__ == "__main__":
    with open(INPUT_PATH, "r", encoding="utf-8") as f:
        raw = json.load(f)

    score_engine = ScoreEngine()
    rows = []
    for r in raw:
        if "error" in r:
            rows.append({**r, "new_tracao_industrial": None, "new_tier": "ERRO NA COLETA"})
            continue

        new_ind = score_engine.calculate_industrial_traction([{}] * r["validated_patent_count"])
        new_eval = {
            "alerta_regulatorio": r["alerta_regulatorio"],
            "sinal_comercial_comex": r["sinal_comercial_comex"],
            "evidencias_verificadas": r["evidencias_verificadas"],
            "tracao_cientifica": f"{r['sci']}/10",
            "tracao_industrial": f"{new_ind}/10",
        }
        new_tier = PredictiveRankingEngine.classify_precedence_tier(new_eval)
        rows.append({**r, "new_tracao_industrial": new_ind, "new_tier": new_tier})

    changed = [r for r in rows if r["old_tier"] != r["new_tier"]]

    header = f"{'Ativo':<28} {'sci':>5} {'pat':>4} {'T_i old':>8} {'T_i new':>8} {'Tier ANTES':<32} {'Tier DEPOIS':<32} {'Mudou?'}"
    print(header)
    print("-" * len(header))
    for r in rows:
        mudou = "SIM" if r["old_tier"] != r["new_tier"] else ""
        print(
            f"{r['asset_id']} {r['canonical_name']:<20} {r['sci']:>5} {r['validated_patent_count']:>4} "
            f"{r['old_tracao_industrial']:>8} {r['new_tracao_industrial']:>8} "
            f"{r['old_tier']:<32} {r['new_tier']:<32} {mudou}"
        )

    print(f"\nTotal de ativos: {len(rows)} | Mudaram de tier: {len(changed)}")
    if changed:
        print("\nAtivos que mudaram de tier:")
        for r in changed:
            print(f"  {r['asset_id']} {r['canonical_name']}: {r['old_tier']} -> {r['new_tier']} "
                  f"(sci={r['sci']}, patentes validadas={r['validated_patent_count']}, "
                  f"T_i {r['old_tracao_industrial']} -> {r['new_tracao_industrial']})")

    # ------------------------------------------------------------------
    # select_predictive_assets() - a seleção que de fato decide o badge
    # "Emerging Stars" exibido no PDF (independente de classify_precedence_tier,
    # usado só para auditoria/console). Mesma lógica de combinação de
    # core.score_engine.ScoreEngine.calculate_supply_risk, reaplicada aqui
    # sobre os níveis já calculados (sem chamada de rede nova).
    def _supply_risk(reg_level: str, com_level: str) -> str:
        if reg_level == "ALERTA ALTO" or com_level == "OFERTA CRÍTICA":
            return "ALTO RISCO"
        elif reg_level in ("ALERTA MÉDIO", "ALERTA DESCONHECIDO") or com_level == "OFERTA LIMITADA":
            return "MEDIO RISCO"
        else:
            return "BAIXO RISCO"

    def _build_evaluations(rows_, ind_field: str) -> list:
        evals = []
        for r in rows_:
            if "error" in r:
                continue
            evals.append({
                "asset_id": r["asset_id"],
                "canonical_name": r["canonical_name"],
                "tracao_cientifica": f"{r['sci']}/10",
                "tracao_industrial": f"{r[ind_field]}/10",
                "risco_oferta": _supply_risk(r["alerta_regulatorio"], r["sinal_comercial_comex"]),
                "alerta_regulatorio": r["alerta_regulatorio"],
                "sinal_comercial_comex": r["sinal_comercial_comex"],
                "evidencias_verificadas": r["evidencias_verificadas"],
            })
        return evals

    ranking_engine = PredictiveRankingEngine()
    old_selected = {
        e["asset_id"]: e["predictive_category"]
        for e in ranking_engine.select_predictive_assets(_build_evaluations(rows, "old_tracao_industrial"))
    }
    new_selected = {
        e["asset_id"]: e["predictive_category"]
        for e in ranking_engine.select_predictive_assets(_build_evaluations(rows, "new_tracao_industrial"))
    }

    print("\n" + "=" * 78)
    print("select_predictive_assets() - Top-8 exibidos no PDF (badge visual)")
    print("=" * 78)
    all_ids = sorted(set(old_selected) | set(new_selected))
    for asset_id in all_ids:
        old_cat = old_selected.get(asset_id, "(não selecionado)")
        new_cat = new_selected.get(asset_id, "(não selecionado)")
        flag = "  <-- MUDOU" if old_cat != new_cat else ""
        print(f"  {asset_id:<8} ANTES={old_cat:<28} DEPOIS={new_cat:<28}{flag}")
