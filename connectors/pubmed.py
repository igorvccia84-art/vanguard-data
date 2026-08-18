import sys
import time
import socket
import hashlib
import urllib.request
import urllib.parse
import urllib.error
import json
import xml.etree.ElementTree as ET
from datetime import date, timedelta
from typing import List, Dict, Any, Optional, Tuple

from core.entity_resolver import EntityResolver


class ConnectorRequestError(RuntimeError):
    """Falha de requisição (timeout, erro de rede ou HTTP) após esgotar as tentativas de retry."""


class PubMedConnector:
    """
    Conector independente para a API do NCBI PubMed / E-utilities.
    Coleta publicações científicas e as enriquece com a Entity Resolution.

    Cada busca é auditável: as exclusões do ativo (core.entity_resolver) são
    aplicadas diretamente na query via cláusulas NOT, a query final exata é
    hasheada em SHA256, e a contagem/metadados brutos da resposta do PubMed
    são retornados junto - tudo necessário para popular
    evaluation_evidence_sources (core/database.py). A busca é restrita à janela
    dos últimos SEARCH_WINDOW_DAYS dias (data de publicação) em relação à data
    atual, para que o relatório reflita sinais recentes de mercado.
    """

    BASE_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
    MIN_REQUEST_INTERVAL = 0.4  # NCBI sem API key: limite de ~3 requisições/segundo
    MAX_RETRIES = 3
    REQUEST_TIMEOUT = 10  # segundos
    SEARCH_WINDOW_DAYS = 15  # janela de novidades exibida no relatório
    TRACTION_WINDOW_DAYS = 365  # janela histórica móvel usada para calcular Tração Científica (core/score_engine.py)

    def __init__(self, resolver: EntityResolver):
        self.resolver = resolver
        self._last_request_at = 0.0

    def _throttled_request(self, url: str) -> bytes:
        """Faz a requisição respeitando o rate limit do NCBI, com retry/backoff em timeout, erro de rede ou HTTP 429."""
        last_error: Optional[Exception] = None
        for attempt in range(self.MAX_RETRIES):
            elapsed = time.monotonic() - self._last_request_at
            if elapsed < self.MIN_REQUEST_INTERVAL:
                time.sleep(self.MIN_REQUEST_INTERVAL - elapsed)

            req = urllib.request.Request(url, headers={'User-Agent': 'VanguardData/1.0'})
            try:
                with urllib.request.urlopen(req, timeout=self.REQUEST_TIMEOUT) as response:
                    self._last_request_at = time.monotonic()
                    return response.read()
            except urllib.error.HTTPError as e:
                self._last_request_at = time.monotonic()
                last_error = e
                if e.code == 429 and attempt < self.MAX_RETRIES - 1:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                raise ConnectorRequestError(f"HTTP {e.code} ao acessar {url}") from e
            except (urllib.error.URLError, socket.timeout, TimeoutError) as e:
                self._last_request_at = time.monotonic()
                last_error = e
                if attempt < self.MAX_RETRIES - 1:
                    time.sleep(1.5 * (attempt + 1))
                    continue

        raise ConnectorRequestError(f"Falha ao acessar {url} após {self.MAX_RETRIES} tentativas: {last_error}")

    @staticmethod
    def _build_query(base_query: str, exclusions: Optional[List[str]] = None) -> str:
        """Monta a query final aplicando as exclusões do ativo como cláusulas NOT (sintaxe E-utilities)."""
        query = base_query
        for term in (exclusions or []):
            query += f' NOT "{term}"'
        return query

    @staticmethod
    def _query_hash(query: str) -> str:
        """SHA256 da query exata enviada ao PubMed, para rastreabilidade em evaluation_evidence_sources."""
        return hashlib.sha256(query.encode('utf-8')).hexdigest()

    def _search_window(self, days: Optional[int] = None, end_days_ago: int = 0) -> Tuple[date, date]:
        """
        Janela de busca: de `days` dias atrás (default SEARCH_WINDOW_DAYS) até
        `end_days_ago` dias atrás (default 0 = hoje). `end_days_ago > 0`
        desloca a janela inteira para o passado, sem sobreposição com o
        presente - usado para consultas de linha de base histórica (ex.:
        calculate_scientific_traction's componente [G] em core/score_engine.py,
        que exige uma janela de 36 meses estritamente anterior aos 12 meses
        de análise, "sem contaminação do período recente").
        """
        today = date.today()
        end_date = today - timedelta(days=end_days_ago)
        start_date = today - timedelta(days=days if days is not None else self.SEARCH_WINDOW_DAYS)
        return start_date, end_date

    def search_articles(
        self,
        query: str,
        exclusions: Optional[List[str]] = None,
        max_results: int = 5,
        days: Optional[int] = None,
        end_days_ago: int = 0
    ) -> Dict[str, Any]:
        """
        Busca PMIDs no PubMed para um termo específico, restrita à janela de
        `days` dias atrás (default SEARCH_WINDOW_DAYS=15) até `end_days_ago`
        dias atrás (default 0 = hoje, data de publicação), e aplicando as
        exclusões do ativo já na origem (reduz ruído sistêmico/off-topic
        antes mesmo da Entity Resolution). Passar `days=TRACTION_WINDOW_DAYS`
        para a janela histórica móvel de 12 meses usada no cálculo de Tração
        Científica (core/score_engine.py), ou `end_days_ago > 0` para uma
        janela de linha de base histórica deslocada para o passado (sem
        sobreposição com o período de análise). `max_results=0` retorna
        apenas a contagem bruta do PubMed (nenhum PMID, nenhum custo de
        efetch subsequente) - suficiente para uma comparação de magnitude
        sem precisar resolver cada artigo individualmente. Retorna PMIDs,
        contagem total reportada pelo PubMed, o hash SHA256 da query exata,
        a janela de datas aplicada e um resumo dos metadados brutos da
        resposta. Resiliente a timeout e falha de rede: em caso de falha,
        retorna uma resposta vazia e degradada em vez de propagar a exceção.
        """
        full_query = self._build_query(query, exclusions)
        query_hash = self._query_hash(full_query)
        start_date, end_date = self._search_window(days=days, end_days_ago=end_days_ago)
        date_range = {"start": start_date.isoformat(), "end": end_date.isoformat()}

        url = (
            f"{self.BASE_URL}/esearch.fcgi?db=pubmed&term={urllib.parse.quote(full_query)}"
            f"&retmode=json&retmax={max_results}"
            f"&datetype=pdat&mindate={start_date.strftime('%Y/%m/%d')}&maxdate={end_date.strftime('%Y/%m/%d')}"
        )

        try:
            data = json.loads(self._throttled_request(url).decode())
        except (ConnectorRequestError, json.JSONDecodeError) as e:
            return {
                "query": full_query,
                "query_hash": query_hash,
                "pmids": [],
                "count": 0,
                "date_range": date_range,
                "raw_metadata": {"error": str(e)}
            }

        esearch_result = data.get("esearchresult", {})
        return {
            "query": full_query,
            "query_hash": query_hash,
            "pmids": esearch_result.get("idlist", []),
            "count": int(esearch_result.get("count") or 0),
            "date_range": date_range,
            "raw_metadata": {
                "retmax": esearch_result.get("retmax"),
                "translation_set": esearch_result.get("translationset"),
                "query_translation": esearch_result.get("querytranslation")
            }
        }

    def fetch_article_details(self, pmid: str) -> Dict[str, Any]:
        """
        Busca os detalhes (título, resumo) de um PMID específico e resolve a
        entidade do ativo. Resiliente a timeout/falha de rede: retorna um
        registro degradado (com campo 'error') em vez de derrubar o pipeline.

        Hook de verificação (blindagem contra PMID alucinado/errôneo): o
        campo 'verified' só é True quando (1) o efetch trouxe um título real
        para o PMID - confirmando que o registro existe de fato no PubMed - e
        (2) a Entity Resolution reconheceu o ativo pesquisado no título/resumo
        do artigo - confirmando que o PMID pertence de fato ao ativo em
        questão, não apenas a um ID válido para outro assunto. Chamadores
        (main.py) devem descartar qualquer PMID com verified=False antes de
        exibi-lo no relatório - nenhum PMID chega ao PDF sem essa confirmação.
        """
        url = f"{self.BASE_URL}/efetch.fcgi?db=pubmed&id={pmid}&retmode=xml"

        try:
            xml_data = self._throttled_request(url)
        except ConnectorRequestError as e:
            return {
                "pmid": pmid,
                "title": "",
                "source": "PubMed",
                "entity_match": None,
                "verified": False,
                "error": str(e)
            }

        try:
            root = ET.fromstring(xml_data)
            article = root.find(".//Article")
            title = article.findtext(".//ArticleTitle") if article is not None else ""
            abstract_node = article.find(".//Abstract/AbstractText") if article is not None else None
            abstract = abstract_node.text if abstract_node is not None else ""
        except ET.ParseError as e:
            return {
                "pmid": pmid,
                "title": "",
                "source": "PubMed",
                "entity_match": None,
                "verified": False,
                "error": f"Falha ao interpretar XML: {e}"
            }

        full_text = f"{title} {abstract}"
        # require_topical_context=True: reforço de relevância tópica - o artigo
        # precisa, além de mencionar o ativo, atingir ao menos o Nível 2 de
        # relevância (core/entity_resolver.py TOPICAL_CONTEXT_TERMS/PRODUCT_FORMULATION_TERMS).
        # Bloqueia falsos positivos como um estudo de fratura mandibular que
        # apenas menciona o nome botânico do ativo sem relação com skincare.
        resolved_entity = self.resolver.resolve(full_text, require_topical_context=True)
        verified = bool(title) and resolved_entity is not None

        return {
            "pmid": pmid,
            "title": title,
            "source": "PubMed",
            "entity_match": resolved_entity,
            "verified": verified
        }


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")

    resolver = EntityResolver(taxonomy_path="data/taxonomy/ativos_mvp.json")
    pubmed = PubMedConnector(resolver=resolver)

    asset = next(a for a in resolver.assets if a["asset_id"] == "AT-001")
    print(f"Buscando no PubMed por '{asset['canonical_name']}' (exclusions={asset['exclusions']})...")

    search_result = pubmed.search_articles(
        asset["botanical_or_cas"][0], exclusions=asset["exclusions"], max_results=2
    )
    print(f"Query exata: {search_result['query']}")
    print(f"Query hash (SHA256): {search_result['query_hash']}")
    print(f"Janela de busca: {search_result['date_range']['start']} a {search_result['date_range']['end']}")
    print(f"Contagem total no PubMed: {search_result['count']}")
    print(f"Metadados brutos: {search_result['raw_metadata']}")

    for pmid in search_result["pmids"]:
        details = pubmed.fetch_article_details(pmid)
        print(f"\n[PMID {pmid}]")
        print(f"Título: {details['title'][:80]}...")
        print(f"Entidade Resolvida: {details['entity_match']}")

    print("\n--- Teste de janela de linha de base histórica (max_results=0, sem efetch) ---")
    baseline_result = pubmed.search_articles(
        asset["botanical_or_cas"][0], exclusions=asset["exclusions"],
        max_results=0, days=pubmed.TRACTION_WINDOW_DAYS + 1095, end_days_ago=pubmed.TRACTION_WINDOW_DAYS
    )
    print(f"Janela de linha de base: {baseline_result['date_range']['start']} a {baseline_result['date_range']['end']} (sem sobreposição com os últimos {pubmed.TRACTION_WINDOW_DAYS} dias)")
    print(f"Contagem bruta na linha de base: {baseline_result['count']}")
    print(f"PMIDs retornados (deve ser vazio, max_results=0): {baseline_result['pmids']}")
