import sqlite3
import json
import sys
import io
from typing import Dict, Any, List

if sys.stdout.encoding != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')


class DatabaseManager:
    """
    Gerenciador de Persistência do Vanguard Data.
    Grava os resultados consolidados em um banco de dados SQLite local.
    """

    def __init__(self, db_path: str = "data/vanguard_data.db"):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        """Cria as tabelas necessárias se não existirem."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS asset_evaluations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    asset_id TEXT NOT NULL,
                    canonical_name TEXT NOT NULL,
                    scientific_traction TEXT NOT NULL,
                    industrial_traction TEXT NOT NULL,
                    supply_risk TEXT NOT NULL,
                    confidence_level TEXT NOT NULL,
                    total_evidences INTEGER NOT NULL,
                    processed_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.commit()

    def save_evaluation(self, asset_id: str, canonical_name: str, assessment: Dict[str, Any]):
        """Salva uma avaliação completa de um ativo no banco."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO asset_evaluations
                (asset_id, canonical_name, scientific_traction, industrial_traction, supply_risk, confidence_level, total_evidences)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                asset_id,
                canonical_name,
                assessment["tracao_cientifica"],
                assessment["tracao_industrial"],
                assessment["risco_oferta"],
                assessment["confianca_sinal"],
                assessment["total_evidencias"]
            ))
            conn.commit()

    def fetch_all_evaluations(self) -> List[Dict[str, Any]]:
        """Busca o histórico de todas as avaliações salvas."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM asset_evaluations ORDER BY processed_at DESC")
            rows = cursor.fetchall()
            return [dict(row) for row in rows]


if __name__ == "__main__":
    db = DatabaseManager()
    print("Banco de dados 'data/vanguard_data.db' inicializado e pronto para uso!")
