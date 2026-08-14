"""
View derivada da taxonomia canônica (data/taxonomy/ativos_mvp.json).
Não editar diretamente — a fonte única de verdade é o arquivo JSON.
"""
import json
import os

_TAXONOMY_PATH = os.path.join(os.path.dirname(__file__), "data", "taxonomy", "ativos_mvp.json")


def load_mock_assets():
    with open(_TAXONOMY_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


MOCK_ASSETS = load_mock_assets()


if __name__ == "__main__":
    print(f"{len(MOCK_ASSETS)} ativos carregados de {_TAXONOMY_PATH}")
