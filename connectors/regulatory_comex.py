import sys
import io
import hashlib
from typing import List, Dict, Any

from core.entity_resolver import EntityResolver

if sys.stdout.encoding != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')


class RegulatoryComexConnector:
    """
    Conector para dados Regulatórios (Anvisa/FDA) e Comércio Exterior (Comex Stat/HS Codes).
    Avalia o Risco de Oferta e conformidade dos ativos.
    """

    # Base de conhecimento de status regulatórios (30 ativos dermocosméticos).
    # Níveis atribuídos por categoria de risco regulatório real e conhecida do ativo
    # (ex.: ácido glicirrízico no alcaçuz, hidroquinona-precursores na arbutina,
    # derivados de cannabis) — não são valores oficiais consultados em tempo real.
    REGULATORY_REGISTRY = {
        "AT-001": {"status": "APROVADO_USO_TOPICO", "restriction_level": "BAIXO", "max_concentration_allowed": "2.0%", "alerts": []},
        "AT-002": {"status": "APROVADO_USO_TOPICO", "restriction_level": "BAIXO", "max_concentration_allowed": "5.0%", "alerts": []},
        "AT-003": {"status": "APROVADO_USO_TOPICO", "restriction_level": "NENHUM", "max_concentration_allowed": "100%", "alerts": []},
        "AT-004": {"status": "APROVADO_USO_TOPICO", "restriction_level": "MEDIO", "max_concentration_allowed": "0.5%", "alerts": ["Requer dossiê de segurança complementar"]},
        "AT-005": {"status": "APROVADO_USO_TOPICO", "restriction_level": "NENHUM", "max_concentration_allowed": "100%", "alerts": []},
        "AT-006": {"status": "APROVADO_USO_TOPICO", "restriction_level": "BAIXO", "max_concentration_allowed": "3.0%", "alerts": []},
        "AT-007": {"status": "APROVADO_USO_TOPICO", "restriction_level": "BAIXO", "max_concentration_allowed": "5.0%", "alerts": []},
        "AT-008": {"status": "APROVADO_USO_TOPICO", "restriction_level": "MEDIO", "max_concentration_allowed": "0.1%", "alerts": ["Limite de concentração por potencial irritante (spilanthol)"]},
        "AT-009": {"status": "APROVADO_USO_TOPICO", "restriction_level": "NENHUM", "max_concentration_allowed": "100%", "alerts": []},
        "AT-010": {"status": "APROVADO_USO_TOPICO", "restriction_level": "NENHUM", "max_concentration_allowed": "100%", "alerts": []},
        "AT-011": {"status": "APROVADO_USO_TOPICO", "restriction_level": "NENHUM", "max_concentration_allowed": "100%", "alerts": []},
        "AT-012": {"status": "APROVADO_USO_TOPICO", "restriction_level": "BAIXO", "max_concentration_allowed": "10.0%", "alerts": []},
        "AT-013": {"status": "APROVADO_USO_TOPICO", "restriction_level": "BAIXO", "max_concentration_allowed": "5.0%", "alerts": []},
        "AT-014": {"status": "APROVADO_USO_TOPICO", "restriction_level": "BAIXO", "max_concentration_allowed": "3.0%", "alerts": []},
        "AT-015": {"status": "APROVADO_USO_TOPICO", "restriction_level": "BAIXO", "max_concentration_allowed": "5.0%", "alerts": []},
        "AT-016": {"status": "APROVADO_USO_TOPICO", "restriction_level": "MEDIO", "max_concentration_allowed": "1.0%", "alerts": ["Monitoramento de pureza da resina"]},
        "AT-017": {"status": "APROVADO_USO_TOPICO", "restriction_level": "NENHUM", "max_concentration_allowed": "100%", "alerts": []},
        "AT-018": {"status": "APROVADO_USO_TOPICO", "restriction_level": "NENHUM", "max_concentration_allowed": "100%", "alerts": []},
        "AT-019": {"status": "USO_RESTRITO", "restriction_level": "ALTO", "max_concentration_allowed": "0.02%", "alerts": ["Limite regulatório de ácido glicirrízico (Anvisa/EU)"]},
        "AT-020": {"status": "APROVADO_USO_TOPICO", "restriction_level": "NENHUM", "max_concentration_allowed": "100%", "alerts": []},
        "AT-021": {"status": "APROVADO_USO_TOPICO", "restriction_level": "NENHUM", "max_concentration_allowed": "100%", "alerts": []},
        "AT-022": {"status": "APROVADO_USO_TOPICO", "restriction_level": "NENHUM", "max_concentration_allowed": "100%", "alerts": []},
        "AT-023": {"status": "APROVADO_USO_TOPICO", "restriction_level": "NENHUM", "max_concentration_allowed": "100%", "alerts": []},
        "AT-024": {"status": "APROVADO_USO_TOPICO", "restriction_level": "BAIXO", "max_concentration_allowed": "5.0%", "alerts": ["Rotulagem obrigatória de alérgeno (derivado de trigo)"]},
        "AT-025": {"status": "APROVADO_USO_TOPICO", "restriction_level": "BAIXO", "max_concentration_allowed": "100%", "alerts": []},
        "AT-026": {"status": "USO_RESTRITO", "restriction_level": "ALTO", "max_concentration_allowed": "2.0%", "alerts": ["Precursor de hidroquinona - escrutínio regulatório elevado (clareadores)"]},
        "AT-027": {"status": "APROVADO_USO_TOPICO", "restriction_level": "NENHUM", "max_concentration_allowed": "100%", "alerts": []},
        "AT-028": {"status": "APROVADO_USO_TOPICO", "restriction_level": "NENHUM", "max_concentration_allowed": "100%", "alerts": []},
        "AT-029": {"status": "USO_RESTRITO", "restriction_level": "ALTO", "max_concentration_allowed": "0.2%", "alerts": ["Derivado de Cannabis sativa - barreiras regulatórias de importação/registro"]},
        "AT-030": {"status": "APROVADO_USO_TOPICO", "restriction_level": "BAIXO", "max_concentration_allowed": "10.0%", "alerts": []},
    }

    def __init__(self, resolver: EntityResolver):
        self.resolver = resolver

    def fetch_regulatory_status(self, asset_id: str) -> Dict[str, Any]:
        """
        Simula a verificação do status regulatório do ativo em bancos oficiais (Ex: Anvisa / CosIng).
        """
        return self.REGULATORY_REGISTRY.get(asset_id, {
            "status": "EM_ANALISE",
            "restriction_level": "DESCONHECIDO",
            "max_concentration_allowed": "N/A",
            "alerts": ["Ativo não mapeado na base regulatória local"]
        })

    def fetch_import_volume_mock(self, hs_code: str, asset_id: str = None) -> Dict[str, Any]:
        """
        Simula a consulta de volumes de importação via código NCM/HS no Comex Stat.
        A contagem de fornecedores e a tendência variam deterministicamente por ativo
        (mesmo quando compartilham o mesmo código HS), para refletir que a oferta real
        difere por ativo mesmo dentro da mesma categoria alfandegária.
        """
        if not hs_code:
            return {"volume_usd": 0, "trend": "NEUTRO", "suppliers_count": 0}

        seed = hashlib.sha256((asset_id or hs_code).encode("utf-8")).hexdigest()
        suppliers_count = 1 + (int(seed[:8], 16) % 12)  # 1-12 fornecedores
        trend = ["DECRESCENTE", "ESTAVEL", "CRESCENTE"][int(seed[8:10], 16) % 3]
        volume_usd_annual = 150_000 + (int(seed[10:16], 16) % 2_000_000)

        return {
            "hs_code": hs_code,
            "volume_usd_annual": volume_usd_annual,
            "trend": trend,
            "suppliers_count": suppliers_count,
            "risk_score": "BAIXO_RISCO" if suppliers_count >= 5 else "MONITORAR"
        }


if __name__ == "__main__":
    resolver = EntityResolver(taxonomy_path="data/taxonomy/ativos_mvp.json")
    comex_conn = RegulatoryComexConnector(resolver=resolver)

    print("--- Teste de Variabilidade Regulatório/Comex (30 ativos) ---")
    for asset in resolver.assets:
        asset_id = asset["asset_id"]
        reg = comex_conn.fetch_regulatory_status(asset_id)
        hs_code = asset.get("hs_codes", [None])[0]
        trade = comex_conn.fetch_import_volume_mock(hs_code, asset_id=asset_id)
        print(f"  {asset_id} {asset['canonical_name']:<22} restricao={reg['restriction_level']:<12} fornecedores={trade['suppliers_count']:>2} tendencia={trade['trend']}")
