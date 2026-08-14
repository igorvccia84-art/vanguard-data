import json
import re
from typing import Optional, Dict, Any, List


class EntityResolver:
    """
    Motor de Resolução de Entidades para a Vanguard Data.
    Converte sinais brutos e heterogêneos de texto em entidades de ativos padronizadas.
    """

    def __init__(self, taxonomy_path: str):
        self.taxonomy_path = taxonomy_path
        self.assets: List[Dict[str, Any]] = []
        self._load_taxonomy()

    def _load_taxonomy(self) -> None:
        """Carrega a taxonomia oficial de ativos a partir do JSON."""
        with open(self.taxonomy_path, 'r', encoding='utf-8') as f:
            self.assets = json.load(f)

    def _normalize_text(self, text: str) -> str:
        """Normaliza strings removendo caracteres especiais e convertendo para minúsculo."""
        text = text.lower()
        text = re.sub(r'[^\w\s-]', '', text)
        return text.strip()

    def resolve(self, text: str) -> Optional[Dict[str, Any]]:
        """
        Dada uma string de entrada (ex: título de artigo ou patente),
        retorna o dicionário do Ativo Canônico correspondente.
        """
        normalized_input = self._normalize_text(text)

        for asset in self.assets:
            # 1. Verifica por CAS Number (Precisão Exata: 100%)
            if asset.get("cas_number") and asset["cas_number"] in text:
                return {
                    "asset_id": asset["asset_id"],
                    "canonical_name": asset["canonical_name"],
                    "match_type": "CAS_NUMBER",
                    "confidence_score": 1.0
                }

            # 2. Verifica por Nome Botânico
            if asset.get("botanical_name"):
                botanical_norm = self._normalize_text(asset["botanical_name"])
                if botanical_norm in normalized_input:
                    return {
                        "asset_id": asset["asset_id"],
                        "canonical_name": asset["canonical_name"],
                        "match_type": "BOTANICAL_NAME",
                        "confidence_score": 0.95
                    }

            # 3. Verifica pela lista de Aliases (Apelidos e INCI)
            for alias in asset.get("aliases", []):
                alias_norm = self._normalize_text(alias)
                if re.search(r'\b' + re.escape(alias_norm) + r'\b', normalized_input):
                    return {
                        "asset_id": asset["asset_id"],
                        "canonical_name": asset["canonical_name"],
                        "match_type": "ALIAS_MATCH",
                        "matched_alias": alias,
                        "confidence_score": 0.90
                    }

        # Nenhum ativo reconhecido no texto
        return None


# Exemplo executável para teste
if __name__ == "__main__":
    resolver = EntityResolver(taxonomy_path="data/taxonomy/ativos_mvp.json")

    # Teste usando nome botânico
    resultado = resolver.resolve("Evaluation of Psoralea corylifolia extract in skin collagen synthesis.")
    print("Resultado do Teste:", resultado)
