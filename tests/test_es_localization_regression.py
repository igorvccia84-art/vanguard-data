"""
Testes de regressão para vazamento de idioma no relatório ES - achado de
auditoria de 2026-08-20: 3 elementos apareciam em português no relatório
espanhol, apesar do resto do texto (gerado pela LLM) estar corretamente
traduzido - sinal de que vinham de fontes fixas que nunca passavam pelo
pipeline de localização:

1. "Limitações:" - rótulo fixo hardcoded em core/llm_analysis.py
   _compose_recommendation_text() (corrigido: LIMITATIONS_LABEL por idioma).
2. Disclaimer "REG: ..." - 'source' de connectors/regulatory_comex.py,
   armazenado só em PT-BR, exposto direto no PDF sem tradução (corrigido:
   localize_source()/SOURCE_TRANSLATIONS).
3. Nome canônico do ativo (ex.: "Alcaçuz" nunca virava "Regaliz") - campo
   fixo da taxonomia reaproveitado nos 3 idiomas (corrigido: campo
   canonical_name_es em data/taxonomy/ativos_mvp.json + main.py escolhendo
   por idioma).

Segunda rodada de auditoria (2026-08-20, mesmo dia): vazamento de
VOCABULÁRIO TÉCNICO em português dentro da PRÓPRIA PROSA gerada pela LLM
para o relatório ES (não mais um literal fixo do template) - ex.:
"rotulagem", "código alfandegário", "Sinal de Risco de Oferta" apareciam
sem tradução mesmo com o resto da frase corretamente em espanhol. Causa
raiz: (a) o prompt inteiro enviado à LLM (system=STRICT_GROUNDING_DIRECTIVE
e o prompt de generate_recommendations) era um único texto fixo em
português para os 3 idiomas - só os VALORES de dado eram trocados, nunca os
rótulos/instruções ao redor; e (b) valores injetados no prompt vindos de
REGULATORY_REGISTRY/EU_REGULATORY_OVERRIDES (status, nível de restrição,
alertas, concentração máxima) e de outras fontes (tendência comercial,
formatação de faixa de USD) continuavam em português cru mesmo para ES.
Corrigido com um prompt ES próprio e integral em core/llm_analysis.py, e
com localize_alerts()/localize_status()/localize_restriction_level()/
localize_trend()/localize_max_concentration() em connectors/regulatory_comex.py.

Este arquivo escaneia a saída REAL do gerador de PDF (reports/pdf_generator.py)
por um blocklist de strings conhecidas como português-only, usando dados
sintéticos realistas (sem chamada de rede/LLM) - trava contra esse tipo
específico de vazamento de idioma voltando a acontecer silenciosamente em
correções futuras. Os testes que envolvem prosa REAL gerada pela LLM (ver
test_generated_es_prose_has_no_portuguese_vocabulary_leak) fazem chamadas
reais à API Anthropic (ANTHROPIC_API_KEY via .env) - não são mockados,
porque o próprio bug só é observável na saída real do modelo.
"""
from core.entity_resolver import EntityResolver
from core.formatting import format_usd_estimate
from core.llm_analysis import LLMAnalysisEngine, STRICT_GROUNDING_DIRECTIVE
from connectors.regulatory_comex import RegulatoryComexConnector, localize_source, localize_alerts
from reports.pdf_generator import PDFReportGenerator

# Strings/fragmentos conhecidos como exclusivamente em português - nunca
# deveriam aparecer no relatório ES. Inclui os 3 achados reais da auditoria.
PT_ONLY_MARKERS = [
    "Limitações:",
    "Alcaçuz",
    "não vinculada a um dispositivo legal auditável",  # disclaimer REG em PT (achado real)
]


def test_limitations_label_is_localized_per_language():
    """_compose_recommendation_text usa 'Limitaciones:' em ES, nunca 'Limitações:' hardcoded."""
    engine = LLMAnalysisEngine()
    claim_block = {
        "claim": "Afirmação de teste com tamanho suficiente para não ser tratada como degenerada.",
        "evidence_ids": [],
        "limitations": "Evidência limitada disponível para sustentar esta afirmação de teste."
    }

    text_es = engine._compose_recommendation_text(claim_block, [], [], "Ativo Teste", "inovacao_pd", "ES")
    text_pt_br = engine._compose_recommendation_text(claim_block, [], [], "Ativo Teste", "inovacao_pd", "PT-BR")
    text_pt_pt = engine._compose_recommendation_text(claim_block, [], [], "Ativo Teste", "inovacao_pd", "PT-PT")

    assert "Limitaciones:" in text_es
    assert "Limitações:" not in text_es
    assert "Limitações:" in text_pt_br
    assert "Limitações:" in text_pt_pt


def test_all_regulatory_sources_have_real_es_translation():
    """Toda 'source' distinta das 3 bases regulatórias (REGULATORY_REGISTRY/EU_/FDA_) tem tradução ES real, não uma cópia do PT-BR nem o marcador de ausência."""
    all_sources = set()
    for registry in (
        RegulatoryComexConnector.REGULATORY_REGISTRY,
        RegulatoryComexConnector.EU_REGULATORY_OVERRIDES,
        RegulatoryComexConnector.FDA_REGULATORY_OVERRIDES,
    ):
        for entry in registry.values():
            all_sources.add(entry["source"])

    assert len(all_sources) > 0
    for source_pt in all_sources:
        translated = localize_source(source_pt, "ES")
        assert translated != source_pt, f"source sem tradução real para ES (idêntica ao PT-BR): {source_pt!r}"
        assert "[TRADUÇÃO ES AUSENTE]" not in translated, f"source sem tradução cadastrada: {source_pt!r}"


def test_taxonomy_has_es_override_for_the_three_audited_assets():
    """
    Trava a correção na ORIGEM (data/taxonomy/ativos_mvp.json canonical_name_es)
    que main.py usa para escolher o nome localizado por idioma - os 3 ativos
    com divergência PT/ES confirmada na auditoria de 2026-08-20 precisam ter
    canonical_name_es com o valor correto, não apenas "algum" valor.
    """
    resolver = EntityResolver(taxonomy_path="data/taxonomy/ativos_mvp.json")
    by_id = {a["asset_id"]: a for a in resolver.assets}

    assert by_id["AT-019"].get("canonical_name_es") == "Regaliz"  # Alcaçuz -> Regaliz
    assert by_id["AT-017"].get("canonical_name_es") == "Caléndula"  # Calendula -> Caléndula
    assert by_id["AT-033"].get("canonical_name_es") == "Ácido Tranexámico"  # Ácido Tranexâmico -> Ácido Tranexámico


def test_es_report_html_has_no_known_portuguese_leaks(tmp_path):
    """
    Gera um relatório ES completo via PDFReportGenerator.generate_report()
    (o mesmo método usado em produção por main.py) com dados sintéticos que
    cobrem os 3 pontos reais de vazamento da auditoria - nome canônico
    localizado, disclaimer REG traduzido e rótulo 'Limitações:'/'Limitaciones:'
    - usando a entrada REAL de Alcaçuz (AT-019) da base regulatória, e
    confirma que nenhuma string conhecida como português-only aparece na
    saída HTML final.
    """
    engine = LLMAnalysisEngine()
    claim_block = {"claim": "Recomendación de prueba.", "evidence_ids": [], "limitations": "Evidencia limitada de prueba."}
    inovacao_pd = engine._compose_recommendation_text(claim_block, [], [], "Regaliz", "inovacao_pd", "ES")

    # Entrada regulatória REAL de produção (Alcaçuz/AT-019, EU_REGULATORY_OVERRIDES)
    # - garante que o teste cobre o dado real, não um mock inventado à parte.
    real_entry = RegulatoryComexConnector.EU_REGULATORY_OVERRIDES["AT-019"]

    evaluations = [{
        "asset_id": "AT-019",
        "canonical_name": "Regaliz",  # nome ES correto (canonical_name_es da taxonomia) - NUNCA "Alcaçuz"
        "predictive_category": "High-Risk / Supply Alert",
        "scientific_traction": "0.0/10",
        "industrial_traction": "3.6/10",
        "supply_risk": "ALTO RISCO",
        "confidence_level": "ALTA",
        "regulatory_source": real_entry["source"],
        "regulatory_last_verified": real_entry["last_verified"],
        "inovacao_pd": inovacao_pd,
        "compras_procurement": "Recomendación de compras de prueba.",
        "pmids": [],
        "patent_ids": []
    }]

    generator = PDFReportGenerator(output_dir=str(tmp_path))
    generator.generate_report(evaluations, lang="ES")

    html_path = tmp_path / "relatorio_vanguard_es.html"
    assert html_path.exists()
    html = html_path.read_text(encoding="utf-8")

    for marker in PT_ONLY_MARKERS:
        assert marker not in html, f"vazamento de português encontrado no relatório ES: {marker!r}"

    # Confirma positivamente que as versões ES corretas ESTÃO presentes -
    # não basta a ausência do português, a tradução real precisa aparecer.
    assert "Regaliz" in html
    assert "Limitaciones:" in html
    assert "(España)" in html


def test_strict_grounding_directive_has_real_es_translation():
    """
    STRICT_GROUNDING_DIRECTIVE (core/llm_analysis.py) é um dict por idioma,
    não mais um único texto fixo em português enviado como system prompt
    para toda chamada à LLM - achado de auditoria: a versão PT-BR continha
    inclusive uma frase-exemplo entre aspas ("o sinal observado indica risco
    elevado de oferta") que a LLM chegava a citar quase literalmente na
    prosa ES. PT-PT reaproveita o texto de PT-BR (idioma idêntico); ES
    precisa de uma tradução própria real, não uma cópia.
    """
    assert set(STRICT_GROUNDING_DIRECTIVE.keys()) >= {"PT-BR", "PT-PT", "ES"}
    assert STRICT_GROUNDING_DIRECTIVE["PT-PT"] == STRICT_GROUNDING_DIRECTIVE["PT-BR"]
    assert STRICT_GROUNDING_DIRECTIVE["ES"] != STRICT_GROUNDING_DIRECTIVE["PT-BR"]
    for marker in PT_ONLY_MARKERS + ["sinal observado", "risco elevado"]:
        assert marker not in STRICT_GROUNDING_DIRECTIVE["ES"], (
            f"diretiva ES contém texto em português ({marker!r}) - deveria ser uma tradução própria"
        )


def test_all_regulatory_alerts_have_real_es_translation():
    """
    Toda 'alerts' distinta de REGULATORY_REGISTRY/EU_REGULATORY_OVERRIDES
    (as 2 bases cujos alertas realmente chegam ao prompt de
    generate_recommendations via connectors.regulatory_comex.fetch_regulatory_status)
    tem tradução ES real registrada em ALERT_TRANSLATIONS, não uma cópia do
    PT-BR nem o marcador de ausência - mesmo padrão de
    test_all_regulatory_sources_have_real_es_translation, agora para o
    campo que alimenta a prosa da LLM em vez do disclaimer REG do PDF.
    """
    all_alerts = set()
    for registry in (
        RegulatoryComexConnector.REGULATORY_REGISTRY,
        RegulatoryComexConnector.EU_REGULATORY_OVERRIDES,
    ):
        for entry in registry.values():
            all_alerts.update(entry.get("alerts") or [])

    assert len(all_alerts) > 0
    for alert_pt in all_alerts:
        translated = localize_alerts([alert_pt], "ES")[0]
        assert translated != alert_pt, f"alerta sem tradução real para ES (idêntico ao PT-BR): {alert_pt!r}"
        assert "[TRADUÇÃO ES AUSENTE]" not in translated, f"alerta sem tradução cadastrada: {alert_pt!r}"
    # localize_alerts precisa preservar o comportamento original (passthrough)
    # para PT-BR/PT-PT - só ES é afetado pela correção.
    assert localize_alerts(list(all_alerts), "PT-BR") == list(all_alerts)


def test_format_usd_estimate_uses_spanish_conjunction():
    """
    format_usd_estimate() (core/formatting.py) usa 'y' (não 'e' português)
    como conjunção da faixa em espanhol - achado de auditoria: o valor
    formatado é injetado direto no prompt da LLM
    (core/llm_analysis.py generate_recommendations), e o "e" literal
    vazava para a prosa ES (ex.: "Entre USD 1,0 M e USD 1,3 M"). PT-BR/PT-PT
    continuam com 'e' (comportamento inalterado).
    """
    es_estimate = format_usd_estimate(1_970_648, lang="ES")
    assert " y " in es_estimate
    assert " e " not in es_estimate

    pt_estimate = format_usd_estimate(1_970_648, lang="PT-BR")
    assert " e " in pt_estimate


def test_generated_es_prose_has_no_portuguese_vocabulary_leak():
    """
    Varredura de vocabulário técnico do domínio em português sobre a PROSA
    REAL gerada pela LLM (não dados sintéticos/mockados) para o relatório
    ES - complementa test_es_report_html_has_no_known_portuguese_leaks
    (que cobre só os 3 literais fixos já identificados) cobrindo a classe
    mais ampla de vazamento: vocabulário que a LLM copia/adapta do próprio
    contexto injetado no prompt, não um literal do template.

    Duas chamadas reais à API Anthropic, cobrindo os dois vetores de
    vazamento reais da auditoria: (1) AT-019/Regaliz com o alerta regulatório
    real da UE (EU_REGULATORY_OVERRIDES, o texto que continha "rotulagem"/
    "controlado via" antes da correção) e Risco de Oferta ALTO; (2) um
    ativo sem alerta regulatório, mas com tendência comercial CRESCENTE
    (para cobrir o vetor de vazamento do valor 'trend').

    O blocklist de vocabulário abaixo NÃO é exaustivo de todo o português
    (isso pegaria falsos positivos com palavras válidas em ambos os
    idiomas, ex.: "alerta"/"oferta"/"sigilo") - é (a) vocabulário técnico
    do domínio deste relatório com grafia que nunca ocorre em espanhol
    padrão, e (b) caracteres que nunca ocorrem na ortografia espanhola
    padrão (til nasal ã/õ, cedilha ç, circunflexo ê), que sinalizam
    vazamento independente de qualquer palavra específica.
    """
    DOMAIN_PT_VOCAB_MARKERS = [
        "rotulagem", "alfandegári", "gargalo", "tração", "tracção",
        "fornecedor", "suprimento", "dossiê", "nível",
    ]
    PT_ONLY_CHARS = ["ã", "õ", "ç", "ê"]

    def _scan(text: str) -> list:
        lowered = text.lower()
        found = [f"vocabulário PT: {m!r}" for m in DOMAIN_PT_VOCAB_MARKERS if m in lowered]
        found += [f"caractere exclusivo do português: {c!r}" for c in PT_ONLY_CHARS if c in lowered]
        return found

    engine = LLMAnalysisEngine()

    eu_at019 = RegulatoryComexConnector.EU_REGULATORY_OVERRIDES["AT-019"]
    scenario_high_risk = engine.generate_recommendations(
        "Regaliz",
        {"tracao_cientifica": "4.4/10", "tracao_industrial": "0.0/10", "risco_oferta": "ALTO RISCO", "confianca_sinal": "MEDIA"},
        regulatory_alerts=eu_at019,
        commercial_signals={"suppliers_count": 11, "trend": "CRESCENTE", "volume_usd_annual": 1_970_648},
        lang="ES",
        pmids=["41820934"],
        patent_ids=[],
        high_confidence=True,
    )

    scenario_emerging = engine.generate_recommendations(
        "Bakuchiol",
        {"tracao_cientifica": "6.3/10", "tracao_industrial": "6.0/10", "risco_oferta": "BAIXO RISCO", "confianca_sinal": "ALTA"},
        regulatory_alerts={"status": "APROVADO_USO_TOPICO", "restriction_level": "BAIXO", "max_concentration_allowed": "2.0%", "alerts": []},
        commercial_signals={"suppliers_count": 5, "trend": "CRESCENTE", "volume_usd_annual": 286_290},
        lang="ES",
        pmids=["42596530"],
        patent_ids=["EP3892258A1"],
        high_confidence=True,
    )

    for scenario_name, recs in (("high_risk/Regaliz", scenario_high_risk), ("emerging/Bakuchiol", scenario_emerging)):
        for field, text in recs.items():
            leaks = _scan(text)
            assert not leaks, (
                f"vazamento de vocabulário português na prosa ES gerada pela LLM "
                f"(cenário={scenario_name}, campo={field}): {leaks}\nTexto completo: {text!r}"
            )
