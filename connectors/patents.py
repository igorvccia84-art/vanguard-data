import sys
import io
import json
import urllib.request
from typing import List, Dict, Any

from core.entity_resolver import EntityResolver

# Força codificação UTF-8 no terminal Windows
if sys.stdout.encoding != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')


class PatentConnector:
    """
    Conector independente para dados de Patentes (EPO/WIPO).
    Consome depósitos de patentes e enriquece com Entity Resolution.
    """

    def __init__(self, resolver: EntityResolver):
        self.resolver = resolver

    def fetch_patents_mock(self, query: str) -> List[Dict[str, Any]]:
        """
        Simula o retorno de dados brutos de uma API de patentes (Ex: EPO OPS / WIPO),
        retornando documentos reais/fictícios com códigos IPC e títulos técnicos.
        """
        # Estrutura padrão de dados retornados por APIs de patentes
        mock_database = [
            {
                "patent_id": "EP3892258A1",
                "title": "Topical compositions comprising Psoralea corylifolia extract for anti-aging",
                "ipc_code": "A61K8/97",
                "filing_year": 2024,
                "assignee": "Derma Innovations Ltd"
            },
            {
                "patent_id": "WO2023105432A1",
                "title": "Method for stabilizing Ferulic Acid in cosmetic emulsions",
                "ipc_code": "A61K8/368",
                "filing_year": 2023,
                "assignee": "Beauty Corp Global"
            }
        ]

        # Filtra documentos que contenham a query no título
        results = [p for p in mock_database if query.lower() in p["title"].lower()]
        return results

    def process_patent(self, patent_data: Dict[str, Any]) -> Dict[str, Any]:
        """Processa a patente e resolve a entidade do ativo associado."""
        title = patent_data.get("title", "")
        resolved_entity = self.resolver.resolve(title)

        return {
            "patent_id": patent_data.get("patent_id"),
            "title": title,
            "ipc_code": patent_data.get("ipc_code"),
            "filing_year": patent_data.get("filing_year"),
            "assignee": patent_data.get("assignee"),
            "source": "EPO/WIPO",
            "entity_match": resolved_entity
        }


if __name__ == "__main__":
    resolver = EntityResolver(taxonomy_path="data/taxonomy/ativos_mvp.json")
    patent_conn = PatentConnector(resolver=resolver)

    print("Buscando Patentes por 'Psoralea corylifolia'...")
    raw_patents = patent_conn.fetch_patents_mock("Psoralea corylifolia")

    for raw in raw_patents:
        processed = patent_conn.process_patent(raw)
        print(f"\n[Patente {processed['patent_id']}]")
        print(f"Título: {processed['title']}")
        print(f"Titular (Assignee): {processed['assignee']}")
        print(f"Entidade Resolvida: {processed['entity_match']}")
