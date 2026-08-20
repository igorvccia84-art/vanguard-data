import sys
import io
import json
import re
from typing import Dict, Any, List

import truststore
truststore.inject_into_ssl()  # usa o armazenamento de certificados do SO (necessário com AV/proxy que interceptam TLS, ex.: Avast)

from dotenv import load_dotenv
import anthropic

from core.formatting import format_usd_estimate
from core.predictive_ranking import MIN_SCI_FOR_EMERGING_STAR, MIN_IND_FOR_EMERGING_STAR

if sys.stdout.encoding != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

load_dotenv()

MODEL_ID = "claude-sonnet-5"

# Isolamento regulatório por idioma do relatório
REGULATORY_BODY = {
    "PT-BR": "Anvisa (Agência Nacional de Vigilância Sanitária, Brasil)",
    "PT-PT": "INFARMED (Autoridade Nacional do Medicamento e Produtos de Saúde, Portugal)",
    "ES": "AEMPS (Agencia Española de Medicamentos y Productos Sanitarios, España)"
}

# Contexto de mercado-alvo por idioma do relatório - injetado explicitamente no
# prompt de generate_recommendations() para forçar uma análise regulatória e
# comercial realmente distinta por região, em vez de uma análise genérica com
# apenas o nome do órgão regulatório trocado.
REGION_CONTEXT = {
    "PT-BR": {
        "region_code": "BR",
        "market_label": "Brasil",
        "regulatory_framework": "Resoluções da Diretoria Colegiada (RDC) da Anvisa aplicáveis a produtos de higiene pessoal, cosméticos e perfumes",
        "trade_data_label": "Comex Stat (MDIC/SECEX, Brasil)"
    },
    "PT-PT": {
        "region_code": "PT",
        "market_label": "Portugal (mercado da União Europeia)",
        "regulatory_framework": "Regulamento (CE) n.º 1223/2009 relativo aos produtos cosméticos (base CosIng/ECHA - base informativa sem valor legal próprio), com fiscalização nacional pelo INFARMED",
        "trade_data_label": "Eurostat Comext / TARIC (dados de comércio exterior da União Europeia)"
    },
    "ES": {
        "region_code": "ES",
        "market_label": "España (mercado de la Unión Europea)",
        "regulatory_framework": "Reglamento (CE) n.º 1223/2009 sobre productos cosméticos (base CosIng/ECHA - base informativa sin valor legal propio), con fiscalización nacional por la AEMPS",
        "trade_data_label": "Eurostat Comext / TARIC (datos de comercio exterior de la Unión Europea)"
    }
}

# Strict Grounding Directive: aplicada como system prompt em toda chamada ao LLM
# desta engine. Contramedida direta contra alucinação - a IA deve se ater 100% ao
# contexto fornecido na mensagem do usuário (abstracts de artigos, títulos de
# patentes, métricas do assessment), nunca preenchendo lacunas com conhecimento
# paramétrico ou suposições plausíveis.
STRICT_GROUNDING_DIRECTIVE = """DIRETRIZ DE FUNDAMENTAÇÃO ESTRITA (Strict Grounding Directive):
Você é um analista que trabalha exclusivamente com os dados fornecidos na mensagem do usuário. É terminantemente proibido:
(1) inventar, extrapolar ou presumir números, percentuais, dosagens, resultados estatísticos ou qualquer dado quantitativo que não esteja explicitamente presente no contexto fornecido;
(2) inventar mecanismos de ação, vias bioquímicas ou processos farmacológicos não mencionados no contexto fornecido;
(3) inventar aplicações, indicações de uso, benefícios, estudos, autores ou publicações que não constem explicitamente no contexto fornecido;
(4) inventar, completar, corrigir ou citar por conta própria qualquer PMID, número de patente ou código identificador - você só pode citar um PMID ou número de patente se ele aparecer literalmente, caractere por caractere, no contexto fornecido nesta mensagem; se não tiver certeza absoluta de um identificador, não o mencione;
(5) atribuir dados financeiros, de volume de mercado ou de comércio exterior a uma base regulatória (ex.: CosIng/ECHA) - bases regulatórias só podem ser citadas para status regulatório, funções cosméticas e restrições legais;
(6) apresentar o Risco de Oferta como um fato certo e definitivo - trate-o sempre como um sinal observado a partir dos dados disponíveis (ex.: "o sinal observado indica risco elevado de oferta"), nunca como uma previsão garantida;
(7) tratar a ausência de patente recente no contexto fornecido como prova de baixo interesse industrial - depósitos dos últimos 18 meses podem estar sob sigilo legal (ainda não publicados) e não aparecem nas buscas públicas.
Toda afirmação factual da sua resposta deve ser rastreável a um trecho específico do contexto fornecido nesta mensagem. Se o contexto fornecido for insuficiente, incompleto ou ausente para sustentar uma afirmação, declare essa limitação explicitamente em vez de preencher a lacuna com suposições ou conhecimento geral."""

# Schema de saída estruturada de generate_recommendations(): cada recomendação
# (inovacao_pd/compras_procurement) é decomposta em 'claim' (a recomendação
# em si), 'evidence_ids' (PMIDs/patentes citados - validados contra a
# allow-list real antes de liberar o payload para o gerador de PDF, ver
# _validate_evidence_ids) e 'limitations' (o que a evidência disponível não
# cobre) - força a LLM a expor o raciocínio de forma auditável, em vez de uma
# única string de prosa onde afirmação e limitação se confundem.
_CLAIM_BLOCK_SCHEMA = {
    "type": "object",
    "properties": {
        "claim": {"type": "string"},
        "evidence_ids": {"type": "array", "items": {"type": "string"}},
        "limitations": {"type": "string"}
    },
    "required": ["claim", "evidence_ids", "limitations"],
    "additionalProperties": False
}

RECOMMENDATION_SCHEMA = {
    "type": "object",
    "properties": {
        "inovacao_pd": _CLAIM_BLOCK_SCHEMA,
        "compras_procurement": _CLAIM_BLOCK_SCHEMA
    },
    "required": ["inovacao_pd", "compras_procurement"],
    "additionalProperties": False
}


# Aspas retas e tipográficas (curly quotes), chaves e crase - resíduos comuns
# de JSON mal fechado ou cercas de código markdown na saída do LLM.
_RESIDUE_PATTERN = re.compile(r'^[\s"\'“”‘’`{}]+|[\s"\'“”‘’`{}]+$')

# Detecta caudas degeneradas de repetição - o modelo entra em loop tentando
# sinalizar o fim da resposta (ex.: ")}(fim)}(concluído)}(pronto)}(fim.)}(.)")
# em vez de simplesmente parar. 3+ grupos parentéticos curtos consecutivos
# não ocorrem em prosa legítima, então tudo a partir do primeiro grupo é corte seguro.
_DEGENERATE_LOOP_PATTERN = re.compile(r'(\([^()]{0,60}\)\}?){3,}')

# Termos/padrões que indicam saída degenerada do LLM (placeholder genérico
# devolvido em vez de uma recomendação real) - nunca devem chegar ao relatório
# final visto pelo cliente. Complementado por uma checagem de comprimento
# mínimo, já que uma recomendação real tem pelo menos 2-3 frases.
_DEGENERATE_TEXT_TOKENS = {
    "placeholder", "n/a", "na", "tbd", "todo", "lorem ipsum", "texto", "sample",
    "example", "sem dados", "no data", "null", "none", "undefined", "-", "--", "..."
}
_MIN_RECOMMENDATION_LENGTH = 20


def _sanitize_text(text: str) -> str:
    """
    Sanitização rígida contra resíduos de formatação em saídas de texto do LLM
    (cercas de código markdown, aspas retas/tipográficas, chaves soltas de JSON
    mal fechado e caudas degeneradas de repetição) antes de qualquer texto ser
    enviado ao gerador de relatórios.
    """
    if not text:
        return text
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`").strip()
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].strip()

    loop_match = _DEGENERATE_LOOP_PATTERN.search(cleaned)
    if loop_match:
        cleaned = cleaned[:loop_match.start()]

    cleaned = _RESIDUE_PATTERN.sub('', cleaned)
    return cleaned.strip()


def _is_degenerate_text(text: str) -> bool:
    """
    Detecta saída degenerada do LLM: vazia, um placeholder genérico (ex.:
    "placeholder", "N/A", "TBD"), um rótulo técnico de erro entre colchetes
    (ex.: "[Falha ao gerar recomendação via LLM]"), ou curta demais para ser
    uma recomendação real. Nada disso deve aparecer no relatório final.
    """
    if not text:
        return True
    stripped = text.strip()
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


# Blindagem determinística contra PMID/patente alucinado pela LLM (independente
# da STRICT_GROUNDING_DIRECTIVE, que é apenas uma instrução ao modelo, não uma
# garantia). Detecta padrões de PMID (ex.: "PMID 42596530") e de número de
# patente EPO/WIPO (ex.: "EP3892258A1", "WO2024087654A1") em texto livre
# gerado pela LLM, para checagem contra a allow-list real de IDs coletados
# pelos conectores (connectors/pubmed.py, connectors/patents.py).
_PMID_MENTION_PATTERN = re.compile(r'\bPMID\s*[:\-]?\s*(\d{4,9})\b', re.IGNORECASE)
_PATENT_MENTION_PATTERN = re.compile(r'\b([A-Z]{2}\d{4,}[A-Z]\d?)\b')


def _scrub_unverified_identifiers(text: str, allowed_pmids: List[str] = None, allowed_patent_ids: List[str] = None) -> str:
    """
    Remove qualquer menção a PMID ou número de patente no texto gerado pela
    LLM que não esteja literalmente na allow-list de IDs reais coletados
    pelos conectores para este ativo (nunca vindos da LLM). Isso é um
    backstop determinístico: mesmo que a LLM ignore a STRICT_GROUNDING_DIRECTIVE
    e cite um PMID/patente inventado (ou correto, mas fora de contexto), o
    trecho é removido antes de o texto chegar ao relatório final.
    """
    if not text:
        return text

    allowed_pmids = set(allowed_pmids or [])
    allowed_patent_ids_norm = {pid.replace("-", "").upper() for pid in (allowed_patent_ids or [])}

    def _strip_pmid(match: "re.Match") -> str:
        return match.group(0) if match.group(1) in allowed_pmids else ""

    def _strip_patent(match: "re.Match") -> str:
        return match.group(0) if match.group(1).replace("-", "").upper() in allowed_patent_ids_norm else ""

    cleaned = _PMID_MENTION_PATTERN.sub(_strip_pmid, text)
    cleaned = _PATENT_MENTION_PATTERN.sub(_strip_patent, cleaned)
    return re.sub(r'\s{2,}', ' ', cleaned).strip()


def _validate_evidence_ids(
    evidence_ids: List[str],
    allowed_pmids: List[str] = None,
    allowed_patent_ids: List[str] = None,
    context_label: str = ""
) -> List[str]:
    """
    Valida cada evidence_id do campo estruturado 'evidence_ids' (RECOMMENDATION_SCHEMA)
    contra a allow-list real de IDs coletados pelos conectores para este ativo.
    Descarta (e loga) qualquer evidence_id que não exista na allow-list -
    o payload liberado para reports/pdf_generator.py nunca carrega um
    evidence_id não verificado, mesmo que a LLM o tenha citado.
    """
    if not evidence_ids:
        return []

    allowed_pmids_set = set(allowed_pmids or [])
    allowed_patent_ids_norm = {pid.replace("-", "").upper() for pid in (allowed_patent_ids or [])}

    valid_ids = []
    for raw_id in evidence_ids:
        eid = str(raw_id).strip()
        if eid in allowed_pmids_set or eid.replace("-", "").upper() in allowed_patent_ids_norm:
            valid_ids.append(eid)
        else:
            print(f"   ⚠️  evidence_id '{eid}' descartado (fora da allow-list real) {context_label}")
    return valid_ids


def _parse_score_value(score_str: Any) -> float:
    """Extrai o valor numérico de uma métrica no formato 'X.X/10' (ex.: score_engine.py)."""
    try:
        return float(str(score_str).split("/")[0])
    except (ValueError, AttributeError, IndexError, TypeError):
        return 0.0


# Análise de suprimento padrão, calculada a partir do Risco de Oferta do ativo -
# usada como rede de segurança quando o LLM devolve uma recomendação de Compras
# & Procurement ausente/degenerada, mesmo após as tentativas de retry.
_DEFAULT_SUPPLY_RECOMMENDATION = {
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
_DEFAULT_INNOVATION_RECOMMENDATION = {
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


# Trava determinística pós-LLM (validador, não instrução de prompt): quando o
# ativo está em TIER_INSUFFICIENT_DATA (core/predictive_ranking.py) ou com
# confianca_sinal == "BAIXA", nenhuma recomendação executiva de compra ou
# priorização é emitida - a chamada à LLM é pulada inteiramente para os dois
# campos (economiza custo de API) e este texto fixo é retornado no lugar.
_INSUFFICIENT_DATA_HOLDING_STATEMENT = {
    "PT-BR": {
        "inovacao_pd": "Recomendação executiva de Inovação & P&D suspensa: dados insuficientes para embasar uma decisão de priorização deste ativo.",
        "compras_procurement": "Recomendação executiva de Compras & Procurement suspensa: dados insuficientes para embasar uma decisão de compra deste ativo."
    },
    "PT-PT": {
        "inovacao_pd": "Recomendação executiva de Inovação & I&D suspensa: dados insuficientes para embasar uma decisão de priorização deste ativo.",
        "compras_procurement": "Recomendação executiva de Compras & Procurement suspensa: dados insuficientes para embasar uma decisão de compra deste ativo."
    },
    "ES": {
        "inovacao_pd": "Recomendación ejecutiva de Innovación & I+D suspendida: datos insuficientes para fundamentar una decisión de priorización de este activo.",
        "compras_procurement": "Recomendación ejecutiva de Compras & Procurement suspendida: datos insuficientes para fundamentar una decisión de compra de este activo."
    }
}


def _default_supply_recommendation(risco_oferta: str, lang_key: str) -> str:
    table = _DEFAULT_SUPPLY_RECOMMENDATION.get(lang_key, _DEFAULT_SUPPLY_RECOMMENDATION["PT-BR"])
    return table.get(risco_oferta, table["MEDIO RISCO"])


def _default_innovation_recommendation(assessment: Dict[str, Any], lang_key: str) -> str:
    sci = _parse_score_value(assessment.get("tracao_cientifica"))
    ind = _parse_score_value(assessment.get("tracao_industrial"))
    # "alta" exige piso mínimo em CADA componente (mesma regra e mesmo
    # motivo de core.predictive_ranking.classify_precedence_tier - um
    # componente zerado nunca deveria produzir uma recomendação de
    # prioridade "alta" só porque o outro está saturado; corrigido em
    # 2026-08-19 junto com o tier "Estrela Emergente").
    if sci >= MIN_SCI_FOR_EMERGING_STAR and ind >= MIN_IND_FOR_EMERGING_STAR:
        tier = "alta"
    else:
        combined = sci + ind
        tier = "media" if combined >= 4 else "baixa"
    table = _DEFAULT_INNOVATION_RECOMMENDATION.get(lang_key, _DEFAULT_INNOVATION_RECOMMENDATION["PT-BR"])
    return table[tier]


class LLMAnalysisEngine:
    """
    Motor de síntese e recomendação via API Anthropic (Claude).
    Resume evidências brutas (PubMed + patentes WIPO/EPO) e gera recomendações
    dinâmicas de Inovação & P&D e Compras & Procurement, com isolamento
    regulatório conforme o idioma do relatório (Anvisa/INFARMED/AEMPS).
    """

    def __init__(self, model: str = MODEL_ID):
        self.model = model
        self.client = anthropic.Anthropic()  # lê ANTHROPIC_API_KEY do ambiente (.env via python-dotenv)

    def summarize_evidence(
        self,
        canonical_name: str,
        pubmed_articles: List[Dict[str, Any]],
        patents: List[Dict[str, Any]]
    ) -> str:
        """Resume as evidências brutas coletadas (artigos do PubMed e patentes WIPO/EPO)."""
        raw_evidence = {
            "artigos_pubmed": [
                {"pmid": a.get("pmid"), "titulo": a.get("title")}
                for a in pubmed_articles
            ],
            "patentes": [
                {"id": p.get("patent_id"), "titulo": p.get("title"), "titular": p.get("assignee")}
                for p in patents
            ]
        }

        if not raw_evidence["artigos_pubmed"] and not raw_evidence["patentes"]:
            return "Nenhuma evidência científica ou industrial coletada para este ativo até o momento."

        prompt = f"""Você é um analista de inteligência de ativos dermocosméticos.
Resuma objetivamente, em português, em no máximo 3 frases, as evidências brutas abaixo coletadas para o ativo "{canonical_name}", destacando o principal achado científico e o principal sinal de proteção industrial (patentes), se houver.

Evidências brutas (JSON):
{json.dumps(raw_evidence, ensure_ascii=False, indent=2)}

Responda apenas com o resumo, sem introduções ou comentários adicionais."""

        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=1024,
                system=STRICT_GROUNDING_DIRECTIVE,
                messages=[{"role": "user", "content": prompt}]
            )
        except anthropic.APIStatusError as e:
            return f"[Falha ao sintetizar evidências via LLM: {e.status_code} - {e.message}]"

        raw_text = next((b.text for b in response.content if b.type == "text"), "")
        sanitized = _sanitize_text(raw_text)
        allowed_pmids = [a.get("pmid") for a in pubmed_articles if a.get("pmid")]
        allowed_patent_ids = [p.get("patent_id") for p in patents if p.get("patent_id")]
        return _scrub_unverified_identifiers(sanitized, allowed_pmids, allowed_patent_ids)

    def generate_recommendations(
        self,
        canonical_name: str,
        assessment: Dict[str, Any],
        regulatory_alerts: Dict[str, Any] = None,
        commercial_signals: Dict[str, Any] = None,
        lang: str = "PT-BR",
        pmids: List[str] = None,
        patent_ids: List[str] = None,
        high_confidence: bool = True,
        is_insufficient_data: bool = False
    ) -> Dict[str, str]:
        """
        Gera recomendações dinâmicas de Inovação & P&D e Compras & Procurement
        para o mercado-alvo específico do idioma do relatório (BR/PT/ES).
        Combina as métricas globais baseadas em evidência (Tração Científica/
        Industrial, iguais em qualquer mercado) com o dossiê regulatório e
        comercial daquela região (status/alertas regulatórios específicos,
        fornecedores/tendência de suprimento locais) via 'regulatory_alerts' e
        'commercial_signals' - saída de
        connectors.regulatory_comex.RegulatoryComexConnector.get_asset_dossier().
        O prompt exige explicitamente que a análise cite esses dados regionais,
        para produzir recomendações realmente distintas por mercado, não uma
        análise genérica com apenas o nome do órgão regulatório trocado.

        A LLM responde no formato estruturado RECOMMENDATION_SCHEMA (claim +
        evidence_ids + limitations por recomendação) - cada evidence_id
        citado é validado contra a allow-list real de PMIDs/patentes
        ('pmids'/'patent_ids', vinda dos conectores) antes de compor o texto
        final; qualquer ID fora da allow-list é descartado (_validate_evidence_ids).

        `high_confidence=False` (nível de evidência do ativo < 2, ver
        core/entity_resolver.py e core/score_engine.calculate_max_relevance_level)
        restringe a LLM a um framing deliberadamente conservador: nenhuma
        recomendação de alta confiança é emitida sem evidência de Nível 2/3 -
        mas ainda produz uma recomendação (hedged).

        `is_insufficient_data=True` (tier "Dados Insuficientes/Não Classificado"
        de core/predictive_ranking.py OU confianca_sinal=="BAIXA") é uma trava
        determinística mais forte que high_confidence: a chamada à LLM é
        pulada inteiramente e um texto fixo de suspensão é retornado para os
        dois campos - nenhuma recomendação executiva de compra ou priorização
        é emitida quando a evidência não sustenta, ponto final (não é uma
        instrução de prompt, que é só uma sugestão à LLM).
        """
        if is_insufficient_data:
            holding_statement = _INSUFFICIENT_DATA_HOLDING_STATEMENT.get(
                lang.upper(), _INSUFFICIENT_DATA_HOLDING_STATEMENT["PT-BR"]
            )
            return dict(holding_statement)

        lang_key = lang.upper()
        regulatory_body = REGULATORY_BODY.get(lang_key, REGULATORY_BODY["PT-BR"])
        region = REGION_CONTEXT.get(lang_key, REGION_CONTEXT["PT-BR"])
        regulatory_alerts = regulatory_alerts or {}
        commercial_signals = commercial_signals or {}
        alerts_text = "; ".join(regulatory_alerts.get("alerts") or []) or "nenhum alerta específico registrado"

        evidence_ids_hint = ", ".join((pmids or []) + (patent_ids or [])) or "nenhum"

        confidence_instruction = (
            "- O ativo tem evidência de Nível 2/3 (sinal tópico/aplicado ou de produto/formulação confirmado): pode usar um framing de confiança normal, sempre ancorado nos dados fornecidos."
            if high_confidence else
            "- ATENÇÃO: este ativo só tem evidência de Nível 1 (mera correspondência de nome, sem sinal tópico/aplicado confirmado) ou evidência insuficiente. NÃO escreva uma recomendação de alta confiança. O 'claim' deve ser explicitamente conservador (ex.: \"evidência preliminar sugere...\", \"ainda não é possível recomendar com confiança...\"), e 'limitations' DEVE declarar que a evidência disponível não passa do Nível 1/insuficiente para uma recomendação de alta confiança."
        )

        prompt = f"""Você é um consultor estratégico de P&D e Procurement para a indústria dermocosmética, especializado no mercado de {region['market_label']}.

Ativo: {canonical_name}

MÉTRICAS GLOBAIS (baseadas em evidência científica/industrial - as mesmas em qualquer mercado):
Tração Científica: {assessment.get('tracao_cientifica')}
Tração Industrial: {assessment.get('tracao_industrial')}
Confiança do Sinal: {assessment.get('confianca_sinal')}

DADOS ESPECÍFICOS DO MERCADO-ALVO ({region['market_label']} / {region['region_code']}) - use-os para diferenciar esta análise de qualquer outra região:
Marco regulatório aplicável: {region['regulatory_framework']}
Órgão regulatório de referência: {regulatory_body}
Status regulatório local: {regulatory_alerts.get('status', 'N/A')} (nível de restrição: {regulatory_alerts.get('restriction_level', 'N/A')})
Concentração máxima permitida para uso tópico nesta jurisdição: {regulatory_alerts.get('max_concentration_allowed', 'N/A')}
Alertas regulatórios específicos desta jurisdição: {alerts_text}
Fonte de dados de suprimento/comércio: {region['trade_data_label']}
Fornecedores mapeados nesta região: {commercial_signals.get('suppliers_count', 'N/A')}
Tendência de oferta nesta região: {commercial_signals.get('trend', 'N/A')}
Volume anual estimado (USD, faixa arredondada) nesta região: {format_usd_estimate(commercial_signals.get('volume_usd_annual'), lang=lang_key)}
Sinal de Risco de Oferta Observado (regulatório + comercial) para {region['market_label']}: {assessment.get('risco_oferta')}

RESSALVAS OBRIGATÓRIAS (considere-as ao escrever claim/limitations):
- Os valores comerciais acima refletem o volume declarado no código alfandegário analisado e podem incluir outros insumos além deste ativo especificamente.
- Sinais industriais (patentes) baseiam-se em publicações públicas; depósitos dos últimos 18 meses podem estar sob sigilo legal e não aparecer nesta busca.

IDs DE EVIDÊNCIA DISPONÍVEIS PARA CITAÇÃO (não invente outros; use string vazia/array vazio se nenhum se aplicar a uma recomendação específica): {evidence_ids_hint}

INSTRUÇÕES OBRIGATÓRIAS:
- Responda no formato estruturado exigido: para cada recomendação, preencha 'claim' (a recomendação em si, curta e acionável, 2-3 frases, no idioma "{lang}"), 'evidence_ids' (só IDs da lista acima que sustentam diretamente esta recomendação) e 'limitations' (o que a evidência disponível NÃO cobre para esta recomendação).
- As recomendações devem refletir especificidades REAIS do marco regulatório e da dinâmica de suprimento de {region['market_label']} listados acima: cite o marco regulatório e/ou o alerta específico ao justificar o 'claim' de Inovação & P&D, e cite os dados de fornecedores/tendência ao justificar o de Compras & Procurement.
- NÃO escreva um 'claim' genérico que sirva igualmente para outro mercado. O texto deve ser claramente distinguível do que seria escrito para outra região, mesmo quando as métricas globais forem idênticas.
- NUNCA responda com um placeholder genérico (ex.: "placeholder", "N/A", "TBD", "a definir") em 'claim'. Mesmo com evidências científicas limitadas, produza sempre uma frase real e específica baseada nos dados regulatórios/comerciais fornecidos acima, e declare a limitação em 'limitations' em vez de inflar o 'claim'.
{confidence_instruction}

1. Uma recomendação para o time de Inovação & P&D (campo inovacao_pd).
2. Uma recomendação para o time de Compras & Procurement (campo compras_procurement).

Responda apenas com o JSON estruturado. Não inclua comentários sobre o processo de geração, marcadores de conclusão (ex.: "fim", "concluído", "pronto") ou qualquer texto após o JSON — pare assim que o conteúdo estiver completo."""

        # Rastreia o último par de textos obtidos (mesmo que degenerado) para poder
        # aproveitar o campo que veio válido caso só o outro precise de fallback.
        last_inovacao, last_compras = "", ""
        last_failure = None

        for attempt in range(2):
            try:
                response = self.client.messages.create(
                    model=self.model,
                    max_tokens=4096,
                    system=STRICT_GROUNDING_DIRECTIVE,
                    output_config={"format": {"type": "json_schema", "schema": RECOMMENDATION_SCHEMA}},
                    messages=[{"role": "user", "content": prompt}]
                )
            except anthropic.APIStatusError as e:
                last_failure = f"HTTP {e.status_code} - {e.message}"
                continue

            raw_text = next(b.text for b in response.content if b.type == "text")

            if response.stop_reason == "max_tokens":
                last_failure = "resposta truncada por limite de tokens"
                continue

            try:
                parsed = json.loads(raw_text)
            except json.JSONDecodeError:
                last_failure = "falha ao interpretar resposta estruturada do LLM"
                continue

            last_inovacao = self._compose_recommendation_text(
                parsed.get("inovacao_pd", {}), pmids, patent_ids, canonical_name, "inovacao_pd"
            )
            last_compras = self._compose_recommendation_text(
                parsed.get("compras_procurement", {}), pmids, patent_ids, canonical_name, "compras_procurement"
            )

            if not _is_degenerate_text(last_inovacao) and not _is_degenerate_text(last_compras):
                return {"inovacao_pd": last_inovacao, "compras_procurement": last_compras}

            last_failure = "recomendação degenerada/placeholder devolvida pelo LLM"

        # Esgotadas as tentativas: nunca expõe rótulo técnico de erro (ex.: "[Falha
        # ao gerar recomendação via LLM]") no relatório final. Cada campo degenerado
        # é substituído por uma análise padrão calculada a partir do assessment;
        # um campo que tenha vindo válido em alguma tentativa é preservado.
        if last_failure:
            print(f"   ⚠️  Recomendação para '{canonical_name}' ({lang}) usando fallback determinístico: {last_failure}")

        inovacao_final = last_inovacao if not _is_degenerate_text(last_inovacao) else _default_innovation_recommendation(assessment, lang_key)
        compras_final = last_compras if not _is_degenerate_text(last_compras) else _default_supply_recommendation(assessment.get("risco_oferta"), lang_key)

        return {"inovacao_pd": inovacao_final, "compras_procurement": compras_final}

    @staticmethod
    def _compose_recommendation_text(
        claim_block: Dict[str, Any],
        pmids: List[str],
        patent_ids: List[str],
        canonical_name: str,
        field_label: str
    ) -> str:
        """
        Converte um bloco estruturado {claim, evidence_ids, limitations}
        (RECOMMENDATION_SCHEMA) no texto final consumido por
        reports/pdf_generator.py. Valida evidence_ids contra a allow-list
        real (_validate_evidence_ids - só para auditoria/log, já que o texto
        final não lista os IDs) e passa claim/limitations por
        _scrub_unverified_identifiers (defesa em profundidade contra ID solto
        em prosa, fora do array evidence_ids).
        """
        if not isinstance(claim_block, dict):
            return ""

        claim = _scrub_unverified_identifiers(_sanitize_text(claim_block.get("claim", "")), pmids, patent_ids)
        limitations = _scrub_unverified_identifiers(_sanitize_text(claim_block.get("limitations", "")), pmids, patent_ids)

        _validate_evidence_ids(
            claim_block.get("evidence_ids") or [], pmids, patent_ids,
            context_label=f"(ativo='{canonical_name}', campo='{field_label}')"
        )

        if not _is_degenerate_text(limitations):
            return f"{claim} Limitações: {limitations}"
        return claim


if __name__ == "__main__":
    engine = LLMAnalysisEngine()

    mock_pubmed = [
        {"pmid": "42526372", "title": "Corylifol A alleviates lipopolysaccharide-induced inflammation"}
    ]
    mock_patents = [
        {"patent_id": "EP3892258A1", "title": "Topical compositions comprising Psoralea corylifolia extract", "assignee": "Derma Innovations Ltd"}
    ]
    mock_assessment = {
        "tracao_cientifica": "4.0/10",
        "tracao_industrial": "4.0/10",
        "risco_oferta": "BAIXO RISCO",
        "confianca_sinal": "ALTA"
    }
    mock_dossiers = {
        "PT-BR": {
            "regulatory_alerts": {"status": "APROVADO_USO_TOPICO", "restriction_level": "BAIXO", "max_concentration_allowed": "2.0%", "alerts": []},
            "commercial_signals": {"suppliers_count": 5, "trend": "CRESCENTE", "volume_usd_annual": 286_290}
        },
        "PT-PT": {
            "regulatory_alerts": {"status": "APROVADO_USO_TOPICO", "restriction_level": "BAIXO", "max_concentration_allowed": "2.0%", "alerts": []},
            "commercial_signals": {"suppliers_count": 11, "trend": "ESTAVEL", "volume_usd_annual": 1_970_648}
        }
    }

    print("--- Teste de Integração LLM (Claude Sonnet 5) ---")
    resumo = engine.summarize_evidence("Bakuchiol", mock_pubmed, mock_patents)
    print(f"\nResumo de Evidências:\n{resumo}")

    # Mesma métrica global (assessment), dossiê regional diferente - prova que a
    # análise diverge de fato entre mercados, não apenas troca o nome do órgão.
    # pmids/patent_ids reais da mock_pubmed/mock_patents acima - allow-list para
    # validação de evidence_ids (JSON estruturado claim/evidence_ids/limitations).
    for lang in ("PT-BR", "PT-PT"):
        dossier = mock_dossiers[lang]
        recs = engine.generate_recommendations(
            "Bakuchiol", mock_assessment,
            regulatory_alerts=dossier["regulatory_alerts"],
            commercial_signals=dossier["commercial_signals"],
            lang=lang,
            pmids=["42526372"],
            patent_ids=["EP3892258A1"],
            high_confidence=True
        )
        print(f"\n=== {lang} (high_confidence=True) ===")
        print(f"Recomendação Inovação & P&D:\n{recs['inovacao_pd']}")
        print(f"\nRecomendação Compras & Procurement:\n{recs['compras_procurement']}")

    # Teste do gate de baixa confiança (Nível 1/evidência insuficiente) - o
    # 'claim' deve vir deliberadamente conservador e 'limitations' deve
    # declarar a limitação de evidência.
    recs_baixa_confianca = engine.generate_recommendations(
        "Bakuchiol", mock_assessment,
        regulatory_alerts=mock_dossiers["PT-BR"]["regulatory_alerts"],
        commercial_signals=mock_dossiers["PT-BR"]["commercial_signals"],
        lang="PT-BR",
        pmids=["42526372"],
        patent_ids=["EP3892258A1"],
        high_confidence=False
    )
    print("\n=== PT-BR (high_confidence=False) ===")
    print(f"Recomendação Inovação & P&D:\n{recs_baixa_confianca['inovacao_pd']}")
    print(f"\nRecomendação Compras & Procurement:\n{recs_baixa_confianca['compras_procurement']}")

    # Teste da trava determinística pós-LLM (is_insufficient_data=True) - a
    # chamada à LLM deve ser pulada inteiramente, retornando o texto fixo de
    # suspensão nos dois campos, sem nenhuma recomendação executiva.
    for lang in ("PT-BR", "ES"):
        recs_insuficiente = engine.generate_recommendations(
            "Bakuchiol", mock_assessment, lang=lang, is_insufficient_data=True
        )
        print(f"\n=== {lang} (is_insufficient_data=True - trava pós-LLM, sem chamada à API) ===")
        print(f"Recomendação Inovação & P&D:\n{recs_insuficiente['inovacao_pd']}")
        print(f"\nRecomendação Compras & Procurement:\n{recs_insuficiente['compras_procurement']}")
