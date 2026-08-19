"""
Teste de regressão dedicado ao PMID 42596530 (Bakuchiol / AT-001).

CONTEXTO: na primeira auditoria do relatório (schema v2.0.0), esse PMID foi
citado como evidência para Bakuchiol e identificado externamente como
fabricado/fora de contexto - o artigo real por trás desse PMID
("Isopsoralen Promotes Mandibular Fracture Healing by Regulating
Autophagy") existe no NCBI, mas não tem relação alguma com Bakuchiol ou
com aplicação dermocosmética/tópica. Este teste faz uma chamada AO VIVO à
E-utilities do NCBI (connectors/pubmed_validator.py) e falha explicitamente
se esse PMID específico voltar a ser aceito como evidência válida para
Bakuchiol - protegendo contra qualquer alteração futura no pipeline
(ex.: afrouxar require_topical_context, mudar TOPICAL_CONTEXT_TERMS, ou
remover a chamada ao validador) que reintroduza esse caso sem ser pega
automaticamente.

Requer rede (chamada real à eutils.ncbi.nlm.nih.gov) - não usa mocks,
propositalmente: o objetivo é confirmar o comportamento contra o dado
real do NCBI, não contra uma simulação que poderia mascarar uma regressão.
"""
import pytest

from core.entity_resolver import EntityResolver
from connectors.pubmed import PubMedConnector
from connectors.pubmed_validator import PMIDValidator

BAKUCHIOL_ASSET_ID = "AT-001"
FABRICATED_PMID = "42596530"


@pytest.fixture
def validator():
    resolver = EntityResolver(taxonomy_path="data/taxonomy/ativos_mvp.json")
    pubmed_conn = PubMedConnector(resolver=resolver)
    return PMIDValidator(pubmed_conn), resolver


def test_bakuchiol_fabricated_pmid_is_rejected(validator):
    """PMID 42596530 deve continuar sendo REJEITADO como evidência para Bakuchiol."""
    pmid_validator, resolver = validator
    asset = next(a for a in resolver.assets if a["asset_id"] == BAKUCHIOL_ASSET_ID)

    result = pmid_validator.validate(FABRICATED_PMID, asset["canonical_name"])

    assert result["exists"] is True, (
        f"PMID {FABRICATED_PMID} deveria continuar existindo no NCBI (artigo real, "
        f"só não relacionado a Bakuchiol) - se isto falhar, o registro pode ter sido "
        f"retirado do PubMed; revisar o teste."
    )
    assert result["entity_confirmed"] is False, (
        f"PMID {FABRICATED_PMID} passou a confirmar a entidade 'Bakuchiol' no título/resumo - "
        f"verificar se o registro do NCBI mudou ou se a lógica de Entity Resolution "
        f"(core/entity_resolver.py) foi afrouxada de forma que reintroduz este falso positivo."
    )
    assert result["valid"] is False, (
        f"REGRESSÃO: PMID {FABRICATED_PMID} foi aceito como evidência válida para Bakuchiol. "
        f"Este é o PMID fabricado/fora de contexto identificado na primeira auditoria do "
        f"relatório - ele NUNCA deve ser citado como evidência real para este ativo."
    )


def test_bakuchiol_has_no_valid_pmid_evidence_today(validator):
    """
    Confirma que, na ausência de literatura real e específica para Bakuchiol
    (Nível >= 2 de relevância tópica), o pipeline não força nenhum PMID como
    substituto - o ativo deve cair para 'dados insuficientes', nunca citar
    um PMID incorreto só para preencher a evidência.
    """
    pmid_validator, resolver = validator
    from main import resolve_search_query

    asset = next(a for a in resolver.assets if a["asset_id"] == BAKUCHIOL_ASSET_ID)
    search_query = resolve_search_query(asset)
    search = pmid_validator.pubmed_connector.search_articles(
        search_query, exclusions=asset.get("exclusions", []), max_results=5
    )

    valid_pmids, rejected = pmid_validator.validate_batch(search["pmids"], asset["canonical_name"])

    assert FABRICATED_PMID not in valid_pmids, (
        f"REGRESSÃO: PMID {FABRICATED_PMID} está na lista de PMIDs válidos retornados "
        f"pelo pipeline para Bakuchiol."
    )
