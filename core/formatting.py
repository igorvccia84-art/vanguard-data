from typing import Optional

# Faixa de arredondamento aplicada a qualquer valor financeiro exibido no
# relatório ou injetado em prompts de LLM - elimina a falsa precisão de
# valores exatos (ex.: "USD 1.147.917") vindos de uma base mock/estimada,
# substituindo-a por uma faixa (+-10%) arredondada em milhões/milhares.
_RANGE_MARGIN = 0.10

_MILLION_SUFFIX = {"PT-BR": "Mi", "PT-PT": "Mi", "ES": "M"}
_THOUSAND_SUFFIX = {"PT-BR": "mil", "PT-PT": "mil", "ES": "mil"}

# Conjunção do intervalo ("Entre X ? Y") - CORRIGIDO (2026-08-20, achado de
# auditoria de vazamento de português na prosa ES): estava hardcoded como "e"
# (português) para os 3 idiomas: como este valor formatado é injetado
# diretamente no prompt de core/llm_analysis.py generate_recommendations(), a
# LLM frequentemente copiava o "e" literal para dentro da prosa em espanhol
# (ex.: "Entre USD 1,0 M e USD 1,3 M") em vez do conectivo correto "y".
_RANGE_CONJUNCTION = {"PT-BR": "e", "PT-PT": "e", "ES": "y"}


def _format_magnitude(value: float, lang_key: str) -> str:
    if value >= 1_000_000:
        suffix = _MILLION_SUFFIX.get(lang_key, "Mi")
        return f"USD {value / 1_000_000:.1f}".replace(".", ",") + f" {suffix}"
    if value >= 1_000:
        suffix = _THOUSAND_SUFFIX.get(lang_key, "mil")
        return f"USD {value / 1_000:.0f} {suffix}"
    return f"USD {value:.0f}"


def format_usd_estimate(value: Optional[int], lang: str = "PT-BR") -> str:
    """
    Converte um valor financeiro exato (ex.: volume_usd_annual de
    connectors/regulatory_comex.py) numa faixa arredondada, sem casas
    decimais falsas - ex.: 1_147_917 -> "Entre USD 1,0 Mi e USD 1,3 Mi".
    Nunca deve ser contornado para expor o valor exato em texto do
    relatório ou em prompts enviados à LLM.
    """
    if value is None:
        return "N/A"

    lang_key = lang.upper()
    try:
        value = float(value)
    except (TypeError, ValueError):
        return "N/A"

    if value <= 0:
        return "USD 0"

    low = value * (1 - _RANGE_MARGIN)
    high = value * (1 + _RANGE_MARGIN)

    conjunction = _RANGE_CONJUNCTION.get(lang_key, "e")
    return f"Entre {_format_magnitude(low, lang_key)} {conjunction} {_format_magnitude(high, lang_key)}"


if __name__ == "__main__":
    for v in (1_147_917, 286_290, 999, 0, None):
        print(f"{v} -> {format_usd_estimate(v, lang='PT-BR')} | ES: {format_usd_estimate(v, lang='ES')}")
