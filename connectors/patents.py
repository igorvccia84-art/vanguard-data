import sys
import io
import re
import os
import time
import socket
import base64
import hashlib
import json
import urllib.request
import urllib.parse
import urllib.error
import xml.etree.ElementTree as ET
from datetime import date, datetime, timezone, timedelta
from typing import List, Dict, Any, Optional, Tuple

from core.entity_resolver import EntityResolver

# Força codificação UTF-8 no terminal Windows
if sys.stdout.encoding != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')


class ConnectorRequestError(RuntimeError):
    """Falha de requisição ao EPO OPS (timeout, erro de rede ou HTTP) após esgotar as tentativas de retry."""


class PatentConnectorConfigError(RuntimeError):
    """EPO_OPS_CONSUMER_KEY/EPO_OPS_CONSUMER_SECRET ausentes ou incompletos em .env - erro de configuração, nunca uma falha transitória de rede (não deve ser mascarado como 'zero patentes encontradas')."""


class PatentConnector:
    """
    Conector independente para dados de Patentes.

    fetch_patents() é o ponto de entrada de produção: busca REAL no EPO OPS
    (Open Patent Services, https://ops.epo.org) via OAuth2 client-credentials
    + CQL (Contextual Query Language), com rate-limit/backoff e cache em
    disco de 14 dias. Deduplica por família de patentes (várias jurisdições -
    EP/WO/US/CN/... - frequentemente protegem a mesma invenção e não devem
    ser contadas como sinais industriais independentes, usando o INPADOC
    family-id retornado pelo próprio OPS) e enriquece com Entity Resolution.
    Cada busca é auditável: a query CQL exata e seu hash SHA256 são
    retornados junto aos resultados. A busca é restrita à janela dos últimos
    SEARCH_WINDOW_DAYS dias (data de publicação) em relação à data atual,
    para que o relatório reflita sinais industriais recentes de mercado.

    fetch_patents_mock() permanece disponível separadamente, só para
    desenvolvimento/teste offline sem credenciais - nunca é usada
    implicitamente por fetch_patents() (ver PatentConnectorConfigError).
    """

    SEARCH_WINDOW_DAYS = 15  # janela de novidades exibida no relatório
    TRACTION_WINDOW_DAYS = 365  # janela histórica móvel usada para calcular Tração Industrial (core/score_engine.py)

    # --- EPO OPS (Open Patent Services) - conector real (OAuth2 + CQL) ---
    # Endpoints e comportamento confirmados por chamada ao vivo durante o
    # desenvolvimento deste conector (não apenas pela documentação):
    #   - OPS aceita no máximo 1 operador NOT por query (erro
    #     CLIENT.NotOperatorMaxNumber acima disso) - por isso todas as
    #     exclusões do ativo são agrupadas numa única cláusula
    #     'not (ab="x" or ab="y" ...)' em vez de um NOT por termo.
    #   - "ANDNOT" (um único token) NÃO é um operador CQL válido no OPS -
    #     resulta silenciosamente em zero resultados. O operador correto é
    #     "not" (equivale a AND NOT em CQL padrão).
    #   - Ausência de resultados retorna HTTP 404 com fault
    #     SERVER.EntityNotFound - tratado como resposta válida (zero
    #     patentes), nunca como falha de conector.
    OPS_TOKEN_URL = "https://ops.epo.org/3.2/auth/accesstoken"
    OPS_SEARCH_URL = "https://ops.epo.org/3.2/rest-services/published-data/search/biblio"
    OPS_MIN_REQUEST_INTERVAL = 1.0  # segundos entre requisições (fair-use; ver header x-throttling-control da resposta)
    OPS_MAX_RETRIES = 3
    OPS_REQUEST_TIMEOUT = 20  # segundos
    OPS_MAX_RESULTS = 25  # tamanho de página (header Range) por busca
    OPS_TOKEN_EXPIRY_BUFFER = 60  # segundos de margem antes do expires_in reportado pelo token OAuth2
    _NS = {"ops": "http://ops.epo.org", "ex": "http://www.epo.org/exchange"}
    _IPC_TEXT_PATTERN = re.compile(r'([A-H]\d{2}[A-Z])\D*(\d{1,4})\D*/\D*(\d{1,6})')

    # Cache em disco de resultados de busca EPO OPS - reexecuções da mesma
    # query CQL (mesmos termos + mesma janela de datas) dentro de
    # CACHE_TTL_DAYS não geram nova chamada de rede/consumo de quota.
    CACHE_DIR = os.path.join("data", "cache", "epo_ops")
    CACHE_TTL_DAYS = 14

    # Validação determinística pré-relatório (mesmo princípio de rigor de
    # connectors/pubmed_validator.py, aplicado a patentes): página pública
    # canônica do Google Patents por número de publicação, como camada
    # independente de confirmação sobre o resultado já real do EPO OPS.
    # Falha de rede/parse fecha para rejeição (fail-closed), nunca para
    # aceitação.
    GOOGLE_PATENTS_URL = "https://patents.google.com/patent/{patent_id}/en"
    VALIDATION_TIMEOUT = 8  # segundos
    VALIDATION_MAX_RETRIES = 2
    _TITLE_TAG_PATTERN = re.compile(r'<title[^>]*>(.*?)</title>', re.IGNORECASE | re.DOTALL)

    def __init__(self, resolver: EntityResolver):
        self.resolver = resolver
        self._access_token: Optional[str] = None
        self._token_expires_at: float = 0.0
        self._last_ops_request_at: float = 0.0
        os.makedirs(self.CACHE_DIR, exist_ok=True)

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

    @staticmethod
    def _dedupe_by_family(patents: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Deduplica por family_id, mantendo o depósito mais antigo (menor filing_year) como representante de cada família. Usado tanto pela base mock quanto pelo EPO OPS real (INPADOC family-id)."""
        families_seen: Dict[str, Dict[str, Any]] = {}
        for patent in patents:
            family_id = patent.get("family_id") or patent["patent_id"]
            existing = families_seen.get(family_id)
            if existing is None or patent.get("filing_year", 9999) < existing.get("filing_year", 9999):
                families_seen[family_id] = patent
        return sorted(families_seen.values(), key=lambda p: p["patent_id"])

    def _search_window(self, days: Optional[int] = None) -> Tuple[date, date]:
        """Janela de busca: dos últimos `days` dias (default SEARCH_WINDOW_DAYS) até a data atual (data de publicação da patente)."""
        end_date = date.today()
        start_date = end_date - timedelta(days=days if days is not None else self.SEARCH_WINDOW_DAYS)
        return start_date, end_date

    def fetch_patents_mock(self, query: str, exclusions: Optional[List[str]] = None, days: Optional[int] = None) -> Dict[str, Any]:
        """
        Busca patentes cujo título contenha a query, restringe à janela dos
        últimos `days` dias (default SEARCH_WINDOW_DAYS=15, publication_date),
        aplica os termos de exclusão do ativo (remove ruído fora da aplicação
        tópica/cosmética) e deduplica por família de patentes - mantendo
        apenas o depósito mais antigo de cada family_id, já que múltiplas
        jurisdições da mesma invenção não representam sinais industriais
        independentes. Passar `days=TRACTION_WINDOW_DAYS` para a janela
        histórica móvel de 12 meses usada no cálculo de Tração Industrial
        (core/score_engine.py), mantendo a janela padrão de 15 dias para as
        novidades exibidas no relatório. Retorna os resultados deduplicados
        junto com a query exata, seu hash SHA256, a janela de datas aplicada
        e as métricas de deduplicação.
        """
        start_date, end_date = self._search_window(days=days)
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

        deduped_results = self._dedupe_by_family(matches)

        return {
            "query": full_query,
            "query_hash": query_hash,
            "results": deduped_results,
            "total_found": len(matches),
            "total_after_dedup": len(deduped_results),
            "duplicate_families_collapsed": len(matches) - len(deduped_results),
            "date_range": date_range
        }

    # ------------------------------------------------------------------
    # EPO OPS (Open Patent Services) - conector real
    # ------------------------------------------------------------------

    @staticmethod
    def _ops_credentials() -> Tuple[str, str]:
        key = os.environ.get("EPO_OPS_CONSUMER_KEY")
        secret = os.environ.get("EPO_OPS_CONSUMER_SECRET")
        if not key or not secret:
            raise PatentConnectorConfigError(
                "EPO_OPS_CONSUMER_KEY/EPO_OPS_CONSUMER_SECRET ausentes em .env - registre uma conta em "
                "https://developers.epo.org e configure as credenciais antes de chamar fetch_patents() "
                "(ou use fetch_patents_mock() explicitamente para desenvolvimento/teste offline)."
            )
        return key, secret

    def _get_access_token(self) -> str:
        """Token OAuth2 client-credentials, cacheado em memória até pouco antes de expirar (OPS_TOKEN_EXPIRY_BUFFER)."""
        now = time.monotonic()
        if self._access_token and now < self._token_expires_at:
            return self._access_token

        key, secret = self._ops_credentials()
        basic = base64.b64encode(f"{key}:{secret}".encode("utf-8")).decode("ascii")
        req = urllib.request.Request(
            self.OPS_TOKEN_URL,
            data=b"grant_type=client_credentials",
            headers={
                "Authorization": f"Basic {basic}",
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": "ActivesPredict/1.0",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.OPS_REQUEST_TIMEOUT) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raise ConnectorRequestError(f"Falha ao autenticar no EPO OPS (OAuth2): HTTP {e.code}") from e
        except (urllib.error.URLError, socket.timeout, TimeoutError, json.JSONDecodeError, KeyError) as e:
            raise ConnectorRequestError(f"Falha ao autenticar no EPO OPS (OAuth2): {e}") from e

        self._access_token = payload["access_token"]
        self._token_expires_at = now + int(payload.get("expires_in", 1199)) - self.OPS_TOKEN_EXPIRY_BUFFER
        return self._access_token

    def _throttled_ops_request(self, url: str) -> bytes:
        """
        Requisição autenticada ao EPO OPS respeitando OPS_MIN_REQUEST_INTERVAL,
        com retry/backoff em HTTP 429/403 (quota/throttling - ver header
        x-throttling-control da resposta) e em timeout/erro de rede. HTTP 404
        com fault SERVER.EntityNotFound é devolvido normalmente ao chamador
        (não é falha - significa 'zero resultados para esta busca exata'),
        nunca convertido em exceção.
        """
        last_error: Optional[str] = None
        for attempt in range(self.OPS_MAX_RETRIES):
            token = self._get_access_token()
            elapsed = time.monotonic() - self._last_ops_request_at
            if elapsed < self.OPS_MIN_REQUEST_INTERVAL:
                time.sleep(self.OPS_MIN_REQUEST_INTERVAL - elapsed)

            req = urllib.request.Request(
                url,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Range": f"1-{self.OPS_MAX_RESULTS}",
                    "User-Agent": "ActivesPredict/1.0",
                },
            )
            try:
                with urllib.request.urlopen(req, timeout=self.OPS_REQUEST_TIMEOUT) as response:
                    self._last_ops_request_at = time.monotonic()
                    return response.read()
            except urllib.error.HTTPError as e:
                self._last_ops_request_at = time.monotonic()
                body = e.read()
                if e.code == 404 and b"SERVER.EntityNotFound" in body:
                    return body
                last_error = f"HTTP {e.code}: {body[:200]!r}"
                if e.code in (429, 403) and attempt < self.OPS_MAX_RETRIES - 1:
                    self._access_token = None  # 403 pode indicar token expirado - força renovação
                    time.sleep(2.0 * (attempt + 1))
                    continue
                raise ConnectorRequestError(f"Falha ao acessar EPO OPS ({url}): {last_error}")
            except (urllib.error.URLError, socket.timeout, TimeoutError) as e:
                self._last_ops_request_at = time.monotonic()
                last_error = str(e)
                if attempt < self.OPS_MAX_RETRIES - 1:
                    time.sleep(2.0 * (attempt + 1))
                    continue

        raise ConnectorRequestError(f"Falha ao acessar EPO OPS após {self.OPS_MAX_RETRIES} tentativas: {last_error}")

    def _cache_path(self, cache_key: str) -> str:
        digest = hashlib.sha256(cache_key.encode("utf-8")).hexdigest()
        return os.path.join(self.CACHE_DIR, f"{digest}.json")

    def _cache_read(self, cache_key: str) -> Optional[Dict[str, Any]]:
        path = self._cache_path(cache_key)
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                entry = json.load(f)
            cached_at = datetime.fromisoformat(entry["cached_at"])
            if datetime.now(timezone.utc) - cached_at > timedelta(days=self.CACHE_TTL_DAYS):
                return None
            return entry["payload"]
        except (OSError, ValueError, KeyError, json.JSONDecodeError):
            return None

    def _cache_write(self, cache_key: str, payload: Dict[str, Any]) -> None:
        try:
            with open(self._cache_path(cache_key), "w", encoding="utf-8") as f:
                json.dump({"cached_at": datetime.now(timezone.utc).isoformat(), "payload": payload}, f, ensure_ascii=False)
        except OSError:
            pass  # cache é best-effort - falha ao gravar nunca deve derrubar a busca

    @staticmethod
    def _escape_cql_term(term: str) -> str:
        return term.replace('"', '\\"')

    def _build_cql_query(self, query: str, exclusions: Optional[List[str]], start_date: date, end_date: date) -> str:
        """
        Monta a query CQL real enviada ao EPO OPS: busca o termo no título OU
        resumo, restrita à janela de datas (pd within), com as exclusões do
        ativo agrupadas numa ÚNICA cláusula 'not (... or ...)' - o OPS aceita
        no máximo 1 operador NOT por query (confirmado ao vivo:
        CLIENT.NotOperatorMaxNumber acima disso).
        """
        q = self._escape_cql_term(query)
        cql = f'(ti="{q}" or ab="{q}") and pd within "{start_date.strftime("%Y%m%d")},{end_date.strftime("%Y%m%d")}"'
        excl_terms = [self._escape_cql_term(t) for t in (exclusions or []) if t]
        if excl_terms:
            grouped = " or ".join(f'ab="{t}"' for t in excl_terms)
            cql += f' not ({grouped})'
        return cql

    def _normalize_ipc(self, raw_text: str) -> str:
        match = self._IPC_TEXT_PATTERN.search(raw_text or "")
        if not match:
            return (raw_text or "").strip()
        section_class_subclass, main_group, subgroup = match.groups()
        return f"{section_class_subclass}{main_group}/{subgroup}"

    def _parse_exchange_document(self, doc_el) -> Optional[Dict[str, Any]]:
        """Extrai os campos usados pelo pipeline (mesmo formato de fetch_patents_mock) de um <exchange-document> do EPO OPS."""
        NS = self._NS
        country = doc_el.get("country", "")
        doc_number = doc_el.get("doc-number", "")
        kind = doc_el.get("kind", "")
        if not (country and doc_number and kind):
            return None
        patent_id = f"{country}{doc_number}{kind}"
        family_id = doc_el.get("family-id") or patent_id

        biblio = doc_el.find("ex:bibliographic-data", NS)
        if biblio is None:
            return None

        titles = biblio.findall("ex:invention-title", NS)
        title = ""
        for t in titles:
            if t.get("lang") == "en" and (t.text or "").strip():
                title = t.text.strip()
                break
        if not title:
            for t in titles:
                if (t.text or "").strip():
                    title = t.text.strip()
                    break

        publication_date = ""
        for doc_id in biblio.findall("ex:publication-reference/ex:document-id", NS):
            if doc_id.get("document-id-type") == "docdb":
                d = doc_id.findtext("ex:date", default="", namespaces=NS)
                if d:
                    publication_date = f"{d[0:4]}-{d[4:6]}-{d[6:8]}"
                break

        filing_year: Optional[int] = None
        for doc_id in biblio.findall("ex:application-reference/ex:document-id", NS):
            d = doc_id.findtext("ex:date", default="", namespaces=NS)
            if d and len(d) >= 4:
                filing_year = int(d[0:4])
                break
        if filing_year is None:
            for doc_id in biblio.findall("ex:priority-claims/ex:priority-claim/ex:document-id", NS):
                d = doc_id.findtext("ex:date", default="", namespaces=NS)
                if d and len(d) >= 4:
                    filing_year = int(d[0:4])
                    break
        if filing_year is None and publication_date:
            filing_year = int(publication_date[0:4])

        assignee = ""
        applicants = biblio.findall("ex:parties/ex:applicants/ex:applicant", NS)
        for a in applicants:
            if a.get("data-format") == "epodoc":
                name = a.findtext("ex:applicant-name/ex:name", default="", namespaces=NS)
                if name:
                    assignee = name.strip()
                    break
        if not assignee and applicants:
            assignee = (applicants[0].findtext("ex:applicant-name/ex:name", default="", namespaces=NS) or "").strip()

        ipc_code = ""
        first_ipc = biblio.find("ex:classifications-ipcr/ex:classification-ipcr/ex:text", NS)
        if first_ipc is not None and first_ipc.text:
            ipc_code = self._normalize_ipc(first_ipc.text)

        return {
            "patent_id": patent_id,
            "family_id": family_id,
            "title": title,
            "ipc_code": ipc_code,
            "filing_year": filing_year or 9999,
            "assignee": assignee,
            "publication_date": publication_date,
        }

    def fetch_patents_live(self, query: str, exclusions: Optional[List[str]] = None, days: Optional[int] = None) -> Dict[str, Any]:
        """
        Busca patentes REAIS no EPO OPS via CQL, restrita à janela dos
        últimos `days` dias (default SEARCH_WINDOW_DAYS, publication_date),
        com as exclusões do ativo aplicadas na origem e deduplicação por
        família de patentes (INPADOC family-id retornado pelo próprio OPS).
        Resultados são cacheados em disco (CACHE_DIR) por CACHE_TTL_DAYS dias,
        chaveados pela query CQL exata + janela - reexecuções dentro desse
        prazo não geram nova chamada de rede nem consomem quota.

        Resiliente a falha de rede/timeout/quota: retorna uma resposta vazia
        e degradada (nunca propaga a exceção, nunca cai para dado mock) -
        mesmo contrato de connectors.pubmed.PubMedConnector.search_articles().
        Erro de CONFIGURAÇÃO (credenciais ausentes) é a única exceção que
        propaga - ver fetch_patents().
        """
        start_date, end_date = self._search_window(days=days)
        date_range = {"start": start_date.isoformat(), "end": end_date.isoformat()}
        full_query = self._build_cql_query(query, exclusions, start_date, end_date)
        query_hash = self._query_hash(full_query)

        cache_key = f"{full_query}|Range=1-{self.OPS_MAX_RESULTS}"
        cached = self._cache_read(cache_key)
        if cached is not None:
            return {**cached, "query": full_query, "query_hash": query_hash, "date_range": date_range, "from_cache": True}

        url = f"{self.OPS_SEARCH_URL}?q={urllib.parse.quote(full_query)}"
        try:
            raw = self._throttled_ops_request(url)
        except ConnectorRequestError as e:
            return {
                "query": full_query, "query_hash": query_hash, "results": [], "total_found": 0,
                "total_after_dedup": 0, "duplicate_families_collapsed": 0, "date_range": date_range,
                "error": str(e), "source": "EPO_OPS_LIVE"
            }

        if b"SERVER.EntityNotFound" in raw:
            payload = {"results": [], "total_found": 0, "total_after_dedup": 0, "duplicate_families_collapsed": 0, "source": "EPO_OPS_LIVE"}
            self._cache_write(cache_key, payload)
            return {**payload, "query": full_query, "query_hash": query_hash, "date_range": date_range}

        try:
            root = ET.fromstring(raw)
        except ET.ParseError as e:
            return {
                "query": full_query, "query_hash": query_hash, "results": [], "total_found": 0,
                "total_after_dedup": 0, "duplicate_families_collapsed": 0, "date_range": date_range,
                "error": f"Falha ao interpretar XML do EPO OPS: {e}", "source": "EPO_OPS_LIVE"
            }

        biblio_search = root.find("ops:biblio-search", self._NS)
        total_found = int(biblio_search.get("total-result-count", "0")) if biblio_search is not None else 0

        raw_patents = [
            parsed for doc_el in root.findall(".//ex:exchange-document", self._NS)
            for parsed in [self._parse_exchange_document(doc_el)] if parsed is not None
        ]
        deduped_results = self._dedupe_by_family(raw_patents)

        payload = {
            "results": deduped_results,
            "total_found": total_found,
            "total_after_dedup": len(deduped_results),
            "duplicate_families_collapsed": len(raw_patents) - len(deduped_results),
            "source": "EPO_OPS_LIVE"
        }
        self._cache_write(cache_key, payload)
        return {**payload, "query": full_query, "query_hash": query_hash, "date_range": date_range}

    def fetch_patents(self, query: str, exclusions: Optional[List[str]] = None, days: Optional[int] = None) -> Dict[str, Any]:
        """
        Ponto de entrada de produção. Exige credenciais reais do EPO OPS
        (EPO_OPS_CONSUMER_KEY/EPO_OPS_CONSUMER_SECRET em .env) - levanta
        PatentConnectorConfigError se ausentes, nunca cai silenciosamente
        para fetch_patents_mock() (base fabricada). Use fetch_patents_mock()
        diretamente e explicitamente para desenvolvimento/teste offline.
        """
        self._ops_credentials()  # valida cedo - erro de configuração explícito, não uma busca vazia
        return self.fetch_patents_live(query, exclusions=exclusions, days=days)

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

    def validate_patent(self, patent_id: str, asset_canonical_name: str) -> Dict[str, Any]:
        """
        Validação determinística pré-relatório de um número de patente: busca
        a página pública canônica do Google Patents (GOOGLE_PATENTS_URL) e
        confirma programaticamente (a) que o documento existe (HTTP 200,
        título extraído da página) e (b) que o título contém a entidade do
        ativo pesquisado (Entity Resolution, core/entity_resolver.py).
        SE A VALIDAÇÃO FALHAR (documento inexistente, timeout/erro de rede
        após as tentativas, ou entidade não confirmada no título): retorna
        valid=False - o chamador (main.py) deve remover estritamente o
        patent_id do relatório. Nunca lança exceção (fail-closed).
        """
        last_error: Optional[str] = None
        html: Optional[str] = None
        url = self.GOOGLE_PATENTS_URL.format(patent_id=patent_id)

        for attempt in range(self.VALIDATION_MAX_RETRIES):
            try:
                req = urllib.request.Request(url, headers={'User-Agent': 'ActivesPredict/1.0'})
                with urllib.request.urlopen(req, timeout=self.VALIDATION_TIMEOUT) as response:
                    html = response.read().decode('utf-8', errors='ignore')
                break
            except urllib.error.HTTPError as e:
                last_error = f"HTTP {e.code}"
                break  # 404/etc. não vale retry - o documento simplesmente não existe nesse número
            except (urllib.error.URLError, socket.timeout, TimeoutError) as e:
                last_error = str(e)
                if attempt < self.VALIDATION_MAX_RETRIES - 1:
                    time.sleep(1.0 * (attempt + 1))
                    continue

        if html is None:
            return {
                "patent_id": patent_id, "valid": False, "exists": False, "entity_confirmed": False,
                "reason": f"Patente {patent_id} não pôde ser confirmada no Google Patents: {last_error}",
                "title": ""
            }

        title_match = self._TITLE_TAG_PATTERN.search(html)
        page_title = title_match.group(1).strip() if title_match else ""
        exists = bool(page_title) and patent_id.upper() in html.upper()

        if not exists:
            return {
                "patent_id": patent_id, "valid": False, "exists": False, "entity_confirmed": False,
                "reason": f"Patente {patent_id} não encontrada no Google Patents (documento inexistente)",
                "title": page_title
            }

        resolved_entity = self.resolver.resolve(page_title)
        entity_confirmed = resolved_entity is not None

        if not entity_confirmed:
            return {
                "patent_id": patent_id, "valid": False, "exists": True, "entity_confirmed": False,
                "reason": f"Título da patente {patent_id} não confirma a entidade '{asset_canonical_name}'",
                "title": page_title
            }

        return {"patent_id": patent_id, "valid": True, "exists": True, "entity_confirmed": True, "reason": None, "title": page_title}

    def validate_patent_batch(self, patent_ids: List[str], asset_canonical_name: str) -> Tuple[List[str], List[Dict[str, Any]]]:
        """Valida uma lista de patent_ids (mesmo contrato de connectors.pubmed_validator.PMIDValidator.validate_batch)."""
        valid_ids: List[str] = []
        rejected: List[Dict[str, Any]] = []
        for patent_id in patent_ids or []:
            result = self.validate_patent(patent_id, asset_canonical_name)
            if result["valid"]:
                valid_ids.append(patent_id)
            else:
                rejected.append(result)
        return valid_ids, rejected


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()

    resolver = EntityResolver(taxonomy_path="data/taxonomy/ativos_mvp.json")
    patent_conn = PatentConnector(resolver=resolver)

    asset = next(a for a in resolver.assets if a["asset_id"] == "AT-029")
    print(f"Buscando Patentes REAIS (EPO OPS) por '{asset['canonical_name']}' (exclusions={asset['exclusions']})...")

    try:
        search_result = patent_conn.fetch_patents("Cannabis sativa", exclusions=asset["exclusions"], days=patent_conn.TRACTION_WINDOW_DAYS)
    except PatentConnectorConfigError as e:
        print(f"\n[!] {e}\nCaindo para fetch_patents_mock() só para esta demonstração offline.")
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
        print(f"Link: {patent_conn.GOOGLE_PATENTS_URL.format(patent_id=processed['patent_id'])}")
        print(f"Entidade Resolvida: {processed['entity_match']}")
