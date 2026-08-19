# Metodologia de Cálculo — PhytoDemand Report (Actives Predict)

Este documento descreve, para cada métrica exibida no relatório, **de onde vem
cada número de entrada** e **a fórmula exata** que o transforma no score
final. Não é a definição conceitual (essa está na legenda do próprio PDF) —
é o rastreamento literal do código-fonte (`core/score_engine.py`), atualizado
em conjunto com ele. Se o código mudar, este arquivo precisa mudar junto.

Um rastro de cálculo ao vivo, com números reais de dois ativos, está em
[`docs/calculation_trace_2026-08-19.md`](docs/calculation_trace_2026-08-19.md)
— gerado por [`scripts/calculation_trace.py`](scripts/calculation_trace.py),
que pode ser reexecutado a qualquer momento para qualquer ativo:
`python scripts/calculation_trace.py AT-XXX`.

## Aviso de proveniência dos dados (leia antes do resto)

Nem toda fonte usada neste protótipo é uma API real. Isto é intencional e
documentado no próprio código, mas repetido aqui porque afeta diretamente a
interpretação de cada score:

| Fonte | Real ou Mock? | Onde |
|---|---|---|
| PubMed (PMIDs, títulos, resumos) | **REAL** — NCBI E-utilities, chamada ao vivo | `connectors/pubmed.py`, `connectors/pubmed_validator.py` |
| Patentes (números, títulos, assignees) | **MOCK/FABRICADO** — base embutida no código, nunca uma API real | `connectors/patents.py` `_mock_database()` |
| Validação de patente citada no relatório (Google Patents) | **REAL** — só filtra o que é *exibido como citação*, ver limitação conhecida abaixo | `connectors/patents.py` `validate_patent()` |
| Comércio exterior (volume, fornecedores, tendência) | **MOCK determinístico** — sem credenciais de API configuradas (`.env`), nunca uma chamada real à Comex Stat/Eurostat | `connectors/regulatory_comex.py`, `connectors/trade_eurostat.py` |
| Status regulatório (Anvisa/INFARMED/AEMPS/FDA) | **MOCK curado manualmente** — base de conhecimento no código, não uma consulta em tempo real | `connectors/regulatory_comex.py` `REGULATORY_REGISTRY` |

## ⚠️ Limitação conhecida: Tração Industrial usa patentes NÃO validadas ao vivo

**Achado desta auditoria (2026-08-19), ainda não corrigido no código:**
a validação real de patentes via Google Patents (`patent_conn.validate_patent_batch`,
chamada em `main.py` na Fase 3) hoje só filtra os `patent_ids` **exibidos como
citação** no relatório (as tags "PAT: ..." ao lado de cada recomendação). Ela
**não realimenta** o cálculo de Tração Industrial — esse score continua sendo
calculado em cima de `_patent_traction_results`, que vem direto da base mock
(`fetch_patents_mock`), sem nunca passar por `validate_patent()`.

Na prática, isso significa que o **número** "Tração Industrial: 8.0/10" pode
aparecer no relatório mesmo quando **nenhuma** das patentes que o compõem
sobrevive à checagem real no Google Patents — foi exatamente o que aconteceu
com Chá Verde e Cúrcuma no rastro de cálculo do dia 2026-08-19 (ver documento
linkado acima): as 2 patentes mock de cada ativo falharam a validação ao vivo
(títulos reais de patentes reais, mas sobre assuntos completamente diferentes:
vidro óptico, impressão jato de tinta), e mesmo assim os dois ativos exibem
8.0/10 de Tração Industrial no PDF.

Isso não é uma falha da validação em si (ela funciona corretamente e é
honesta sobre suas próprias rejeições) — é uma lacuna de integração: o
resultado da validação não propaga de volta para o score. Corrigir isso
exigiria recalcular `tracao_industrial` a partir de `patent_ids` (já
validados) em vez de `_patent_traction_results` (mock bruto), o que por sua
vez tende a zerar a Tração Industrial de praticamente todos os ativos
selecionados hoje, já que a base de patentes inteira é fabricada e não
corresponde a nenhuma patente real sobre o ativo em questão.

## 1. Tração Científica (T_c)

**Onde:** `core/score_engine.py` → `calculate_scientific_traction_breakdown()`

**Entradas:**
- `pubmed_matches`: lista de artigos do PubMed na **janela móvel de 12 meses**
  (`connectors/pubmed.py` `TRACTION_WINDOW_DAYS = 365`), cada um processado
  por `PubMedConnector.fetch_article_details()` — que faz um `efetch` REAL
  no NCBI e só marca `entity_match` quando o título/resumo confirma a
  entidade do ativo com relevância tópica (Nível ≥ 2, `core/entity_resolver.py`).
  Artigos sem `entity_match` **não contam** para nenhum componente.
- `baseline_36m_count`: contagem bruta (não individualmente verificada, só o
  `count` retornado pelo `esearch` — custo de API mínimo) de artigos numa
  janela de 36 meses **estritamente anterior** aos 12 meses de análise
  (meses 13–48 atrás, sem sobreposição). Usada só pelo componente [G].

**Regra de piso (verificada por teste, `tests/test_score_engine.py`):**
se `verified_count == 0` (nenhum artigo com `entity_match`), a função retorna
`score = 0.0` diretamente — **não** passa pela fórmula ponderada abaixo. Isso
é deliberado: o componente [G] usa 5.0 como valor neutro quando não há
literatura para comparar, e se essa checagem não existisse, um ativo sem
nenhuma evidência receberia `0.35 * 5.0 = 1.75/10` em vez de `0.0/10`.

**Fórmula (quando `verified_count > 0`):**

```
T_c = w_V·[V] + w_G·[G] + w_A·[A] + w_Q·[Q]

pesos (PROVISÓRIOS, não calibrados por backtesting):
  w_V = 0.25   w_G = 0.35   w_A = 0.20   w_Q = 0.20

[V] Volume       = min(10, Σ(confidence_score dos artigos verificados) × 2.2)
[G] Crescimento  = 5 + 5 · clamp((taxa_atual − taxa_base) / taxa_base, −1, 1)
                     taxa_atual = verified_count / 365
                     taxa_base  = baseline_36m_count / 1095
                     (sem baseline: [G] = 5.0, neutro — sem julgamento de tendência)
[A] Aplicabilidade = 10 × (nº artigos com relevance_level ≥ 2 / verified_count)
[Q] Qualidade      = 10 × (média dos confidence_score dos artigos verificados)
```

## 2. Tração Industrial (T_i)

**Onde:** `core/score_engine.py` → `calculate_industrial_traction()`

**Entradas:** `patent_matches` — patentes deduplicadas por família na janela
de 12 meses, vindas de `connectors/patents.py` `fetch_patents_mock()` (⚠️ base
MOCK, ver aviso de proveniência acima) e processadas por `process_patent()`
(que só faz correspondência de texto local do título mock contra o
`entity_resolver` — nunca uma checagem de existência real nesta etapa).

**Fórmula:**
```
se total_patents == 0: T_i = 0.0

assignees = titulares únicos entre as patentes
diversity_bonus = min(2.0, nº de assignees únicos × 1.0)
base_score = min(8.0, total_patents × 3.0)
T_i = min(10.0, base_score + diversity_bonus)
```

## 3. Sinal de Risco de Oferta Observado

**Onde:** `core/score_engine.py` → `calculate_supply_risk()`, combinando dois
sub-sinais independentes:

- **Alerta Regulatório** (`calculate_regulatory_alert_level`): deriva de
  `regulatory_alerts["restriction_level"]` — vem de
  `connectors/regulatory_comex.py` `get_regulatory_matrix()`, que compara as
  3 jurisdições monitoradas (Anvisa/UE/FDA) e usa o **pior caso**. Fonte:
  base de conhecimento curada manualmente no código (`REGULATORY_REGISTRY`),
  não uma consulta em tempo real.
- **Sinal Comercial/Comex** (`calculate_commercial_signal_level`): deriva de
  `commercial_signals["suppliers_count"]` — `< 2` fornecedores = "OFERTA
  CRÍTICA", `< 5` = "OFERTA LIMITADA", senão "OFERTA SAUDÁVEL". Fonte: mock
  determinístico (`connectors/trade_eurostat.py` para PT/ES,
  `connectors/regulatory_comex.py` seed BR para PT-BR) — nunca uma chamada
  real à Comex Stat/Eurostat neste ambiente (sem credenciais em `.env`).

```
se Alerta Regulatório == "ALERTA ALTO" OU Sinal Comercial == "OFERTA CRÍTICA":
    risco = "ALTO RISCO"
senão se Alerta Regulatório em ("ALERTA MÉDIO","ALERTA DESCONHECIDO") OU Sinal Comercial == "OFERTA LIMITADA":
    risco = "MEDIO RISCO"
senão:
    risco = "BAIXO RISCO"
```

O volume financeiro exibido (`Entre USD X e USD Y`) é o valor bruto mock
(`volume_usd_annual`) passado por `core/formatting.py` `format_usd_estimate()`,
que aplica uma faixa de ±10% e arredonda — **nunca** entra diretamente em
nenhum cálculo de score, é só descritivo.

## 4. Confiança do Sinal

**Onde:** `core/score_engine.py` → `calculate_confidence_level()`

**Entrada:** `all_matches = pubmed_data + patent_data` (⚠️ inclui as
patentes mock, mesmo problema de proveniência do item 2 acima).

```
conf_scores = [confidence_score de cada match com entity_match presente]
se lista vazia: "BAIXA"
avg = média(conf_scores)
se avg >= 0.95 E nº total de matches >= 2: "ALTA"
senão se avg >= 0.85: "MÉDIA"
senão: "BAIXA"
```

## Testes automatizados que cobrem esta metodologia

- `tests/test_score_engine.py` — confirma que ausência total de evidência
  (`pubmed_matches=[]`, `patent_matches=[]`) produz exatamente `0.0/10` nos
  dois scores, nunca um valor intermediário; e que evidência real presente
  produz um score `> 0.0` (contraste).
- `tests/test_pmid_validator_regression.py` — regressão dedicada ao PMID
  fabricado `42596530` (Bakuchiol), identificado na primeira auditoria do
  relatório: faz uma chamada ao vivo à NCBI e falha explicitamente se esse
  PMID específico voltar a ser aceito como evidência válida.
