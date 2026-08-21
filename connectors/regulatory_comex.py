import sys
import io
import re
import hashlib
from typing import Dict, Any, List, Optional

from core.entity_resolver import EntityResolver
from connectors.trade_eurostat import TradeEurostatConnector

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

    # Código de país declarante (reporter_code) OBRIGATÓRIO por idioma do
    # relatório para consultas ao Eurostat Comext/TARIC (connectors/trade_eurostat.py)
    # - CORREÇÃO: antes desta versão, PT-PT e ES compartilhavam o mesmo
    # bucket regional genérico "EU" em fetch_import_volume_mock(), produzindo
    # dados de comércio idênticos entre Portugal e Espanha. Cada idioma da
    # UE agora resolve para seu próprio código de país declarante real.
    LANG_TO_REPORTER_CODE = {"PT-PT": "PT", "ES": "ES"}

    # Base de conhecimento de status regulatórios (35 ativos dermocosméticos,
    # incluindo a classe de Ácidos Cosmecêuticos). Níveis atribuídos por
    # categoria de risco regulatório real e conhecida do ativo (ex.: ácido
    # glicirrízico no alcaçuz, hidroquinona-precursores na arbutina, derivados
    # de cannabis, limites de AHA/BHA leave-on) - não são valores oficiais
    # consultados em tempo real. Mantida MANUALMENTE - não há mecanismo de
    # atualização automática nem de detecção de desatualização.
    #
    # RASTREABILIDADE (2026-08-20, obrigatório em TODA entrada desta base e
    # de EU_REGULATORY_OVERRIDES/FDA_REGULATORY_OVERRIDES abaixo - validado
    # na importação do módulo, ver _validate_regulatory_registries()):
    #   - "source": string não vazia citando a norma/resolução que embasa a
    #     entrada (ex.: "Anvisa RDC 07/2015"), OU declarando explicitamente a
    #     ausência de uma citação específica quando a classificação vem só de
    #     avaliação interna, não de um dispositivo legal citável.
    #   - "last_verified": data YYYY-MM-DD da última confirmação manual de
    #     que a entrada ainda reflete a regulação vigente. NUNCA atualizada
    #     automaticamente - reflete só quando alguém revisou a entrada à mão.
    # Cadência de revisão recomendada: SEMESTRAL (regulação muda bem mais
    # devagar que publicação científica/patente - ver METHODOLOGY.md, seção
    # "Rastreabilidade do REGULATORY_REGISTRY", para a cadência completa e o
    # motivo da diferença em relação à revisão trimestral de
    # INDUSTRIAL_TRACTION_N_REF/MIN_SCI_FOR_EMERGING_STAR).
    REGULATORY_REGISTRY = {
        "AT-001": {"status": "APROVADO_USO_TOPICO", "restriction_level": "BAIXO", "max_concentration_allowed": "2.0%", "alerts": [], "source": "Sem RDC/resolução específica citada nesta base - classificação por avaliação interna da equipe de regulatório, não vinculada a um dispositivo legal auditável nesta entrada.", "last_verified": "2026-08-20"},
        "AT-002": {"status": "APROVADO_USO_TOPICO", "restriction_level": "BAIXO", "max_concentration_allowed": "5.0%", "alerts": [], "source": "Sem RDC/resolução específica citada nesta base - classificação por avaliação interna da equipe de regulatório, não vinculada a um dispositivo legal auditável nesta entrada.", "last_verified": "2026-08-20"},
        "AT-003": {"status": "APROVADO_USO_TOPICO", "restriction_level": "NENHUM", "max_concentration_allowed": "100%", "alerts": [], "source": "Sem RDC/resolução específica citada nesta base - classificação por avaliação interna da equipe de regulatório, não vinculada a um dispositivo legal auditável nesta entrada.", "last_verified": "2026-08-20"},
        "AT-004": {"status": "APROVADO_USO_TOPICO", "restriction_level": "MEDIO", "max_concentration_allowed": "0.5%", "alerts": ["Requer dossiê de segurança complementar"], "source": "Sem RDC/resolução específica citada nesta base - classificação por avaliação interna da equipe de regulatório, não vinculada a um dispositivo legal auditável nesta entrada.", "last_verified": "2026-08-20"},
        "AT-005": {"status": "APROVADO_USO_TOPICO", "restriction_level": "NENHUM", "max_concentration_allowed": "100%", "alerts": [], "source": "Sem RDC/resolução específica citada nesta base - classificação por avaliação interna da equipe de regulatório, não vinculada a um dispositivo legal auditável nesta entrada.", "last_verified": "2026-08-20"},
        "AT-006": {"status": "APROVADO_USO_TOPICO", "restriction_level": "BAIXO", "max_concentration_allowed": "3.0%", "alerts": [], "source": "Sem RDC/resolução específica citada nesta base - classificação por avaliação interna da equipe de regulatório, não vinculada a um dispositivo legal auditável nesta entrada.", "last_verified": "2026-08-20"},
        "AT-007": {"status": "APROVADO_USO_TOPICO", "restriction_level": "BAIXO", "max_concentration_allowed": "5.0%", "alerts": [], "source": "Sem RDC/resolução específica citada nesta base - classificação por avaliação interna da equipe de regulatório, não vinculada a um dispositivo legal auditável nesta entrada.", "last_verified": "2026-08-20"},
        "AT-008": {"status": "APROVADO_USO_TOPICO", "restriction_level": "MEDIO", "max_concentration_allowed": "0.1%", "alerts": ["Limite de concentração por potencial irritante (spilanthol)"], "source": "Sem RDC/resolução específica citada nesta base - classificação por avaliação interna da equipe de regulatório, não vinculada a um dispositivo legal auditável nesta entrada.", "last_verified": "2026-08-20"},
        "AT-009": {"status": "APROVADO_USO_TOPICO", "restriction_level": "NENHUM", "max_concentration_allowed": "100%", "alerts": [], "source": "Sem RDC/resolução específica citada nesta base - classificação por avaliação interna da equipe de regulatório, não vinculada a um dispositivo legal auditável nesta entrada.", "last_verified": "2026-08-20"},
        "AT-010": {"status": "APROVADO_USO_TOPICO", "restriction_level": "NENHUM", "max_concentration_allowed": "100%", "alerts": [], "source": "Sem RDC/resolução específica citada nesta base - classificação por avaliação interna da equipe de regulatório, não vinculada a um dispositivo legal auditável nesta entrada.", "last_verified": "2026-08-20"},
        "AT-011": {"status": "APROVADO_USO_TOPICO", "restriction_level": "NENHUM", "max_concentration_allowed": "100%", "alerts": [], "source": "Sem RDC/resolução específica citada nesta base - classificação por avaliação interna da equipe de regulatório, não vinculada a um dispositivo legal auditável nesta entrada.", "last_verified": "2026-08-20"},
        "AT-012": {"status": "APROVADO_USO_TOPICO", "restriction_level": "BAIXO", "max_concentration_allowed": "10.0%", "alerts": [], "source": "Sem RDC/resolução específica citada nesta base - classificação por avaliação interna da equipe de regulatório, não vinculada a um dispositivo legal auditável nesta entrada.", "last_verified": "2026-08-20"},
        "AT-013": {"status": "APROVADO_USO_TOPICO", "restriction_level": "BAIXO", "max_concentration_allowed": "5.0%", "alerts": [], "source": "Sem RDC/resolução específica citada nesta base - classificação por avaliação interna da equipe de regulatório, não vinculada a um dispositivo legal auditável nesta entrada.", "last_verified": "2026-08-20"},
        "AT-014": {"status": "APROVADO_USO_TOPICO", "restriction_level": "BAIXO", "max_concentration_allowed": "3.0%", "alerts": [], "source": "Sem RDC/resolução específica citada nesta base - classificação por avaliação interna da equipe de regulatório, não vinculada a um dispositivo legal auditável nesta entrada.", "last_verified": "2026-08-20"},
        "AT-015": {"status": "APROVADO_USO_TOPICO", "restriction_level": "BAIXO", "max_concentration_allowed": "5.0%", "alerts": [], "source": "Sem RDC/resolução específica citada nesta base - classificação por avaliação interna da equipe de regulatório, não vinculada a um dispositivo legal auditável nesta entrada.", "last_verified": "2026-08-20"},
        "AT-016": {"status": "APROVADO_USO_TOPICO", "restriction_level": "MEDIO", "max_concentration_allowed": "1.0%", "alerts": ["Monitoramento de pureza da resina"], "source": "Sem RDC/resolução específica citada nesta base - classificação por avaliação interna da equipe de regulatório, não vinculada a um dispositivo legal auditável nesta entrada.", "last_verified": "2026-08-20"},
        "AT-017": {"status": "APROVADO_USO_TOPICO", "restriction_level": "NENHUM", "max_concentration_allowed": "100%", "alerts": [], "source": "Sem RDC/resolução específica citada nesta base - classificação por avaliação interna da equipe de regulatório, não vinculada a um dispositivo legal auditável nesta entrada.", "last_verified": "2026-08-20"},
        "AT-018": {"status": "APROVADO_USO_TOPICO", "restriction_level": "NENHUM", "max_concentration_allowed": "100%", "alerts": [], "source": "Sem RDC/resolução específica citada nesta base - classificação por avaliação interna da equipe de regulatório, não vinculada a um dispositivo legal auditável nesta entrada.", "last_verified": "2026-08-20"},
        "AT-019": {"status": "USO_RESTRITO", "restriction_level": "ALTO", "max_concentration_allowed": "0.02%", "alerts": ["Limite regulatório de ácido glicirrízico (Anvisa/EU)"], "source": "Sem RDC/resolução específica citada nesta base - classificação por avaliação interna da equipe de regulatório, não vinculada a um dispositivo legal auditável nesta entrada.", "last_verified": "2026-08-20"},
        "AT-020": {"status": "APROVADO_USO_TOPICO", "restriction_level": "NENHUM", "max_concentration_allowed": "100%", "alerts": [], "source": "Sem RDC/resolução específica citada nesta base - classificação por avaliação interna da equipe de regulatório, não vinculada a um dispositivo legal auditável nesta entrada.", "last_verified": "2026-08-20"},
        "AT-021": {"status": "APROVADO_USO_TOPICO", "restriction_level": "NENHUM", "max_concentration_allowed": "100%", "alerts": [], "source": "Sem RDC/resolução específica citada nesta base - classificação por avaliação interna da equipe de regulatório, não vinculada a um dispositivo legal auditável nesta entrada.", "last_verified": "2026-08-20"},
        "AT-022": {"status": "APROVADO_USO_TOPICO", "restriction_level": "NENHUM", "max_concentration_allowed": "100%", "alerts": [], "source": "Sem RDC/resolução específica citada nesta base - classificação por avaliação interna da equipe de regulatório, não vinculada a um dispositivo legal auditável nesta entrada.", "last_verified": "2026-08-20"},
        "AT-023": {"status": "APROVADO_USO_TOPICO", "restriction_level": "NENHUM", "max_concentration_allowed": "100%", "alerts": [], "source": "Sem RDC/resolução específica citada nesta base - classificação por avaliação interna da equipe de regulatório, não vinculada a um dispositivo legal auditável nesta entrada.", "last_verified": "2026-08-20"},
        "AT-024": {"status": "APROVADO_USO_TOPICO", "restriction_level": "BAIXO", "max_concentration_allowed": "5.0%", "alerts": ["Rotulagem obrigatória de alérgeno (derivado de trigo)"], "source": "Sem RDC/resolução específica citada nesta base - classificação por avaliação interna da equipe de regulatório, não vinculada a um dispositivo legal auditável nesta entrada.", "last_verified": "2026-08-20"},
        "AT-025": {"status": "APROVADO_USO_TOPICO", "restriction_level": "BAIXO", "max_concentration_allowed": "100%", "alerts": [], "source": "Sem RDC/resolução específica citada nesta base - classificação por avaliação interna da equipe de regulatório, não vinculada a um dispositivo legal auditável nesta entrada.", "last_verified": "2026-08-20"},
        "AT-026": {"status": "USO_RESTRITO", "restriction_level": "ALTO", "max_concentration_allowed": "2.0%", "alerts": ["Precursor de hidroquinona - escrutínio regulatório elevado (clareadores)"], "source": "Sem RDC/resolução específica citada nesta base - classificação por avaliação interna da equipe de regulatório, não vinculada a um dispositivo legal auditável nesta entrada.", "last_verified": "2026-08-20"},
        "AT-027": {"status": "APROVADO_USO_TOPICO", "restriction_level": "NENHUM", "max_concentration_allowed": "100%", "alerts": [], "source": "Sem RDC/resolução específica citada nesta base - classificação por avaliação interna da equipe de regulatório, não vinculada a um dispositivo legal auditável nesta entrada.", "last_verified": "2026-08-20"},
        "AT-028": {"status": "APROVADO_USO_TOPICO", "restriction_level": "NENHUM", "max_concentration_allowed": "100%", "alerts": [], "source": "Sem RDC/resolução específica citada nesta base - classificação por avaliação interna da equipe de regulatório, não vinculada a um dispositivo legal auditável nesta entrada.", "last_verified": "2026-08-20"},
        "AT-029": {"status": "USO_RESTRITO", "restriction_level": "ALTO", "max_concentration_allowed": "0.2%", "alerts": ["Derivado de Cannabis sativa - barreiras regulatórias de importação/registro"], "source": "Sem RDC/resolução específica citada nesta base - classificação por avaliação interna da equipe de regulatório, não vinculada a um dispositivo legal auditável nesta entrada.", "last_verified": "2026-08-20"},
        "AT-030": {"status": "APROVADO_USO_TOPICO", "restriction_level": "BAIXO", "max_concentration_allowed": "10.0%", "alerts": [], "source": "Sem RDC/resolução específica citada nesta base - classificação por avaliação interna da equipe de regulatório, não vinculada a um dispositivo legal auditável nesta entrada.", "last_verified": "2026-08-20"},
        "AT-031": {"status": "APROVADO_USO_TOPICO", "restriction_level": "MEDIO", "max_concentration_allowed": "10.0%", "alerts": ["Uso profissional acima de 10% restrito a procedimentos de peeling"], "source": "Sem RDC/resolução específica citada nesta base - classificação por avaliação interna da equipe de regulatório, não vinculada a um dispositivo legal auditável nesta entrada.", "last_verified": "2026-08-20"},
        "AT-032": {"status": "USO_RESTRITO", "restriction_level": "MEDIO", "max_concentration_allowed": "2.0%", "alerts": ["Proibido em produtos leave-on para menores de 3 anos (EU 2019/831)"], "source": "Regulamento (UE) 2019/831 (citado nesta entrada da base Anvisa por proximidade de regra - inconsistência de proveniência pré-existente, ver METHODOLOGY.md)", "last_verified": "2026-08-20"},
        "AT-033": {"status": "EM_ANALISE", "restriction_level": "ALTO", "max_concentration_allowed": "N/A", "alerts": ["Sem monografia tópica consolidada na Anvisa - uso off-label em skincare"], "source": "Sem RDC/resolução específica citada nesta base - classificação por avaliação interna da equipe de regulatório, não vinculada a um dispositivo legal auditável nesta entrada.", "last_verified": "2026-08-20"},
        "AT-034": {"status": "APROVADO_USO_TOPICO", "restriction_level": "BAIXO", "max_concentration_allowed": "10.0%", "alerts": [], "source": "Sem RDC/resolução específica citada nesta base - classificação por avaliação interna da equipe de regulatório, não vinculada a um dispositivo legal auditável nesta entrada.", "last_verified": "2026-08-20"},
        "AT-035": {"status": "USO_RESTRITO", "restriction_level": "MEDIO", "max_concentration_allowed": "20.0%", "alerts": ["Acima de 10% classificado como uso dermatológico/prescrição em alguns mercados"], "source": "Sem RDC/resolução específica citada nesta base - classificação por avaliação interna da equipe de regulatório, não vinculada a um dispositivo legal auditável nesta entrada.", "last_verified": "2026-08-20"},
    }

    # Sobrescreve REGULATORY_REGISTRY apenas para PT-PT/ES (jurisdição UE), e
    # apenas nos ativos cuja regra realmente diverge da base Anvisa - a maioria
    # dos extratos botânicos comuns é tratada de forma equivalente nas duas
    # jurisdições e não precisa de entrada aqui. Referências: Regulamento (CE)
    # 1223/2009 (cosméticos, UE) e seus Anexos II/III, consultáveis via CosIng/ECHA.
    EU_REGULATORY_OVERRIDES = {
        "AT-019": {"status": "USO_RESTRITO", "restriction_level": "MEDIO", "max_concentration_allowed": "N/A (rotulagem obrigatória)", "alerts": ["Sem limite numérico de ácido glicirrízico no Regulamento (CE) 1223/2009 - controlado via rotulagem de alérgenos"], "source": "Regulamento (CE) 1223/2009 (sem limite numérico de ácido glicirrízico - controlado via rotulagem de alérgenos, sem entrada de Anexo específica citada)", "last_verified": "2026-08-20"},
        "AT-026": {"status": "USO_RESTRITO", "restriction_level": "ALTO", "max_concentration_allowed": "2.0% (creme facial) / 0.5% (loção corporal)", "alerts": ["Alpha-Arbutin listado no Anexo III, entrada 77, do Regulamento (CE) 1223/2009"], "source": "Regulamento (CE) 1223/2009, Anexo III, entrada 77 (Alpha-Arbutin)", "last_verified": "2026-08-20"},
        "AT-029": {"status": "USO_RESTRITO", "restriction_level": "ALTO", "max_concentration_allowed": "N/A", "alerts": ["Extratos de folha/flor de Cannabis sativa restritos no Anexo II; derivados de semente (óleo/CBD de cânhamo industrial) avaliados caso a caso pela ECHA"], "source": "Regulamento (CE) 1223/2009, Anexo II (extratos de folha/flor de Cannabis sativa); avaliação ECHA caso a caso para derivados de semente", "last_verified": "2026-08-20"},
        "AT-031": {"status": "APROVADO_USO_TOPICO", "restriction_level": "BAIXO", "max_concentration_allowed": "pH ≥ 3.5 (sem teto percentual fixo no Anexo III para uso profissional)", "alerts": [], "source": "Regulamento (CE) 1223/2009, Anexo III (sem entrada numérica específica citada nesta base)", "last_verified": "2026-08-20"},
        "AT-032": {"status": "USO_RESTRITO", "restriction_level": "MEDIO", "max_concentration_allowed": "2.0% (leave-on) / 3.0% (rinse-off)", "alerts": ["Proibido em produtos leave-on para menores de 3 anos - Regulamento (UE) 2019/831, Anexo III entrada 98"], "source": "Regulamento (UE) 2019/831, Anexo III, entrada 98", "last_verified": "2026-08-20"},
        "AT-033": {"status": "EM_ANALISE", "restriction_level": "ALTO", "max_concentration_allowed": "N/A", "alerts": ["Sem entrada específica no CosIng para uso tópico cosmético - uso off-label"], "source": "Regulamento (CE) 1223/2009 (sem entrada específica no CosIng identificada para uso tópico cosmético desta categoria)", "last_verified": "2026-08-20"},
        "AT-035": {"status": "APROVADO_USO_TOPICO", "restriction_level": "MEDIO", "max_concentration_allowed": "10.0%", "alerts": ["Concentrações mais altas (uso dermatológico) tratadas como produto medicinal, fora do escopo do Regulamento (CE) 1223/2009"], "source": "Regulamento (CE) 1223/2009 (delimitação de escopo cosmético vs. produto medicinal, sem entrada de Anexo específica citada)", "last_verified": "2026-08-20"},
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
        "AT-019": {"status": "USO_RESTRITO", "restriction_level": "MEDIO", "max_concentration_allowed": "N/A (sem teto percentual fixo sob o regime FD&C Act)", "alerts": ["Ácido glicirrízico sem limite numérico codificado pela FDA para cosméticos - avaliação via GRAS/relatórios de segurança do CIR"], "source": "Sem citação de CFR/monografia OTC específica nesta base - avaliação interna qualitativa da postura da FDA para cosméticos (que não exige aprovação prévia sob o FD&C Act), não vinculada a um número de CFR auditável nesta entrada.", "last_verified": "2026-08-20"},
        "AT-026": {"status": "USO_RESTRITO", "restriction_level": "ALTO", "max_concentration_allowed": "N/A", "alerts": ["Precursores de hidroquinona sob escrutínio elevado da FDA para produtos de clareamento de pele"], "source": "Sem citação de CFR/monografia OTC específica nesta base - avaliação interna qualitativa da postura da FDA para cosméticos (que não exige aprovação prévia sob o FD&C Act), não vinculada a um número de CFR auditável nesta entrada.", "last_verified": "2026-08-20"},
        "AT-029": {"status": "USO_RESTRITO", "restriction_level": "ALTO", "max_concentration_allowed": "N/A", "alerts": ["Ingredientes derivados de Cannabis sativa sob posição regulatória ainda não consolidada da FDA para uso cosmético"], "source": "Sem citação de CFR/monografia OTC específica nesta base - avaliação interna qualitativa da postura da FDA para cosméticos (que não exige aprovação prévia sob o FD&C Act), não vinculada a um número de CFR auditável nesta entrada.", "last_verified": "2026-08-20"},
        "AT-031": {"status": "APROVADO_USO_TOPICO", "restriction_level": "BAIXO", "max_concentration_allowed": "N/A (sem teto percentual fixo; uso profissional avaliado por pH/formulação)", "alerts": [], "source": "Sem citação de CFR/monografia OTC específica nesta base - avaliação interna qualitativa da postura da FDA para cosméticos (que não exige aprovação prévia sob o FD&C Act), não vinculada a um número de CFR auditável nesta entrada.", "last_verified": "2026-08-20"},
        "AT-032": {"status": "APROVADO_USO_TOPICO", "restriction_level": "BAIXO", "max_concentration_allowed": "N/A (monografia OTC não aplicável a uso cosmético leave-on)", "alerts": [], "source": "Sem citação de CFR/monografia OTC específica nesta base - avaliação interna qualitativa da postura da FDA para cosméticos (que não exige aprovação prévia sob o FD&C Act), não vinculada a um número de CFR auditável nesta entrada.", "last_verified": "2026-08-20"},
        "AT-033": {"status": "EM_ANALISE", "restriction_level": "ALTO", "max_concentration_allowed": "N/A", "alerts": ["Sem monografia OTC específica para uso tópico cosmético identificada - uso off-label"], "source": "Sem citação de CFR/monografia OTC específica nesta base - avaliação interna qualitativa da postura da FDA para cosméticos (que não exige aprovação prévia sob o FD&C Act), não vinculada a um número de CFR auditável nesta entrada.", "last_verified": "2026-08-20"},
        "AT-035": {"status": "APROVADO_USO_TOPICO", "restriction_level": "MEDIO", "max_concentration_allowed": "N/A", "alerts": ["Concentrações mais altas podem ser tratadas como produto OTC (monografia de acne), fora do escopo cosmético"], "source": "Sem citação de CFR/monografia OTC específica nesta base - avaliação interna qualitativa da postura da FDA para cosméticos (que não exige aprovação prévia sob o FD&C Act), não vinculada a um número de CFR auditável nesta entrada.", "last_verified": "2026-08-20"},
    }

    # Ranking de severidade usado para calcular o "máximo de severidade
    # regulatória observado entre as jurisdições monitoradas" em
    # get_regulatory_matrix() - mesma ordem implícita já usada por
    # core.score_engine.calculate_regulatory_alert_level (DESCONHECIDO tratado
    # como equivalente a MEDIO, não como pior caso automático).
    _SEVERITY_RANK = {"NENHUM": 0, "BAIXO": 1, "MEDIO": 2, "DESCONHECIDO": 2, "ALTO": 3}

    def __init__(self, resolver: EntityResolver):
        self.resolver = resolver
        self.trade_eurostat = TradeEurostatConnector()

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
                "alerts": ["Ativo não mapeado na base regulatória local"],
                "source": "N/A - ativo não mapeado em nenhuma entrada curada da base regulatória",
                "last_verified": None
            }))

            if lang.upper() in ("PT-PT", "ES") and asset_id in self.EU_REGULATORY_OVERRIDES:
                return dict(self.EU_REGULATORY_OVERRIDES[asset_id])

            return base_status
        except Exception as e:
            return {
                "status": "FALHA_CONSULTA",
                "restriction_level": "DESCONHECIDO",
                "max_concentration_allowed": "N/A",
                "alerts": [f"Falha ao consultar base regulatória: {e}"],
                "source": "N/A - falha de consulta",
                "last_verified": None
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
            "alerts": ["Ativo não mapeado na base regulatória FDA (mock)"],
            "source": "N/A - ativo não mapeado em nenhuma entrada curada da base regulatória FDA",
            "last_verified": None
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
        Consulta os volumes de importação/suprimento via código NCM/HS, na
        base comercial correspondente ao idioma do relatório: Comex Stat
        (Brasil, mock local) para PT-BR; connectors.trade_eurostat.TradeEurostatConnector,
        consultado com o reporter_code do país declarante específico do
        mercado-alvo (LANG_TO_REPORTER_CODE - 'PT' para PT-PT, 'ES' para ES),
        para PT-PT/ES. CORRIGIDO: antes, PT-PT e ES compartilhavam o mesmo
        bucket regional genérico "EU", produzindo dados de comércio
        IDÊNTICOS entre Portugal e Espanha - cada um agora resolve para seu
        próprio país declarante real no Eurostat Comext/TARIC, garantindo
        que os fornecedores/tendência/volume divirjam entre os dois
        mercados (ver core/sanity_checks.py para a trava que audita essa
        divergência). Resiliente: nunca propaga exceção - degrada para um
        sinal neutro em vez de derrubar o pipeline.
        """
        if not hs_code:
            return {"hs_code": None, "volume_usd_annual": 0, "trend": "NEUTRO", "suppliers_count": 0, "risk_score": "DESCONHECIDO"}

        reporter_code = self.LANG_TO_REPORTER_CODE.get(lang.upper())
        if reporter_code:
            return self.trade_eurostat.fetch_trade_data(hs_code, reporter_code=reporter_code, asset_id=asset_id)

        try:
            seed = hashlib.sha256(f"{asset_id or hs_code}|BR".encode("utf-8")).hexdigest()
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
                "region": "BR",
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


class RegulatoryRegistryTraceabilityError(ValueError):
    """
    Uma entrada de REGULATORY_REGISTRY/EU_REGULATORY_OVERRIDES/FDA_REGULATORY_OVERRIDES
    está sem 'source' e/ou 'last_verified' válidos. Levantado na importação
    deste módulo (ver _validate_regulatory_registries() abaixo) - nunca
    silenciosamente ignorado. Corrigido em 2026-08-20 (auditoria: os dois
    campos passaram a ser obrigatórios em toda entrada, ver METHODOLOGY.md,
    seção "Rastreabilidade do REGULATORY_REGISTRY").
    """


_LAST_VERIFIED_DATE_PATTERN = re.compile(r'^\d{4}-\d{2}-\d{2}$')


# Tradução de 'source' por idioma do relatório - CORRIGIDO (2026-08-20):
# 'source' é armazenado em PT-BR (idioma mestre) dentro de REGULATORY_REGISTRY/
# EU_REGULATORY_OVERRIDES/FDA_REGULATORY_OVERRIDES, mas é exposto DIRETO no
# PDF (evidence-tag "REG: ...", reports/pdf_generator.py) sem passar pela LLM
# (ao contrário de 'alerts', que só chega ao leitor via texto já traduzido
# pela síntese da LLM) - por isso nunca era traduzido, e vazava em português
# até para o relatório ES (achado de auditoria: os 3 ativos High-Risk do
# relatório ES mostravam o disclaimer REG inteiro em português).
#
# Chaveado pelo texto EXATO em PT-BR (o valor 'source' de cada entrada) - só
# 10 strings distintas existem nas 49 entradas das 3 bases (muita reutilização
# de template), então esta tabela cobre a base inteira sem precisar
# triplicar cada entrada num dict aninhado por idioma. PT-PT não precisa de
# tradução própria aqui: o texto-fonte já está em português, que é o idioma
# de PT-PT também - a checagem que importa para PT-PT é de CONTEÚDO (citar
# um instrumento legal brasileiro como "RDC" num relatório de Portugal), não
# de IDIOMA; registrado como observação separada em METHODOLOGY.md, não
# corrigido aqui (fora do escopo desta rodada - é sobre proveniência da
# citação, não sobre tradução).
#
# Validado na importação do módulo (_validate_regulatory_registries()
# abaixo): toda 'source' distinta usada nas 3 bases PRECISA ter uma entrada
# "ES" aqui, ou a importação falha - mesmo princípio de "nunca falhar em
# silêncio" já aplicado a source/last_verified.
SOURCE_TRANSLATIONS: Dict[str, Dict[str, str]] = {
    "Sem RDC/resolução específica citada nesta base - classificação por avaliação interna da equipe de regulatório, não vinculada a um dispositivo legal auditável nesta entrada.": {
        "ES": "Sin RDC/resolución específica citada en esta base - clasificación por evaluación interna del equipo de regulación, no vinculada a un dispositivo legal auditable en esta entrada."
    },
    "Regulamento (UE) 2019/831 (citado nesta entrada da base Anvisa por proximidade de regra - inconsistência de proveniência pré-existente, ver METHODOLOGY.md)": {
        "ES": "Reglamento (UE) 2019/831 (citado en esta entrada de la base Anvisa por proximidad de regla - inconsistencia de procedencia preexistente, ver METHODOLOGY.md)"
    },
    "Regulamento (CE) 1223/2009 (sem limite numérico de ácido glicirrízico - controlado via rotulagem de alérgenos, sem entrada de Anexo específica citada)": {
        "ES": "Reglamento (CE) 1223/2009 (sin límite numérico de ácido glicirrízico - controlado mediante etiquetado de alérgenos, sin entrada de Anexo específica citada)"
    },
    "Regulamento (CE) 1223/2009, Anexo III, entrada 77 (Alpha-Arbutin)": {
        "ES": "Reglamento (CE) 1223/2009, Anexo III, entrada 77 (Alfa-Arbutina)"
    },
    "Regulamento (CE) 1223/2009, Anexo II (extratos de folha/flor de Cannabis sativa); avaliação ECHA caso a caso para derivados de semente": {
        "ES": "Reglamento (CE) 1223/2009, Anexo II (extractos de hoja/flor de Cannabis sativa); evaluación ECHA caso por caso para derivados de semilla"
    },
    "Regulamento (CE) 1223/2009, Anexo III (sem entrada numérica específica citada nesta base)": {
        "ES": "Reglamento (CE) 1223/2009, Anexo III (sin entrada numérica específica citada en esta base)"
    },
    "Regulamento (UE) 2019/831, Anexo III, entrada 98": {
        "ES": "Reglamento (UE) 2019/831, Anexo III, entrada 98"
    },
    "Regulamento (CE) 1223/2009 (sem entrada específica no CosIng identificada para uso tópico cosmético desta categoria)": {
        "ES": "Reglamento (CE) 1223/2009 (sin entrada específica en CosIng identificada para uso tópico cosmético de esta categoría)"
    },
    "Regulamento (CE) 1223/2009 (delimitação de escopo cosmético vs. produto medicinal, sem entrada de Anexo específica citada)": {
        "ES": "Reglamento (CE) 1223/2009 (delimitación de alcance cosmético vs. producto medicinal, sin entrada de Anexo específica citada)"
    },
    "Sem citação de CFR/monografia OTC específica nesta base - avaliação interna qualitativa da postura da FDA para cosméticos (que não exige aprovação prévia sob o FD&C Act), não vinculada a um número de CFR auditável nesta entrada.": {
        "ES": "Sin cita de CFR/monografía OTC específica en esta base - evaluación interna cualitativa de la postura de la FDA para cosméticos (que no exige aprobación previa bajo la FD&C Act), no vinculada a un número de CFR auditable en esta entrada."
    },
}


def localize_source(source_pt: str, lang: str) -> str:
    """
    Traduz um valor 'source' (armazenado em PT-BR nas 3 bases regulatórias)
    para o idioma do relatório. PT-BR/PT-PT retornam o texto como está (já
    em português). ES busca em SOURCE_TRANSLATIONS - se o texto exato não
    estiver cadastrado lá (ex.: 'source' novo adicionado sem tradução),
    retorna o PT-BR original com um prefixo de alerta visível em vez de
    falhar silenciosamente ou expor texto em português sem aviso nenhum
    (fail-loud também em tempo de renderização, não só na importação do
    módulo).
    """
    lang_key = lang.upper()
    if lang_key != "ES":
        return source_pt
    translations = SOURCE_TRANSLATIONS.get(source_pt)
    if not translations or "ES" not in translations:
        return f"[TRADUÇÃO ES AUSENTE] {source_pt}"
    return translations["ES"]


# Tradução de 'alerts' (lista de avisos regulatórios em texto livre, armazenada
# em PT-BR em REGULATORY_REGISTRY/EU_REGULATORY_OVERRIDES) por idioma - CORRIGIDO
# (2026-08-20, achado de auditoria: vazamento de vocabulário técnico em
# português na prosa gerada pela LLM para o relatório ES). Diferente de
# 'source' (só exposto no PDF via evidence-tag REG, para ativos High-Risk),
# 'alerts' é injetado no PROMPT de core/llm_analysis.py generate_recommendations
# (campo 'Alertas regulatórios específicos desta jurisdição') para TODO ativo
# com ao menos um alerta, em qualquer categoria de triagem - por isso o
# vazamento aparecia mesmo em ativos fora de High-Risk. FDA_REGULATORY_OVERRIDES
# não precisa de entrada aqui: seus 'alerts' nunca chegam ao prompt da LLM nem
# ao PDF (main.py só repassa a jurisdição ANVISA/EU_1223_2009 para o dossiê
# regional consultado pela LLM; FDA entra apenas no cálculo de severidade
# cross-jurisdição de get_regulatory_matrix(), como valor numérico).
#
# Chaveado pelo texto EXATO em PT-BR de cada alerta individual (não a lista
# inteira) - validado na importação do módulo (_validate_regulatory_registries()),
# mesmo princípio de "nunca falhar em silêncio" já aplicado a source/last_verified.
ALERT_TRANSLATIONS: Dict[str, Dict[str, str]] = {
    "Requer dossiê de segurança complementar": {
        "ES": "Requiere dossier de seguridad complementario"
    },
    "Limite de concentração por potencial irritante (spilanthol)": {
        "ES": "Límite de concentración por potencial irritante (espilantol)"
    },
    "Monitoramento de pureza da resina": {
        "ES": "Monitoreo de pureza de la resina"
    },
    "Limite regulatório de ácido glicirrízico (Anvisa/EU)": {
        "ES": "Límite regulatorio de ácido glicirrícico (Anvisa/UE)"
    },
    "Rotulagem obrigatória de alérgeno (derivado de trigo)": {
        "ES": "Etiquetado obligatorio de alérgeno (derivado de trigo)"
    },
    "Precursor de hidroquinona - escrutínio regulatório elevado (clareadores)": {
        "ES": "Precursor de hidroquinona - escrutinio regulatorio elevado (blanqueadores)"
    },
    "Derivado de Cannabis sativa - barreiras regulatórias de importação/registro": {
        "ES": "Derivado de Cannabis sativa - barreras regulatorias de importación/registro"
    },
    "Uso profissional acima de 10% restrito a procedimentos de peeling": {
        "ES": "Uso profesional por encima del 10% restringido a procedimientos de peeling"
    },
    "Proibido em produtos leave-on para menores de 3 anos (EU 2019/831)": {
        "ES": "Prohibido en productos leave-on para menores de 3 años (UE 2019/831)"
    },
    "Sem monografia tópica consolidada na Anvisa - uso off-label em skincare": {
        "ES": "Sin monografía tópica consolidada en Anvisa - uso off-label en skincare"
    },
    "Acima de 10% classificado como uso dermatológico/prescrição em alguns mercados": {
        "ES": "Por encima del 10% clasificado como uso dermatológico/con receta en algunos mercados"
    },
    "Sem limite numérico de ácido glicirrízico no Regulamento (CE) 1223/2009 - controlado via rotulagem de alérgenos": {
        "ES": "Sin límite numérico de ácido glicirrícico en el Reglamento (CE) 1223/2009 - controlado mediante etiquetado de alérgenos"
    },
    "Alpha-Arbutin listado no Anexo III, entrada 77, do Regulamento (CE) 1223/2009": {
        "ES": "Alfa-Arbutina listada en el Anexo III, entrada 77, del Reglamento (CE) 1223/2009"
    },
    "Extratos de folha/flor de Cannabis sativa restritos no Anexo II; derivados de semente (óleo/CBD de cânhamo industrial) avaliados caso a caso pela ECHA": {
        "ES": "Extractos de hoja/flor de Cannabis sativa restringidos en el Anexo II; derivados de semilla (aceite/CBD de cáñamo industrial) evaluados caso por caso por la ECHA"
    },
    "Proibido em produtos leave-on para menores de 3 anos - Regulamento (UE) 2019/831, Anexo III entrada 98": {
        "ES": "Prohibido en productos leave-on para menores de 3 años - Reglamento (UE) 2019/831, Anexo III entrada 98"
    },
    "Sem entrada específica no CosIng para uso tópico cosmético - uso off-label": {
        "ES": "Sin entrada específica en CosIng para uso tópico cosmético - uso off-label"
    },
    "Concentrações mais altas (uso dermatológico) tratadas como produto medicinal, fora do escopo do Regulamento (CE) 1223/2009": {
        "ES": "Concentraciones más altas (uso dermatológico) tratadas como producto medicinal, fuera del alcance del Reglamento (CE) 1223/2009"
    },
    "Ativo não mapeado na base regulatória local": {
        "ES": "Activo no mapeado en la base regulatoria local"
    },
}

# Tradução de 'max_concentration_allowed' por idioma - só as entradas que
# carregam anotação textual em PT (não os valores puramente numéricos/"N/A",
# que já são neutros entre os idiomas). Mesmo motivo/mecanismo de vazamento
# de ALERT_TRANSLATIONS acima: o valor é injetado direto no prompt da LLM.
MAX_CONCENTRATION_TRANSLATIONS: Dict[str, Dict[str, str]] = {
    "N/A (rotulagem obrigatória)": {
        "ES": "N/A (etiquetado obligatorio)"
    },
    "2.0% (creme facial) / 0.5% (loção corporal)": {
        "ES": "2.0% (crema facial) / 0.5% (loción corporal)"
    },
    "pH ≥ 3.5 (sem teto percentual fixo no Anexo III para uso profissional)": {
        "ES": "pH ≥ 3.5 (sin techo porcentual fijo en el Anexo III para uso profesional)"
    },
}

# Tradução ES dos 3 pequenos vocabulários fechados (status/restriction_level/
# trend) usados nos dossiês regulatório e comercial - PT-BR/PT-PT continuam
# recebendo o valor canônico original (sem mudança de comportamento para
# esses 2 idiomas), só ES recebe um rótulo traduzido antes de entrar no
# prompt da LLM (mesmo mecanismo de vazamento das tabelas acima).
STATUS_LABEL_ES = {
    "APROVADO_USO_TOPICO": "Aprobado para uso tópico",
    "USO_RESTRITO": "Uso restringido",
    "EM_ANALISE": "En análisis",
    "FALHA_CONSULTA": "Fallo en la consulta",
}
RESTRICTION_LEVEL_LABEL_ES = {
    "NENHUM": "Ninguno",
    "BAIXO": "Bajo",
    "MEDIO": "Medio",
    "ALTO": "Alto",
    "DESCONHECIDO": "Desconocido",
}
TREND_LABEL_ES = {
    "CRESCENTE": "Creciente",
    "ESTAVEL": "Estable",
    "DECRESCENTE": "Decreciente",
    "NEUTRO": "Neutro",
}


def localize_alerts(alerts: Optional[List[str]], lang: str) -> List[str]:
    """
    Traduz cada alerta da lista (armazenados em PT-BR) para o idioma do
    relatório antes de entrar no prompt da LLM. PT-BR/PT-PT retornam a lista
    como está. ES busca em ALERT_TRANSLATIONS por alerta individual - se um
    alerta específico não estiver cadastrado lá, retorna o PT-BR original com
    um prefixo de alerta visível (fail-loud também em tempo de execução, não
    só na importação do módulo).
    """
    if not alerts:
        return []
    lang_key = lang.upper()
    if lang_key != "ES":
        return list(alerts)
    localized = []
    for alert_pt in alerts:
        translations = ALERT_TRANSLATIONS.get(alert_pt)
        if not translations or "ES" not in translations:
            localized.append(f"[TRADUÇÃO ES AUSENTE] {alert_pt}")
        else:
            localized.append(translations["ES"])
    return localized


def localize_max_concentration(value: Optional[str], lang: str) -> Optional[str]:
    """Traduz 'max_concentration_allowed' (quando carrega anotação textual em PT) para o idioma do relatório."""
    lang_key = lang.upper()
    if lang_key != "ES" or not value:
        return value
    translations = MAX_CONCENTRATION_TRANSLATIONS.get(value)
    return translations["ES"] if translations and "ES" in translations else value


def localize_status(value: Optional[str], lang: str) -> Optional[str]:
    """Traduz o status regulatório canônico (ex.: 'APROVADO_USO_TOPICO') para o idioma do relatório."""
    lang_key = lang.upper()
    if lang_key != "ES" or not value:
        return value
    return STATUS_LABEL_ES.get(value, value)


def localize_restriction_level(value: Optional[str], lang: str) -> Optional[str]:
    """Traduz o nível de restrição canônico (ex.: 'ALTO') para o idioma do relatório."""
    lang_key = lang.upper()
    if lang_key != "ES" or not value:
        return value
    return RESTRICTION_LEVEL_LABEL_ES.get(value, value)


def localize_trend(value: Optional[str], lang: str) -> Optional[str]:
    """Traduz a tendência comercial canônica (ex.: 'CRESCENTE') para o idioma do relatório."""
    lang_key = lang.upper()
    if lang_key != "ES" or not value:
        return value
    return TREND_LABEL_ES.get(value, value)


def _validate_regulatory_registries() -> None:
    """
    Falha explicitamente (levanta RegulatoryRegistryTraceabilityError) se
    QUALQUER entrada das 3 bases regulatórias estiver sem 'source' (string
    não vazia - citando a norma/resolução que embasa a entrada, ou
    declarando explicitamente a ausência de uma citação específica), sem
    'last_verified' (string YYYY-MM-DD válida - data da última confirmação
    manual de que a entrada ainda reflete a regulação vigente), se o
    'source' da entrada não tiver uma tradução ES registrada em
    SOURCE_TRANSLATIONS (CORRIGIDO 2026-08-20 - achado de auditoria: o
    disclaimer REG vazava em português no relatório ES), ou se algum alerta
    de REGULATORY_REGISTRY/EU_REGULATORY_OVERRIDES não tiver tradução ES
    registrada em ALERT_TRANSLATIONS (CORRIGIDO 2026-08-20 - achado de
    auditoria: vazamento de vocabulário técnico em português na PROSA gerada
    pela LLM, via 'alerts' injetado no prompt de generate_recommendations;
    FDA_REGULATORY_OVERRIDES fica de fora desta checagem porque seus
    'alerts' nunca chegam ao prompt da LLM nem ao PDF, ver ALERT_TRANSLATIONS
    acima). Chamada uma vez na importação do módulo - uma entrada nova
    adicionada sem esses requisitos derruba a importação do pacote inteiro,
    não passa despercebida.
    """
    registries = (
        ("REGULATORY_REGISTRY", RegulatoryComexConnector.REGULATORY_REGISTRY),
        ("EU_REGULATORY_OVERRIDES", RegulatoryComexConnector.EU_REGULATORY_OVERRIDES),
        ("FDA_REGULATORY_OVERRIDES", RegulatoryComexConnector.FDA_REGULATORY_OVERRIDES),
    )
    # Só as 2 bases cujos 'alerts' realmente chegam ao prompt da LLM (ver
    # docstring acima) - FDA_REGULATORY_OVERRIDES não participa desta checagem.
    registries_requiring_alert_translation = {"REGULATORY_REGISTRY", "EU_REGULATORY_OVERRIDES"}
    for registry_name, registry in registries:
        for asset_id, entry in registry.items():
            source = entry.get("source")
            if not source or not isinstance(source, str):
                raise RegulatoryRegistryTraceabilityError(
                    f"{registry_name}['{asset_id}'] está sem 'source' (obrigatório, string não vazia) - "
                    "toda entrada precisa citar a base/norma que embasa a classificação, mesmo que seja "
                    "para declarar explicitamente a ausência de uma citação específica."
                )
            last_verified = entry.get("last_verified")
            if not last_verified or not isinstance(last_verified, str) or not _LAST_VERIFIED_DATE_PATTERN.match(last_verified):
                raise RegulatoryRegistryTraceabilityError(
                    f"{registry_name}['{asset_id}'] está sem 'last_verified' válido (obrigatório, formato "
                    "YYYY-MM-DD) - toda entrada precisa registrar a data da última confirmação manual de "
                    "que ainda reflete a regulação vigente."
                )
            if source not in SOURCE_TRANSLATIONS or "ES" not in SOURCE_TRANSLATIONS[source]:
                raise RegulatoryRegistryTraceabilityError(
                    f"{registry_name}['{asset_id}'] tem 'source' sem tradução ES registrada em "
                    f"SOURCE_TRANSLATIONS - toda 'source' distinta usada nas 3 bases precisa de uma "
                    f"tradução ES antes de entrar em produção, para não vazar texto em português no "
                    f"relatório espanhol. Texto sem tradução: {source!r}"
                )
            if registry_name in registries_requiring_alert_translation:
                for alert in entry.get("alerts") or []:
                    if alert not in ALERT_TRANSLATIONS or "ES" not in ALERT_TRANSLATIONS[alert]:
                        raise RegulatoryRegistryTraceabilityError(
                            f"{registry_name}['{asset_id}'] tem um alerta sem tradução ES registrada em "
                            f"ALERT_TRANSLATIONS - todo alerta distinto usado nestas bases precisa de uma "
                            f"tradução ES antes de entrar em produção, para não vazar vocabulário técnico "
                            f"em português na prosa gerada pela LLM para o relatório espanhol. "
                            f"Texto sem tradução: {alert!r}"
                        )


_validate_regulatory_registries()


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
