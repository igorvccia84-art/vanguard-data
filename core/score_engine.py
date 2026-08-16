import sys
import io
from typing import List, Dict, Any, Optional

if sys.stdout.encoding != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

MODEL_VERSION = "2.0.0"


class ScoreEngine:
    """
    Motor de Cálculo de Score Decomposto da Vanguard Data.
    Substitui a caixa-preta por 3 pilares transparentes e auditáveis.

    Consome o dossiê refatorado do RegulatoryComexConnector
    (connectors/regulatory_comex.py), que separa 'alertas_regulatorios'
    (conformidade/uso permitido) de 'sinais_comerciais_comex' (oferta/
    importação) - os dois pilares nunca são somados numa métrica opaca única;
    o Risco de Oferta final expõe qual dos dois o determinou.
    """

    def calculate_scientific_traction(self, pubmed_matches: List[Dict[str, Any]]) -> float:
        """
        Calcula a Tração Científica (0 a 10) ponderando apenas artigos com
        Entity Resolution bem-sucedida (entity_match), pesados pela confiança
        do match — evita que artigos não resolvidos (ruído) contem igual a
        artigos de match confirmado, e produz variação real entre ativos.
        """
        resolved = [m for m in pubmed_matches if isinstance(m, dict) and m.get("entity_match")]
        if not resolved:
            return 0.0

        weighted_sum = sum(m["entity_match"].get("confidence_score", 0.5) for m in resolved)
        score = min(10.0, weighted_sum * 2.2)
        return round(score, 1)

    def calculate_industrial_traction(self, patent_matches: List[Dict[str, Any]]) -> float:
        """Calcula a Tração Industrial (0 a 10) baseada em patentes (já deduplicadas por família) e inovação."""
        total_patents = len(patent_matches)
        if total_patents == 0:
            return 0.0

        assignees = set(p.get("assignee") for p in patent_matches if p.get("assignee"))
        diversity_bonus = min(2.0, len(assignees) * 1.0)

        base_score = min(8.0, total_patents * 3.0)
        return round(min(10.0, base_score + diversity_bonus), 1)

    def calculate_regulatory_alert_level(self, regulatory_alerts: Dict[str, Any]) -> str:
        """
        Classifica isoladamente o Alerta Regulatório (conformidade/uso permitido),
        a partir de connectors.regulatory_comex.fetch_regulatory_status()
        (chave 'alertas_regulatorios' do dossiê).
        """
        restriction = (regulatory_alerts or {}).get("restriction_level", "DESCONHECIDO")
        return {
            "ALTO": "ALERTA ALTO",
            "MEDIO": "ALERTA MÉDIO",
            "BAIXO": "ALERTA BAIXO",
            "NENHUM": "SEM ALERTA",
        }.get(restriction, "ALERTA DESCONHECIDO")

    def calculate_commercial_signal_level(self, commercial_signals: Dict[str, Any]) -> str:
        """
        Classifica isoladamente o Sinal Comercial/Comex (oferta/importação),
        a partir de connectors.regulatory_comex.fetch_import_volume_mock()
        (chave 'sinais_comerciais_comex' do dossiê) — sem considerar regulação.
        """
        suppliers = (commercial_signals or {}).get("suppliers_count", 0)
        if suppliers < 2:
            return "OFERTA CRÍTICA"
        elif suppliers < 5:
            return "OFERTA LIMITADA"
        else:
            return "OFERTA SAUDÁVEL"

    def calculate_supply_risk(self, regulatory_alerts: Dict[str, Any], commercial_signals: Dict[str, Any]) -> str:
        """
        Calcula a classificação consolidada de Risco de Oferta a partir dos dois
        pilares independentes (Alerta Regulatório e Sinal Comercial/Comex),
        aplicando o pior caso entre os dois. Os dois indicadores continuam
        disponíveis separadamente no retorno de generate_assessment().
        """
        regulatory_level = self.calculate_regulatory_alert_level(regulatory_alerts)
        commercial_level = self.calculate_commercial_signal_level(commercial_signals)

        if regulatory_level == "ALERTA ALTO" or commercial_level == "OFERTA CRÍTICA":
            return "ALTO RISCO"
        elif regulatory_level in ("ALERTA MÉDIO", "ALERTA DESCONHECIDO") or commercial_level == "OFERTA LIMITADA":
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
        regulatory_alerts: Dict[str, Any] = None,
        commercial_signals: Dict[str, Any] = None,
        query_hashes: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """
        Gera a avaliação final decomposta do ativo. Consome diretamente o formato
        refatorado dos conectores: 'regulatory_alerts' (dossier['alertas_regulatorios'])
        e 'commercial_signals' (dossier['sinais_comerciais_comex']) de
        RegulatoryComexConnector.get_asset_dossier(). Retorna o dicionário completo
        de scores, incluindo o model_version desta execução e os query_hashes das
        evidências coletadas (para auditoria em evaluation_evidence_sources /
        rastreabilidade no PDF).
        """
        regulatory_alerts = regulatory_alerts or {}
        commercial_signals = commercial_signals or {}

        sc_score = self.calculate_scientific_traction(pubmed_data)
        ind_score = self.calculate_industrial_traction(patent_data)

        regulatory_alert_level = self.calculate_regulatory_alert_level(regulatory_alerts)
        commercial_signal_level = self.calculate_commercial_signal_level(commercial_signals)
        supply_risk = self.calculate_supply_risk(regulatory_alerts, commercial_signals)

        all_signals = pubmed_data + patent_data
        confidence = self.calculate_confidence_level(all_signals)

        return {
            "model_version": MODEL_VERSION,
            "tracao_cientifica": f"{sc_score}/10",
            "tracao_industrial": f"{ind_score}/10",
            "alerta_regulatorio": regulatory_alert_level,
            "sinal_comercial_comex": commercial_signal_level,
            "risco_oferta": supply_risk,
            "confianca_sinal": confidence,
            "total_evidencias": len(all_signals),
            "query_hashes": query_hashes or []
        }


if __name__ == "__main__":
    engine = ScoreEngine()

    mock_pubmed = [{"entity_match": {"confidence_score": 0.95}}, {"entity_match": {"confidence_score": 0.95}}]
    mock_patents = [{"assignee": "Derma Ltd", "entity_match": {"confidence_score": 0.95}}]
    mock_regulatory_alerts = {"restriction_level": "BAIXO", "alerts": []}
    mock_commercial_signals = {"suppliers_count": 8, "trend": "CRESCENTE"}
    mock_query_hashes = ["9c4a63a04bee3dbaff001145eb35bb6352592580d0592d02f96489b77a013b8", "53f1e4ad83e6db511b620ca438e81b495c9ac6a7abb97f9cb3cda732982e4eb"]

    avaliacao = engine.generate_assessment(
        mock_pubmed, mock_patents, mock_regulatory_alerts, mock_commercial_signals, query_hashes=mock_query_hashes
    )

    print("--- Avaliação Decomposta Atualizada ---")
    print(f"Model Version:            {avaliacao['model_version']}")
    print(f"Tração Científica:        {avaliacao['tracao_cientifica']}")
    print(f"Tração Industrial:        {avaliacao['tracao_industrial']}")
    print(f"Alerta Regulatório:       {avaliacao['alerta_regulatorio']}")
    print(f"Sinal Comercial/Comex:    {avaliacao['sinal_comercial_comex']}")
    print(f"Risco de Oferta (consol.):{avaliacao['risco_oferta']}")
    print(f"Confiança do Sinal:       {avaliacao['confianca_sinal']}")
    print(f"Total Evidências:         {avaliacao['total_evidencias']}")
    print(f"Query Hashes:             {avaliacao['query_hashes']}")
