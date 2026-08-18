import sys
import io
import hashlib
from typing import Dict, Any, Optional

from core.entity_resolver import EntityResolver

if sys.stdout.encoding != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')


class RegulatoryComexConnector:
    """
    Conector para dados Regulatórios (Anvisa/INFARMED/AEMPS/CosIng-ECHA) e
    Comércio Exterior (Comex Stat, Brasil; Eurostat Comext/TARIC, União
    Europeia). CosIng/ECHA é citado exclusivamente para status regulatório,
    funções cosméticas e restrições legais - nunca como fonte de dados
    financeiros/comerciais (volumes, fornecedores, importação), que são
    sempre atribuídos à base de comércio exterior correspondente. Cada
    consulta é auditável:
    a chave de busca exata é hasheada em SHA256, e o dossiê consolidado separa
    claramente os Alertas Regulatórios (conformidade/uso permitido) dos Sinais
    Comerciais/Comex (oferta/importação), para que o Score Engine não confunda
    risco de conformidade com risco de oferta.

    Tanto os dados regulatórios quanto os sinais comerciais/de suprimento são
    calculados conforme a jurisdição do idioma do relatório: PT-BR usa a base
    local (Anvisa/Comex Stat, Brasil); PT-PT e ES compartilham a mesma base da
    União Europeia (INFARMED/AEMPS como autoridade nacional de referência,
    CosIng/ECHA como base regulatória harmonizada sob o Regulamento (CE)
    1223/2009), com EU_REGULATORY_OVERRIDES aplicando os pontos em que a regra
    europeia diverge de fato da brasileira, e uma base de fornecedores
    separada por região (Brasil vs. UE) em fetch_import_volume_mock().
    """

    # Autoridade regulatória de referência e fonte de dados comerciais/comex por
    # idioma do relatório - usadas na atribuição de fonte exibida no PDF (Anvisa e
    # ComexStat para PT-BR; INFARMED/AEMPS e CosIng-ECHA, harmonizados na UE, para
    # PT-PT/ES). Os textos de 'regulatory_body' casam com core/llm_analysis.py
    # (REGULATORY_BODY), usado no prompt de recomendações por idioma.
    REGIONAL_AUTHORITIES = {
        "PT-BR": {
            "regulatory_body": "Anvisa (Agência Nacional de Vigilância Sanitária, Brasil)",
            "trade_source": "Comex Stat (MDIC/SECEX, Brasil)"
        },
        "PT-PT": {
            "regulatory_body": "INFARMED (Autoridade Nacional do Medicamento e Produtos de Saúde, Portugal)",
            "trade_source": "Eurostat Comext / TARIC (dados de comércio exterior da União Europeia)"
        },
        "ES": {
            "regulatory_body": "AEMPS (Agencia Española de Medicamentos y Productos Sanitarios, España)",
            "trade_source": "Eurostat Comext / TARIC (datos de comercio exterior de la Unión Europea)"
        }
    }

    # Base de conhecimento de status regulatórios (35 ativos dermocosméticos,
    # incluindo a classe de Ácidos Cosmecêuticos). Níveis atribuídos por
    # categoria de risco regulatório real e conhecida do ativo (ex.: ácido
    # glicirrízico no alcaçuz, hidroquinona-precursores na arbutina, derivados
    # de cannabis, limites de AHA/BHA leave-on) - não são valores oficiais
    # consultados em tempo real.
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
        "AT-031": {"status": "APROVADO_USO_TOPICO", "restriction_level": "MEDIO", "max_concentration_allowed": "10.0%", "alerts": ["Uso profissional acima de 10% restrito a procedimentos de peeling"]},
        "AT-032": {"status": "USO_RESTRITO", "restriction_level": "MEDIO", "max_concentration_allowed": "2.0%", "alerts": ["Proibido em produtos leave-on para menores de 3 anos (EU 2019/831)"]},
        "AT-033": {"status": "EM_ANALISE", "restriction_level": "ALTO", "max_concentration_allowed": "N/A", "alerts": ["Sem monografia tópica consolidada na Anvisa - uso off-label em skincare"]},
        "AT-034": {"status": "APROVADO_USO_TOPICO", "restriction_level": "BAIXO", "max_concentration_allowed": "10.0%", "alerts": []},
        "AT-035": {"status": "USO_RESTRITO", "restriction_level": "MEDIO", "max_concentration_allowed": "20.0%", "alerts": ["Acima de 10% classificado como uso dermatológico/prescrição em alguns mercados"]},
    }

    # Sobrescreve REGULATORY_REGISTRY apenas para PT-PT/ES (jurisdição UE), e
    # apenas nos ativos cuja regra realmente diverge da base Anvisa - a maioria
    # dos extratos botânicos comuns é tratada de forma equivalente nas duas
    # jurisdições e não precisa de entrada aqui. Referências: Regulamento (CE)
    # 1223/2009 (cosméticos, UE) e seus Anexos II/III, consultáveis via CosIng/ECHA.
    EU_REGULATORY_OVERRIDES = {
        "AT-019": {"status": "USO_RESTRITO", "restriction_level": "MEDIO", "max_concentration_allowed": "N/A (rotulagem obrigatória)", "alerts": ["Sem limite numérico de ácido glicirrízico no Regulamento (CE) 1223/2009 - controlado via rotulagem de alérgenos"]},
        "AT-026": {"status": "USO_RESTRITO", "restriction_level": "ALTO", "max_concentration_allowed": "2.0% (creme facial) / 0.5% (loção corporal)", "alerts": ["Alpha-Arbutin listado no Anexo III, entrada 77, do Regulamento (CE) 1223/2009"]},
        "AT-029": {"status": "USO_RESTRITO", "restriction_level": "ALTO", "max_concentration_allowed": "N/A", "alerts": ["Extratos de folha/flor de Cannabis sativa restritos no Anexo II; derivados de semente (óleo/CBD de cânhamo industrial) avaliados caso a caso pela ECHA"]},
        "AT-031": {"status": "APROVADO_USO_TOPICO", "restriction_level": "BAIXO", "max_concentration_allowed": "pH ≥ 3.5 (sem teto percentual fixo no Anexo III para uso profissional)", "alerts": []},
        "AT-032": {"status": "USO_RESTRITO", "restriction_level": "MEDIO", "max_concentration_allowed": "2.0% (leave-on) / 3.0% (rinse-off)", "alerts": ["Proibido em produtos leave-on para menores de 3 anos - Regulamento (UE) 2019/831, Anexo III entrada 98"]},
        "AT-033": {"status": "EM_ANALISE", "restriction_level": "ALTO", "max_concentration_allowed": "N/A", "alerts": ["Sem entrada específica no CosIng para uso tópico cosmético - uso off-label"]},
        "AT-035": {"status": "APROVADO_USO_TOPICO", "restriction_level": "MEDIO", "max_concentration_allowed": "10.0%", "alerts": ["Concentrações mais altas (uso dermatológico) tratadas como produto medicinal, fora do escopo do Regulamento (CE) 1223/2009"]},
    }

    # Sobrescreve REGULATORY_REGISTRY para a jurisdição FDA (EUA), no mesmo
    # padrão de EU_REGULATORY_OVERRIDES (mesmos ativos de maior sensibilidade
    # regulatória). VALOR ILUSTRATIVO/MOCK PARA FINS DE PROTÓTIPO - assim como
    # o restante desta base de conhecimento, não são valores oficiais
    # consultados em tempo real nem orientação regulatória validada por
    # especialista em regulação da FDA; requer validação por especialista
    # antes de qualquer uso além deste protótipo auditável. Usado por
    # get_regulatory_matrix() para compor a matriz regulatória por jurisdição.
    FDA_REGULATORY_OVERRIDES = {
        "AT-019": {"status": "USO_RESTRITO", "restriction_level": "MEDIO", "max_concentration_allowed": "N/A (sem teto percentual fixo sob o regime FD&C Act)", "alerts": ["Ácido glicirrízico sem limite numérico codificado pela FDA para cosméticos - avaliação via GRAS/relatórios de segurança do CIR"]},
        "AT-026": {"status": "USO_RESTRITO", "restriction_level": "ALTO", "max_concentration_allowed": "N/A", "alerts": ["Precursores de hidroquinona sob escrutínio elevado da FDA para produtos de clareamento de pele"]},
        "AT-029": {"status": "USO_RESTRITO", "restriction_level": "ALTO", "max_concentration_allowed": "N/A", "alerts": ["Ingredientes derivados de Cannabis sativa sob posição regulatória ainda não consolidada da FDA para uso cosmético"]},
        "AT-031": {"status": "APROVADO_USO_TOPICO", "restriction_level": "BAIXO", "max_concentration_allowed": "N/A (sem teto percentual fixo; uso profissional avaliado por pH/formulação)", "alerts": []},
        "AT-032": {"status": "APROVADO_USO_TOPICO", "restriction_level": "BAIXO", "max_concentration_allowed": "N/A (monografia OTC não aplicável a uso cosmético leave-on)", "alerts": []},
        "AT-033": {"status": "EM_ANALISE", "restriction_level": "ALTO", "max_concentration_allowed": "N/A", "alerts": ["Sem monografia OTC específica para uso tópico cosmético identificada - uso off-label"]},
        "AT-035": {"status": "APROVADO_USO_TOPICO", "restriction_level": "MEDIO", "max_concentration_allowed": "N/A", "alerts": ["Concentrações mais altas podem ser tratadas como produto OTC (monografia de acne), fora do escopo cosmético"]},
    }

    # Ranking de severidade usado para calcular o "máximo de severidade
    # regulatória observado entre as jurisdições monitoradas" em
    # get_regulatory_matrix() - mesma ordem implícita já usada por
    # core.score_engine.calculate_regulatory_alert_level (DESCONHECIDO tratado
    # como equivalente a MEDIO, não como pior caso automático).
    _SEVERITY_RANK = {"NENHUM": 0, "BAIXO": 1, "MEDIO": 2, "DESCONHECIDO": 2, "ALTO": 3}

    def __init__(self, resolver: EntityResolver):
        self.resolver = resolver

    @staticmethod
    def _query_hash(query: str) -> str:
        """SHA256 da chave de consulta exata, para rastreabilidade em evaluation_evidence_sources."""
        return hashlib.sha256(query.encode('utf-8')).hexdigest()

    def fetch_regulatory_status(self, asset_id: str, lang: str = "PT-BR") -> Dict[str, Any]:
        """
        Simula a verificação do status regulatório do ativo na base oficial da
        jurisdição correspondente ao idioma do relatório: Anvisa para PT-BR;
        base harmonizada da UE (CosIng/ECHA, com INFARMED/AEMPS como
        referência nacional) para PT-PT/ES, aplicando EU_REGULATORY_OVERRIDES
        sobre a base Anvisa onde a regra diverge de fato. Resiliente: nunca
        propaga exceção - degrada para um status de falha de consulta em vez
        de derrubar o pipeline.
        """
        try:
            base_status = dict(self.REGULATORY_REGISTRY.get(asset_id, {
                "status": "EM_ANALISE",
                "restriction_level": "DESCONHECIDO",
                "max_concentration_allowed": "N/A",
                "alerts": ["Ativo não mapeado na base regulatória local"]
            }))

            if lang.upper() in ("PT-PT", "ES") and asset_id in self.EU_REGULATORY_OVERRIDES:
                return dict(self.EU_REGULATORY_OVERRIDES[asset_id])

            return base_status
        except Exception as e:
            return {
                "status": "FALHA_CONSULTA",
                "restriction_level": "DESCONHECIDO",
                "max_concentration_allowed": "N/A",
                "alerts": [f"Falha ao consultar base regulatória: {e}"]
            }

    def get_regulatory_matrix(self, asset_id: str) -> Dict[str, Any]:
        """
        Decompõe a severidade regulatória por jurisdição monitorada (Anvisa/
        Brasil, Regulamento (CE) 1223/2009/UE, FDA/EUA - esta última mock/
        ilustrativa, ver FDA_REGULATORY_OVERRIDES) e calcula o pior caso entre
        as três, rotulado explicitamente como "Máximo de severidade
        regulatória observado entre as jurisdições monitoradas". Diferente de
        fetch_regulatory_status()/get_asset_dossier() (que retornam só a
        jurisdição do idioma do relatório), este método sempre olha as 3
        simultaneamente - usado por main.py para alimentar a árvore de
        precedência (core/predictive_ranking.py) com o pior caso global, não
        apenas o da jurisdição local do relatório.
        """
        fda_status = dict(self.FDA_REGULATORY_OVERRIDES.get(asset_id, self.REGULATORY_REGISTRY.get(asset_id, {
            "status": "EM_ANALISE",
            "restriction_level": "DESCONHECIDO",
            "max_concentration_allowed": "N/A",
            "alerts": ["Ativo não mapeado na base regulatória FDA (mock)"]
        })))

        jurisdictions = {
            "ANVISA": self.fetch_regulatory_status(asset_id, lang="PT-BR"),
            "EU_1223_2009": self.fetch_regulatory_status(asset_id, lang="PT-PT"),
            "FDA": fda_status
        }

        max_source, max_status = max(
            jurisdictions.items(),
            key=lambda item: self._SEVERITY_RANK.get(item[1].get("restriction_level"), 0)
        )

        return {
            "jurisdictions": jurisdictions,
            "max_severity_level": max_status.get("restriction_level"),
            "max_severity_source": max_source,
            "max_severity_status": max_status,
            "max_severity_label": "Máximo de severidade regulatória observado entre as jurisdições monitoradas"
        }

    def fetch_import_volume_mock(self, hs_code: str, asset_id: str = None, lang: str = "PT-BR") -> Dict[str, Any]:
        """
        Simula a consulta de volumes de importação/suprimento via código NCM/HS,
        na base comercial da região correspondente ao idioma do relatório: Comex
        Stat (Brasil) para PT-BR, ou a base de suprimento europeia (CosIng/ECHA,
        harmonizada entre Portugal e Espanha) para PT-PT/ES. A contagem de
        fornecedores e a tendência variam deterministicamente por ativo E por
        região (mesmo ativo pode ter cadeia de suprimento com tamanho/tendência
        diferente no Brasil vs. na UE), refletindo que a oferta real de mercado
        não é global e homogênea. Resiliente: nunca propaga exceção - degrada
        para um sinal neutro em vez de derrubar o pipeline.
        """
        if not hs_code:
            return {"hs_code": None, "volume_usd_annual": 0, "trend": "NEUTRO", "suppliers_count": 0, "risk_score": "DESCONHECIDO"}

        try:
            region_key = "EU" if lang.upper() in ("PT-PT", "ES") else "BR"
            seed = hashlib.sha256(f"{asset_id or hs_code}|{region_key}".encode("utf-8")).hexdigest()
            suppliers_count = 1 + (int(seed[:8], 16) % 12)  # 1-12 fornecedores
            trend = ["DECRESCENTE", "ESTAVEL", "CRESCENTE"][int(seed[8:10], 16) % 3]
            volume_usd_annual = 150_000 + (int(seed[10:16], 16) % 2_000_000)

            # C_trade (concentração observada nos fluxos comerciais do código NCM/HS)
            # e V_unit-value (volatilidade do valor unitário declarado em alfândega):
            # campos descritivos/aditivos, não usados em calculate_commercial_signal_level
            # nem calculate_supply_risk (core/score_engine.py) - a lógica de risco já
            # auditada nas rodadas anteriores continua baseada só em suppliers_count/trend.
            c_trade_concentration = round(1 / suppliers_count, 3)
            v_unit_value_volatility = round((int(seed[16:20], 16) % 100) / 100.0, 2)

            return {
                "hs_code": hs_code,
                "region": region_key,
                "volume_usd_annual": volume_usd_annual,
                "trend": trend,
                "suppliers_count": suppliers_count,
                "risk_score": "BAIXO_RISCO" if suppliers_count >= 5 else "MONITORAR",
                "c_trade_concentration": c_trade_concentration,
                "v_unit_value_volatility": v_unit_value_volatility
            }
        except Exception as e:
            return {
                "hs_code": hs_code,
                "volume_usd_annual": 0,
                "trend": "NEUTRO",
                "suppliers_count": 0,
                "risk_score": "DESCONHECIDO",
                "error": str(e)
            }

    def get_asset_dossier(self, asset_id: str, hs_code: Optional[str] = None, lang: str = "PT-BR") -> Dict[str, Any]:
        """
        Consulta consolidada e auditável de um ativo, calculada conforme a
        jurisdição do idioma do relatório: retorna a chave de busca exata
        (já incluindo a jurisdição) e seu hash SHA256, a autoridade regulatória
        e a fonte de dados comerciais de referência ('regulatory_body',
        'trade_source'), e separa nitidamente os 'alertas_regulatorios'
        (conformidade/uso permitido) dos 'sinais_comerciais_comex' (oferta/
        importação/suprimento) - os dois nunca devem ser somados na mesma
        métrica de risco. Ambos são calculados por região: 'alertas_regulatorios'
        via EU_REGULATORY_OVERRIDES onde a regra diverge de fato da base Anvisa,
        e 'sinais_comerciais_comex' via uma base de fornecedores distinta para
        Brasil vs. União Europeia.
        """
        lang_key = lang.upper()
        authorities = self.REGIONAL_AUTHORITIES.get(lang_key, self.REGIONAL_AUTHORITIES["PT-BR"])

        query = f"asset_id={asset_id}&hs_code={hs_code or 'N/A'}&lang={lang_key}"
        query_hash = self._query_hash(query)

        return {
            "query": query,
            "query_hash": query_hash,
            "regulatory_body": authorities["regulatory_body"],
            "trade_source": authorities["trade_source"],
            "alertas_regulatorios": self.fetch_regulatory_status(asset_id, lang=lang_key),
            "sinais_comerciais_comex": self.fetch_import_volume_mock(hs_code, asset_id=asset_id, lang=lang_key)
        }


if __name__ == "__main__":
    resolver = EntityResolver(taxonomy_path="data/taxonomy/ativos_mvp.json")
    comex_conn = RegulatoryComexConnector(resolver=resolver)

    print("--- Teste de Dossiê Regulatório/Comex por Jurisdição (35 ativos) ---")
    for asset in resolver.assets:
        asset_id = asset["asset_id"]
        hs_code = asset.get("hs_codes", [None])[0]

        row = f"  {asset_id} {asset['canonical_name']:<24}"
        for lang in ("PT-BR", "PT-PT", "ES"):
            dossier = comex_conn.get_asset_dossier(asset_id, hs_code=hs_code, lang=lang)
            reg = dossier["alertas_regulatorios"]
            row += f" | {lang}={reg['restriction_level']:<12}"
        print(row)

    print("\n--- Exemplo de divergência de jurisdição: Ácido Salicílico (AT-032) ---")
    for lang in ("PT-BR", "PT-PT", "ES"):
        dossier = comex_conn.get_asset_dossier("AT-032", hs_code="2918.21.00", lang=lang)
        print(f"\n[{lang}] Fonte regulatória: {dossier['regulatory_body']}")
        print(f"[{lang}] Fonte comercial: {dossier['trade_source']}")
        print(f"[{lang}] Alertas regulatórios: {dossier['alertas_regulatorios']}")
        print(f"[{lang}] Query hash: {dossier['query_hash'][:16]}...")
        commercial = comex_conn.fetch_import_volume_mock("2918.21.00", asset_id="AT-032", lang=lang)
        print(f"[{lang}] C_trade (concentração): {commercial['c_trade_concentration']} | V_unit-value (volatilidade): {commercial['v_unit_value_volatility']}")

    print("\n--- Matriz Regulatória Cross-Jurisdição (Anvisa/UE/FDA) - Cânhamo/CBD (AT-029) ---")
    matrix = comex_conn.get_regulatory_matrix("AT-029")
    for jurisdiction, status in matrix["jurisdictions"].items():
        print(f"  {jurisdiction}: {status['restriction_level']} ({status['status']})")
    print(f"  {matrix['max_severity_label']}: {matrix['max_severity_level']} (fonte: {matrix['max_severity_source']})")
