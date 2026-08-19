import sys
import io
from typing import Any, Dict, List

if sys.stdout.encoding != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# Fração máxima tolerada de ativos com dados de comércio IDÊNTICOS entre dois
# países declarantes (ex.: Portugal vs. Espanha) antes de a trava de sanidade
# bloquear a geração do PDF. Acima de 10% dos ativos com fornecedores/
# tendência/volume idênticos entre dois mercados distintos é evidência de um
# bug de acurácia regional (bucket genérico "EU" reaproveitado entre países -
# ver connectors/trade_eurostat.py), não de uma coincidência de mercado real.
MAX_IDENTICAL_TRADE_FRACTION = 0.10


class TradeDataSanityError(RuntimeError):
    """
    Levantada quando a trava de sanidade detecta dados de comércio exterior
    suspeitosamente idênticos entre dois países declarantes distintos - sinal
    de que o pipeline está reaproveitando a mesma consulta/seed entre
    mercados que deveriam divergir. Bloqueia estritamente a geração do PDF
    (main.py deve propagar esta exceção e encerrar a execução sem produzir
    relatório) até a causa raiz ser corrigida.
    """


def _trade_signature(trade_data: Dict[str, Any]) -> tuple:
    """Assinatura comparável dos campos de comércio que devem divergir entre países declarantes distintos."""
    return (
        trade_data.get("suppliers_count"),
        trade_data.get("trend"),
        trade_data.get("volume_usd_annual"),
    )


def check_cross_country_trade_divergence(
    trade_data_by_country: Dict[str, Dict[str, Dict[str, Any]]],
    threshold: float = MAX_IDENTICAL_TRADE_FRACTION
) -> Dict[str, Any]:
    """
    Compara os dados de comércio exterior (connectors/trade_eurostat.py)
    coletados para o MESMO conjunto de ativos em países declarantes
    diferentes (ex.: {"PT": {asset_id: trade_dict, ...}, "ES": {...}}).

    Para cada par de países fornecido, conta a fração de ativos cujos dados
    de comércio (fornecedores, tendência, volume anual) são exatamente
    idênticos entre os dois países. Se essa fração exceder `threshold`
    (default 10%) para QUALQUER par, levanta TradeDataSanityError e bloqueia
    a geração do PDF - dados de comércio verdadeiramente específicos por
    país têm baixíssima probabilidade de colidir em mais de 10% dos ativos
    por acaso (o espaço de valores possíveis é grande: 1-12 fornecedores x 3
    tendências x ~2M variações de volume).

    Retorna um relatório de auditoria (fração idêntica por par de países,
    lista de asset_ids colidentes) quando a checagem passa, para log no
    console (main.py).
    """
    countries = sorted(trade_data_by_country.keys())
    report: Dict[str, Any] = {"pairs": {}, "passed": True}

    for i in range(len(countries)):
        for j in range(i + 1, len(countries)):
            country_a, country_b = countries[i], countries[j]
            data_a = trade_data_by_country[country_a]
            data_b = trade_data_by_country[country_b]

            shared_asset_ids = sorted(set(data_a.keys()) & set(data_b.keys()))
            if not shared_asset_ids:
                continue

            identical_asset_ids = [
                asset_id for asset_id in shared_asset_ids
                if _trade_signature(data_a[asset_id]) == _trade_signature(data_b[asset_id])
            ]
            fraction = len(identical_asset_ids) / len(shared_asset_ids)

            pair_key = f"{country_a}_vs_{country_b}"
            report["pairs"][pair_key] = {
                "total_assets_compared": len(shared_asset_ids),
                "identical_assets": identical_asset_ids,
                "identical_fraction": round(fraction, 4)
            }

            if fraction > threshold:
                report["passed"] = False
                raise TradeDataSanityError(
                    f"Dados de comércio exterior suspeitosamente idênticos entre {country_a} e {country_b}: "
                    f"{len(identical_asset_ids)}/{len(shared_asset_ids)} ativos ({fraction:.1%}) têm fornecedores/"
                    f"tendência/volume EXATAMENTE iguais, acima do limite de {threshold:.0%}. "
                    f"Geração de PDF BLOQUEADA - verifique connectors/trade_eurostat.py "
                    f"(reporter_code deve ser específico do país, nunca um bucket regional genérico). "
                    f"Ativos colidentes: {identical_asset_ids}"
                )

    return report


if __name__ == "__main__":
    print("--- Teste: dados de comércio corretamente divergentes (deve passar) ---")
    ok_data = {
        "PT": {"AT-001": {"suppliers_count": 3, "trend": "CRESCENTE", "volume_usd_annual": 500_000}},
        "ES": {"AT-001": {"suppliers_count": 7, "trend": "ESTAVEL", "volume_usd_annual": 1_200_000}},
    }
    result = check_cross_country_trade_divergence(ok_data)
    print(f"Passou: {result['passed']} | Relatório: {result['pairs']}")

    print("\n--- Teste: dados idênticos entre PT e ES (deve levantar TradeDataSanityError) ---")
    bad_data = {
        "PT": {
            "AT-001": {"suppliers_count": 5, "trend": "CRESCENTE", "volume_usd_annual": 900_000},
            "AT-002": {"suppliers_count": 5, "trend": "CRESCENTE", "volume_usd_annual": 900_000},
        },
        "ES": {
            "AT-001": {"suppliers_count": 5, "trend": "CRESCENTE", "volume_usd_annual": 900_000},
            "AT-002": {"suppliers_count": 5, "trend": "CRESCENTE", "volume_usd_annual": 900_000},
        },
    }
    try:
        check_cross_country_trade_divergence(bad_data)
    except TradeDataSanityError as e:
        print(f"OK - exceção levantada conforme esperado:\n{e}")
