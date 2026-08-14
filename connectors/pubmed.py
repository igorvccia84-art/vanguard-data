import sys
import urllib.request
import urllib.parse
import json
import xml.etree.ElementTree as ET
from typing import List, Dict, Any

from core.entity_resolver import EntityResolver


class PubMedConnector:
    """
    Conector independente para a API do NCBI PubMed / E-utilities.
    Coleta publicações científicas e as enriquece com a Entity Resolution.
    """

    BASE_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"

    def __init__(self, resolver: EntityResolver):
        self.resolver = resolver

    def search_articles(self, query: str, max_results: int = 5) -> List[str]:
        """Busca IDs de artigos (PMIDs) no PubMed para um termo específico."""
        url = f"{self.BASE_URL}/esearch.fcgi?db=pubmed&term={urllib.parse.quote(query)}&retmode=json&retmax={max_results}"

        req = urllib.request.Request(url, headers={'User-Agent': 'VanguardData/1.0'})
        with urllib.request.urlopen(req) as response:
            data = json.loads(response.read().decode())

        return data.get("esearchresult", {}).get("idlist", [])

    def fetch_article_details(self, pmid: str) -> Dict[str, Any]:
        """Busca os detalhes (título, resumo, data) de um PMID específico."""
        url = f"{self.BASE_URL}/efetch.fcgi?db=pubmed&id={pmid}&retmode=xml"

        req = urllib.request.Request(url, headers={'User-Agent': 'VanguardData/1.0'})
        with urllib.request.urlopen(req) as response:
            xml_data = response.read()

        root = ET.fromstring(xml_data)
        article = root.find(".//Article")
        title = article.findtext(".//ArticleTitle") if article is not None else ""
        abstract_node = article.find(".//Abstract/AbstractText") if article is not None else None
        abstract = abstract_node.text if abstract_node is not None else ""

        full_text = f"{title} {abstract}"
        resolved_entity = self.resolver.resolve(full_text)

        return {
            "pmid": pmid,
            "title": title,
            "source": "PubMed",
            "entity_match": resolved_entity
        }


if __name__ == "__main__":
    # Evita UnicodeEncodeError no console do Windows (cp1252) com títulos contendo caracteres não-ASCII
    sys.stdout.reconfigure(encoding="utf-8")

    # Teste rápido de integração
    resolver = EntityResolver(taxonomy_path="data/taxonomy/ativos_mvp.json")
    pubmed = PubMedConnector(resolver=resolver)

    print("Buscando no PubMed por 'Psoralea corylifolia'...")
    pmids = pubmed.search_articles("Psoralea corylifolia", max_results=2)

    for pmid in pmids:
        details = pubmed.fetch_article_details(pmid)
        print(f"\n[PMID {pmid}]")
        print(f"Título: {details['title'][:80]}...")
        print(f"Entidade Resolvida: {details['entity_match']}")
