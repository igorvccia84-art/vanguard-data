import sqlite3
import json
import sys
import io
import uuid
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional

if sys.stdout.encoding != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

SCHEMA_VERSION = "2.0.0"

DEFAULT_DOMAIN = {
    "domain_code": "DERMOCOSMETICS",
    "domain_name": "Dermocosméticos",
    "description": "Ativos botânicos e ingredientes ativos para a indústria dermocosmética."
}

DEFAULT_PRODUCT = {
    "product_code": "PHYTODEMAND_REPORT",
    "product_name": "PhytoDemand Report",
    "description": "Relatório executivo de inteligência preditiva de ativos botânicos."
}


class DatabaseManager:
    """
    Gerenciador de Persistência do Vanguard Data.

    Schema auditável e multi-nicho: cada execução do pipeline (pipeline_runs)
    fica associada a um domínio de mercado (market_domains) e a um produto de
    relatório (report_products), permitindo rastrear qual versão de schema e
    de modelo gerou cada avaliação de ativo (asset_evaluations) e as fontes de
    evidência que a sustentam (evaluation_evidence_sources).
    """

    def __init__(self, db_path: str = "data/vanguard_data.db"):
        self.db_path = db_path
        self._init_db()
        self._seed_defaults()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _init_db(self):
        """Cria as tabelas necessárias se não existirem."""
        with self._connect() as conn:
            cursor = conn.cursor()

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS market_domains (
                    domain_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    domain_code TEXT NOT NULL UNIQUE,
                    domain_name TEXT NOT NULL,
                    description TEXT,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS report_products (
                    product_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    product_code TEXT NOT NULL UNIQUE,
                    product_name TEXT NOT NULL,
                    domain_id INTEGER NOT NULL REFERENCES market_domains(domain_id),
                    description TEXT,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS pipeline_runs (
                    run_id TEXT PRIMARY KEY,
                    domain_id INTEGER NOT NULL REFERENCES market_domains(domain_id),
                    product_id INTEGER NOT NULL REFERENCES report_products(product_id),
                    schema_version TEXT NOT NULL,
                    model_version TEXT,
                    started_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    completed_at DATETIME,
                    status TEXT NOT NULL DEFAULT 'RUNNING'
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS asset_evaluations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL REFERENCES pipeline_runs(run_id),
                    domain_id INTEGER NOT NULL REFERENCES market_domains(domain_id),
                    asset_id TEXT NOT NULL,
                    canonical_name TEXT NOT NULL,
                    scores TEXT NOT NULL,
                    processed_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS evaluation_evidence_sources (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    evaluation_id INTEGER NOT NULL REFERENCES asset_evaluations(id),
                    source_type TEXT NOT NULL,
                    query_hash TEXT,
                    raw_response_summary TEXT,
                    items_found INTEGER,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)

            conn.commit()

    def _seed_defaults(self):
        """Popula os registros padrão de domínio (DERMOCOSMETICS) e produto (PHYTODEMAND_REPORT)."""
        self.get_or_create_domain(**DEFAULT_DOMAIN)
        domain_id = self.get_domain_id(DEFAULT_DOMAIN["domain_code"])
        self.get_or_create_product(domain_id=domain_id, **DEFAULT_PRODUCT)

    # ------------------------------------------------------------------
    # market_domains / report_products
    # ------------------------------------------------------------------

    def get_or_create_domain(self, domain_code: str, domain_name: str, description: str = None) -> int:
        with self._connect() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT domain_id FROM market_domains WHERE domain_code = ?", (domain_code,))
            row = cursor.fetchone()
            if row:
                return row[0]

            cursor.execute("""
                INSERT INTO market_domains (domain_code, domain_name, description)
                VALUES (?, ?, ?)
            """, (domain_code, domain_name, description))
            conn.commit()
            return cursor.lastrowid

    def get_domain_id(self, domain_code: str) -> Optional[int]:
        with self._connect() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT domain_id FROM market_domains WHERE domain_code = ?", (domain_code,))
            row = cursor.fetchone()
            return row[0] if row else None

    def get_or_create_product(self, product_code: str, product_name: str, domain_id: int, description: str = None) -> int:
        with self._connect() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT product_id FROM report_products WHERE product_code = ?", (product_code,))
            row = cursor.fetchone()
            if row:
                return row[0]

            cursor.execute("""
                INSERT INTO report_products (product_code, product_name, domain_id, description)
                VALUES (?, ?, ?, ?)
            """, (product_code, product_name, domain_id, description))
            conn.commit()
            return cursor.lastrowid

    def get_product_id(self, product_code: str) -> Optional[int]:
        with self._connect() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT product_id FROM report_products WHERE product_code = ?", (product_code,))
            row = cursor.fetchone()
            return row[0] if row else None

    # ------------------------------------------------------------------
    # pipeline_runs
    # ------------------------------------------------------------------

    def start_run(
        self,
        domain_code: str = DEFAULT_DOMAIN["domain_code"],
        product_code: str = DEFAULT_PRODUCT["product_code"],
        model_version: str = None,
        schema_version: str = SCHEMA_VERSION
    ) -> str:
        """Abre uma nova execução do pipeline e retorna o run_id (UUID)."""
        domain_id = self.get_domain_id(domain_code)
        product_id = self.get_product_id(product_code)
        if domain_id is None:
            raise ValueError(f"Domínio de mercado desconhecido: {domain_code}")
        if product_id is None:
            raise ValueError(f"Produto de relatório desconhecido: {product_code}")

        run_id = str(uuid.uuid4())
        with self._connect() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO pipeline_runs (run_id, domain_id, product_id, schema_version, model_version, status)
                VALUES (?, ?, ?, ?, ?, 'RUNNING')
            """, (run_id, domain_id, product_id, schema_version, model_version))
            conn.commit()
        return run_id

    def complete_run(self, run_id: str, status: str = "COMPLETED"):
        """Marca a execução como concluída (ou falha) e registra o timestamp de término."""
        with self._connect() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE pipeline_runs
                SET status = ?, completed_at = ?
                WHERE run_id = ?
            """, (status, datetime.now(timezone.utc).isoformat(), run_id))
            conn.commit()

    # ------------------------------------------------------------------
    # asset_evaluations / evaluation_evidence_sources
    # ------------------------------------------------------------------

    def save_evaluation(
        self,
        run_id: str,
        asset_id: str,
        canonical_name: str,
        scores: Dict[str, Any],
        domain_code: str = DEFAULT_DOMAIN["domain_code"]
    ) -> int:
        """Salva uma avaliação de ativo vinculada a uma execução do pipeline e retorna o evaluation_id."""
        domain_id = self.get_domain_id(domain_code)
        if domain_id is None:
            raise ValueError(f"Domínio de mercado desconhecido: {domain_code}")

        with self._connect() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO asset_evaluations (run_id, domain_id, asset_id, canonical_name, scores)
                VALUES (?, ?, ?, ?, ?)
            """, (run_id, domain_id, asset_id, canonical_name, json.dumps(scores, ensure_ascii=False)))
            conn.commit()
            return cursor.lastrowid

    def save_evidence_source(
        self,
        evaluation_id: int,
        source_type: str,
        query_hash: str = None,
        raw_response_summary: str = None,
        items_found: int = 0
    ) -> int:
        """Registra a origem de evidência (ex.: PUBMED, PATENTS, REGULATORY, TRADE) usada em uma avaliação."""
        with self._connect() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO evaluation_evidence_sources
                (evaluation_id, source_type, query_hash, raw_response_summary, items_found)
                VALUES (?, ?, ?, ?, ?)
            """, (evaluation_id, source_type, query_hash, raw_response_summary, items_found))
            conn.commit()
            return cursor.lastrowid

    def fetch_all_evaluations(self, run_id: str = None) -> List[Dict[str, Any]]:
        """Busca o histórico de avaliações salvas, opcionalmente filtrado por execução."""
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            if run_id:
                cursor.execute(
                    "SELECT * FROM asset_evaluations WHERE run_id = ? ORDER BY processed_at DESC",
                    (run_id,)
                )
            else:
                cursor.execute("SELECT * FROM asset_evaluations ORDER BY processed_at DESC")
            rows = cursor.fetchall()

            evaluations = []
            for row in rows:
                item = dict(row)
                item["scores"] = json.loads(item["scores"])
                evaluations.append(item)
            return evaluations

    def fetch_evidence_sources(self, evaluation_id: int) -> List[Dict[str, Any]]:
        """Busca as fontes de evidência registradas para uma avaliação específica."""
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM evaluation_evidence_sources WHERE evaluation_id = ? ORDER BY created_at",
                (evaluation_id,)
            )
            rows = cursor.fetchall()
            return [dict(row) for row in rows]


if __name__ == "__main__":
    db = DatabaseManager()
    print("Banco de dados 'data/vanguard_data.db' inicializado com o schema auditável e multi-nicho!")
    print(f"  Domínio padrão: {DEFAULT_DOMAIN['domain_code']}")
    print(f"  Produto padrão: {DEFAULT_PRODUCT['product_code']}")
    print(f"  Schema version: {SCHEMA_VERSION}")
