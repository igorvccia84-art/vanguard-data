import sys
import io
from typing import List, Dict, Any

from core.entity_resolver import EntityResolver

if sys.stdout.encoding != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')


class RegulatoryComexConnector:
    """
    Conector para dados Regulatórios (Anvisa/FDA) e Comércio Exterior (Comex Stat/HS Codes).
    Avalia o Risco de Oferta e conformidade dos ativos.
    """

    def __init__(self, resolver: EntityResolver):
        self.resolver = resolver

    def fetch_regulatory_status(self, asset_id: str) -> Dict[str, Any]:
        """
        Simula a verificação do status regulatório do ativo em bancos oficiais (Ex: Anvisa / CosIng).
        """
        # Base de conhecimento de status regulatórios
        registry = {
            "AT-001": {
                "status": "APROVADO_USO_TOPICO",
                "restriction_level": "BAIXO",
                "max_concentration_allowed": "2.0%",
                "alerts": []
            },
            "AT-002": {
                "status": "APROVADO_USO_TOPICO",
                "restriction_level": "BAIXO",
                "max_concentration_allowed": "1.0%",
                "alerts": []
            },
            "AT-003": {
                "status": "APROVADO_USO_TOPICO",
                "restriction_level": "NENHUM",
                "max_concentration_allowed": "10.0%",
                "alerts": []
            }
        }

        return registry.get(asset_id, {
            "status": "EM_ANALISE",
            "restriction_level": "DESCONHECIDO",
            "max_concentration_allowed": "N/A",
            "alerts": ["Ativo não mapeado na base regulatória local"]
        })

    def fetch_import_volume_mock(self, hs_code: str) -> Dict[str, Any]:
        """
        Simula a consulta de volumes de importação via código NCM/HS no Comex Stat.
        """
        if not hs_code:
            return {"volume_usd": 0, "trend": "NEUTRO", "suppliers_count": 0}

        # Simulação de volume comercial por NCM
        return {
            "hs_code": hs_code,
            "volume_usd_annual": 1250000,
            "trend": "CRESCENTE",
            "suppliers_count": 8,
            "risk_score": "BAIXO_RISCO"
        }


if __name__ == "__main__":
    resolver = EntityResolver(taxonomy_path="data/taxonomy/ativos_mvp.json")
    comex_conn = RegulatoryComexConnector(resolver=resolver)

    print("--- Teste de Integração Regulatório/Comex ---")
    reg_data = comex_conn.fetch_regulatory_status("AT-001")
    trade_data = comex_conn.fetch_import_volume_mock("1302.19.99")

    print(f"Status Regulatório (Bakuchiol): {reg_data['status']}")
    print(f"Nível de Restrição:            {reg_data['restriction_level']}")
    print(f"Tendência de Importação:       {trade_data['trend']}")
