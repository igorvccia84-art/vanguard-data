"""
Testes da lógica de cálculo de scores (core/score_engine.py) - cobrem
especificamente a regra de que AUSÊNCIA TOTAL de evidência real deve
produzir 0.0/10, nunca um valor intermediário "de segurança" ou
independente do dado de entrada. Isso importa porque o componente [G]
(Crescimento) de calculate_scientific_traction_breakdown usa 5.0 como
valor NEUTRO quando não há linha de base para comparar - sem a checagem
explícita de verified_count==0 no início da função, um ativo sem nenhuma
evidência receberia erroneamente T_c = w_G*5.0 = 1.75/10 em vez de 0.0/10.
"""
from core.score_engine import ScoreEngine


def test_scientific_traction_zero_when_no_verified_pubmed_matches():
    """Nenhum match de PubMed com entity_match confirmado -> T_c deve ser exatamente 0.0, não o neutro do componente [G]."""
    engine = ScoreEngine()
    breakdown = engine.calculate_scientific_traction_breakdown(pubmed_matches=[], baseline_36m_count=None)

    assert breakdown["score"] == 0.0
    # Trava contra regressão: mesmo com baseline_36m_count preenchido (o que ativaria
    # o componente [G] normalmente), a ausência de matches verificados ainda deve zerar.
    breakdown_with_baseline = engine.calculate_scientific_traction_breakdown(pubmed_matches=[], baseline_36m_count=50)
    assert breakdown_with_baseline["score"] == 0.0


def test_industrial_traction_zero_when_no_patent_matches():
    """Nenhuma patente -> T_i deve ser exatamente 0.0."""
    engine = ScoreEngine()
    assert engine.calculate_industrial_traction(patent_matches=[]) == 0.0


def test_generate_assessment_zero_evidence_end_to_end():
    """
    Chamada completa de generate_assessment() (a mesma função usada em produção,
    main.py) sem nenhuma evidência real de PubMed/patentes - confirma que os
    dois scores finais expostos no relatório vêm 0.0/10, e não um valor
    'hardcoded para não deixar em branco'.
    """
    engine = ScoreEngine()
    assessment = engine.generate_assessment(
        pubmed_data=[], patent_data=[],
        regulatory_alerts={}, commercial_signals={},
        query_hashes=[], baseline_36m_count=0
    )

    assert assessment["tracao_cientifica"] == "0.0/10"
    assert assessment["tracao_industrial"] == "0.0/10"
    assert assessment["evidencias_verificadas"] == 0
    assert assessment["nivel_evidencia_maximo"] == 0
    assert assessment["confianca_sinal"] == "BAIXA"


def test_scientific_traction_nonzero_when_evidence_present():
    """Contraste: com pelo menos um match verificado e de bom relevance_level, o score deve sair do piso 0.0."""
    engine = ScoreEngine()
    mock_matches = [
        {"entity_match": {"confidence_score": 0.95, "relevance_level": 3}},
        {"entity_match": {"confidence_score": 0.90, "relevance_level": 2}},
    ]
    breakdown = engine.calculate_scientific_traction_breakdown(mock_matches, baseline_36m_count=5)
    assert breakdown["score"] > 0.0
