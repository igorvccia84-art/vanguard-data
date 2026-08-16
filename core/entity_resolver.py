import json
import re
from typing import Optional, Dict, Any, List

SCHEMA_VERSION = "2.0.0"

# CAS Registry Number: 2-7 dígitos, hífen, 2 dígitos, hífen, 1 dígito de checksum.
_CAS_PATTERN = re.compile(r'^\d{2,7}-\d{2}-\d$')

# Regras de exclusão globais: aplicadas a todos os ativos para reduzir ruído de
# contextos sistêmicos, veterinários, agrícolas ou industriais que não
# representam aplicação tópica/cosmética (ex.: um ativo botânico estudado como
# ração animal ou fertilizante não deve contar como evidência de skincare).
GLOBAL_EXCLUSIONS: List[str] = [
    "veterinary use",
    "animal feed",
    "agricultural fertilizer",
    "pesticide formulation",
    "wastewater treatment"
]


class EntityResolver:
    """
    Núcleo Auditável de Resolução de Entidades da Vanguard Data.
    Converte sinais brutos e heterogêneos de texto em registros de Ativo
    Canônico padronizados, aplicando regras de exclusão/negativa de busca
    para evitar contagem dupla e ruído fora da aplicação tópica/cosmética.
    """

    REQUIRED_FIELDS = (
        "asset_id", "canonical_name", "inci_name",
        "botanical_or_cas", "chemical_family", "aliases", "exclusions"
    )

    def __init__(self, taxonomy_path: str):
        self.taxonomy_path = taxonomy_path
        self.assets: List[Dict[str, Any]] = []
        self._load_taxonomy()

    def _load_taxonomy(self) -> None:
        """Carrega a taxonomia oficial de ativos a partir do JSON e valida o schema mínimo."""
        with open(self.taxonomy_path, 'r', encoding='utf-8') as f:
            raw_assets = json.load(f)

        for asset in raw_assets:
            missing = [field for field in self.REQUIRED_FIELDS if field not in asset]
            if missing:
                raise ValueError(
                    f"Ativo '{asset.get('asset_id', '?')}' não está em conformidade com o "
                    f"schema {SCHEMA_VERSION}: campos ausentes {missing}"
                )

        self.assets = raw_assets

    def _normalize_text(self, text: str) -> str:
        """Normaliza strings removendo caracteres especiais e convertendo para minúsculo."""
        text = text.lower()
        text = re.sub(r'[^\w\s-]', '', text)
        return text.strip()

    def _standardize_record(self, asset: Dict[str, Any]) -> Dict[str, Any]:
        """Retorna sempre o registro padronizado e limpo do ativo (sem metadados internos)."""
        return {field: asset[field] for field in self.REQUIRED_FIELDS}

    def _is_excluded(self, asset: Dict[str, Any], normalized_input: str) -> bool:
        """Verifica se o texto de entrada cai em algum contexto de exclusão (global ou específico do ativo)."""
        for term in GLOBAL_EXCLUSIONS + asset.get("exclusions", []):
            term_norm = self._normalize_text(term)
            if term_norm and re.search(r'\b' + re.escape(term_norm) + r'\b', normalized_input):
                return True
        return False

    def resolve(self, text: str) -> Optional[Dict[str, Any]]:
        """
        Dada uma string de entrada (ex: título de artigo ou patente), retorna
        sempre o registro canônico padronizado do Ativo correspondente, mesclado
        com os metadados do match (match_type, confidence_score). Textos que
        caem em uma regra de exclusão são tratados como não-match, evitando
        ruído fora da aplicação tópica/cosmética e contagem dupla de evidências.
        """
        normalized_input = self._normalize_text(text)

        for asset in self.assets:
            if self._is_excluded(asset, normalized_input):
                continue

            match_meta = self._match_asset(asset, text, normalized_input)
            if match_meta:
                return {**self._standardize_record(asset), **match_meta}

        # Nenhum ativo reconhecido no texto (ou apenas em contexto excluído)
        return None

    def _match_asset(self, asset: Dict[str, Any], text: str, normalized_input: str) -> Optional[Dict[str, Any]]:
        """Tenta casar um único ativo contra o texto, na ordem de maior precisão."""

        # 1. botanical_or_cas: número CAS (Precisão Exata: 100%) ou nome botânico (95%)
        for identifier in asset.get("botanical_or_cas", []):
            if _CAS_PATTERN.match(identifier):
                if identifier in text:
                    return {"match_type": "CAS_NUMBER", "confidence_score": 1.0}
            else:
                identifier_norm = self._normalize_text(identifier)
                if identifier_norm and identifier_norm in normalized_input:
                    return {"match_type": "BOTANICAL_NAME", "confidence_score": 0.95}

        # 2. Aliases (apelidos, INCI, abreviações de mercado)
        for alias in asset.get("aliases", []):
            alias_norm = self._normalize_text(alias)
            if alias_norm and re.search(r'\b' + re.escape(alias_norm) + r'\b', normalized_input):
                return {"match_type": "ALIAS_MATCH", "matched_alias": alias, "confidence_score": 0.90}

        return None


# Exemplo executável para teste
if __name__ == "__main__":
    resolver = EntityResolver(taxonomy_path="data/taxonomy/ativos_mvp.json")

    # Teste usando nome botânico
    resultado = resolver.resolve("Evaluation of Psoralea corylifolia extract in skin collagen synthesis.")
    print("Resultado do Teste (botânico):", resultado)

    # Teste usando regra de exclusão (contexto sistêmico/oral, não tópico)
    resultado_excluido = resolver.resolve("Tranexamic acid administered intravenously for postpartum hemorrhage.")
    print("Resultado do Teste (excluído):", resultado_excluido)
