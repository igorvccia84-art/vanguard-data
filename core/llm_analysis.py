import sys
import io
import json
import re
from typing import Dict, Any, List

import truststore
truststore.inject_into_ssl()  # usa o armazenamento de certificados do SO (necessário com AV/proxy que interceptam TLS, ex.: Avast)

from dotenv import load_dotenv
import anthropic

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
        "regulatory_framework": "Regulamento (CE) n.º 1223/2009 relativo aos produtos cosméticos (base CosIng/ECHA), com fiscalização nacional pelo INFARMED",
        "trade_data_label": "base de fornecimento europeia (CosIng/ECHA)"
    },
    "ES": {
        "region_code": "ES",
        "market_label": "España (mercado de la Unión Europea)",
        "regulatory_framework": "Reglamento (CE) n.º 1223/2009 sobre productos cosméticos (base CosIng/ECHA), con fiscalización nacional por la AEMPS",
        "trade_data_label": "base de suministro europea (CosIng/ECHA)"
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
(3) inventar aplicações, indicações de uso, benefícios, estudos, autores ou publicações que não constem explicitamente no contexto fornecido.
Toda afirmação factual da sua resposta deve ser rastreável a um trecho específico do contexto fornecido nesta mensagem. Se o contexto fornecido for insuficiente, incompleto ou ausente para sustentar uma afirmação, declare essa limitação explicitamente em vez de preencher a lacuna com suposições ou conhecimento geral."""

RECOMMENDATION_SCHEMA = {
    "type": "object",
    "properties": {
        "inovacao_pd": {"type": "string"},
        "compras_procurement": {"type": "string"}
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


def _default_supply_recommendation(risco_oferta: str, lang_key: str) -> str:
    table = _DEFAULT_SUPPLY_RECOMMENDATION.get(lang_key, _DEFAULT_SUPPLY_RECOMMENDATION["PT-BR"])
    return table.get(risco_oferta, table["MEDIO RISCO"])


def _default_innovation_recommendation(assessment: Dict[str, Any], lang_key: str) -> str:
    combined = _parse_score_value(assessment.get("tracao_cientifica")) + _parse_score_value(assessment.get("tracao_industrial"))
    tier = "alta" if combined >= 10 else "media" if combined >= 4 else "baixa"
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
        return _sanitize_text(raw_text)

    def generate_recommendations(
        self,
        canonical_name: str,
        assessment: Dict[str, Any],
        regulatory_alerts: Dict[str, Any] = None,
        commercial_signals: Dict[str, Any] = None,
        lang: str = "PT-BR"
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
        """
        lang_key = lang.upper()
        regulatory_body = REGULATORY_BODY.get(lang_key, REGULATORY_BODY["PT-BR"])
        region = REGION_CONTEXT.get(lang_key, REGION_CONTEXT["PT-BR"])
        regulatory_alerts = regulatory_alerts or {}
        commercial_signals = commercial_signals or {}
        alerts_text = "; ".join(regulatory_alerts.get("alerts") or []) or "nenhum alerta específico registrado"

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
Volume anual estimado (USD) nesta região: {commercial_signals.get('volume_usd_annual', 'N/A')}
Risco de Oferta consolidado (regulatório + comercial) para {region['market_label']}: {assessment.get('risco_oferta')}

INSTRUÇÕES OBRIGATÓRIAS:
- Gere duas recomendações curtas e acionáveis (2-3 frases cada), no idioma "{lang}".
- As recomendações devem refletir especificidades REAIS do marco regulatório e da dinâmica de suprimento de {region['market_label']} listados acima: cite o marco regulatório e/ou o alerta específico ao justificar a recomendação de Inovação & P&D, e cite os dados de fornecedores/tendência ao justificar a de Compras & Procurement.
- NÃO escreva uma análise genérica que sirva igualmente para outro mercado. O texto deve ser claramente distinguível do que seria escrito para outra região, mesmo quando as métricas globais forem idênticas.
- NUNCA responda com um placeholder genérico (ex.: "placeholder", "N/A", "TBD", "a definir"). Mesmo com evidências científicas limitadas, produza sempre uma frase real e específica baseada nos dados regulatórios/comerciais fornecidos acima.

1. Uma recomendação para o time de Inovação & P&D.
2. Uma recomendação para o time de Compras & Procurement.

Responda apenas com o conteúdo das duas recomendações. Não inclua comentários sobre o processo de geração, marcadores de conclusão (ex.: "fim", "concluído", "pronto") ou qualquer texto após a última recomendação — pare assim que o conteúdo estiver completo."""

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

            last_inovacao = _sanitize_text(parsed.get("inovacao_pd", ""))
            last_compras = _sanitize_text(parsed.get("compras_procurement", ""))

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
    for lang in ("PT-BR", "PT-PT"):
        dossier = mock_dossiers[lang]
        recs = engine.generate_recommendations(
            "Bakuchiol", mock_assessment,
            regulatory_alerts=dossier["regulatory_alerts"],
            commercial_signals=dossier["commercial_signals"],
            lang=lang
        )
        print(f"\n=== {lang} ===")
        print(f"Recomendação Inovação & P&D:\n{recs['inovacao_pd']}")
        print(f"\nRecomendação Compras & Procurement:\n{recs['compras_procurement']}")
