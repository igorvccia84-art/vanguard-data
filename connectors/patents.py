import sys
import io
import hashlib
from datetime import date, timedelta
from typing import List, Dict, Any, Optional, Tuple

from core.entity_resolver import EntityResolver

# Força codificação UTF-8 no terminal Windows
if sys.stdout.encoding != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')


class PatentConnector:
    """
    Conector independente para dados de Patentes (EPO/WIPO).
    Consome depósitos de patentes, deduplica por família de patentes (várias
    jurisdições - EP/WO/US/CN/... - frequentemente protegem a mesma invenção e
    não devem ser contadas como sinais industriais independentes) e enriquece
    com Entity Resolution. Cada busca é auditável: os termos de busca/exclusão
    aplicados e o hash SHA256 da query exata são retornados junto aos resultados.
    A busca é restrita à janela dos últimos SEARCH_WINDOW_DAYS dias (data de
    publicação/evento mais recente da patente) em relação à data atual, para
    que o relatório reflita sinais industriais recentes de mercado.
    """

    SEARCH_WINDOW_DAYS = 15

    def __init__(self, resolver: EntityResolver):
        self.resolver = resolver

    def _mock_database(self) -> List[Dict[str, Any]]:
        """
        Simula o retorno de dados brutos de uma API de patentes (Ex: EPO OPS / WIPO),
        retornando documentos reais/fictícios com códigos IPC, títulos técnicos e o
        identificador de família de patentes (family_id) - depósitos com o mesmo
        family_id são contrapartes em jurisdições diferentes da mesma invenção
        (cobertura deliberadamente parcial dos ativos do catálogo - ausência de
        patente é um sinal industrial real, não uma lacuna a preencher artificialmente).

        'filing_year' é o ano histórico de depósito (usado no desempate da
        deduplicação por família). 'publication_date' é a data do evento mais
        recente da patente (publicação/atualização de status) - calculada a
        partir de 'days_ago' relativo à data atual, já que uma publicação pode
        ocorrer bem depois do depósito (defasagem padrão de ~18 meses em
        EPO/WIPO); é sobre 'publication_date' que a janela de 15 dias filtra.
        """
        today = date.today()

        def _published(days_ago: int) -> str:
            return (today - timedelta(days=days_ago)).isoformat()

        return [
            {"patent_id": "EP3892258A1", "family_id": "PF-2024-001", "title": "Topical compositions comprising Psoralea corylifolia extract for anti-aging", "ipc_code": "A61K8/97", "filing_year": 2024, "assignee": "Derma Innovations Ltd", "publication_date": _published(3)},
            {"patent_id": "WO2023105432A1", "family_id": "PF-2023-014", "title": "Method for stabilizing Ferulic Acid in cosmetic emulsions", "ipc_code": "A61K8/368", "filing_year": 2023, "assignee": "Beauty Corp Global", "publication_date": _published(200)},
            {"patent_id": "US20230187654A1", "family_id": "PF-2023-002", "title": "Centella asiatica extract nanoemulsion for barrier repair formulations", "ipc_code": "A61K8/97", "filing_year": 2023, "assignee": "SkinTech Labs", "publication_date": _published(9)},
            {"patent_id": "EP4102345A1", "family_id": "PF-2022-007", "title": "Cosmetic composition comprising Centella asiatica and hyaluronic acid complex", "ipc_code": "A61K8/9789", "filing_year": 2022, "assignee": "Cosmo Innovations SA", "publication_date": _published(400)},
            {"patent_id": "WO2024011234A1", "family_id": "PF-2024-009", "title": "Polygonum cuspidatum resveratrol delivery system for topical antioxidant compositions", "ipc_code": "A61K8/365", "filing_year": 2024, "assignee": "Longevity Actives Inc", "publication_date": _published(14)},
            {"patent_id": "CN115137654A", "family_id": "PF-2022-011", "title": "Ginkgo biloba leaf extract microcapsule for anti-pollution skincare", "ipc_code": "A61K8/97", "filing_year": 2022, "assignee": "Shanghai Derma Group", "publication_date": _published(600)},
            {"patent_id": "US20220331678A1", "family_id": "PF-2023-005", "title": "Camellia sinensis leaf extract combined with niacinamide for brightening", "ipc_code": "A61K8/9789", "filing_year": 2023, "assignee": "Beauty Corp Global", "publication_date": _published(90)},
            {"patent_id": "EP4189012A1", "family_id": "PF-2024-003", "title": "Fermented Camellia sinensis extract for sensitive skin formulations", "ipc_code": "A61K8/9789", "filing_year": 2024, "assignee": "Cosmo Innovations SA", "publication_date": _published(45)},
            {"patent_id": "AU2023200123A1", "family_id": "PF-2023-008", "title": "Terminalia ferdinandiana fruit extract stabilization method for vitamin C delivery", "ipc_code": "A61K8/97", "filing_year": 2023, "assignee": "Outback Botanicals Pty", "publication_date": _published(250)},
            {"patent_id": "WO2023098765A1", "family_id": "PF-2022-015", "title": "Curcuma longa root extract liposomal composition for anti-inflammatory skincare", "ipc_code": "A61K8/97", "filing_year": 2022, "assignee": "Derma Innovations Ltd", "publication_date": _published(500)},
            {"patent_id": "KR20230045678A", "family_id": "PF-2023-011", "title": "Curcuma longa and centella asiatica synergistic complex for redness relief", "ipc_code": "A61K8/9789", "filing_year": 2023, "assignee": "Seoul Biocosmetics Co", "publication_date": _published(120)},
            {"patent_id": "JP2024056789A", "family_id": "PF-2024-006", "title": "Curcuma longa fermented extract for skin barrier enhancement", "ipc_code": "A61K8/97", "filing_year": 2024, "assignee": "Shanghai Derma Group", "publication_date": _published(7)},
            {"patent_id": "EP3987654A1", "family_id": "PF-2021-004", "title": "Glycyrrhiza glabra root extract for pigmentation control with reduced irritancy", "ipc_code": "A61K8/9789", "filing_year": 2021, "assignee": "Longevity Actives Inc", "publication_date": _published(800)},
            {"patent_id": "US20210298765A1", "family_id": "PF-2021-009", "title": "Aloe barbadensis leaf juice hydrogel base for post-procedure skincare", "ipc_code": "A61K8/97", "filing_year": 2021, "assignee": "SkinTech Labs", "publication_date": _published(900)},
            {"patent_id": "WO2024087654A1", "family_id": "PF-2024-012", "title": "Cannabis sativa derived cannabidiol composition for topical anti-inflammatory use", "ipc_code": "A61K31/05", "filing_year": 2024, "assignee": "GreenLeaf Dermaceuticals", "publication_date": _published(11)},
            {"patent_id": "US20240156789A1", "family_id": "PF-2024-012", "title": "Stable cannabidiol emulsion for sensitive skin barrier repair", "ipc_code": "A61K8/9789", "filing_year": 2024, "assignee": "GreenLeaf Dermaceuticals", "publication_date": _published(2)},
            {"patent_id": "EP4056789A1", "family_id": "PF-2022-010", "title": "Arctostaphylos uva-ursi leaf extract alpha-arbutin standardized complex for brightening", "ipc_code": "A61K8/365", "filing_year": 2022, "assignee": "Cosmo Innovations SA", "publication_date": _published(300)},
            {"patent_id": "US20220087654A1", "family_id": "PF-2022-013", "title": "Sugarcane-derived squalane composition with improved oxidative stability", "ipc_code": "A61K8/34", "filing_year": 2022, "assignee": "Beauty Corp Global", "publication_date": _published(150)}
        ]

    @staticmethod
    def _build_query(base_query: str, exclusions: Optional[List[str]] = None) -> str:
        """Monta a query final aplicando as exclusões do ativo como cláusulas ANDNOT (sintaxe comum a Espacenet/PATENTSCOPE)."""
        query = base_query
        for term in (exclusions or []):
            query += f' ANDNOT "{term}"'
        return query

    @staticmethod
    def _query_hash(query: str) -> str:
        """SHA256 da query exata de busca, para rastreabilidade em evaluation_evidence_sources."""
        return hashlib.sha256(query.encode('utf-8')).hexdigest()

    def _search_window(self) -> Tuple[date, date]:
        """Janela de busca: dos últimos SEARCH_WINDOW_DAYS dias até a data atual (data de publicação da patente)."""
        end_date = date.today()
        start_date = end_date - timedelta(days=self.SEARCH_WINDOW_DAYS)
        return start_date, end_date

    def fetch_patents_mock(self, query: str, exclusions: Optional[List[str]] = None) -> Dict[str, Any]:
        """
        Busca patentes cujo título contenha a query, restringe à janela dos
        últimos SEARCH_WINDOW_DAYS dias (publication_date), aplica os termos de
        exclusão do ativo (remove ruído fora da aplicação tópica/cosmética) e
        deduplica por família de patentes - mantendo apenas o depósito mais
        antigo de cada family_id, já que múltiplas jurisdições da mesma
        invenção não representam sinais industriais independentes. Retorna os
        resultados deduplicados junto com a query exata, seu hash SHA256, a
        janela de datas aplicada e as métricas de deduplicação.
        """
        start_date, end_date = self._search_window()
        date_range = {"start": start_date.isoformat(), "end": end_date.isoformat()}

        try:
            mock_database = self._mock_database()
        except Exception as e:
            full_query = self._build_query(query, exclusions)
            return {
                "query": full_query,
                "query_hash": self._query_hash(full_query),
                "results": [],
                "total_found": 0,
                "total_after_dedup": 0,
                "duplicate_families_collapsed": 0,
                "date_range": date_range,
                "error": str(e)
            }

        full_query = self._build_query(query, exclusions)
        query_hash = self._query_hash(full_query)

        query_norm = query.lower()
        exclusion_terms_norm = [t.lower() for t in (exclusions or [])]

        matches = [
            p for p in mock_database
            if query_norm in p["title"].lower()
            and not any(term in p["title"].lower() for term in exclusion_terms_norm)
            and date_range["start"] <= p["publication_date"] <= date_range["end"]
        ]

        families_seen: Dict[str, Dict[str, Any]] = {}
        for patent in matches:
            family_id = patent.get("family_id") or patent["patent_id"]
            existing = families_seen.get(family_id)
            # Mantém o depósito mais antigo (menor filing_year) como representante da família
            if existing is None or patent.get("filing_year", 9999) < existing.get("filing_year", 9999):
                families_seen[family_id] = patent

        deduped_results = sorted(families_seen.values(), key=lambda p: p["patent_id"])

        return {
            "query": full_query,
            "query_hash": query_hash,
            "results": deduped_results,
            "total_found": len(matches),
            "total_after_dedup": len(deduped_results),
            "duplicate_families_collapsed": len(matches) - len(deduped_results),
            "date_range": date_range
        }

    def process_patent(self, patent_data: Dict[str, Any]) -> Dict[str, Any]:
        """Processa a patente e resolve a entidade do ativo associado."""
        title = patent_data.get("title", "")
        resolved_entity = self.resolver.resolve(title)

        return {
            "patent_id": patent_data.get("patent_id"),
            "family_id": patent_data.get("family_id"),
            "title": title,
            "ipc_code": patent_data.get("ipc_code"),
            "filing_year": patent_data.get("filing_year"),
            "publication_date": patent_data.get("publication_date"),
            "assignee": patent_data.get("assignee"),
            "source": "EPO/WIPO",
            "entity_match": resolved_entity
        }


if __name__ == "__main__":
    resolver = EntityResolver(taxonomy_path="data/taxonomy/ativos_mvp.json")
    patent_conn = PatentConnector(resolver=resolver)

    asset = next(a for a in resolver.assets if a["asset_id"] == "AT-029")
    print(f"Buscando Patentes por '{asset['canonical_name']}' (exclusions={asset['exclusions']})...")

    search_result = patent_conn.fetch_patents_mock("Cannabis sativa", exclusions=asset["exclusions"])
    print(f"Query exata: {search_result['query']}")
    print(f"Query hash (SHA256): {search_result['query_hash']}")
    print(f"Janela de busca: {search_result['date_range']['start']} a {search_result['date_range']['end']}")
    print(f"Total encontrado: {search_result['total_found']} | Após dedup por família: {search_result['total_after_dedup']} "
          f"| Famílias duplicadas colapsadas: {search_result['duplicate_families_collapsed']}")

    for raw in search_result["results"]:
        processed = patent_conn.process_patent(raw)
        print(f"\n[Patente {processed['patent_id']}] (família {processed['family_id']})")
        print(f"Título: {processed['title']}")
        print(f"Titular (Assignee): {processed['assignee']}")
        print(f"Entidade Resolvida: {processed['entity_match']}")
