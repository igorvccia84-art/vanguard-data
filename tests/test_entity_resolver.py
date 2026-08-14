import pytest

from core.entity_resolver import EntityResolver


@pytest.fixture
def resolver():
    """Instância do motor de resolução apontando para o arquivo de ativos do MVP."""
    return EntityResolver(taxonomy_path="data/taxonomy/ativos_mvp.json")


def test_resolve_by_botanical_name(resolver):
    """Testa se o motor resolve corretamente através do nome botânico."""
    text = "Study on the effects of Psoralea corylifolia on collagen."
    result = resolver.resolve(text)

    assert result is not None
    assert result["asset_id"] == "AT-001"
    assert result["canonical_name"] == "Bakuchiol"
    assert result["match_type"] == "BOTANICAL_NAME"


def test_resolve_by_cas_number(resolver):
    """Testa se o motor resolve com 100% de confiança pelo número CAS."""
    text = "Chemical compound CAS 10309-37-2 purity report."
    result = resolver.resolve(text)

    assert result is not None
    assert result["asset_id"] == "AT-001"
    assert result["confidence_score"] == 1.0


def test_resolve_unmapped_asset(resolver):
    """Testa se o motor retorna None para um ativo que não existe no dicionário."""
    text = "Analysis of water and vitamin C solution."
    result = resolver.resolve(text)

    assert result is None
