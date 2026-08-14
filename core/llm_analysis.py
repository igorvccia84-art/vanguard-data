import sys
import io
import json
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

RECOMMENDATION_SCHEMA = {
    "type": "object",
    "properties": {
        "inovacao_pd": {"type": "string"},
        "compras_procurement": {"type": "string"}
    },
    "required": ["inovacao_pd", "compras_procurement"],
    "additionalProperties": False
}


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
                max_tokens=500,
                messages=[{"role": "user", "content": prompt}]
            )
        except anthropic.APIStatusError as e:
            return f"[Falha ao sintetizar evidências via LLM: {e.status_code} - {e.message}]"

        return next((b.text for b in response.content if b.type == "text"), "").strip()

    def generate_recommendations(
        self,
        canonical_name: str,
        assessment: Dict[str, Any],
        lang: str = "PT-BR"
    ) -> Dict[str, str]:
        """
        Gera recomendações dinâmicas de Inovação & P&D e Compras & Procurement,
        baseadas nas métricas reais (Tração Científica, Tração Industrial, Risco de
        Oferta) e no órgão regulatório correspondente ao idioma do relatório.
        """
        regulatory_body = REGULATORY_BODY.get(lang.upper(), REGULATORY_BODY["PT-BR"])

        prompt = f"""Você é um consultor estratégico de P&D e Procurement para a indústria dermocosmética.

Ativo: {canonical_name}
Tração Científica: {assessment.get('tracao_cientifica')}
Tração Industrial: {assessment.get('tracao_industrial')}
Risco de Oferta: {assessment.get('risco_oferta')}
Confiança do Sinal: {assessment.get('confianca_sinal')}
Órgão regulatório de referência para este relatório: {regulatory_body}

Com base nessas métricas, gere duas recomendações curtas e acionáveis (2-3 frases cada), no idioma "{lang}", considerando o contexto regulatório de {regulatory_body}:

1. Uma recomendação para o time de Inovação & P&D.
2. Uma recomendação para o time de Compras & Procurement."""

        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=1024,
                output_config={"format": {"type": "json_schema", "schema": RECOMMENDATION_SCHEMA}},
                messages=[{"role": "user", "content": prompt}]
            )
        except anthropic.APIStatusError as e:
            error_msg = f"[Falha ao gerar recomendação via LLM: {e.status_code} - {e.message}]"
            return {"inovacao_pd": error_msg, "compras_procurement": error_msg}

        raw_text = next(b.text for b in response.content if b.type == "text")

        if response.stop_reason == "max_tokens":
            error_msg = "[Resposta do LLM truncada por limite de tokens - recomendação indisponível]"
            return {"inovacao_pd": error_msg, "compras_procurement": error_msg}

        try:
            return json.loads(raw_text)
        except json.JSONDecodeError:
            error_msg = "[Falha ao interpretar resposta estruturada do LLM]"
            return {"inovacao_pd": error_msg, "compras_procurement": error_msg}


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

    print("--- Teste de Integração LLM (Claude Sonnet 5) ---")
    resumo = engine.summarize_evidence("Bakuchiol", mock_pubmed, mock_patents)
    print(f"\nResumo de Evidências:\n{resumo}")

    recs = engine.generate_recommendations("Bakuchiol", mock_assessment, lang="PT-BR")
    print(f"\nRecomendação Inovação & P&D:\n{recs['inovacao_pd']}")
    print(f"\nRecomendação Compras & Procurement:\n{recs['compras_procurement']}")
