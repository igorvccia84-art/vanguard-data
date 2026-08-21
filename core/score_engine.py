import sys
import io
import math
from typing import List, Dict, Any, Optional

if sys.stdout.encoding != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

MODEL_VERSION = "2.3.0"  # v2.3.0: T_c reestruturada em 4 componentes explícitos e ponderados (V/G/A/Q, ver calculate_scientific_traction_breakdown); [G] usa linha de base histórica de 36 meses estritamente anterior à janela de análise, sem sobreposição

# Janela de linha de base histórica (dias) para o componente [G] (Crescimento
# vs. linha de base) de calculate_scientific_traction_breakdown - 36 meses
# (3 * 365) estritamente ANTERIORES aos 12 meses de análise, sem sobreposição
# ("sem contaminação do período recente"). Consumida via
# connectors.pubmed.PubMedConnector.search_articles(days=TRACTION_WINDOW_DAYS + SCI_BASELINE_HISTORY_DAYS, end_days_ago=TRACTION_WINDOW_DAYS).
SCI_BASELINE_HISTORY_DAYS = 1095

# Pesos heurísticos provisórios de T_c = w_V*[V] + w_G*[G] + w_A*[A] + w_Q*[Q]
# (somam 1.0). PESOS PROVISÓRIOS, AINDA NÃO CALIBRADOS POR VALIDAÇÃO EXTERNA/
# BACKTESTING - ver PROVISIONAL_WEIGHTS_DISCLAIMER, exibido no console
# (main.py, Fase 1) e no PDF (reports/pdf_generator.py, caixa de metodologia).
W_SCI_VOLUME = 0.25       # [V]
W_SCI_GROWTH = 0.35       # [G]
W_SCI_APPLICABILITY = 0.20  # [A]
W_SCI_QUALITY = 0.20      # [Q]

PROVISIONAL_WEIGHTS_DISCLAIMER = "Pesos provisórios, ainda não calibrados por validação externa/backtesting."

# Volume mínimo de evidência verificada (matches com Entity Resolution
# confirmada, somando PubMed 12 meses + patentes) para um ativo ser elegível
# à classificação por evidência (Estrela Emergente/Dark Horse em
# core/predictive_ranking.py) - abaixo disso, é "Dados Insuficientes/Não
# Classificado": 1 único match é anedota, não sinal estatístico.
MIN_VERIFIED_EVIDENCE = 2

# Referência de calibração (N_ref) para calculate_industrial_traction():
# T_i = 10 x log(1+count) / log(1+N_ref), onde `count` é o nº de patentes
# validadas (janela de 12 meses, já confirmadas ao vivo no Google Patents -
# ver connectors/patents.py validate_patent_batch). Um ativo com
# `count == N_ref` atinge o teto de 10.0/10 por construção; a curva log dá
# retornos decrescentes (a diferença entre 1 e 2 patentes pesa mais que
# entre 5 e 6), em vez do degrau anterior que saturava em 10.0 já a partir
# de 3 patentes de 2 titulares - ver METHODOLOGY.md, seção "Tração
# Industrial", para o histórico completo da correção.
#
# CALIBRADO EM 2026-08-19 a partir de dado real: rodando o catálogo
# completo (35 ativos) uma única vez com o conector real do EPO OPS
# (scripts/tier_recalibration_collect.py), a contagem MÁXIMA de patentes
# validadas observada em qualquer ativo foi 6 (AT-029, Cânhamo/CBD) - a
# distribuição completa: 15 ativos com 0, 11 com 1, 2 com 2, 1 com 3, 1 com
# 4, 4 com 5, 1 com 6. N_ref = 6 reflete o que "dominância industrial" de
# fato parece neste catálogo/nicho (ativos botânicos dermocosméticos, janela
# de 12 meses) - não um número arbitrário importado de outro domínio (ex.:
# portfólios de patente farmacêutica, que são ordens de grandeza maiores).
#
# REVISÃO PERIÓDICA: recalibrar TRIMESTRALMENTE (ou sempre que um novo
# recorde de patentes validadas for observado no catálogo), reexecutando
# scripts/tier_recalibration_collect.py e atualizando esta constante + o
# registro de calibração em METHODOLOGY.md. Um N_ref desatualizado (baixo
# demais) volta a saturar o topo da escala; um N_ref alto demais comprime
# todo o catálogo perto de zero - os dois sintomas são visíveis comparando
# a distribuição de tracao_industrial de uma execução completa contra a
# calibração registrada.
INDUSTRIAL_TRACTION_N_REF = 6

# Rótulos de jurisdição regulatória exibidos na Matriz Regulatória do PDF
# (reports/pdf_generator.py), estritamente filtrados pelo target_market do
# relatório - nunca a lista cheia de jurisdições monitoradas internamente por
# connectors.regulatory_comex.get_regulatory_matrix() (que sempre calcula o
# pior caso entre Anvisa/UE/FDA para fins de score, independente do mercado
# do relatório). Evita vazamento de template (ex.: "Anvisa (Brasil)" num
# relatório PT-PT/ES).
_EU_COSMETICS_REGULATION_LABEL = "Regulamento (CE) 1223/2009 (UE)"
MOCK_JURISDICTION_LABEL = "FDA (EUA, mock/ilustrativo)"

MARKET_JURISDICTION_LABELS: Dict[str, List[str]] = {
    "BR": ["Anvisa (Agência Nacional de Vigilância Sanitária, Brasil)"],
    "PT": ["INFARMED (Autoridade Nacional do Medicamento e Produtos de Saúde, Portugal)", _EU_COSMETICS_REGULATION_LABEL],
    "ES": ["AEMPS (Agencia Española de Medicamentos y Productos Sanitarios, España)", _EU_COSMETICS_REGULATION_LABEL],
}


def get_display_jurisdictions(target_market: str, include_mock: bool = False) -> List[str]:
    """
    Lista de jurisdições regulatórias a exibir na Matriz Regulatória do PDF
    para um `target_market` específico ('BR', 'PT' ou 'ES') - nunca as
    jurisdições de outros mercados. `include_mock=True` (só para ambientes de
    teste explícitos - jamais em builds de exportação final) acrescenta a
    jurisdição mock/ilustrativa da FDA ao final da lista.
    """
    labels = list(MARKET_JURISDICTION_LABELS.get((target_market or "").upper(), []))
    if include_mock:
        labels.append(MOCK_JURISDICTION_LABEL)
    return labels


class ScoreEngine:
    """
    Motor de Cálculo de Score Decomposto da Actives Predict.
    Substitui a caixa-preta por 3 pilares transparentes e auditáveis.

    Consome o dossiê refatorado do RegulatoryComexConnector
    (connectors/regulatory_comex.py), que separa 'alertas_regulatorios'
    (conformidade/uso permitido) de 'sinais_comerciais_comex' (oferta/
    importação) - os dois pilares nunca são somados numa métrica opaca única;
    o Risco de Oferta final expõe qual dos dois o determinou.
    """

    @staticmethod
    def _verified_pubmed_matches(pubmed_matches: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Filtro único de 'match verificado' (entity_match confirmado) - usado por calculate_scientific_traction_breakdown() e por extract_verified_pmids(), para as duas nunca divergirem sobre o que conta como evidência real."""
        return [m for m in pubmed_matches if isinstance(m, dict) and m.get("entity_match")]

    def extract_verified_pmids(self, pubmed_matches: List[Dict[str, Any]]) -> List[str]:
        """
        PMIDs de `pubmed_matches` (janela de tração de 12 meses, ver
        connectors.pubmed.PubMedConnector TRACTION_WINDOW_DAYS) que
        efetivamente entram na fórmula de T_c - exatamente o mesmo filtro
        usado internamente por calculate_scientific_traction_breakdown()
        (`_verified_pubmed_matches`), nunca reimplementado em separado.

        CORRIGIDO (2026-08-20, achado de auditoria): main.py citava no
        relatório final só os PMIDs de uma busca de "novidade" de 15 dias
        (connectors.pubmed.PubMedConnector.search_articles() sem `days=`),
        uma janela DIFERENTE da que efetivamente calcula T_c - um ativo podia
        ter evidência real e verificada por trás do score (ex.: Calendula/
        AT-017, ver docs/calculation_trace_calendula_2026-08-19.md) e o
        relatório ainda assim declarar "nenhuma evidência disponível", porque
        a janela de 15 dias não tinha capturado os mesmos artigos. Exposto
        aqui para main.py citar como evidência do CÁLCULO em si, não apenas
        de novidade recente.
        """
        return [m["pmid"] for m in self._verified_pubmed_matches(pubmed_matches) if m.get("pmid")]

    @staticmethod
    def extract_traction_patent_ids(patent_matches: List[Dict[str, Any]]) -> List[str]:
        """
        IDs de patente de `patent_matches` (janela de tração de 12 meses) que
        efetivamente entram na fórmula de T_i - toda a lista, sem filtro
        adicional de entity_match (calculate_industrial_traction() usa
        `len(patent_matches)` diretamente; `patent_matches` já chega aqui
        filtrado pela validação real via Google Patents, ver main.py
        `valid_traction_patent_ids`). Mesmo motivo/uso de extract_verified_pmids()
        acima - expõe a evidência real do cálculo para citação no relatório.
        """
        return [p["patent_id"] for p in patent_matches if isinstance(p, dict) and p.get("patent_id")]

    def calculate_scientific_traction_breakdown(
        self,
        pubmed_matches: List[Dict[str, Any]],
        baseline_36m_count: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Calcula a Tração Científica (T_c, 0 a 10) como um modelo linear de 4
        componentes explícitos e auditáveis, cada um normalizado 0-10:

          T_c = w_V*[V] + w_G*[G] + w_A*[A] + w_Q*[Q]

        - [V] Volume: magnitude da evidência verificada na janela de 12 meses
          (Entity Resolution confirmada) - `min(10, verificados_12m * 2.2)`,
          mesma base matemática usada antes desta reestruturação.
        - [G] Crescimento vs. linha de base histórica: compara a taxa de
          publicação verificada dos últimos 12 meses contra `baseline_36m_count`
          - a contagem BRUTA do PubMed (sem Entity Resolution, por custo de
          API) numa janela de 36 meses ESTRITAMENTE ANTERIOR aos 12 meses de
          análise (meses 13-48 atrás, ver SCI_BASELINE_HISTORY_DAYS/
          connectors.pubmed.PubMedConnector `end_days_ago`) - sem sobreposição
          com o período recente. `ratio = (taxa_atual-taxa_base)/taxa_base`
          (clamp +-1; sem linha de base, ratio neutro), mapeado para 0-10 via
          `5 + 5*ratio` (5/10 = neutro, sem viés de alta nem de queda).
        - [A] Aplicabilidade: fração dos matches verificados de 12 meses que
          atingem Nível de relevância >= 2 (core/entity_resolver.py -
          contexto tópico/aplicado ou de produto/formulação confirmado).
        - [Q] Qualidade da Correspondência: confidence_score médio dos matches
          verificados de 12 meses, escalado para 0-10.

        Retorna o score final e os 4 componentes/pesos, para auditoria
        (generate_assessment() persiste o breakdown completo). Pesos
        provisórios - ver PROVISIONAL_WEIGHTS_DISCLAIMER.
        """
        resolved = self._verified_pubmed_matches(pubmed_matches)
        verified_count = len(resolved)

        weights = {"V": W_SCI_VOLUME, "G": W_SCI_GROWTH, "A": W_SCI_APPLICABILITY, "Q": W_SCI_QUALITY}

        if verified_count == 0:
            components = {"V": 0.0, "G": 5.0, "A": 0.0, "Q": 0.0}
            return {"score": 0.0, "components": components, "weights": weights}

        confidence_scores = [m["entity_match"].get("confidence_score", 0.5) for m in resolved]
        relevance_levels = [m["entity_match"].get("relevance_level", 1) for m in resolved]

        v_component = min(10.0, sum(confidence_scores) * 2.2)

        if baseline_36m_count and baseline_36m_count > 0:
            baseline_rate = baseline_36m_count / SCI_BASELINE_HISTORY_DAYS
            current_rate = verified_count / 365.0
            g_ratio = max(-1.0, min(1.0, (current_rate - baseline_rate) / baseline_rate))
            g_component = 5.0 + 5.0 * g_ratio
        else:
            g_component = 5.0  # sem linha de base para comparar - sem julgamento de tendência

        applicable_count = sum(1 for level in relevance_levels if level >= 2)
        a_component = 10.0 * (applicable_count / verified_count)

        q_component = 10.0 * (sum(confidence_scores) / verified_count)

        components = {
            "V": round(v_component, 2), "G": round(g_component, 2),
            "A": round(a_component, 2), "Q": round(q_component, 2)
        }
        score = (
            weights["V"] * components["V"] + weights["G"] * components["G"]
            + weights["A"] * components["A"] + weights["Q"] * components["Q"]
        )
        score = round(max(0.0, min(10.0, score)), 1)

        return {"score": score, "components": components, "weights": weights}

    def calculate_scientific_traction(
        self,
        pubmed_matches: List[Dict[str, Any]],
        baseline_36m_count: Optional[int] = None
    ) -> float:
        """Atalho que retorna só o score final de calculate_scientific_traction_breakdown() - ver docstring lá para a decomposição V/G/A/Q."""
        return self.calculate_scientific_traction_breakdown(pubmed_matches, baseline_36m_count=baseline_36m_count)["score"]

    def calculate_industrial_traction(self, patent_matches: List[Dict[str, Any]]) -> float:
        """
        Calcula a Tração Industrial (0 a 10) baseada em patentes (já
        deduplicadas por família) e inovação. `patent_matches` deve conter o
        conjunto de evidências da janela histórica móvel de 12 meses
        (connectors.patents.PatentConnector com days=TRACTION_WINDOW_DAYS) —
        mesma observação de calculate_scientific_traction. Patentes estão
        sujeitas à janela legal de publicação (defasagem entre depósito e
        publicação), por isso a janela de 12 meses é mais representativa da
        proteção industrial real do que a janela de novidades de 15 dias.

        Fórmula log-comprimida, calibrada por N_ref (ver
        INDUSTRIAL_TRACTION_N_REF acima):

            T_i = 10 x log(1 + count) / log(1 + N_ref)

        CORRIGIDO (2026-08-19): a fórmula anterior (base_score = min(8,
        count*3) + diversity_bonus = min(2, nº titulares únicos)) saturava
        em 10.0/10 já a partir de 3 patentes de 2 titulares distintos - uma
        função degrau, sem diferenciar 3 patentes de 300 (achado real:
        Chá Verde/AT-009 com 3 patentes e Cúrcuma/AT-015 com 5 patentes
        marcavam o mesmo 10.0/10 - ver METHODOLOGY.md). A curva log dá
        retornos decrescentes e usa como teto o maior volume de patentes
        validadas de fato observado no catálogo (não um número arbitrário),
        preservando a mesma regra de piso (`count == 0` -> `0.0`, nunca
        `log(1)/log(1+N_ref) = 0` calculado à toa) e o mesmo teto absoluto de
        10.0 (`min(10.0, ...)`, necessário porque um novo recorde de
        patentes pode ultrapassar o N_ref calibrado até a próxima revisão
        trimestral). O `diversity_bonus` (nº de titulares distintos) foi
        removido: na calibração de 2026-08-19, EM TODOS os 35 ativos do
        catálogo o nº de titulares distintos foi idêntico ao nº de patentes
        validadas (nenhum titular repetiu patente validada em nenhum ativo)
        - o termo nunca divergiu de `count` nesta base real, então não
        carregava informação adicional para justificar a complexidade extra.
        """
        total_patents = len(patent_matches)
        if total_patents == 0:
            return 0.0

        raw_score = 10.0 * math.log(1 + total_patents) / math.log(1 + INDUSTRIAL_TRACTION_N_REF)
        return round(min(10.0, raw_score), 1)

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
        Classifica isoladamente o R_trade (ex-R_supply) - Sinal de
        vulnerabilidade observado nos fluxos comerciais analisados, a partir
        de connectors.regulatory_comex.fetch_import_volume_mock() (chave
        'sinais_comerciais_comex' do dossiê) — sem considerar regulação (R_reg).
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

    def calculate_verified_evidence_count(self, pubmed_data: List[Dict[str, Any]], patent_data: List[Dict[str, Any]]) -> int:
        """
        Conta o total de evidências com Entity Resolution confirmada
        (entity_match presente), somando PubMed (janela de 12 meses) e
        patentes - usado por core/predictive_ranking.py para a categoria de
        precedência "Dados Insuficientes/Não Classificado" (MIN_VERIFIED_EVIDENCE).
        """
        pubmed_verified = sum(1 for m in pubmed_data if isinstance(m, dict) and m.get("entity_match"))
        patent_verified = sum(1 for m in patent_data if isinstance(m, dict) and m.get("entity_match"))
        return pubmed_verified + patent_verified

    def calculate_max_relevance_level(self, pubmed_data: List[Dict[str, Any]], patent_data: List[Dict[str, Any]]) -> int:
        """
        Maior 'relevance_level' (1/2/3, ver core/entity_resolver.py) entre
        todas as evidências resolvidas do ativo (PubMed 12 meses + patentes).
        0 se não houver nenhuma evidência resolvida. Usado por main.py para
        decidir se o ativo é elegível a recomendações de alta confiança da
        LLM (core/llm_analysis.py) - só Nível 2 ou 3.
        """
        levels = [
            m["entity_match"].get("relevance_level", 1)
            for m in (pubmed_data + patent_data)
            if isinstance(m, dict) and m.get("entity_match")
        ]
        return max(levels) if levels else 0

    def generate_assessment(
        self,
        pubmed_data: List[Dict[str, Any]],
        patent_data: List[Dict[str, Any]],
        regulatory_alerts: Dict[str, Any] = None,
        commercial_signals: Dict[str, Any] = None,
        query_hashes: Optional[List[str]] = None,
        baseline_36m_count: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Gera a avaliação final decomposta do ativo. Consome diretamente o formato
        refatorado dos conectores: 'regulatory_alerts' (dossier['alertas_regulatorios']
        - idealmente já o cross-jurisdição de RegulatoryComexConnector.get_regulatory_matrix(),
        ver main.py) e 'commercial_signals' (dossier['sinais_comerciais_comex']) de
        RegulatoryComexConnector.get_asset_dossier(). Retorna o dicionário completo
        de scores, incluindo o model_version desta execução e os query_hashes das
        evidências coletadas (para auditoria em evaluation_evidence_sources /
        rastreabilidade no PDF).

        `baseline_36m_count` (opcional): contagem bruta do PubMed na janela de
        linha de base histórica de 36 meses (ver SCI_BASELINE_HISTORY_DAYS),
        usada só pelo componente [G] de calculate_scientific_traction_breakdown
        - não afeta nenhum outro cálculo.
        """
        regulatory_alerts = regulatory_alerts or {}
        commercial_signals = commercial_signals or {}

        sc_breakdown = self.calculate_scientific_traction_breakdown(pubmed_data, baseline_36m_count=baseline_36m_count)
        sc_score = sc_breakdown["score"]
        ind_score = self.calculate_industrial_traction(patent_data)

        regulatory_alert_level = self.calculate_regulatory_alert_level(regulatory_alerts)
        commercial_signal_level = self.calculate_commercial_signal_level(commercial_signals)
        supply_risk = self.calculate_supply_risk(regulatory_alerts, commercial_signals)

        all_signals = pubmed_data + patent_data
        confidence = self.calculate_confidence_level(all_signals)
        evidencias_verificadas = self.calculate_verified_evidence_count(pubmed_data, patent_data)
        nivel_evidencia_maximo = self.calculate_max_relevance_level(pubmed_data, patent_data)

        return {
            "model_version": MODEL_VERSION,
            "tracao_cientifica": f"{sc_score}/10",
            "tracao_cientifica_componentes": sc_breakdown,
            "tracao_industrial": f"{ind_score}/10",
            "alerta_regulatorio": regulatory_alert_level,
            "sinal_comercial_comex": commercial_signal_level,
            "risco_oferta": supply_risk,
            "confianca_sinal": confidence,
            "total_evidencias": len(all_signals),
            "evidencias_verificadas": evidencias_verificadas,
            "nivel_evidencia_maximo": nivel_evidencia_maximo,
            "query_hashes": query_hashes or []
        }


if __name__ == "__main__":
    engine = ScoreEngine()

    mock_pubmed = [
        {"entity_match": {"confidence_score": 0.95, "relevance_level": 3}},
        {"entity_match": {"confidence_score": 0.95, "relevance_level": 2}}
    ]
    mock_patents = [{"assignee": "Derma Ltd", "entity_match": {"confidence_score": 0.95, "relevance_level": 1}}]
    mock_regulatory_alerts = {"restriction_level": "BAIXO", "alerts": []}
    mock_commercial_signals = {"suppliers_count": 8, "trend": "CRESCENTE"}
    mock_query_hashes = ["9c4a63a04bee3dbaff001145eb35bb6352592580d0592d02f96489b77a013b8", "53f1e4ad83e6db511b620ca438e81b495c9ac6a7abb97f9cb3cda732982e4eb"]

    print(f"[Ressalva] {PROVISIONAL_WEIGHTS_DISCLAIMER}")

    avaliacao = engine.generate_assessment(
        mock_pubmed, mock_patents, mock_regulatory_alerts, mock_commercial_signals,
        query_hashes=mock_query_hashes, baseline_36m_count=8  # linha de base estável (~mesma taxa dos últimos 12 meses)
    )

    print("\n--- Avaliação Decomposta Atualizada ---")
    print(f"Model Version:            {avaliacao['model_version']}")
    print(f"Tração Científica:        {avaliacao['tracao_cientifica']}")
    print(f"  Componentes V/G/A/Q:    {avaliacao['tracao_cientifica_componentes']['components']}")
    print(f"  Pesos:                  {avaliacao['tracao_cientifica_componentes']['weights']}")
    print(f"Tração Industrial:        {avaliacao['tracao_industrial']}")
    print(f"Alerta Regulatório:       {avaliacao['alerta_regulatorio']}")
    print(f"Sinal Comercial/Comex (R_trade): {avaliacao['sinal_comercial_comex']}")
    print(f"Risco de Oferta (consol.):{avaliacao['risco_oferta']}")
    print(f"Confiança do Sinal:       {avaliacao['confianca_sinal']}")
    print(f"Total Evidências:         {avaliacao['total_evidencias']}")
    print(f"Evidências Verificadas:   {avaliacao['evidencias_verificadas']}")
    print(f"Nível de Evidência Máximo:{avaliacao['nivel_evidencia_maximo']}")
    print(f"Query Hashes:             {avaliacao['query_hashes']}")

    print("\n--- Componente [G]: literatura recente em declínio vs. linha de base histórica de 36 meses ---")
    sc_estavel = engine.calculate_scientific_traction(mock_pubmed, baseline_36m_count=8)
    sc_declinio = engine.calculate_scientific_traction(mock_pubmed, baseline_36m_count=200)  # linha de base historicamente muito mais ativa
    print(f"Tração Científica (linha de base estável): {sc_estavel}/10")
    print(f"Tração Científica (linha de base historicamente alta, sinal atual fraco em comparação): {sc_declinio}/10 (deve ser < {sc_estavel})")
