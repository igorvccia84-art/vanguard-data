import sys
import io
from typing import List, Dict, Any

if sys.stdout.encoding != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

CATEGORY_EMERGING_STARS = "Emerging Stars"
CATEGORY_HIGH_RISK = "High-Risk / Supply Alert"
CATEGORY_DARK_HORSES = "Disruptive Dark Horses"


class PredictiveRankingEngine:
    """
    Filtro preditivo que seleciona exatamente 8 ativos por relatório a partir
    do catálogo global de 30, classificando-os em 3 categorias preditivas:

      - Emerging Stars: maior soma de Tração Científica + Tração Industrial
        (sinal forte e já validado em dois pilares simultaneamente).
      - High-Risk / Supply Alert: Risco de Oferta ALTO ou MÉDIO (gargalo
        regulatório e/ou concentração de fornecedores) — prioriza ALTO,
        desempatando por Tração Científica.
      - Disruptive Dark Horses: Tração Científica positiva combinada com
        Tração Industrial nula — sinal científico emergente ainda sem
        exploração industrial, candidato a disrupção antes do mercado reagir.
    """

    def __init__(self, emerging_stars_count: int = 3, high_risk_count: int = 3, dark_horses_count: int = 2):
        self.emerging_stars_count = emerging_stars_count
        self.high_risk_count = high_risk_count
        self.dark_horses_count = dark_horses_count

    @staticmethod
    def _parse_score(score_str: str) -> float:
        try:
            return float(score_str.split("/")[0])
        except (ValueError, AttributeError, IndexError):
            return 0.0

    @staticmethod
    def _risk_rank(risco_oferta: str) -> int:
        return {"ALTO RISCO": 2, "MEDIO RISCO": 1, "BAIXO RISCO": 0}.get(risco_oferta, 0)

    def select_predictive_assets(self, evaluations: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Recebe a lista de avaliações de TODOS os ativos do catálogo e retorna
        exatamente 8, cada um com o campo adicional 'predictive_category'.
        """
        enriched = [
            {
                **e,
                "_sci": self._parse_score(e["tracao_cientifica"]),
                "_ind": self._parse_score(e["tracao_industrial"]),
                "_risk_rank": self._risk_rank(e["risco_oferta"]),
            }
            for e in evaluations
        ]

        selected_ids = set()
        selected = []

        # 1. Emerging Stars — maior soma Científica + Industrial
        for c in sorted(enriched, key=lambda x: (x["_sci"] + x["_ind"]), reverse=True):
            if len(selected) >= self.emerging_stars_count:
                break
            selected.append({**c, "predictive_category": CATEGORY_EMERGING_STARS})
            selected_ids.add(c["asset_id"])

        # 2. High-Risk / Supply Alert — maior risco de oferta, desempate por Científica
        risk_candidates = sorted(
            [c for c in enriched if c["asset_id"] not in selected_ids and c["_risk_rank"] > 0],
            key=lambda x: (x["_risk_rank"], x["_sci"]),
            reverse=True
        )
        target = len(selected) + self.high_risk_count
        for c in risk_candidates:
            if len(selected) >= target:
                break
            selected.append({**c, "predictive_category": CATEGORY_HIGH_RISK})
            selected_ids.add(c["asset_id"])

        # 3. Disruptive Dark Horses — sinal científico positivo sem tração industrial
        dark_horse_candidates = sorted(
            [c for c in enriched if c["asset_id"] not in selected_ids and c["_sci"] > 0 and c["_ind"] == 0],
            key=lambda x: x["_sci"],
            reverse=True
        )
        target = len(selected) + self.dark_horses_count
        for c in dark_horse_candidates:
            if len(selected) >= target:
                break
            selected.append({**c, "predictive_category": CATEGORY_DARK_HORSES})
            selected_ids.add(c["asset_id"])

        # 4. Preenchimento — se alguma categoria não teve candidatos suficientes,
        #    completa até 8 com os próximos melhores por sinal combinado.
        if len(selected) < 8:
            remaining = sorted(
                [c for c in enriched if c["asset_id"] not in selected_ids],
                key=lambda x: (x["_sci"] + x["_ind"]),
                reverse=True
            )
            for c in remaining:
                if len(selected) >= 8:
                    break
                selected.append({**c, "predictive_category": CATEGORY_EMERGING_STARS})
                selected_ids.add(c["asset_id"])

        for c in selected:
            c.pop("_sci", None)
            c.pop("_ind", None)
            c.pop("_risk_rank", None)

        return selected[:8]


if __name__ == "__main__":
    engine = PredictiveRankingEngine()
    mock_evaluations = [
        {
            "asset_id": f"AT-{i:03d}", "canonical_name": f"Ativo {i}",
            "tracao_cientifica": f"{(i % 10)}.0/10", "tracao_industrial": f"{(i * 3 % 10)}.0/10",
            "risco_oferta": ["BAIXO RISCO", "MEDIO RISCO", "ALTO RISCO"][i % 3],
            "confianca_sinal": "ALTA"
        }
        for i in range(1, 31)
    ]
    selected = engine.select_predictive_assets(mock_evaluations)
    print(f"--- {len(selected)} ativos selecionados ---")
    for s in selected:
        print(
            f"  {s['asset_id']} {s['canonical_name']:<12} [{s['predictive_category']}] "
            f"Cientifica={s['tracao_cientifica']} Industrial={s['tracao_industrial']} Risco={s['risco_oferta']}"
        )
