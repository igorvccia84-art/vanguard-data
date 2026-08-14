import sys
import io
from typing import List, Dict, Any

if sys.stdout.encoding != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')


class ScoreEngine:
    """
    Motor de Cálculo de Score Decomposto da Vanguard Data.
    Substitui a caixa-preta por 3 pilares transparentes e auditáveis.
    """

    def calculate_scientific_traction(self, pubmed_matches: List[Dict[str, Any]]) -> float:
        """Calcula a Tração Científica (0 a 10) baseada em artigos validados."""
        total_articles = len(pubmed_matches)
        if total_articles == 0:
            return 0.0

        score = min(10.0, total_articles * 2.0)
        return round(score, 1)

    def calculate_industrial_traction(self, patent_matches: List[Dict[str, Any]]) -> float:
        """Calcula a Tração Industrial (0 a 10) baseada em patentes e inovação."""
        total_patents = len(patent_matches)
        if total_patents == 0:
            return 0.0

        assignees = set(p.get("assignee") for p in patent_matches if p.get("assignee"))
        diversity_bonus = min(2.0, len(assignees) * 1.0)

        base_score = min(8.0, total_patents * 3.0)
        return round(min(10.0, base_score + diversity_bonus), 1)

    def calculate_supply_risk(self, reg_data: Dict[str, Any], trade_data: Dict[str, Any]) -> str:
        """
        Calcula a classificação de Risco de Oferta baseada em restrições
        regulatórias e fornecedores do comércio exterior.
        """
        restriction = reg_data.get("restriction_level", "BAIXO")
        suppliers = trade_data.get("suppliers_count", 0)

        if restriction == "ALTO" or suppliers < 2:
            return "ALTO RISCO"
        elif restriction == "MEDIO" or suppliers < 5:
            return "MEDIO RISCO"
        else:
            return "BAIXO RISCO"

    def calculate_confidence_level(self, all_matches: List[Dict[str, Any]]) -> str:
        """Determina a Confiança dos Sinais baseada na qualidade do Entity Resolution."""
        if not all_matches:
            return "BAIXA"

        conf_scores = [
            m.get("entity_match", {}).get("confidence_score", 0.0)
            for m in all_matches if isinstance(m, dict) and m.get("entity_match")
        ]

        if not conf_scores:
            return "BAIXA"

        avg_confidence = round(sum(conf_scores) / len(conf_scores), 4)
        if avg_confidence >= 0.95 and len(all_matches) >= 2:
            return "ALTA"
        elif avg_confidence >= 0.85:
            return "MÉDIA"
        else:
            return "BAIXA"

    def generate_assessment(
        self,
        pubmed_data: List[Dict[str, Any]],
        patent_data: List[Dict[str, Any]],
        reg_data: Dict[str, Any] = None,
        trade_data: Dict[str, Any] = None
    ) -> Dict[str, Any]:
        """Gera a avaliação final decomposta do ativo em 3 pilares."""
        reg_data = reg_data or {}
        trade_data = trade_data or {}

        sc_score = self.calculate_scientific_traction(pubmed_data)
        ind_score = self.calculate_industrial_traction(patent_data)
        supply_risk = self.calculate_supply_risk(reg_data, trade_data)

        all_signals = pubmed_data + patent_data
        confidence = self.calculate_confidence_level(all_signals)

        return {
            "tracao_cientifica": f"{sc_score}/10",
            "tracao_industrial": f"{ind_score}/10",
            "risco_oferta": supply_risk,
            "confianca_sinal": confidence,
            "total_evidencias": len(all_signals)
        }


if __name__ == "__main__":
    engine = ScoreEngine()

    mock_pubmed = [{"entity_match": {"confidence_score": 0.95}}, {"entity_match": {"confidence_score": 0.95}}]
    mock_patents = [{"assignee": "Derma Ltd", "entity_match": {"confidence_score": 0.95}}]
    mock_reg = {"restriction_level": "BAIXO"}
    mock_trade = {"suppliers_count": 8}

    avaliacao = engine.generate_assessment(mock_pubmed, mock_patents, mock_reg, mock_trade)

    print("--- Avaliação Decomposta Atualizada ---")
    print(f"Tração Científica: {avaliacao['tracao_cientifica']}")
    print(f"Tração Industrial: {avaliacao['tracao_industrial']}")
    print(f"Risco de Oferta:    {avaliacao['risco_oferta']}")
    print(f"Confiança do Sinal: {avaliacao['confianca_sinal']}")
    print(f"Total Evidências:   {avaliacao['total_evidencias']}")
