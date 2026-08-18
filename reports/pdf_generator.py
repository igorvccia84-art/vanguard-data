import os
import re
import sys
import io
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Dict, Any, List, Optional, Tuple

if sys.stdout.encoding != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# Paleta oficial - Verde Florestal & Amarelo Dourado (nenhum tom de azul permitido)
COLOR_FOREST_GREEN = "#1B4D3E"
COLOR_GOLD = "#D4AF37"
COLOR_GOLD_MUTED = "#C5A059"
COLOR_GOLD_PALE = "#FFF9E6"
COLOR_SOFT_RED = "#C0392B"

SCHEMA_VERSION = "2.0.0"
MODEL_VERSION = "2.0.0"
BRAND_NAME = "Vanguard Data Intelligence"
SEARCH_WINDOW_DAYS = 15  # deve casar com connectors/pubmed.py e connectors/patents.py

# Autoridade regulatória e fonte de dados comerciais/comex padrão por idioma do
# relatório, usadas quando o chamador não injeta explicitamente o dossiê da
# jurisdição (ex.: uso standalone deste módulo). Em produção, main.py sempre
# repassa os valores reais retornados por
# connectors.regulatory_comex.RegulatoryComexConnector.get_asset_dossier() -
# os textos aqui casam com REGIONAL_AUTHORITIES daquele conector.
DEFAULT_REGIONAL_AUTHORITIES = {
    "PT-BR": {
        "regulatory_body": "Anvisa (Agência Nacional de Vigilância Sanitária, Brasil)",
        "trade_source": "Comex Stat (MDIC/SECEX, Brasil)"
    },
    "PT-PT": {
        "regulatory_body": "INFARMED (Autoridade Nacional do Medicamento e Produtos de Saúde, Portugal)",
        "trade_source": "Eurostat Comext / TARIC (dados de comércio exterior da União Europeia)"
    },
    "ES": {
        "regulatory_body": "AEMPS (Agencia Española de Medicamentos y Productos Sanitarios, España)",
        "trade_source": "Eurostat Comext / TARIC (datos de comercio exterior de la Unión Europea)"
    }
}

PREDICTIVE_CATEGORY_CLASS = {
    "Emerging Stars": "cat-emerging",
    "High-Risk / Supply Alert": "cat-highrisk",
    "Disruptive Dark Horses": "cat-darkhorse",
}

# Rótulos localizados das categorias preditivas (core/predictive_ranking.py gera os
# identificadores canônicos em inglês; aqui eles são traduzidos para exibição -
# a chave de busca em PREDICTIVE_CATEGORY_CLASS continua sendo o identificador original).
PREDICTIVE_CATEGORY_LABELS = {
    "PT-BR": {
        "Emerging Stars": "Estrela Emergente",
        "High-Risk / Supply Alert": "Risco Alto / Alerta de Oferta",
        "Disruptive Dark Horses": "Sinal Científico sem Confirmação Industrial",
    },
    "PT-PT": {
        "Emerging Stars": "Estrela Emergente",
        "High-Risk / Supply Alert": "Risco Elevado / Alerta de Abastecimento",
        "Disruptive Dark Horses": "Sinal Científico sem Confirmação Industrial",
    },
    "ES": {
        "Emerging Stars": "Estrella Emergente",
        "High-Risk / Supply Alert": "Riesgo Alto / Alerta de Suministro",
        "Disruptive Dark Horses": "Señal Científica sin Confirmación Industrial",
    },
}

# Definições metodológicas curtas das categorias preditivas (core/predictive_ranking.py),
# exibidas como legenda/glossário logo abaixo da tabela de ativos no relatório.
# Linguagem simples e orientada a negócios (não-técnica), para leitura executiva.
PREDICTIVE_CATEGORY_DEFINITIONS = {
    "PT-BR": {
        "Emerging Stars": "Ativo com alta validação científica e forte proteção por patentes observadas no período analisado.",
        "Disruptive Dark Horses": "Tração científica observada sem correspondência de patentes verificada no período analisado - sinal ainda não confirmado industrialmente.",
        "High-Risk / Supply Alert": "Sinal de mercado observado com gargalos de fornecimento ou restrições regulatórias identificados. Requer atenção de suprimentos.",
    },
    "PT-PT": {
        "Emerging Stars": "Ativo com elevada validação científica e forte proteção por patentes observadas no período analisado.",
        "Disruptive Dark Horses": "Tração científica observada sem correspondência de patentes verificada no período analisado - sinal ainda não confirmado industrialmente.",
        "High-Risk / Supply Alert": "Sinal de mercado observado com estrangulamentos no abastecimento ou restrições regulatórias identificados. Requer atenção da cadeia de abastecimento.",
    },
    "ES": {
        "Emerging Stars": "Activo con alta validación científica y fuerte protección por patentes observadas en el período analizado.",
        "Disruptive Dark Horses": "Tracción científica observada sin correspondencia de patentes verificada en el período analizado - señal aún no confirmada industrialmente.",
        "High-Risk / Supply Alert": "Señal de mercado observada con cuellos de botella de suministro o restricciones regulatorias identificados. Requiere atención de la cadena de suministro.",
    },
}

# Rótulos localizados do Risco de Oferta (core/score_engine.py gera os valores
# canônicos em português sem acento - "ALTO RISCO"/"MEDIO RISCO"/"BAIXO RISCO";
# aqui são traduzidos para exibição. A classe CSS do badge continua derivada do
# valor canônico original, nunca do rótulo traduzido.
SUPPLY_RISK_LABELS = {
    "PT-BR": {"ALTO RISCO": "ALTO RISCO", "MEDIO RISCO": "MÉDIO RISCO", "BAIXO RISCO": "BAIXO RISCO"},
    "PT-PT": {"ALTO RISCO": "RISCO ALTO", "MEDIO RISCO": "RISCO MÉDIO", "BAIXO RISCO": "RISCO BAIXO"},
    "ES": {"ALTO RISCO": "RIESGO ALTO", "MEDIO RISCO": "RIESGO MEDIO", "BAIXO RISCO": "RIESGO BAJO"},
}

# Rótulos localizados da Confiança do Sinal (core/score_engine.py gera os valores
# canônicos em português - "ALTA"/"MÉDIA"/"BAIXA"). A classe CSS do badge continua
# derivada do valor canônico original, nunca do rótulo traduzido.
CONFIDENCE_LABELS = {
    "PT-BR": {"ALTA": "ALTA", "MÉDIA": "MÉDIA", "BAIXA": "BAIXA"},
    "PT-PT": {"ALTA": "ALTA", "MÉDIA": "MÉDIA", "BAIXA": "BAIXA"},
    "ES": {"ALTA": "ALTA", "MÉDIA": "MEDIA", "BAIXA": "BAJA"},
}

# Kind code de patente (ex.: "A1", "B2", ou letra única "A") no final do número
# de publicação - usado para exibir o número formatado com hífen (WO2024087654A1
# -> WO2024087654-A1), como nos exemplos oficiais de EPO/WIPO.
_PATENT_KIND_CODE_PATTERN = re.compile(r'^(.*\d)([A-Z]\d?)$')

# Termos/padrões que indicam uma recomendação ausente/degenerada vinda de
# core/llm_analysis.py (placeholder genérico ou rótulo técnico de erro) - rede
# de segurança no próprio template: nada disso deve chegar ao PDF final visto
# pelo cliente, mesmo que a validação em llm_analysis.py falhe por algum motivo.
_DEGENERATE_TEXT_TOKENS = {
    "placeholder", "n/a", "na", "tbd", "todo", "lorem ipsum", "texto", "sample",
    "example", "sem dados", "no data", "null", "none", "undefined", "-", "--", "..."
}
_MIN_RECOMMENDATION_LENGTH = 20


# Blindagem determinística contra PMID/patente alucinado pela LLM - camada
# independente da mesma checagem em core/llm_analysis.py (reports/ não deve
# depender do motor de LLM). Mesmo que a camada da LLM falhe, nenhum PMID ou
# número de patente que não conste na allow-list real (vinda dos conectores)
# chega ao PDF final.
_PMID_MENTION_PATTERN = re.compile(r'\bPMID\s*[:\-]?\s*(\d{4,9})\b', re.IGNORECASE)
_PATENT_MENTION_PATTERN = re.compile(r'\b([A-Z]{2}\d{4,}[A-Z]\d?)\b')


def _scrub_unverified_identifiers(text: Optional[str], allowed_pmids: Optional[List[str]] = None, allowed_patent_ids: Optional[List[str]] = None) -> Optional[str]:
    """Remove menções a PMID/patente no texto que não estejam na allow-list real coletada pelos conectores para este ativo."""
    if not text:
        return text

    allowed_pmids_set = set(allowed_pmids or [])
    allowed_patent_ids_norm = {pid.replace("-", "").upper() for pid in (allowed_patent_ids or [])}

    def _strip_pmid(match: "re.Match") -> str:
        return match.group(0) if match.group(1) in allowed_pmids_set else ""

    def _strip_patent(match: "re.Match") -> str:
        return match.group(0) if match.group(1).replace("-", "").upper() in allowed_patent_ids_norm else ""

    cleaned = _PMID_MENTION_PATTERN.sub(_strip_pmid, text)
    cleaned = _PATENT_MENTION_PATTERN.sub(_strip_patent, cleaned)
    return re.sub(r'\s{2,}', ' ', cleaned).strip()


def _is_degenerate_recommendation(text: Optional[str]) -> bool:
    """Detecta texto ausente, placeholder genérico, rótulo de erro entre colchetes, ou curto demais para ser uma recomendação real."""
    if not text:
        return True
    stripped = str(text).strip()
    if not stripped:
        return True
    if stripped.startswith("[") and stripped.endswith("]"):
        return True
    cleaned = stripped.strip('.').lower()
    if cleaned in _DEGENERATE_TEXT_TOKENS:
        return True
    if len(cleaned) < _MIN_RECOMMENDATION_LENGTH:
        return True
    return False


# Análise de suprimento padrão, calculada a partir do Risco de Oferta do
# ativo - usada quando a recomendação de Compras & Procurement gerada pela IA
# vem ausente/degenerada. Mesmos textos de core/llm_analysis.py, mantidos aqui
# porque reports/ não deve depender do motor de LLM (camadas independentes).
DEFAULT_SUPPLY_RECOMMENDATION = {
    "PT-BR": {
        "BAIXO RISCO": "Risco de Oferta baixo para este ativo: recomenda-se negociar contratos regulares com os fornecedores mapeados, sem necessidade de medidas emergenciais de mitigação.",
        "MEDIO RISCO": "Risco de Oferta moderado para este ativo: recomenda-se monitorar a base de fornecedores e avaliar a qualificação de fontes alternativas antes de expandir os volumes de compra.",
        "ALTO RISCO": "Risco de Oferta elevado para este ativo: recomenda-se priorizar a diversificação de fornecedores e considerar estoque de segurança para mitigar riscos de descontinuidade de suprimento."
    },
    "PT-PT": {
        "BAIXO RISCO": "Risco de Oferta baixo para este ativo: recomenda-se negociar contratos regulares com os fornecedores mapeados, sem necessidade de medidas de contingência.",
        "MEDIO RISCO": "Risco de Oferta moderado para este ativo: recomenda-se monitorizar a base de fornecedores e avaliar a qualificação de fontes alternativas antes de expandir os volumes de compra.",
        "ALTO RISCO": "Risco de Oferta elevado para este ativo: recomenda-se priorizar a diversificação de fornecedores e considerar stock de segurança para mitigar riscos de descontinuidade no abastecimento."
    },
    "ES": {
        "BAIXO RISCO": "Riesgo de Oferta bajo para este activo: se recomienda negociar contratos regulares con los proveedores mapeados, sin necesidad de medidas de contingencia.",
        "MEDIO RISCO": "Riesgo de Oferta moderado para este activo: se recomienda monitorear la base de proveedores y evaluar la cualificación de fuentes alternativas antes de ampliar los volúmenes de compra.",
        "ALTO RISCO": "Riesgo de Oferta elevado para este activo: se recomienda priorizar la diversificación de proveedores y considerar stock de seguridad para mitigar riesgos de discontinuidad del suministro."
    }
}

# Recomendação de Inovação & P&D padrão, calculada a partir da soma de Tração
# Científica + Industrial - mesma rede de segurança para o outro campo do schema.
DEFAULT_INNOVATION_RECOMMENDATION = {
    "PT-BR": {
        "alta": "Tração Científica e Industrial favoráveis para este ativo: recomenda-se priorizar investimento em P&D para consolidar a posição de mercado e acelerar o desenvolvimento de novas formulações.",
        "media": "Sinal científico/industrial moderado para este ativo: recomenda-se acompanhar a evolução da literatura antes de comprometer recursos significativos de P&D.",
        "baixa": "Sinal científico/industrial ainda incipiente para este ativo: recomenda-se monitoramento contínuo da literatura emergente antes de qualquer investimento relevante em P&D."
    },
    "PT-PT": {
        "alta": "Tracção Científica e Industrial favoráveis para este ativo: recomenda-se priorizar investimento em I&D para consolidar a posição de mercado e acelerar o desenvolvimento de novas formulações.",
        "media": "Sinal científico/industrial moderado para este ativo: recomenda-se acompanhar a evolução da literatura antes de comprometer recursos significativos de I&D.",
        "baixa": "Sinal científico/industrial ainda incipiente para este ativo: recomenda-se monitorização contínua da literatura emergente antes de qualquer investimento relevante em I&D."
    },
    "ES": {
        "alta": "Tracción Científica e Industrial favorables para este activo: se recomienda priorizar la inversión en I+D para consolidar la posición de mercado y acelerar el desarrollo de nuevas formulaciones.",
        "media": "Señal científica/industrial moderada para este activo: se recomienda seguir la evolución de la literatura antes de comprometer recursos significativos de I+D.",
        "baixa": "Señal científica/industrial aún incipiente para este activo: se recomienda monitoreo continuo de la literatura emergente antes de cualquier inversión relevante en I+D."
    }
}


class PDFReportGenerator:
    """
    Gerador do PhytoDemand Report (produto executivo da Vanguard Data).
    Gera relatórios nos formatos HTML e PDF nos idiomas PT-BR, PT-PT e ES.
    Identidade visual: Verde Florestal (#1B4D3E) e Amarelo Dourado (#D4AF37) - sem azul.

    Identificadores técnicos de auditoria (Run ID, Schema Version, Model
    Version, Data/Hora de Processamento) aparecem em uma única linha discreta
    no rodapé de cada página, sem repetição em cabeçalhos ou caixas de
    destaque do corpo - mantidos apenas para fins de auditoria interna. O
    corpo do relatório traz, em vez disso, uma caixa de "Período de Análise e
    Fontes de Dados" (Período, autoridade regulatória e fonte de comércio
    exterior da jurisdição, e o aviso metodológico sobre a janela móvel de 12
    meses). Os identificadores reais das evidências coletadas (PMIDs do
    PubMed e registros de patentes) aparecem associados diretamente à
    recomendação estratégica de cada ativo, não em um bloco agregado
    genérico, e nunca incluem PMIDs/patentes gerados pela LLM. Uma legenda
    metodológica das categorias preditivas acompanha a tabela de ativos.
    """

    TRANSLATIONS = {
        "PT-BR": {
            "title": "PhytoDemand Report",
            "document_classification": "Relatório Interno de Revisão Técnica e Metodológica",
            "subtitle": "Protótipo Auditável de Priorização Estratégica baseado em Sinais Científicos, Industriais, Regulatórios e Comerciais",
            "asset_id": "ID do Ativo",
            "canonical_name": "Nome Canônico",
            "predictive_category": "Categoria de Triagem",
            "sci_traction": "Tração Científica",
            "ind_traction": "Tração Industrial",
            "supply_risk": "Sinal de Risco de Oferta Observado",
            "confidence": "Confiança do Sinal",
            "recommendations_title": "Recomendações Estratégicas",
            "col_asset": "Ativo",
            "col_rd": "Inovação & P&D",
            "col_procurement": "Compras & Procurement",
            "col_evidence_pmid": "PMID",
            "col_evidence_pat": "PAT",
            "footer": "Relatório gerado automaticamente pela Plataforma Vanguard Data (Brasil)",
            "audit_title": "Período de Análise e Fontes de Dados",
            "audit_run_id": "ID de Execução (Run ID)",
            "audit_processed_at": "Data/Hora de Processamento",
            "audit_schema_version": "Versão do Schema",
            "audit_model_version": "Versão do Modelo",
            "audit_brand": "Plataforma",
            "audit_period": "Período de Análise",
            "audit_period_to": "a",
            "audit_period_window_suffix": "(Novidades dos Últimos 15 Dias)",
            "audit_data_sources": "Fontes de Dados",
            "methodology_disclaimer": "Atualização quinzenal; tendência calculada sobre histórico móvel de 12 meses.",
            "commercial_disclaimer": "Valores comerciais refletem o volume declarado no código alfandegário analisado e podem incluir outros insumos além do ativo.",
            "patent_disclaimer": "Sinais industriais baseados em publicações públicas; depósitos recentes dos últimos 18 meses estão sujeitos a sigilo legal.",
            "weights_disclaimer": "Pesos provisórios, ainda não calibrados por validação externa/backtesting.",
            "regulatory_matrix_title": "Matriz Regulatória por Jurisdição",
            "regulatory_matrix_max_severity": "Máximo de severidade regulatória observado entre as jurisdições monitoradas",
            "methodology_title": "Legenda Metodológica das Categorias de Triagem"
        },
        "PT-PT": {
            "title": "PhytoDemand Report",
            "document_classification": "Relatório Interno de Revisão Técnica e Metodológica",
            "subtitle": "Protótipo Auditável de Priorização Estratégica baseado em Sinais Científicos, Industriais, Regulatórios e Comerciais",
            "asset_id": "ID do Ativo",
            "canonical_name": "Nome Canónico",
            "predictive_category": "Categoria de Triagem",
            "sci_traction": "Tracção Científica",
            "ind_traction": "Tracção Industrial",
            "supply_risk": "Sinal de Risco de Oferta Observado",
            "confidence": "Confiança do Sinal",
            "recommendations_title": "Recomendações Estratégicas",
            "col_asset": "Ativo",
            "col_rd": "Inovação & I&D",
            "col_procurement": "Compras & Procurement",
            "col_evidence_pmid": "PMID",
            "col_evidence_pat": "PAT",
            "footer": "Relatório gerado automaticamente pela Plataforma Vanguard Data (Portugal)",
            "audit_title": "Período de Análise e Fontes de Dados",
            "audit_run_id": "ID de Execução (Run ID)",
            "audit_processed_at": "Data/Hora de Processamento",
            "audit_schema_version": "Versão do Schema",
            "audit_model_version": "Versão do Modelo",
            "audit_brand": "Plataforma",
            "audit_period": "Período de Análise",
            "audit_period_to": "a",
            "audit_period_window_suffix": "(Novidades dos Últimos 15 Dias)",
            "audit_data_sources": "Fontes de Dados",
            "methodology_disclaimer": "Atualização quinzenal; tendência calculada sobre histórico móvel de 12 meses.",
            "commercial_disclaimer": "Valores comerciais refletem o volume declarado no código alfandegário analisado e podem incluir outros insumos além do ativo.",
            "patent_disclaimer": "Sinais industriais baseados em publicações públicas; depósitos recentes dos últimos 18 meses estão sujeitos a sigilo legal.",
            "weights_disclaimer": "Pesos provisórios, ainda não calibrados por validação externa/backtesting.",
            "regulatory_matrix_title": "Matriz Regulatória por Jurisdição",
            "regulatory_matrix_max_severity": "Máximo de severidade regulatória observado entre as jurisdições monitoradas",
            "methodology_title": "Legenda Metodológica das Categorias de Triagem"
        },
        "ES": {
            "title": "PhytoDemand Report",
            "document_classification": "Informe Interno de Revisión Técnica y Metodológica",
            "subtitle": "Prototipo Auditable de Priorización Estratégica basado en Señales Científicas, Industriales, Regulatorias y Comerciales",
            "asset_id": "ID del Activo",
            "canonical_name": "Nombre Canónico",
            "predictive_category": "Categoría de Triaje",
            "sci_traction": "Tracción Científica",
            "ind_traction": "Tracción Industrial",
            "supply_risk": "Señal de Riesgo de Oferta Observada",
            "confidence": "Confianza de la Señal",
            "recommendations_title": "Recomendaciones Estratégicas",
            "col_asset": "Activo",
            "col_rd": "Innovación & I+D",
            "col_procurement": "Compras & Procurement",
            "col_evidence_pmid": "PMID",
            "col_evidence_pat": "PAT",
            "footer": "Informe generado automáticamente por la Plataforma Vanguard Data",
            "audit_title": "Período de Análisis y Fuentes de Datos",
            "audit_run_id": "ID de Ejecución (Run ID)",
            "audit_processed_at": "Fecha/Hora de Procesamiento",
            "audit_schema_version": "Versión del Esquema",
            "audit_model_version": "Versión del Modelo",
            "audit_brand": "Plataforma",
            "audit_period": "Período de Análisis",
            "audit_period_to": "a",
            "audit_period_window_suffix": "(Novedades de los Últimos 15 Días)",
            "audit_data_sources": "Fuentes de Datos",
            "methodology_disclaimer": "Actualización quincenal; la tendencia se calcula sobre un historial móvil de 12 meses.",
            "commercial_disclaimer": "Los valores comerciales reflejan el volumen declarado en el código arancelario analizado y pueden incluir otros insumos además del activo.",
            "patent_disclaimer": "Señales industriales basadas en publicaciones públicas; los depósitos recientes de los últimos 18 meses están sujetos a secreto legal.",
            "weights_disclaimer": "Pesos provisionales, aún no calibrados por validación externa/backtesting.",
            "regulatory_matrix_title": "Matriz Regulatoria por Jurisdicción",
            "regulatory_matrix_max_severity": "Máximo de severidad regulatoria observado entre las jurisdicciones monitoreadas",
            "methodology_title": "Leyenda Metodológica de las Categorías de Triaje"
        }
    }

    def __init__(self, output_dir: str = "reports/output"):
        self.output_dir = output_dir
        os.makedirs(self.output_dir, exist_ok=True)

    @staticmethod
    def _format_patent_id(patent_id: str) -> str:
        """Formata o número de patente com hífen antes do kind code, ex.: WO2024087654A1 -> WO2024087654-A1."""
        if not patent_id or '-' in patent_id:
            return patent_id
        match = _PATENT_KIND_CODE_PATTERN.match(patent_id)
        if match:
            return f"{match.group(1)}-{match.group(2)}"
        return patent_id

    @staticmethod
    def _default_analysis_period() -> Tuple[str, str]:
        """Janela padrão de análise: dos últimos SEARCH_WINDOW_DAYS dias até hoje (mesma janela aplicada nos conectores)."""
        end_date = date.today()
        start_date = end_date - timedelta(days=SEARCH_WINDOW_DAYS)
        return start_date.isoformat(), end_date.isoformat()

    @staticmethod
    def _default_supply_recommendation(supply_risk: Optional[str], lang_key: str) -> str:
        """Análise de suprimento padrão calculada a partir do Risco de Oferta do ativo."""
        table = DEFAULT_SUPPLY_RECOMMENDATION.get(lang_key, DEFAULT_SUPPLY_RECOMMENDATION["PT-BR"])
        return table.get(supply_risk, table["MEDIO RISCO"])

    @staticmethod
    def _default_innovation_recommendation(item: Dict[str, Any], lang_key: str) -> str:
        """Recomendação de Inovação & P&D padrão calculada a partir de Tração Científica + Industrial do ativo."""
        def _parse_score(score_str: Any) -> float:
            try:
                return float(str(score_str).split("/")[0])
            except (ValueError, AttributeError, IndexError, TypeError):
                return 0.0

        combined = _parse_score(item.get("scientific_traction")) + _parse_score(item.get("industrial_traction"))
        tier = "alta" if combined >= 10 else "media" if combined >= 4 else "baixa"
        table = DEFAULT_INNOVATION_RECOMMENDATION.get(lang_key, DEFAULT_INNOVATION_RECOMMENDATION["PT-BR"])
        return table[tier]

    def generate_report(
        self,
        evaluations: List[Dict[str, Any]],
        lang: str = "PT-BR",
        run_id: Optional[str] = None,
        schema_version: str = SCHEMA_VERSION,
        model_version: str = MODEL_VERSION,
        period_start: Optional[str] = None,
        period_end: Optional[str] = None,
        regulatory_body: Optional[str] = None,
        trade_source: Optional[str] = None,
        regulatory_matrix: Optional[Dict[str, Any]] = None
    ) -> str:
        """
        Gera o HTML e converte diretamente para arquivo PDF.

        `regulatory_matrix` (opcional): `{"jurisdictions_monitored": [...]}` -
        lista das jurisdições consideradas (Anvisa/UE/FDA) no cálculo do
        Alerta Regulatório de CADA ativo da tabela (que já reflete o pior
        caso entre elas, ver main.py + connectors.regulatory_comex.get_regulatory_matrix())
        - é uma nota metodológica de nível de relatório, não uma matriz por
        ativo individual (o relatório cobre 8 ativos diferentes). Se omitido,
        a seção não é exibida (uso standalone deste módulo).
        """
        t = self.TRANSLATIONS.get(lang.upper(), self.TRANSLATIONS["PT-BR"])

        run_id = run_id or str(uuid.uuid4())
        processed_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        default_period_start, default_period_end = self._default_analysis_period()
        period_start = period_start or default_period_start
        period_end = period_end or default_period_end

        default_authorities = DEFAULT_REGIONAL_AUTHORITIES.get(lang.upper(), DEFAULT_REGIONAL_AUTHORITIES["PT-BR"])
        regulatory_body = regulatory_body or default_authorities["regulatory_body"]
        trade_source = trade_source or default_authorities["trade_source"]

        regulatory_matrix_html = ""
        if regulatory_matrix:
            jurisdictions_monitored = " · ".join(regulatory_matrix.get("jurisdictions_monitored", []))
            regulatory_matrix_html = f"""
            <div class="audit-row">
                <div class="audit-label">{t['regulatory_matrix_title']}</div>
                <div class="audit-value">{jurisdictions_monitored}</div>
            </div>
            """

        rows_html = ""
        for item in evaluations:
            category = item.get("predictive_category", "")
            category_class = PREDICTIVE_CATEGORY_CLASS.get(category, "cat-emerging")
            category_label = PREDICTIVE_CATEGORY_LABELS.get(lang.upper(), {}).get(category, category)

            supply_risk_class = item['supply_risk'].lower().replace(' ', '-')
            supply_risk_label = SUPPLY_RISK_LABELS.get(lang.upper(), {}).get(item['supply_risk'], item['supply_risk'])

            confidence_class = item['confidence_level'].lower()
            confidence_label = CONFIDENCE_LABELS.get(lang.upper(), {}).get(item['confidence_level'], item['confidence_level'])

            rows_html += f"""
            <tr>
                <td><strong>{item['asset_id']}</strong></td>
                <td>{item['canonical_name']}</td>
                <td style="text-align: center;"><span class="badge {category_class}">{category_label}</span></td>
                <td style="text-align: center;">{item['scientific_traction']}</td>
                <td style="text-align: center;">{item['industrial_traction']}</td>
                <td style="text-align: center;"><span class="badge {supply_risk_class}">{supply_risk_label}</span></td>
                <td style="text-align: center;"><span class="badge conf-{confidence_class}">{confidence_label}</span></td>
            </tr>
            """

        recommendation_rows_html = ""
        for item in evaluations:
            inovacao_pd = item.get("inovacao_pd")
            compras_procurement = item.get("compras_procurement")
            if not inovacao_pd and not compras_procurement:
                continue

            # Rede de segurança: nunca exibe uma recomendação ausente/degenerada
            # (placeholder, rótulo técnico de erro, texto vazio ou curto demais).
            # Quando isso ocorre, a célula é alimentada por uma análise padrão
            # calculada a partir do risco_oferta (Compras & Procurement) ou da
            # Tração Científica/Industrial (Inovação & P&D) do próprio ativo.
            if _is_degenerate_recommendation(inovacao_pd):
                inovacao_pd = self._default_innovation_recommendation(item, lang.upper())
            if _is_degenerate_recommendation(compras_procurement):
                compras_procurement = self._default_supply_recommendation(item.get("supply_risk"), lang.upper())

            asset_pmids = item.get("pmids", []) or []
            asset_patent_ids = item.get("patent_ids", []) or []
            inovacao_pd = _scrub_unverified_identifiers(inovacao_pd, asset_pmids, asset_patent_ids)
            compras_procurement = _scrub_unverified_identifiers(compras_procurement, asset_pmids, asset_patent_ids)

            evidence_tags = "".join(f'<span class="evidence-tag">{t["col_evidence_pmid"]}: {p}</span>' for p in item.get("pmids", []) or [])
            evidence_tags += "".join(
                f'<span class="evidence-tag">{t["col_evidence_pat"]}: {self._format_patent_id(p)}</span>'
                for p in item.get("patent_ids", []) or []
            )
            evidence_html = f'<div class="evidence-refs">{evidence_tags}</div>' if evidence_tags else ""

            recommendation_rows_html += f"""
            <tr>
                <td class="col-ativo">{item['canonical_name']}{evidence_html}</td>
                <td>{inovacao_pd or '—'}</td>
                <td>{compras_procurement or '—'}</td>
            </tr>
            """

        category_defs = PREDICTIVE_CATEGORY_DEFINITIONS.get(lang.upper(), PREDICTIVE_CATEGORY_DEFINITIONS["PT-BR"])
        methodology_html = ""
        for category_key in ("Emerging Stars", "Disruptive Dark Horses", "High-Risk / Supply Alert"):
            label = PREDICTIVE_CATEGORY_LABELS.get(lang.upper(), {}).get(category_key, category_key)
            css_class = PREDICTIVE_CATEGORY_CLASS.get(category_key, "cat-emerging")
            definition = category_defs.get(category_key, "")
            methodology_html += f"""
            <div class="methodology-item">
                <span class="badge {css_class}">{label}</span>
                <span class="methodology-text">{definition}</span>
            </div>
            """

        # Cabeçalho de página: identidade da marca/relatório apenas - sem
        # identificadores técnicos (Run ID/Schema/Model), que ficam
        # concentrados numa única linha discreta no rodapé (ver abaixo).
        audit_header_html = (
            f'<span class="audit-brand">{BRAND_NAME}</span>'
            f'<span class="audit-sep">|</span>'
            f'<span>{t["title"]}</span>'
        )
        # Rodapé de página: única linha discreta com a identificação técnica
        # completa (Run ID, Schema, Model, Data/Hora de Processamento), para
        # fins de auditoria interna - não repetida em nenhum outro bloco.
        audit_footer_html = (
            f'{BRAND_NAME}'
            f'<span class="audit-sep">|</span>'
            f'{t["audit_run_id"]}: {run_id}'
            f'<span class="audit-sep">|</span>'
            f'Schema v{schema_version}'
            f'<span class="audit-sep">|</span>'
            f'Model v{model_version}'
            f'<span class="audit-sep">|</span>'
            f'{t["audit_processed_at"]}: {processed_at}'
        )

        html_content = f"""<!DOCTYPE html>
<html lang="{lang.lower()}">
<head>
    <meta charset="UTF-8">
    <title>{t['title']}</title>
    <style>
        @page {{
            size: A4;
            margin: 32mm 12mm 22mm 12mm;
            @frame header_frame {{
                -pdf-frame-content: audit_header_content;
                top: 8mm; left: 12mm; width: 186mm; height: 14mm;
            }}
            @frame footer_frame {{
                -pdf-frame-content: audit_footer_content;
                bottom: 8mm; left: 12mm; width: 186mm; height: 10mm;
            }}
        }}
        body {{
            font-family: 'Helvetica Neue', Arial, sans-serif;
            color: #2c3e50;
            margin: 0;
            padding: 0;
            background-color: #ffffff;
        }}
        #audit_header_content, #audit_footer_content {{
            position: fixed;
            left: 0; right: 0;
        }}
        #audit_header_content {{
            top: 0;
            background-color: {COLOR_FOREST_GREEN};
            color: #ffffff;
            font-size: 7pt;
            padding: 4px 8px;
            border-radius: 3px;
        }}
        #audit_footer_content {{
            bottom: 0;
            font-size: 6.5pt;
            color: #718096;
            padding: 3px 8px;
            border-top: 1px solid #e2e8f0;
        }}
        .audit-sep {{
            margin: 0 6px;
            opacity: 0.6;
        }}
        .header {{
            border-bottom: 3px solid {COLOR_FOREST_GREEN};
            padding-bottom: 12px;
            margin-bottom: 20px;
        }}
        .header h1 {{
            color: {COLOR_FOREST_GREEN};
            font-size: 16pt;
            margin: 0 0 6px 0;
            text-transform: uppercase;
        }}
        .header h2 {{
            color: #718096;
            font-size: 10pt;
            margin: 0;
            font-weight: normal;
        }}
        .document-classification {{
            display: inline-block;
            color: {COLOR_FOREST_GREEN};
            background-color: {COLOR_GOLD_PALE};
            border: 1px solid {COLOR_GOLD_MUTED};
            border-radius: 3px;
            font-size: 7.5pt;
            font-weight: bold;
            text-transform: uppercase;
            letter-spacing: 0.4px;
            padding: 2px 8px;
            margin: 0 0 8px 0;
        }}
        .audit-box {{
            border: 1.5px solid {COLOR_FOREST_GREEN};
            background-color: {COLOR_GOLD_PALE};
            border-radius: 4px;
            padding: 10px 14px;
            margin-bottom: 22px;
        }}
        .audit-box h3 {{
            color: {COLOR_FOREST_GREEN};
            font-size: 10.5pt;
            text-transform: uppercase;
            margin: 0 0 8px 0;
            letter-spacing: 0.5px;
        }}
        .audit-grid {{
            display: table;
            width: 100%;
            margin-bottom: 8px;
        }}
        .methodology-disclaimer {{
            margin: 6px 0 0 0;
            font-size: 7pt;
            font-style: italic;
            color: #4a5568;
        }}
        .audit-row {{
            display: table-row;
        }}
        .audit-label {{
            display: table-cell;
            font-size: 7.5pt;
            font-weight: bold;
            color: #4a5568;
            padding: 2px 8px 2px 0;
            width: 32%;
            white-space: nowrap;
        }}
        .audit-value {{
            display: table-cell;
            font-size: 8pt;
            color: #1a202c;
            padding: 2px 0;
            font-family: 'Courier New', monospace;
        }}
        .audit-empty {{
            font-size: 7.5pt;
            color: #a0aec0;
            font-style: italic;
        }}
        .evidence-refs {{
            margin-top: 5px;
        }}
        .evidence-tag {{
            display: block;
            font-family: 'Courier New', monospace;
            font-size: 6.5pt;
            color: {COLOR_GOLD_PALE};
            opacity: 0.9;
            word-break: break-all;
            line-height: 1.35;
        }}
        .methodology-box {{
            border: 1px solid #e2e8f0;
            border-left: 4px solid {COLOR_GOLD_MUTED};
            background-color: #fafafa;
            border-radius: 4px;
            padding: 10px 14px;
            margin: 10px 0 24px 0;
        }}
        .methodology-box h4 {{
            color: {COLOR_FOREST_GREEN};
            font-size: 9pt;
            text-transform: uppercase;
            margin: 0 0 8px 0;
            letter-spacing: 0.4px;
        }}
        .methodology-item {{
            margin-bottom: 6px;
            font-size: 7.5pt;
            line-height: 1.4;
        }}
        .methodology-item .badge {{
            margin-right: 6px;
        }}
        .methodology-text {{
            color: #4a5568;
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
            margin-top: 15px;
        }}
        th {{
            background-color: {COLOR_FOREST_GREEN};
            color: #ffffff;
            font-size: 8.5pt;
            text-transform: uppercase;
            padding: 8px 6px;
            text-align: left;
        }}
        td {{
            padding: 8px 6px;
            font-size: 9pt;
            border-bottom: 1px solid #e2e8f0;
        }}
        tr:nth-child(even) {{
            background-color: #f7fafc;
        }}
        .badge {{
            padding: 3px 6px;
            border-radius: 4px;
            font-size: 7.5pt;
            font-weight: bold;
        }}
        .baixo-risco {{ background-color: #c6f6d5; color: #22543d; }}
        .medio-risco {{ background-color: {COLOR_GOLD_PALE}; color: #7a5c00; }}
        .alto-risco {{ background-color: {COLOR_GOLD}; color: #4a3800; }}
        .conf-alta {{ color: {COLOR_FOREST_GREEN}; font-weight: bold; }}
        .conf-média, .conf-media {{ color: #a8791a; font-weight: bold; }}
        .conf-baixa {{ color: {COLOR_SOFT_RED}; font-weight: bold; }}
        .cat-emerging {{ background-color: {COLOR_FOREST_GREEN}; color: #ffffff; }}
        .cat-highrisk {{ background-color: {COLOR_GOLD}; color: #4a3800; }}
        .cat-darkhorse {{ background-color: {COLOR_GOLD_PALE}; color: #7a5c00; border: 1px solid {COLOR_GOLD_MUTED}; }}
        .section-title {{
            color: {COLOR_FOREST_GREEN};
            font-size: 12pt;
            margin: 30px 0 4px 0;
            text-transform: uppercase;
        }}
        table.recommendations {{
            table-layout: fixed;
        }}
        table.recommendations th.header-pd {{
            background-color: {COLOR_FOREST_GREEN};
            color: #ffffff;
        }}
        table.recommendations th.header-procurement {{
            background-color: {COLOR_GOLD_MUTED};
            color: #ffffff;
        }}
        table.recommendations td {{
            vertical-align: top;
            line-height: 1.4;
            font-size: 8.5pt;
        }}
        table.recommendations tr:nth-child(even) td {{
            background-color: {COLOR_GOLD_PALE};
        }}
        table.recommendations td.col-ativo {{
            background-color: {COLOR_FOREST_GREEN} !important;
            color: #ffffff !important;
            font-weight: bold;
            width: 18%;
        }}
        .footer {{
            margin-top: 30px;
            border-top: 1px solid #e2e8f0;
            padding-top: 10px;
            font-size: 8pt;
            color: #a0aec0;
            text-align: center;
        }}
    </style>
</head>
<body>
    <div id="audit_header_content">{audit_header_html}</div>
    <div id="audit_footer_content">{audit_footer_html}</div>

    <div class="header">
        <h1>{t['title']}</h1>
        <p class="document-classification">{t['document_classification']}</p>
        <h2>{t['subtitle']}</h2>
    </div>

    <div class="audit-box">
        <h3>{t['audit_title']}</h3>
        <div class="audit-grid">
            <div class="audit-row">
                <div class="audit-label">{t['audit_period']}</div>
                <div class="audit-value">{period_start} {t['audit_period_to']} {period_end} {t['audit_period_window_suffix']}</div>
            </div>
            <div class="audit-row">
                <div class="audit-label">{t['audit_data_sources']}</div>
                <div class="audit-value">{regulatory_body} · {trade_source}</div>
            </div>
            {regulatory_matrix_html}
        </div>
        <p class="methodology-disclaimer">{t['methodology_disclaimer']}</p>
        <p class="methodology-disclaimer">{t['commercial_disclaimer']}</p>
        <p class="methodology-disclaimer">{t['patent_disclaimer']}</p>
        <p class="methodology-disclaimer">{t['weights_disclaimer']}</p>
        {f'<p class="methodology-disclaimer">{t["regulatory_matrix_max_severity"]}.</p>' if regulatory_matrix else ''}
    </div>

    <table>
        <thead>
            <tr>
                <th>{t['asset_id']}</th>
                <th>{t['canonical_name']}</th>
                <th style="text-align: center;">{t['predictive_category']}</th>
                <th style="text-align: center;">{t['sci_traction']}</th>
                <th style="text-align: center;">{t['ind_traction']}</th>
                <th style="text-align: center;">{t['supply_risk']}</th>
                <th style="text-align: center;">{t['confidence']}</th>
            </tr>
        </thead>
        <tbody>
            {rows_html}
        </tbody>
    </table>

    <div class="methodology-box">
        <h4>{t['methodology_title']}</h4>
        {methodology_html}
    </div>
    {f'''<h3 class="section-title">{t['recommendations_title']}</h3>
    <table class="recommendations">
        <thead>
            <tr>
                <th>{t['col_asset']}</th>
                <th class="header-pd">{t['col_rd']}</th>
                <th class="header-procurement">{t['col_procurement']}</th>
            </tr>
        </thead>
        <tbody>
            {recommendation_rows_html}
        </tbody>
    </table>''' if recommendation_rows_html else ''}
    <div class="footer">
        {t['footer']}
    </div>
</body>
</html>"""

        prefix = lang.lower().replace('-', '_')
        html_path = os.path.join(self.output_dir, f"relatorio_vanguard_{prefix}.html")
        pdf_path = os.path.join(self.output_dir, f"relatorio_vanguard_{prefix}.pdf")

        # Salva o arquivo HTML
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html_content)

        # Converte para PDF
        try:
            from weasyprint import HTML
            HTML(filename=html_path).write_pdf(pdf_path)
            print(f"   [+] PDF gerado com sucesso via WeasyPrint: {pdf_path}")
        except Exception:
            try:
                from xhtml2pdf import pisa
                with open(pdf_path, "wb") as pdf_file:
                    pisa.CreatePDF(html_content, dest=pdf_file)
                print(f"   [+] PDF gerado com sucesso via xhtml2pdf: {pdf_path}")
            except Exception as e:
                print(f"   [-] Falha ao gerar PDF automaticamente ({e}). HTML mantido em: {html_path}")
                return html_path

        return pdf_path

    def export_all_languages(
        self,
        evaluations: List[Dict[str, Any]],
        run_id: Optional[str] = None,
        schema_version: str = SCHEMA_VERSION,
        model_version: str = MODEL_VERSION,
        period_start: Optional[str] = None,
        period_end: Optional[str] = None
    ) -> List[str]:
        """Gera os relatórios PDF para PT-BR, PT-PT e ES, compartilhando o mesmo Run ID e Período de Análise entre os três idiomas."""
        run_id = run_id or str(uuid.uuid4())
        if period_start is None or period_end is None:
            period_start, period_end = self._default_analysis_period()
        generated_files = []
        for lang in ["PT-BR", "PT-PT", "ES"]:
            file_path = self.generate_report(
                evaluations, lang=lang, run_id=run_id, schema_version=schema_version, model_version=model_version,
                period_start=period_start, period_end=period_end
            )
            generated_files.append(file_path)
        return generated_files


if __name__ == "__main__":
    generator = PDFReportGenerator()
    demo_run_id = str(uuid.uuid4())
    mock_evals = [
        {
            "asset_id": "AT-001", "canonical_name": "Bakuchiol", "predictive_category": "Emerging Stars",
            "scientific_traction": "6.3/10", "industrial_traction": "6.0/10",
            "supply_risk": "BAIXO RISCO", "confidence_level": "ALTA",
            "inovacao_pd": "Investir em estudos de estabilidade e eficácia comparativa frente ao retinol.",
            "compras_procurement": "Negociar contratos de médio prazo com fornecedores já qualificados.",
            "pmids": ["42596530", "42459258"],
            "patent_ids": ["EP3892258A1"]
        },
        {
            "asset_id": "AT-019", "canonical_name": "Alcaçuz", "predictive_category": "High-Risk / Supply Alert",
            "scientific_traction": "4.4/10", "industrial_traction": "0.0/10",
            "supply_risk": "ALTO RISCO", "confidence_level": "MÉDIA",
            "inovacao_pd": "Avaliar alternativas de padronização para reduzir dependência regulatória.",
            "compras_procurement": "Qualificar fornecedores adicionais para mitigar risco de escassez.",
            "pmids": ["41820934"],
            "patent_ids": []
        },
        {
            "asset_id": "AT-005", "canonical_name": "Bidens Pilosa", "predictive_category": "Disruptive Dark Horses",
            "scientific_traction": "2.1/10", "industrial_traction": "0.0/10",
            "supply_risk": "BAIXO RISCO", "confidence_level": "BAIXA",
            "inovacao_pd": "Monitorar literatura emergente antes de comprometer recursos de P&D.",
            "compras_procurement": "Sem ação de compras necessária neste estágio de maturidade.",
            "pmids": [],
            "patent_ids": []
        }
    ]
    generator.export_all_languages(mock_evals, run_id=demo_run_id)
    print(f"\nRun ID de auditoria usado nos 3 idiomas: {demo_run_id}")
