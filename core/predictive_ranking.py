import sys
import io
from typing import List, Dict, Any

from core.score_engine import MIN_VERIFIED_EVIDENCE

if sys.stdout.encoding != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

CATEGORY_EMERGING_STARS = "Emerging Stars"
CATEGORY_HIGH_RISK = "High-Risk / Supply Alert"
CATEGORY_DARK_HORSES = "Disruptive Dark Horses"

# Tiers da árvore de decisão de precedência da matriz de classificação
# (core/score_engine.py separa R_reg em 'alerta_regulatorio' e R_trade em
# 'sinal_comercial_comex'). Usados por classify_precedence_tier() para
# classificar o catálogo COMPLETO (auditoria/console) e por
# select_predictive_assets() para decidir, com precedência, quais dos 8
# ativos exibidos no relatório entram em cada categoria visual:
#
#   Risco Regulatório > Risco de Oferta > Dados Insuficientes/Não Classificado
#   > Estrela Emergente > Dark Horse > Monitoramento
#
# Risco regulatório (R_reg) tem prioridade sobre risco de oferta comercial
# (R_trade), que por sua vez tem prioridade sobre a checagem de evidência
# mínima - um ativo com risco regulatório/comercial real é sinalizado mesmo
# com pouca evidência científica, pois esses dois tiers não dependem de
# Tração Científica/Industrial. Só depois de descartados os riscos é que a
# falta de evidência (< MIN_VERIFIED_EVIDENCE) barra a classificação por
# oportunidade (Estrela Emergente/Dark Horse) - evita, por exemplo, rotular
# de "Dark Horse" um ativo com um único match isolado (anedota, não sinal).
# "Dados Insuficientes/Não Classificado" e "Monitoramento" não são categorias
# visíveis no PDF (que só exibe os 8 selecionados com badge próprio) - servem
# para auditoria do catálogo completo (ver Fase 2 de main.py) e para a regra
# de elegibilidade de select_predictive_assets().
TIER_REGULATORY_RISK = "Risco Regulatório"
TIER_SUPPLY_RISK = "Risco de Oferta"
TIER_INSUFFICIENT_DATA = "Dados Insuficientes / Não Classificado"
TIER_EMERGING_STAR = "Estrela Emergente"
TIER_DARK_HORSE = "Dark Horse"
TIER_MONITORING = "Monitoramento"

# Soma mínima de Tração Científica + Industrial para o tier "Estrela
# Emergente" na classificação do catálogo completo (classify_precedence_tier) -
# mesmo limiar usado em core/llm_analysis.py e reports/pdf_generator.py para
# a recomendação padrão de Inovação & P&D de nível "alta".
_EMERGING_TIER_THRESHOLD = 10.0

# Níveis de 'sinal_comercial_comex' (core.score_engine.calculate_commercial_signal_level)
# que caracterizam Risco de Oferta na árvore de precedência.
_SUPPLY_RISK_LEVELS = {"OFERTA CRÍTICA", "OFERTA LIMITADA"}

# Causas raiz do Relatório de Cobertura (generate_coverage_report) para
# ativos classificados em TIER_INSUFFICIENT_DATA - diagnóstico de por que a
# evidência ficou abaixo do mínimo, para orientar curadoria da taxonomia
# (core/entity_resolver.py) e ajuste de queries/exclusões, não só reportar
# "dados insuficientes" sem explicar o motivo.
COVERAGE_CAUSE_NO_LITERATURE = "Ausência real de literatura"
COVERAGE_CAUSE_ENTITY_RESOLUTION_FAILURE = "Falha de Entity Resolution"
COVERAGE_CAUSE_RESTRICTIVE_QUERY = "Query restritiva (heurística)"
COVERAGE_CAUSE_NO_DERMOCOSMETIC_USE = "Ausência de uso dermocosmético"
COVERAGE_CAUSE_UNKNOWN = "Causa não determinada"


class PredictiveRankingEngine:
    """
    Filtro preditivo que seleciona exatamente 8 ativos por relatório a partir
    do catálogo global, classificando-os em 3 categorias preditivas visíveis
    no PDF, com a seleção da categoria de risco respeitando a árvore de
    precedência da matriz de classificação:

      Risco Regulatório > Risco de Oferta > Dados Insuficientes/Não Classificado > Estrela Emergente > Dark Horse > Monitoramento

      - High-Risk / Supply Alert: prioriza primeiro os ativos com Risco
        Regulatório ALTO (R_reg, connectors.regulatory_comex - conformidade/
        uso permitido), depois os com Risco de Oferta comercial elevado
        (R_trade - concentração/tendência de fornecedores) — um ativo com
        risco regulatório alto nunca é "roubado" pela categoria Estrela
        Emergente, mesmo com Tração Científica/Industrial altas.
      - Emerging Stars: maior soma de Tração Científica + Tração Industrial,
        entre os ativos que não caíram em nenhum tier de risco acima.
      - Disruptive Dark Horses: Tração Científica positiva combinada com
        Tração Industrial nula, entre os remanescentes.
      - Monitoramento: não é uma categoria visível no relatório (que só
        mostra os 8 selecionados) — é o tier residual de classify_precedence_tier()
        para os demais ativos do catálogo, usado apenas para auditoria/log
        completo (ver Fase 2 de main.py).
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

    @classmethod
    def classify_precedence_tier(cls, evaluation: Dict[str, Any]) -> str:
        """
        Classifica um único ativo num dos 6 tiers da árvore de precedência,
        a partir dos campos já retornados por core.score_engine.generate_assessment()
        ('alerta_regulatorio' = R_reg, 'sinal_comercial_comex' = R_trade,
        'evidencias_verificadas', 'tracao_cientifica', 'tracao_industrial').
        Campos ausentes são tratados como desconhecidos/insuficientes (nunca
        dispara um tier de risco/oportunidade por ausência de dado -
        'evidencias_verificadas' ausente é tratado como 0, o mais conservador).
        """
        if evaluation.get("alerta_regulatorio") == "ALERTA ALTO":
            return TIER_REGULATORY_RISK
        if evaluation.get("sinal_comercial_comex") in _SUPPLY_RISK_LEVELS:
            return TIER_SUPPLY_RISK
        if evaluation.get("evidencias_verificadas", 0) < MIN_VERIFIED_EVIDENCE:
            return TIER_INSUFFICIENT_DATA

        sci = cls._parse_score(evaluation.get("tracao_cientifica", "0/10"))
        ind = cls._parse_score(evaluation.get("tracao_industrial", "0/10"))
        if (sci + ind) >= _EMERGING_TIER_THRESHOLD:
            return TIER_EMERGING_STAR
        if sci > 0 and ind == 0:
            return TIER_DARK_HORSE
        return TIER_MONITORING

    @staticmethod
    def _diagnose_coverage_cause(evaluation: Dict[str, Any]) -> str:
        """
        Diagnóstico heurístico da causa raiz de um ativo ter caído em
        TIER_INSUFFICIENT_DATA, a partir de campos adicionais que main.py
        anexa a cada avaliação ('pubmed_raw_count_12m' - contagem bruta do
        PubMed antes de qualquer resolução; 'tem_exclusoes' - se o ativo tem
        termos de exclusão configurados). Simplificação documentada: usa
        'evidencias_verificadas' combinado (PubMed + patentes), não separado
        por fonte - o diagnóstico é uma heurística para orientar curadoria
        (core/entity_resolver.py, exclusões do ativo), não uma prova formal.
        """
        raw_count = evaluation.get("pubmed_raw_count_12m")
        verified = evaluation.get("evidencias_verificadas", 0)
        nivel_max = evaluation.get("nivel_evidencia_maximo", 0)
        tem_exclusoes = evaluation.get("tem_exclusoes", False)

        if raw_count is None:
            return COVERAGE_CAUSE_UNKNOWN
        if raw_count == 0:
            return COVERAGE_CAUSE_NO_LITERATURE
        if verified == 0:
            return COVERAGE_CAUSE_ENTITY_RESOLUTION_FAILURE
        if tem_exclusoes and verified < MIN_VERIFIED_EVIDENCE:
            return COVERAGE_CAUSE_RESTRICTIVE_QUERY
        if nivel_max < 2:
            return COVERAGE_CAUSE_NO_DERMOCOSMETIC_USE
        return COVERAGE_CAUSE_UNKNOWN

    def generate_coverage_report(self, evaluations: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Relatório de Cobertura: para cada ativo do catálogo classificado em
        TIER_INSUFFICIENT_DATA (classify_precedence_tier), atribui uma causa
        raiz diagnosticada (_diagnose_coverage_cause). Auditoria interna
        (console, Fase 2 de main.py) - não entra no PDF, mesmo padrão já
        usado para os tiers Monitoramento/Dados Insuficientes.
        """
        report = []
        for evaluation in evaluations:
            if self.classify_precedence_tier(evaluation) != TIER_INSUFFICIENT_DATA:
                continue
            report.append({
                "asset_id": evaluation.get("asset_id"),
                "canonical_name": evaluation.get("canonical_name"),
                "causa_raiz": self._diagnose_coverage_cause(evaluation)
            })
        return report

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
                "_is_regulatory_risk": e.get("alerta_regulatorio") == "ALERTA ALTO",
                "_is_supply_risk": e.get("sinal_comercial_comex") in _SUPPLY_RISK_LEVELS,
                "_has_sufficient_evidence": e.get("evidencias_verificadas", 0) >= MIN_VERIFIED_EVIDENCE,
            }
            for e in evaluations
        ]

        selected_ids = set()
        selected = []

        # 1. High-Risk / Supply Alert — árvore de precedência: Risco Regulatório
        #    primeiro, Risco de Oferta comercial depois; desempate por
        #    _risk_rank (risco de oferta consolidado) e Tração Científica.
        #    Avaliado ANTES de Estrela Emergente para que um ativo com risco
        #    regulatório/comercial alto nunca seja "roubado" pela categoria
        #    de oportunidade, mesmo com scores altos.
        risk_candidates = sorted(
            [c for c in enriched if c["_is_regulatory_risk"] or c["_is_supply_risk"]],
            key=lambda x: (2 if x["_is_regulatory_risk"] else 1, x["_risk_rank"], x["_sci"]),
            reverse=True
        )
        for c in risk_candidates:
            if len(selected) >= self.high_risk_count:
                break
            selected.append({**c, "predictive_category": CATEGORY_HIGH_RISK})
            selected_ids.add(c["asset_id"])

        # 2. Emerging Stars — maior soma Científica + Industrial, entre os
        #    ativos que não caíram em nenhum tier de risco acima E que têm
        #    evidência mínima verificada (MIN_VERIFIED_EVIDENCE) - um ativo
        #    "Dados Insuficientes/Não Classificado" nunca é elegível a esta
        #    categoria de oportunidade, por maior que seja seu score bruto.
        target = len(selected) + self.emerging_stars_count
        for c in sorted(
            [c for c in enriched if c["asset_id"] not in selected_ids and c["_has_sufficient_evidence"]],
            key=lambda x: (x["_sci"] + x["_ind"]),
            reverse=True
        ):
            if len(selected) >= target:
                break
            selected.append({**c, "predictive_category": CATEGORY_EMERGING_STARS})
            selected_ids.add(c["asset_id"])

        # 3. Disruptive Dark Horses — sinal científico positivo sem tração
        #    industrial, mesma exigência de evidência mínima verificada do
        #    passo 2 (evita rotular de "Dark Horse" um ativo baseado num
        #    único match isolado - anedota, não sinal estatístico).
        dark_horse_candidates = sorted(
            [
                c for c in enriched
                if c["asset_id"] not in selected_ids and c["_has_sufficient_evidence"]
                and c["_sci"] > 0 and c["_ind"] == 0
            ],
            key=lambda x: x["_sci"],
            reverse=True
        )
        target = len(selected) + self.dark_horses_count
        for c in dark_horse_candidates:
            if len(selected) >= target:
                break
            selected.append({**c, "predictive_category": CATEGORY_DARK_HORSES})
            selected_ids.add(c["asset_id"])

        # 4. Preenchimento — se alguma categoria não teve candidatos
        #    suficientes, completa até 8 com os próximos melhores por sinal
        #    combinado (sem badge próprio no PDF - exibidos como Emerging
        #    Stars, mesma convenção já usada antes desta mudança). Primeiro
        #    passo só entre ativos com evidência mínima verificada (tier
        #    Monitoramento); só recorre ao tier Dados Insuficientes/Não
        #    Classificado como último recurso, para ainda entregar exatamente 8.
        if len(selected) < 8:
            remaining_with_evidence = sorted(
                [c for c in enriched if c["asset_id"] not in selected_ids and c["_has_sufficient_evidence"]],
                key=lambda x: (x["_sci"] + x["_ind"]),
                reverse=True
            )
            for c in remaining_with_evidence:
                if len(selected) >= 8:
                    break
                selected.append({**c, "predictive_category": CATEGORY_EMERGING_STARS})
                selected_ids.add(c["asset_id"])

        if len(selected) < 8:
            remaining_any = sorted(
                [c for c in enriched if c["asset_id"] not in selected_ids],
                key=lambda x: (x["_sci"] + x["_ind"]),
                reverse=True
            )
            for c in remaining_any:
                if len(selected) >= 8:
                    break
                selected.append({**c, "predictive_category": CATEGORY_EMERGING_STARS})
                selected_ids.add(c["asset_id"])

        for c in selected:
            c.pop("_sci", None)
            c.pop("_ind", None)
            c.pop("_risk_rank", None)
            c.pop("_is_regulatory_risk", None)
            c.pop("_is_supply_risk", None)
            c.pop("_has_sufficient_evidence", None)

        return selected[:8]


if __name__ == "__main__":
    engine = PredictiveRankingEngine()
    mock_evaluations = [
        {
            "asset_id": f"AT-{i:03d}", "canonical_name": f"Ativo {i}",
            "tracao_cientifica": f"{(i % 10)}.0/10", "tracao_industrial": f"{(i * 3 % 10)}.0/10",
            "risco_oferta": ["BAIXO RISCO", "MEDIO RISCO", "ALTO RISCO"][i % 3],
            "alerta_regulatorio": ["SEM ALERTA", "ALERTA MÉDIO", "ALERTA ALTO"][i % 3],
            "sinal_comercial_comex": ["OFERTA SAUDÁVEL", "OFERTA LIMITADA", "OFERTA CRÍTICA"][i % 3],
            "evidencias_verificadas": i % 4,  # varia 0-3, exercita o novo tier "Dados Insuficientes"
            "nivel_evidencia_maximo": i % 3,
            "pubmed_raw_count_12m": [0, 3, 3, 3][i % 4],  # casa com evidencias_verificadas=0 -> ausência real ou falha de resolução
            "tem_exclusoes": i % 2 == 0,
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

    print("\n--- Classificação do catálogo completo pela árvore de precedência ---")
    tier_counts: Dict[str, int] = {}
    for e in mock_evaluations:
        tier = engine.classify_precedence_tier(e)
        tier_counts[tier] = tier_counts.get(tier, 0) + 1
    for tier, count in tier_counts.items():
        print(f"  {tier}: {count} ativo(s)")

    print("\n--- Relatório de Cobertura (causa raiz - Dados Insuficientes) ---")
    for row in engine.generate_coverage_report(mock_evaluations):
        print(f"  {row['asset_id']} {row['canonical_name']:<12} -> {row['causa_raiz']}")
