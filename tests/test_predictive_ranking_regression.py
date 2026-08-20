"""
Testes de regressão para core/predictive_ranking.py PredictiveRankingEngine.select_predictive_assets() -
cobrem especificamente o achado de auditoria de 2026-08-19: um passo de
"preenchimento" completava a seleção até exatamente 8 linhas com os
"próximos melhores por sinal combinado", mesmo sem nenhum candidato
genuíno - e rotulava esse preenchimento como "Emerging Stars" (ver
METHODOLOGY.md, seção "Preenchimento fabricado removido"). Auditoria de
relatórios já gerados confirmou que isso já tinha contaminado 5 das 8
linhas "Estrela Emergente" de um relatório real (PT-BR) e 3 das 4 de outro
(PT-PT) - nenhuma delas com Tração Científica + Industrial suficiente para
justificar a categoria.

Também cobre um segundo achado, do mesmo dia, ao investigar o efeito da
correção acima: um ativo com sinal de risco real (regulatório OU comercial)
que não coubesse no teto de high_risk_count (3 por padrão) continuava
elegível às categorias de oportunidade (Estrela Emergente/Dark Horse) nos
passos seguintes de select_predictive_assets(), porque o filtro desses
passos só excluía quem já estava em `selected_ids` (ou seja, quem COUBE no
teto), nunca quem tinha o sinal de risco em si. classify_precedence_tier()
nunca teve esse problema (risco sempre precede oportunidade, sem exceção -
não usa teto nenhum); o defeito era específico da função que decide o badge
exibido no PDF.

Este arquivo trava três comportamentos, para que nenhuma das duas
regressões volte:
1. A lista retornada NUNCA é preenchida até um total fixo - pode ter menos
   de 8 (inclusive 0) ativos.
2. TODA linha retornada tem evidência mínima verificada e o piso mínimo em
   cada componente (Tração Científica/Industrial) que sua categoria exige -
   nunca uma linha "emprestada" de outra categoria só para completar número.
3. Um ativo com sinal de risco real NUNCA é elegível a "Estrela Emergente"/
   "Dark Horse", mesmo que não caiba no teto de high_risk_count.
"""
from core.predictive_ranking import (
    PredictiveRankingEngine,
    CATEGORY_EMERGING_STARS,
    CATEGORY_HIGH_RISK,
    CATEGORY_DARK_HORSES,
    MIN_SCI_FOR_EMERGING_STAR,
    MIN_IND_FOR_EMERGING_STAR,
    _SUPPLY_RISK_LEVELS,
)
from core.score_engine import MIN_VERIFIED_EVIDENCE


def _base_evaluation(asset_id, sci, ind, evidencias_verificadas, alerta_regulatorio="SEM ALERTA", sinal_comercial_comex="OFERTA SAUDÁVEL", risco_oferta="BAIXO RISCO"):
    return {
        "asset_id": asset_id,
        "canonical_name": f"Ativo {asset_id}",
        "tracao_cientifica": f"{sci}/10",
        "tracao_industrial": f"{ind}/10",
        "risco_oferta": risco_oferta,
        "alerta_regulatorio": alerta_regulatorio,
        "sinal_comercial_comex": sinal_comercial_comex,
        "evidencias_verificadas": evidencias_verificadas,
    }


def test_select_predictive_assets_never_pads_below_genuine_qualifiers():
    """
    Catálogo com só 1 ativo genuinamente qualificado (Estrela Emergente) e
    nenhum outro candidato de nenhuma categoria - a seleção deve retornar
    EXATAMENTE 1, nunca preenchida até 8 com os "próximos melhores".
    Reproduz literalmente a condição que, antes da correção de 2026-08-19,
    disparava o passo de preenchimento.
    """
    engine = PredictiveRankingEngine()
    evaluations = [
        _base_evaluation("AT-901", sci=6.0, ind=6.0, evidencias_verificadas=3),  # único genuíno
    ] + [
        # 10 ativos SEM evidência suficiente e SEM sinal de risco - exatamente
        # o tipo de linha que o preenchimento antigo varria para completar 8.
        _base_evaluation(f"AT-9{i:02d}", sci=0.0, ind=0.0, evidencias_verificadas=0)
        for i in range(10, 20)
    ]

    selected = engine.select_predictive_assets(evaluations)

    assert len(selected) == 1
    assert selected[0]["asset_id"] == "AT-901"
    assert selected[0]["predictive_category"] == CATEGORY_EMERGING_STARS


def test_select_predictive_assets_returns_empty_list_when_nothing_qualifies():
    """Catálogo inteiro sem nenhum candidato genuíno em nenhuma categoria -> lista vazia, nunca preenchida com o 'melhor entre os ruins'."""
    engine = PredictiveRankingEngine()
    evaluations = [
        _base_evaluation(f"AT-9{i:02d}", sci=0.0, ind=0.0, evidencias_verificadas=0)
        for i in range(1, 15)
    ]

    selected = engine.select_predictive_assets(evaluations)

    assert selected == []


def test_zero_science_high_industrial_never_selected_as_emerging_star():
    """
    Regressão do caso real (Cúrcuma/AT-015, Bidens Pilosa/AT-005, Semente de
    Uva/AT-010, Romã/AT-011 - auditoria de 2026-08-19): Tração Científica
    ZERADA com Tração Industrial saturada nunca deve qualificar como
    Estrela Emergente, mesmo sendo tecnicamente "o melhor sinal combinado"
    disponível se for o único candidato restante.
    """
    engine = PredictiveRankingEngine()
    evaluations = [
        _base_evaluation("AT-015", sci=0.0, ind=10.0, evidencias_verificadas=5),  # perfil real da Cúrcuma
    ]

    selected = engine.select_predictive_assets(evaluations)

    assert selected == []
    assert PredictiveRankingEngine.classify_precedence_tier(evaluations[0]) != "Estrela Emergente"


def test_every_selected_asset_has_minimum_evidence_supporting_its_category():
    """
    Trava genérica: para QUALQUER seleção, cada linha retornada precisa ter
    evidência mínima verificada e o piso mínimo em cada componente exigido
    pela sua própria categoria - nunca uma linha "emprestada" de outra
    categoria ou de um passo de preenchimento para bater um total fixo.
    """
    engine = PredictiveRankingEngine()
    evaluations = [
        _base_evaluation("AT-801", sci=7.0, ind=8.0, evidencias_verificadas=4),  # Estrela Emergente genuína
        _base_evaluation("AT-802", sci=6.0, ind=0.0, evidencias_verificadas=2),  # Dark Horse genuína
        _base_evaluation("AT-803", sci=0.0, ind=9.0, evidencias_verificadas=5),  # perfil do bug real - não deve aparecer
        _base_evaluation("AT-804", sci=5.9, ind=4.9, evidencias_verificadas=3),  # abaixo do piso em ind - não deve aparecer
        _base_evaluation("AT-805", sci=8.0, ind=8.0, evidencias_verificadas=1),  # evidência insuficiente - não deve aparecer
        _base_evaluation("AT-806", sci=0.0, ind=0.0, evidencias_verificadas=0, alerta_regulatorio="ALERTA ALTO", risco_oferta="ALTO RISCO"),  # High-Risk genuína
    ]

    selected = engine.select_predictive_assets(evaluations)
    selected_ids = {s["asset_id"] for s in selected}

    # Os 3 genuínos aparecem; os 3 não-genuínos (perfil do bug real, abaixo do
    # piso, evidência insuficiente) NUNCA aparecem, mesmo que a lista fique
    # com só 3 linhas em vez de um total fixo maior.
    assert selected_ids == {"AT-801", "AT-802", "AT-806"}

    for row in selected:
        category = row["predictive_category"]
        sci = float(row["tracao_cientifica"].split("/")[0])
        ind = float(row["tracao_industrial"].split("/")[0])

        if category == CATEGORY_EMERGING_STARS:
            assert row["evidencias_verificadas"] >= MIN_VERIFIED_EVIDENCE
            assert sci >= MIN_SCI_FOR_EMERGING_STAR
            assert ind >= MIN_IND_FOR_EMERGING_STAR
        elif category == CATEGORY_DARK_HORSES:
            assert row["evidencias_verificadas"] >= MIN_VERIFIED_EVIDENCE
            assert sci > 0
            assert ind == 0
        elif category == CATEGORY_HIGH_RISK:
            # Risco Regulatório/Comercial deriva de uma fonte de evidência
            # independente (connectors.regulatory_comex) - não exige
            # evidencias_verificadas de PubMed/patentes, por design.
            assert row["alerta_regulatorio"] == "ALERTA ALTO" or row["sinal_comercial_comex"] in _SUPPLY_RISK_LEVELS
        else:
            raise AssertionError(f"Categoria inesperada retornada: {category!r}")


def test_regulatory_risk_asset_beyond_high_risk_cap_never_promoted_to_emerging_star():
    """
    Regressão do "bug do 4º ativo mal rotulado" (achado de auditoria de
    2026-08-19, ao investigar o efeito da correção do preenchimento): com
    high_risk_count=3 (padrão), 4 ativos com Risco Regulatório ALTO real -
    todos batendo também o piso de Estrela Emergente (sci/ind altos). Só 3
    cabem na categoria de risco (teto); o 4º NUNCA pode "vazar" para
    Estrela Emergente só porque sobrou vaga lá - deve simplesmente não
    aparecer em nenhuma categoria nesta edição.
    """
    engine = PredictiveRankingEngine()  # high_risk_count=3 (padrão)
    risk_assets = [
        _base_evaluation(f"AT-70{i}", sci=9.0 - i, ind=7.0, evidencias_verificadas=3,
                          alerta_regulatorio="ALERTA ALTO", risco_oferta="ALTO RISCO")
        for i in range(1, 5)  # AT-701 (sci=8.0) ... AT-704 (sci=5.0), todos >= piso de Emerging Star
    ]

    selected = engine.select_predictive_assets(risk_assets)
    selected_ids = {s["asset_id"] for s in selected}

    assert len(selected) == 3  # teto de high_risk_count - o 4º fica de fora
    assert all(s["predictive_category"] == CATEGORY_HIGH_RISK for s in selected)
    assert "AT-704" not in selected_ids  # o excluído do teto (menor sci) NUNCA aparece em nenhuma categoria
    # confirma que o tier real do excluído também não é Estrela Emergente -
    # mesma garantia que classify_precedence_tier() já dava, agora também
    # respeitada por select_predictive_assets().
    assert PredictiveRankingEngine.classify_precedence_tier(risk_assets[3]) == "Risco Regulatório"


def test_supply_risk_asset_beyond_high_risk_cap_never_promoted_to_dark_horse():
    """Mesma regressão do teste acima, mas para Risco de Oferta comercial (não regulatório) e categoria Dark Horse (não Emerging Star)."""
    engine = PredictiveRankingEngine()
    risk_assets = [
        _base_evaluation(f"AT-71{i}", sci=6.0, ind=0.0, evidencias_verificadas=2,
                          sinal_comercial_comex="OFERTA CRÍTICA", risco_oferta="ALTO RISCO")
        for i in range(1, 5)  # perfil de Dark Horse (sci>0, ind==0) SE o risco não fosse considerado
    ]

    selected = engine.select_predictive_assets(risk_assets)
    selected_ids = {s["asset_id"] for s in selected}

    assert len(selected) == 3
    assert all(s["predictive_category"] == CATEGORY_HIGH_RISK for s in selected)
    assert "AT-714" not in selected_ids
    assert PredictiveRankingEngine.classify_precedence_tier(risk_assets[3]) == "Risco de Oferta"
