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

# Filtragem de relevância tópica em 3 níveis (contraparte do GLOBAL_EXCLUSIONS,
# que é uma lista negativa). Um match por nome botânico/CAS/alias prova que o
# texto menciona o ativo (Nível 1), mas não prova que o achado é relevante
# para aplicação dermocosmética/nutracêutica - ex.: um artigo sobre
# consolidação de fratura mandibular pode mencionar "Psoralea corylifolia"
# sem ter qualquer relação com skincare. resolve() calcula um relevance_level
# (1/2/3) para todo texto, independente do ativo:
#   Nível 1 - Correspondência de Entidade: só o nome/CAS/alias bate (_match_asset).
#   Nível 2 - Sinal Tópico/Aplicado: o texto também contém um termo geral de
#             contexto dermatológico/nutracêutico (TOPICAL_CONTEXT_TERMS).
#   Nível 3 - Evidência de Produto/Formulação: o texto contém, além disso, um
#             termo de formulação/estudo aplicado (PRODUCT_FORMULATION_TERMS)
#             - evidência mais forte que um simples sinal tópico genérico.
# require_topical_context=True (usado por connectors/pubmed.py) rejeita
# relevance_level < 2, mesmo que o nome do ativo esteja presente. Termos em
# inglês, pois títulos/resumos do PubMed são majoritariamente em inglês.
TOPICAL_CONTEXT_TERMS: List[str] = [
    # dermatologia / pele / uso tópico
    "skin", "skincare", "cutaneous", "dermal", "dermis", "epidermis", "epidermal",
    "dermatolog", "topical", "cosmetic", "cosmeceutical", "keratinocyte", "fibroblast",
    "collagen", "wrinkle", "photoaging", "photo-aging", "photoprotect", "melanin",
    "melanocyte", "pigmentation", "hyperpigmentation", "moisturiz", "anti-aging",
    "antiaging", "acne", "eczema", "psoriasis", "rosacea", "sunscreen", "spf",
    "elastin", "sebum", "sebaceous", "scalp", "skin barrier",
    # nutracêutico / suplementação funcional
    "nutraceutical", "nutricosmetic", "dietary supplement", "functional food",
    "oral supplementation", "antioxidant supplementation"
]

# Evidência de Produto/Formulação (Nível 3) - mais específica que um simples
# termo tópico genérico: indica que o achado vem de um estudo aplicado
# (ensaio clínico, teste em pele/tecido) ou de uma composição/formulação real,
# não apenas de um artigo que menciona a palavra "pele" de passagem.
PRODUCT_FORMULATION_TERMS: List[str] = [
    "formulation", "emulsion", "serum", "lotion", "cream base", "cosmetic product",
    "finished product", "clinical trial", "randomized controlled trial",
    "double-blind", "human subjects", "in vivo", "ex vivo", "clinical study",
    "efficacy study", "skin wound", "cutaneous wound", "skin equivalent",
    "reconstructed skin", "skin explant", "patch test", "dermatologically tested"
]


def _build_context_pattern(terms: List[str]) -> "re.Pattern":
    """
    Compila os termos de um vocabulário de contexto num único padrão, com
    fronteira de palavra só na abertura de cada termo (não no fechamento),
    para que radicais como "dermatolog"/"moisturiz"/"photoprotect" casem
    também com suas flexões (dermatology/dermatological, moisturizing,
    photoprotective...) sem deixar de bloquear falsos positivos no meio de
    outra palavra.
    """
    return re.compile(r'\b(' + '|'.join(re.escape(term) for term in terms) + r')', re.IGNORECASE)


_TOPICAL_CONTEXT_PATTERN = _build_context_pattern(TOPICAL_CONTEXT_TERMS)
_PRODUCT_FORMULATION_PATTERN = _build_context_pattern(PRODUCT_FORMULATION_TERMS)


class EntityResolver:
    """
    Núcleo Auditável de Resolução de Entidades da Actives Predict.
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

    @staticmethod
    def _relevance_level(text: str) -> int:
        """
        Calcula o Nível de relevância tópica (1/2/3) de um texto, independente
        de qual ativo eventualmente casar nele:
          1 - nenhum termo de contexto tópico/produto encontrado (relevância
              não pode ser confirmada além da simples correspondência de nome).
          2 - contém termo de TOPICAL_CONTEXT_TERMS (sinal tópico/aplicado).
          3 - contém termo de TOPICAL_CONTEXT_TERMS *e* de
              PRODUCT_FORMULATION_TERMS (evidência de produto/formulação).
        """
        has_topical = bool(_TOPICAL_CONTEXT_PATTERN.search(text))
        has_product = bool(_PRODUCT_FORMULATION_PATTERN.search(text))
        if has_topical and has_product:
            return 3
        if has_topical:
            return 2
        return 1

    def resolve(self, text: str, require_topical_context: bool = False) -> Optional[Dict[str, Any]]:
        """
        Dada uma string de entrada (ex: título de artigo ou patente), retorna
        sempre o registro canônico padronizado do Ativo correspondente, mesclado
        com os metadados do match (match_type, confidence_score, relevance_level).
        Textos que caem em uma regra de exclusão são tratados como não-match,
        evitando ruído fora da aplicação tópica/cosmética e contagem dupla de
        evidências.

        Todo match recebe um `relevance_level` (1/2/3, ver _relevance_level) -
        Nível 1 (Correspondência de Entidade), Nível 2 (Sinal Tópico/Aplicado)
        ou Nível 3 (Evidência de Produto/Formulação). `require_topical_context=True`
        reforça o filtro: o texto precisa atingir ao menos o Nível 2 para o
        match ser aceito - caso contrário, é tratado como não-match mesmo que o
        nome do ativo esteja presente (ex.: um artigo sobre fratura mandibular
        que menciona "Psoralea corylifolia" não deve contar como evidência
        para Bakuchiol). Usado por connectors/pubmed.py para título+resumo de
        artigos, onde o risco de contexto irrelevante é maior do que em
        títulos de patente (connectors/patents.py não usa esse gate, mas ainda
        recebe o relevance_level calculado, sem filtro).
        """
        normalized_input = self._normalize_text(text)
        relevance_level = self._relevance_level(normalized_input)

        if require_topical_context and relevance_level < 2:
            return None

        for asset in self.assets:
            if self._is_excluded(asset, normalized_input):
                continue

            match_meta = self._match_asset(asset, text, normalized_input)
            if match_meta:
                return {**self._standardize_record(asset), **match_meta, "relevance_level": relevance_level}

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

    # Teste dos 3 níveis de relevância tópica
    texto_nivel1 = "A brief note on Psoralea corylifolia taxonomy and distribution."
    texto_nivel2 = "Psoralea corylifolia extract shows anti-aging effects on skin collagen."
    texto_nivel3 = "A randomized controlled clinical trial of a topical emulsion formulation containing Psoralea corylifolia extract for skin wrinkle reduction."
    for label, texto in [("Nível 1 esperado", texto_nivel1), ("Nível 2 esperado", texto_nivel2), ("Nível 3 esperado", texto_nivel3)]:
        resultado = resolver.resolve(texto)
        print(f"{label}: relevance_level={resultado['relevance_level'] if resultado else None}")
