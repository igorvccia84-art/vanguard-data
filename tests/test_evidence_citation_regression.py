"""
Teste de regressão: PMIDs/patentes REALMENTE usados no cálculo de T_c/T_i
(core/score_engine.py) precisam aparecer no relatório final - achado de
auditoria de 2026-08-20: Calendula (AT-017) tinha tracao_cientifica=5.0/10 e
tracao_industrial=5.6/10 calculados a partir de 2 PMIDs + 2 patentes
verificados (ver docs/calculation_trace_calendula_2026-08-19.md), mas o
relatório final dizia "nenhuma evidência disponível" - uma afirmação
incorreta, porque a evidência existe e foi verificada.

CAUSA RAIZ (investigada via git log/git show antes de corrigir): regressão
introduzida no commit 5723fe6 ("chore(backup): backup automatizado -
2026-08-18 00:19"). Até esse commit, uma ÚNICA busca ao PubMed/patentes
alimentava tanto o cálculo do score quanto a lista de PMIDs/patentes citada
no relatório - as duas nunca podiam divergir. Esse commit introduziu uma
segunda busca (janela histórica móvel de 12 meses, TRACTION_WINDOW_DAYS)
especificamente para recalcular Tração Científica/Industrial de forma mais
representativa - e trocou o INPUT do score para essa nova busca
(`pubmed_data=pubmed_traction_results`), mas não atualizou a lista citada no
relatório ('pmids'/'patent_ids' no dict de avaliação), que continuou vindo
só da busca de "novidade" de 15 dias original. As duas janelas passaram a
poder divergir livremente a partir daí: um ativo pode ter 0 PMIDs/patentes
publicados nos últimos 15 dias e ainda assim ter T_c/T_i calculados a partir
de evidência real dentro da janela de 12 meses - exatamente o caso de
Calendula. A regressão não foi um efeito colateral de nenhuma das correções
de i18n/high-risk desta sessão; é anterior a todas elas (commit 5723fe6,
bem antes de 3aed79e/8e66f3f/a617e6f/d4ae89b).

Corrigida em 2 pontos:
1. core/score_engine.py ScoreEngine.extract_verified_pmids()/
   extract_traction_patent_ids() - expõe exatamente os PMIDs/patentes que
   entram na fórmula (mesmo filtro usado internamente pela própria fórmula
   via _verified_pubmed_matches(), nunca reimplementado em separado, para
   citação e cálculo nunca mais divergirem silenciosamente).
2. main.py - a lista citada no relatório ('pmids'/'patent_ids' de cada
   avaliação) passa a ser a UNIÃO entre a novidade de 15 dias e a evidência
   real que efetivamente entrou na fórmula (ver comentário longo em
   main.py, "PubMed (Tração)").
3. reports/pdf_generator.py - PMIDs/patentes agora aparecem como link
   clicável (NCBI PubMed / Google Patents) nas evidence-tags do relatório.

Este arquivo trava essa consistência (dado interno vs. output final) em 3
camadas, para pegar essa classe de regressão antes de chegar no PDF:
- extração isolada bate com o que a própria fórmula conta como verificado;
- reprodução do cenário real de Calendula (evidência só na janela de
  tração, nenhuma novidade recente) confirma que o PDF final mostra essa
  evidência, com link clicável;
- contraparte negativa: um ativo genuinamente sem evidência em nenhuma
  janela continua sem nada citado (a correção não deve inventar evidência).
"""
from core.score_engine import ScoreEngine
from reports.pdf_generator import PDFReportGenerator


def test_extract_verified_pmids_matches_scientific_traction_formula():
    """
    extract_verified_pmids() precisa retornar exatamente os PMIDs dos
    matches que calculate_scientific_traction_breakdown() efetivamente usa
    (entity_match confirmado) - nunca uma lista maior (citaria PMIDs que não
    contam para o score) nem menor (esconderia evidência real que conta).
    """
    engine = ScoreEngine()
    pubmed_matches = [
        {"pmid": "111", "entity_match": {"confidence_score": 0.95, "relevance_level": 2}},
        {"pmid": "222", "entity_match": {"confidence_score": 0.95, "relevance_level": 3}},
        {"pmid": "333", "entity_match": None},  # não confirmado - não conta para T_c
        {"pmid": "444"},  # sem 'entity_match' - não conta para T_c
    ]
    verified_pmids = engine.extract_verified_pmids(pubmed_matches)
    assert verified_pmids == ["111", "222"]

    # O componente [Q] (qualidade média da correspondência) de
    # calculate_scientific_traction_breakdown() é calculado só sobre os
    # matches verificados - reconstruído aqui para confirmar que a extração
    # bate exatamente com o subconjunto que a fórmula usa internamente
    # (2 PMIDs de confidence_score=0.95 cada -> Q = 10*(0.95+0.95)/2 = 9.5).
    breakdown = engine.calculate_scientific_traction_breakdown(pubmed_matches)
    assert breakdown["components"]["Q"] == 9.5
    assert len(verified_pmids) == 2


def test_extract_traction_patent_ids_matches_industrial_traction_formula():
    """extract_traction_patent_ids() retorna exatamente os patent_ids que calculate_industrial_traction() conta (len(patent_matches))."""
    engine = ScoreEngine()
    patent_matches = [
        {"patent_id": "CN121648039A", "assignee": "UNIV HUBEI SCIENCE & TECHNOLOGY"},
        {"patent_id": "MX2024003541A", "assignee": "UNIV AUTONOMA DE NUEVO LEON"},
    ]
    ids = engine.extract_traction_patent_ids(patent_matches)
    assert ids == ["CN121648039A", "MX2024003541A"]
    assert len(ids) == len(patent_matches)

    t_i_two = engine.calculate_industrial_traction(patent_matches)
    t_i_one = engine.calculate_industrial_traction(patent_matches[:1])
    assert t_i_two > t_i_one > 0.0


def test_pdf_shows_evidence_that_drove_the_score_even_without_recent_novelty(tmp_path):
    """
    Reproduz o cenário real de Calendula (AT-017, 2026-08-19): T_c/T_i
    calculados a partir de evidência real (2 PMIDs + 2 patentes verificados
    na janela de tração de 12 meses), mas ZERO PMIDs/patentes na janela de
    novidade de 15 dias - o relatório final precisa mostrar a evidência REAL
    do cálculo mesmo quando a novidade recente está vazia, com link clicável.
    """
    engine = ScoreEngine()
    pubmed_traction_results = [
        {"pmid": "42586652", "entity_match": {"confidence_score": 0.95, "relevance_level": 2}},
        {"pmid": "42465172", "entity_match": {"confidence_score": 0.95, "relevance_level": 2}},
        {"pmid": "42587194", "entity_match": None},  # rejeitado na Entity Resolution - não conta
    ]
    patent_traction_results = [
        {"patent_id": "CN121648039A", "assignee": "UNIV HUBEI SCIENCE & TECHNOLOGY"},
        {"patent_id": "MX2024003541A", "assignee": "UNIV AUTONOMA DE NUEVO LEON"},
    ]

    # Mesma lógica de main.py: união entre novidade de 15 dias (vazia neste
    # cenário, como Calendula na execução real) e evidência real da janela
    # de tração que efetivamente entrou na fórmula.
    verified_pmids_15d, valid_patent_ids_15d = [], []
    cited_pmids = list(dict.fromkeys(verified_pmids_15d + engine.extract_verified_pmids(pubmed_traction_results)))
    cited_patent_ids = list(dict.fromkeys(valid_patent_ids_15d + engine.extract_traction_patent_ids(patent_traction_results)))

    assert cited_pmids == ["42586652", "42465172"]
    assert cited_patent_ids == ["CN121648039A", "MX2024003541A"]

    evaluations = [{
        "asset_id": "AT-017",
        "canonical_name": "Calendula",
        "predictive_category": "Emerging Stars",
        "scientific_traction": "5.0/10",
        "industrial_traction": "5.6/10",
        "supply_risk": "BAIXO RISCO",
        "confidence_level": "ALTA",
        "inovacao_pd": "Recomendação de teste com tamanho suficiente para não ser tratada como degenerada.",
        "compras_procurement": "Outra recomendação de teste com tamanho suficiente para não ser degenerada.",
        "pmids": cited_pmids,
        "patent_ids": cited_patent_ids,
    }]

    generator = PDFReportGenerator(output_dir=str(tmp_path))
    generator.generate_report(evaluations, lang="PT-BR")
    html = (tmp_path / "relatorio_vanguard_pt_br.html").read_text(encoding="utf-8")

    for pmid in cited_pmids:
        assert pmid in html, f"PMID {pmid!r} (usado no cálculo de T_c) não aparece no PDF final - regressão do achado de auditoria (Calendula)"
        assert f'href="https://pubmed.ncbi.nlm.nih.gov/{pmid}/"' in html, f"PMID {pmid!r} sem link clicável para o NCBI"
    for patent_id in cited_patent_ids:
        assert patent_id in html, f"Patente {patent_id!r} (usada no cálculo de T_i) não aparece no PDF final - regressão do achado de auditoria (Calendula)"
        assert f'href="https://patents.google.com/patent/{patent_id}/en"' in html, f"Patente {patent_id!r} sem link clicável para o Google Patents"


def test_no_evidence_ids_means_the_asset_genuinely_has_none():
    """
    Contraparte do teste acima: um ativo SEM nenhum PMID/patente verificado
    em nenhuma das duas janelas (novidade OU tração) continua sem nada
    citado - a correção soma evidência real que existia e estava escondida,
    não inventa evidência para ativos que genuinamente não têm nenhuma.
    """
    engine = ScoreEngine()
    pubmed_traction_results = [{"pmid": "999", "entity_match": None}]
    patent_traction_results = []

    cited_pmids = list(dict.fromkeys([] + engine.extract_verified_pmids(pubmed_traction_results)))
    cited_patent_ids = list(dict.fromkeys([] + engine.extract_traction_patent_ids(patent_traction_results)))

    assert cited_pmids == []
    assert cited_patent_ids == []
