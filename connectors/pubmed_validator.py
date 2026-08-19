import sys
import io
from typing import Any, Dict, List, Optional, Tuple

from connectors.pubmed import PubMedConnector

if sys.stdout.encoding != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')


class PMIDValidator:
    """
    Rotina de checagem OBRIGATÓRIA pré-relatório para qualquer PMID citado
    pelo pipeline ou pela LLM (core/llm_analysis.py). Reexecuta a consulta
    real na E-utilities API do NCBI (eutils.ncbi.nlm.nih.gov, via efetch -
    conectada por composição a connectors.pubmed.PubMedConnector, que já
    aplica o rate limit/retry do NCBI) e só considera um PMID válido quando
    AMBAS as condições são confirmadas programaticamente:

      (a) o PMID existe de fato no NCBI - o efetch retornou um título real
          para o registro;
      (b) o título ou o resumo (abstract) do artigo contém a entidade do
          ativo pesquisado (Entity Resolution de core/entity_resolver.py,
          exigindo contexto tópico/aplicado - Nível >= 2, ver
          require_topical_context em PubMedConnector.fetch_article_details).

    SE A VALIDAÇÃO FALHAR (PMID inexistente OU entidade não confirmada no
    título/resumo): o PMID é estritamente rejeitado - nenhum PMID
    inventado/plausível chega ao relatório. O chamador (main.py) deve
    descartar todo PMID rejeitado antes de compor a lista final exibida no
    PDF; se um ativo não tiver nenhuma citação válida remanescente, o claim
    correspondente deve cair para "DADOS INSUFICIENTES" (ver
    core/llm_analysis.py, is_insufficient_data).
    """

    def __init__(self, pubmed_connector: PubMedConnector):
        self.pubmed_connector = pubmed_connector

    def validate(self, pmid: str, asset_canonical_name: str) -> Dict[str, Any]:
        """
        Valida um único PMID contra o NCBI. Retorna um registro auditável
        completo (pmid, valid, exists, entity_confirmed, reason, title) -
        nunca lança exceção (falha de rede/parse já é tratada como
        exists=False/entity_confirmed=False por PubMedConnector.fetch_article_details,
        e portanto valid=False - falha fecha para rejeição, não para aceitação).
        """
        details = self.pubmed_connector.fetch_article_details(pmid)

        exists = bool(details.get("title"))
        entity_confirmed = details.get("entity_match") is not None
        valid = bool(details.get("verified"))

        if not exists:
            reason = f"PMID {pmid} não retornou título via efetch no NCBI (registro inexistente ou inacessível)"
        elif not entity_confirmed:
            reason = f"Título/resumo do PMID {pmid} não confirma a entidade '{asset_canonical_name}' em contexto tópico/aplicado (Nível >= 2)"
        else:
            reason = None

        return {
            "pmid": pmid,
            "valid": valid,
            "exists": exists,
            "entity_confirmed": entity_confirmed,
            "reason": reason,
            "title": details.get("title", "")
        }

    def validate_batch(self, pmids: List[str], asset_canonical_name: str) -> Tuple[List[str], List[Dict[str, Any]]]:
        """
        Valida uma lista de PMIDs (ex.: citados pela LLM em evidence_ids ou
        coletados pelo pipeline). Retorna (pmids_validos, rejeitados) -
        `pmids_validos` é a única lista segura para exibição no relatório;
        `rejeitados` traz o motivo de cada remoção, para log de auditoria.
        """
        valid_pmids: List[str] = []
        rejected: List[Dict[str, Any]] = []

        for pmid in pmids or []:
            result = self.validate(pmid, asset_canonical_name)
            if result["valid"]:
                valid_pmids.append(pmid)
            else:
                rejected.append(result)

        return valid_pmids, rejected


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")

    from core.entity_resolver import EntityResolver

    resolver = EntityResolver(taxonomy_path="data/taxonomy/ativos_mvp.json")
    pubmed_conn = PubMedConnector(resolver=resolver)
    validator = PMIDValidator(pubmed_conn)

    asset = next(a for a in resolver.assets if a["asset_id"] == "AT-001")
    print(f"--- Validação Determinística de PMIDs via NCBI E-utilities: {asset['canonical_name']} ---")

    search = pubmed_conn.search_articles(asset["botanical_or_cas"][0], exclusions=asset["exclusions"], max_results=3, days=pubmed_conn.TRACTION_WINDOW_DAYS)
    real_pmids = search["pmids"]
    print(f"PMIDs reais encontrados na janela de 12 meses: {real_pmids}")

    if real_pmids:
        valid, rejected = validator.validate_batch([real_pmids[0]], asset["canonical_name"])
        print(f"\n[Caso de SUCESSO] PMID {real_pmids[0]} -> valido={bool(valid)}")

    fake_pmid = "1"  # PMID de 1 dígito - existe no NCBI mas não guarda relação alguma com o ativo pesquisado
    valid, rejected = validator.validate_batch([fake_pmid], asset["canonical_name"])
    print(f"\n[Caso de BLOQUEIO] PMID inventado/fora de contexto {fake_pmid} -> valido={bool(valid)}")
    for r in rejected:
        print(f"  Motivo da rejeição: {r['reason']}")
