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
        # (cobertura deliberadamente parcial dos 30 ativos - ausência de patente
        # é um sinal industrial real, não uma lacuna a preencher artificialmente)
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
            },
            {
                "patent_id": "US20230187654A1",
                "title": "Centella asiatica extract nanoemulsion for barrier repair formulations",
                "ipc_code": "A61K8/97",
                "filing_year": 2023,
                "assignee": "SkinTech Labs"
            },
            {
                "patent_id": "EP4102345A1",
                "title": "Cosmetic composition comprising Centella asiatica and hyaluronic acid complex",
                "ipc_code": "A61K8/9789",
                "filing_year": 2022,
                "assignee": "Cosmo Innovations SA"
            },
            {
                "patent_id": "WO2024011234A1",
                "title": "Polygonum cuspidatum resveratrol delivery system for topical antioxidant compositions",
                "ipc_code": "A61K8/365",
                "filing_year": 2024,
                "assignee": "Longevity Actives Inc"
            },
            {
                "patent_id": "CN115137654A",
                "title": "Ginkgo biloba leaf extract microcapsule for anti-pollution skincare",
                "ipc_code": "A61K8/97",
                "filing_year": 2022,
                "assignee": "Shanghai Derma Group"
            },
            {
                "patent_id": "US20220331678A1",
                "title": "Camellia sinensis leaf extract combined with niacinamide for brightening",
                "ipc_code": "A61K8/9789",
                "filing_year": 2023,
                "assignee": "Beauty Corp Global"
            },
            {
                "patent_id": "EP4189012A1",
                "title": "Fermented Camellia sinensis extract for sensitive skin formulations",
                "ipc_code": "A61K8/9789",
                "filing_year": 2024,
                "assignee": "Cosmo Innovations SA"
            },
            {
                "patent_id": "AU2023200123A1",
                "title": "Terminalia ferdinandiana fruit extract stabilization method for vitamin C delivery",
                "ipc_code": "A61K8/97",
                "filing_year": 2023,
                "assignee": "Outback Botanicals Pty"
            },
            {
                "patent_id": "WO2023098765A1",
                "title": "Curcuma longa root extract liposomal composition for anti-inflammatory skincare",
                "ipc_code": "A61K8/97",
                "filing_year": 2022,
                "assignee": "Derma Innovations Ltd"
            },
            {
                "patent_id": "KR20230045678A",
                "title": "Curcuma longa and centella asiatica synergistic complex for redness relief",
                "ipc_code": "A61K8/9789",
                "filing_year": 2023,
                "assignee": "Seoul Biocosmetics Co"
            },
            {
                "patent_id": "JP2024056789A",
                "title": "Curcuma longa fermented extract for skin barrier enhancement",
                "ipc_code": "A61K8/97",
                "filing_year": 2024,
                "assignee": "Shanghai Derma Group"
            },
            {
                "patent_id": "EP3987654A1",
                "title": "Glycyrrhiza glabra root extract for pigmentation control with reduced irritancy",
                "ipc_code": "A61K8/9789",
                "filing_year": 2021,
                "assignee": "Longevity Actives Inc"
            },
            {
                "patent_id": "US20210298765A1",
                "title": "Aloe barbadensis leaf juice hydrogel base for post-procedure skincare",
                "ipc_code": "A61K8/97",
                "filing_year": 2021,
                "assignee": "SkinTech Labs"
            },
            {
                "patent_id": "WO2024087654A1",
                "title": "Cannabis sativa derived cannabidiol composition for topical anti-inflammatory use",
                "ipc_code": "A61K31/05",
                "filing_year": 2024,
                "assignee": "GreenLeaf Dermaceuticals"
            },
            {
                "patent_id": "US20240156789A1",
                "title": "Stable cannabidiol emulsion for sensitive skin barrier repair",
                "ipc_code": "A61K8/9789",
                "filing_year": 2024,
                "assignee": "GreenLeaf Dermaceuticals"
            },
            {
                "patent_id": "EP4056789A1",
                "title": "Arctostaphylos uva-ursi leaf extract alpha-arbutin standardized complex for brightening",
                "ipc_code": "A61K8/365",
                "filing_year": 2022,
                "assignee": "Cosmo Innovations SA"
            },
            {
                "patent_id": "US20220087654A1",
                "title": "Sugarcane-derived squalane composition with improved oxidative stability",
                "ipc_code": "A61K8/34",
                "filing_year": 2022,
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
