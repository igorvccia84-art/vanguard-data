import sys
import io
import hashlib
from typing import Any, Dict, Optional

if sys.stdout.encoding != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')


class TradeEurostatConnector:
    """
    Conector de Comércio Exterior da União Europeia via Eurostat Comext/TARIC.

    CORREÇÃO DE ACURÁCIA REGIONAL: toda consulta exige um `reporter_code`
    (código de país declarante ISO-3166 alpha-2, ex.: 'PT' para Portugal,
    'ES' para Espanha) explicitamente definido pelo mercado-alvo do
    relatório - NUNCA um bucket genérico "EU" agregando todos os países-
    membros. Cada país-membro reporta seu próprio fluxo comercial ao
    TARIC/Comext; tratar Portugal e Espanha como uma única série de
    fornecedores esconde justamente a divergência regional que este
    conector existe para capturar (bug corrigido nesta versão: a versão
    anterior, connectors.regulatory_comex.fetch_import_volume_mock, usava
    `region_key = "EU"` tanto para PT-PT quanto para ES, produzindo dados
    de comércio IDÊNTICOS entre os dois mercados - ver core/sanity_checks.py
    para a trava de sanidade que detecta essa classe de erro).

    Este protótipo não tem credenciais reais da API Eurostat configuradas
    (.env); os valores retornados são determinísticos e MOCK/ilustrativos,
    mas agora corretamente segregados por `reporter_code` - a mesma
    simulação (seed) nunca é reaproveitada entre dois países declarantes
    diferentes.
    """

    # Códigos de país declarante (reporter_code) suportados neste protótipo -
    # ISO-3166 alpha-2, os mesmos códigos usados nas consultas reais ao
    # Eurostat Comext/TARIC (dic. 'geo' / 'reporter').
    VALID_REPORTER_CODES = {"PT", "ES", "FR", "DE", "IT", "NL", "BE"}

    def fetch_trade_data(self, hs_code: Optional[str], reporter_code: str, asset_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Consulta o volume de comércio exterior (mock/ilustrativo) de um
        código NCM/HS para um país declarante específico da União Europeia.
        `reporter_code` é OBRIGATÓRIO e deve ser um código de país
        declarante válido - levanta ValueError se ausente/inválido, para que
        um chamador nunca degrade silenciosamente para uma consulta sem
        jurisdição definida (ver connectors/regulatory_comex.py, que só deve
        chamar este conector já resolvido para 'PT' ou 'ES' pelo idioma do
        relatório).
        """
        if not reporter_code or reporter_code.upper() not in self.VALID_REPORTER_CODES:
            raise ValueError(
                f"reporter_code obrigatório e deve ser um código de país declarante Eurostat/TARIC válido "
                f"(ex.: 'PT', 'ES'); recebido: {reporter_code!r}"
            )
        reporter = reporter_code.upper()

        if not hs_code:
            return {
                "hs_code": None, "reporter_code": reporter, "volume_usd_annual": 0,
                "trend": "NEUTRO", "suppliers_count": 0, "risk_score": "DESCONHECIDO"
            }

        try:
            # Seed determinístico chaveado ESTRITAMENTE por país declarante
            # (nunca por um bucket regional "EU") - é isto que garante que
            # PT e ES produzam séries de comércio divergentes para o mesmo
            # ativo/HS code.
            seed = hashlib.sha256(f"{asset_id or hs_code}|{reporter}".encode("utf-8")).hexdigest()
            suppliers_count = 1 + (int(seed[:8], 16) % 12)  # 1-12 fornecedores
            trend = ["DECRESCENTE", "ESTAVEL", "CRESCENTE"][int(seed[8:10], 16) % 3]
            volume_usd_annual = 150_000 + (int(seed[10:16], 16) % 2_000_000)
            c_trade_concentration = round(1 / suppliers_count, 3)
            v_unit_value_volatility = round((int(seed[16:20], 16) % 100) / 100.0, 2)

            return {
                "hs_code": hs_code,
                "reporter_code": reporter,
                "region": "EU",
                "volume_usd_annual": volume_usd_annual,
                "trend": trend,
                "suppliers_count": suppliers_count,
                "risk_score": "BAIXO_RISCO" if suppliers_count >= 5 else "MONITORAR",
                "c_trade_concentration": c_trade_concentration,
                "v_unit_value_volatility": v_unit_value_volatility
            }
        except Exception as e:
            return {
                "hs_code": hs_code, "reporter_code": reporter, "volume_usd_annual": 0,
                "trend": "NEUTRO", "suppliers_count": 0, "risk_score": "DESCONHECIDO", "error": str(e)
            }


if __name__ == "__main__":
    conn = TradeEurostatConnector()

    print("--- Teste de Divergência de Comércio por País Declarante (Eurostat Comext/TARIC) ---")
    for asset_id, hs_code in [("AT-001", "1302.19.99"), ("AT-029", "1302.19.99")]:
        pt_data = conn.fetch_trade_data(hs_code, reporter_code="PT", asset_id=asset_id)
        es_data = conn.fetch_trade_data(hs_code, reporter_code="ES", asset_id=asset_id)
        print(f"\n[{asset_id}] HS {hs_code}")
        print(f"  PT: fornecedores={pt_data['suppliers_count']} tendencia={pt_data['trend']} volume={pt_data['volume_usd_annual']}")
        print(f"  ES: fornecedores={es_data['suppliers_count']} tendencia={es_data['trend']} volume={es_data['volume_usd_annual']}")
        idêntico = (pt_data['suppliers_count'], pt_data['trend'], pt_data['volume_usd_annual']) == \
                   (es_data['suppliers_count'], es_data['trend'], es_data['volume_usd_annual'])
        print(f"  Dados idênticos entre PT/ES? {idêntico} (esperado: False)")

    print("\n--- Teste de reporter_code ausente/inválido (deve levantar ValueError) ---")
    try:
        conn.fetch_trade_data("1302.19.99", reporter_code=None)
    except ValueError as e:
        print(f"  OK - ValueError levantado conforme esperado: {e}")
