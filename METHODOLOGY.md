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

A calibração de `INDUSTRIAL_TRACTION_N_REF` (ver "Tração Industrial
saturava..." abaixo) é reexecutável em dois passos: `python -m
scripts.tier_recalibration_collect` roda o catálogo completo ao vivo e salva
a evidência bruta em `data/cache/tier_recalibration_raw.json`; `python -m
scripts.tier_recalibration_report` reaplica a fórmula/regra atuais sobre
essa evidência (sem nova chamada de rede) e imprime a tabela antes/depois.

## Aviso de proveniência dos dados (leia antes do resto)

Nem toda fonte usada neste protótipo é uma API real. Isto é intencional e
documentado no próprio código, mas repetido aqui porque afeta diretamente a
interpretação de cada score:

| Fonte | Real ou Mock? | Onde |
|---|---|---|
| PubMed (PMIDs, títulos, resumos) | **REAL** — NCBI E-utilities, chamada ao vivo | `connectors/pubmed.py`, `connectors/pubmed_validator.py` |
| Patentes (números, títulos, assignees) | **REAL** — EPO OPS (Open Patent Services), OAuth2 + busca CQL ao vivo, desde 2026-08-19 (ver seção abaixo). `fetch_patents_mock()` continua existindo só para dev/teste offline, nunca chamada implicitamente | `connectors/patents.py` `fetch_patents()` / `fetch_patents_live()` |
| Validação de patente (Google Patents) | **REAL** — camada independente de confirmação sobre o resultado já real do EPO OPS; filtra tanto o que é *exibido como citação* quanto o que *entra no cálculo de Tração Industrial* (corrigido em 2026-08-19, ver abaixo) | `connectors/patents.py` `validate_patent()` |
| Comércio exterior (volume, fornecedores, tendência) | **MOCK determinístico** — sem credenciais de API configuradas (`.env`), nunca uma chamada real à Comex Stat/Eurostat | `connectors/regulatory_comex.py`, `connectors/trade_eurostat.py` |
| Status regulatório (Anvisa/INFARMED/AEMPS/FDA) | **MOCK curado manualmente** — base de conhecimento no código, não uma consulta em tempo real | `connectors/regulatory_comex.py` `REGULATORY_REGISTRY` |

## ✅ IMPLEMENTADO (2026-08-19): Conector real do EPO OPS substitui a base mock de patentes

**O que mudou:** `connectors/patents.py` ganhou `fetch_patents()`/`fetch_patents_live()` — um
conector real do EPO OPS (Open Patent Services, `https://ops.epo.org`), com:
- **OAuth2 client-credentials** (`EPO_OPS_CONSUMER_KEY`/`EPO_OPS_CONSUMER_SECRET` em `.env`,
  nunca commitado - `.env` está no `.gitignore`), token cacheado em memória até pouco antes de expirar.
- **Busca real em CQL** (Contextual Query Language) no título/resumo (`ti=... or ab=...`),
  restrita à janela de datas (`pd within`) e com as exclusões do ativo aplicadas na origem.
  Achado de teste ao vivo, não documentado de forma óbvia: o OPS aceita **no máximo 1
  operador NOT por query** (erro `CLIENT.NotOperatorMaxNumber` acima disso) - por isso as
  exclusões do ativo entram agrupadas numa única cláusula `not (ab="x" or ab="y" ...)`, e o
  operador correto é `not` (um único token `ANDNOT`, usado no rascunho anterior deste
  conector, **não** é um operador CQL válido no OPS - resulta silenciosamente em zero
  resultados, sem erro).
- **Parsing de XML real** (`xml.etree.ElementTree`) da resposta `published-data/search/biblio`:
  título, titular (assignee), data de publicação, ano de depósito e classificação IPC vêm do
  documento OPS; a família de patentes usa o `family-id` (INPADOC) retornado pelo próprio OPS
  no lugar do `family_id` fabricado da base mock.
  - `fetch_patents()` é o ponto de entrada de produção (usado por `main.py`,
    `scripts/calculation_trace.py`, `scripts/audit_report_single_asset.py`) e **exige** as
    credenciais - levanta `PatentConnectorConfigError` se ausentes, nunca cai
    silenciosamente para a base mock.
- **Rate-limit/backoff**: intervalo mínimo entre requisições + retry com backoff exponencial
  em HTTP 429/403 (quota/throttling) e em timeout/erro de rede - mesmo padrão de
  `connectors/pubmed.py`. HTTP 404 com fault `SERVER.EntityNotFound` é tratado como resposta
  válida de "zero resultados", nunca como falha.
- **Cache em disco de 14 dias** (`data/cache/epo_ops/`, no `.gitignore` - dado transitório, não
  fonte), chaveado pela query CQL exata + janela de datas: reexecuções da mesma busca dentro
  desse prazo não geram nova chamada de rede nem consomem quota do OPS.

**Confirmação com dados reais** (`python -m scripts.calculation_trace AT-009 AT-015`,
2026-08-19, após a ativação da conta EPO OPS): Chá Verde encontrou 35 patentes no OPS (25
retornadas após dedup por família na página de resultados), das quais **3** sobreviveram à
validação ao vivo no Google Patents (ex.: `KR20260047328A` — *"PDRN A Composition for skin
condition improvement comprising PDRN derived from Camellia sinensis"*,
https://patents.google.com/patent/KR20260047328A/en); Cúrcuma encontrou 32 patentes, das quais
**5** sobreviveram (ex.: `KR20260062743A` — *"Method for manufacturing of Curcuma longa Rhizome
and the cosmetic composition containing the same"*,
https://patents.google.com/patent/KR20260062743A/en). Tração Industrial deixou de ser 0.0/10 nos
dois casos — passando a derivar de patentes reais e verificáveis publicamente, não de dado
fabricado. (Os valores exatos exibidos nesta primeira confirmação, 10.0/10 para ambos, foram
posteriormente identificados como sintoma de uma fórmula saturada — ver a seção seguinte para a
correção e os valores recalibrados: 7.1/10 e 9.2/10, respectivamente.)

## ✅ CORRIGIDO (2026-08-19): Tração Industrial saturava com poucas patentes; tier "Estrela Emergente" aceitava ciência zerada

**O achado:** na primeira confirmação com dados reais do EPO OPS (seção
acima), Chá Verde (3 patentes validadas) e Cúrcuma (5 patentes validadas)
mostraram exatamente o mesmo 10.0/10 de Tração Industrial. Investigando por
quê, dois problemas distintos foram encontrados na mesma tarde:

1. **`calculate_industrial_traction()` saturava em 10.0/10 já a partir de 3
   patentes de 2 titulares distintos** — a fórmula anterior
   (`base_score = min(8, contagem×3)`, `diversity_bonus = min(2, nº titulares)`)
   era uma função degrau: qualquer ativo acima desse piso mínimo batia o
   teto absoluto, sem diferenciar 3 patentes de 300.
2. **A regra de tier "Estrela Emergente" usava uma SOMA, não um piso em cada
   componente** (`core/predictive_ranking.py`) — `sci + ind >= 10.0`
   permitia que Tração Industrial saturada sozinha (`ind = 10.0`) empurrasse
   um ativo para "Estrela Emergente" mesmo com Tração Científica **zerada**
   (`sci = 0.0`). A legenda do relatório define essa categoria como
   "elevada validação científica" — um componente zerado nunca deveria
   satisfazer essa definição. Rodando o catálogo completo (ver calibração
   abaixo), esse padrão apareceu em **4 dos 35 ativos** (Cúrcuma incluída),
   não só no par originalmente reportado.

### Correção 1 — Tração Industrial: fórmula log-comprimida com N_ref calibrado

**Onde:** `core/score_engine.py` → `calculate_industrial_traction()` /
`INDUSTRIAL_TRACTION_N_REF`

```
se total_patents == 0: T_i = 0.0
senão: T_i = min(10.0, 10 × log(1 + total_patents) / log(1 + N_REF))
```

`N_REF` é o volume de patentes validadas que define o que "dominância
industrial" significa **neste catálogo/nicho específico** (ativos botânicos
dermocosméticos, janela móvel de 12 meses) — não um número importado de
outro domínio (portfólios de patente farmacêutica, por exemplo, são ordens
de grandeza maiores e não serviriam de referência aqui).

**Calibração (2026-08-19):** `scripts/tier_recalibration_collect.py` rodou o
catálogo completo (35 ativos) uma única vez com os conectores reais (PubMed/
NCBI + EPO OPS + validação Google Patents), coletando a contagem de
patentes validadas de cada um. Distribuição observada:

| Patentes validadas | Nº de ativos |
|---:|---:|
| 0 | 15 |
| 1 | 11 |
| 2 | 2 |
| 3 | 1 (Chá Verde) |
| 4 | 1 |
| 5 | 4 (Cúrcuma incluída) |
| 6 | 1 (máximo observado — Cânhamo/CBD) |

`N_REF = 6` (o máximo observado). **Nota de proveniência:** diferente do
piso 5.0 de elegibilidade (ver caixa "PISO PROVISÓRIO" mais abaixo neste
documento), `N_REF=6` vem de uma estatística sobre os 35 ativos inteiros
(o máximo observado), não de um único caso isolado - mas ainda é um valor
extremo (máximo, não mediana/percentil), sensível a outlier e a como o
catálogo evolui; carrega o mesmo compromisso de revisão trimestral. Um
ativo com 6 patentes validadas atinge o teto de 10.0/10 por construção; a
curva log dá retornos decrescentes:

| Patentes validadas | T_i (fórmula nova) | T_i (fórmula antiga) |
|---:|---:|---:|
| 0 | 0.0 | 0.0 |
| 1 | 3.6 | 4.0 |
| 2 | 5.6 | 8.0 |
| 3 | 7.1 | 10.0 |
| 4 | 8.3 | 10.0 |
| 5 | 9.2 | 10.0 |
| 6 | 10.0 | 10.0 |

O `diversity_bonus` (nº de titulares distintos) da fórmula anterior foi
**removido**: na calibração de 2026-08-19, em **todos** os 35 ativos do
catálogo o nº de titulares distintos foi idêntico ao nº de patentes
validadas — nenhum titular repetiu uma patente validada em nenhum ativo. O
termo nunca divergiu da contagem simples nesta base real, então não
carregava informação adicional que justificasse a complexidade extra.

**Cadência de revisão:** recalibrar **trimestralmente** (ou sempre que um
novo recorde de patentes validadas for observado no catálogo), reexecutando
`scripts/tier_recalibration_collect.py` e atualizando `INDUSTRIAL_TRACTION_N_REF`
— um N_ref desatualizado (baixo demais) volta a saturar o topo da escala; um
N_ref alto demais comprime todo o catálogo perto de zero. Próxima
recalibração agendada: 2026-11-19.

### Correção 2 — Tier "Estrela Emergente": piso mínimo em CADA componente, não soma

**Onde:** `core/predictive_ranking.py` → `classify_precedence_tier()`
(classificação do catálogo completo, auditoria/console) **e**
`select_predictive_assets()` (seleção dos 8 ativos exibidos no PDF — mesmo
defeito, encontrado numa segunda função independente ao aplicar a correção).

```
MIN_SCI_FOR_EMERGING_STAR = 5.0  # ponto médio da escala 0-10
MIN_IND_FOR_EMERGING_STAR = 5.0  # mesmo valor neutro que o componente [G] de T_c já usa

Estrela Emergente  ⟺  sci >= MIN_SCI_FOR_EMERGING_STAR  E  ind >= MIN_IND_FOR_EMERGING_STAR
```

5.0 é o ponto médio da escala 0-10 e já é o valor "neutro" usado pelo
componente [G] de Tração Científica na ausência de linha de base (ver
seção 1 abaixo) — não é um número novo introduzido só para este piso. Como
`MIN_SCI + MIN_IND == 10.0`, este par de pisos independentes subsume
estritamente a antiga checagem de soma (qualquer par que passe os dois
pisos automaticamente soma ≥ 10.0), por isso a soma foi removida em vez de
mantida como uma terceira condição redundante.

A mesma correção foi aplicada nos dois lugares onde a fórmula antiga estava
duplicada como atalho de texto (não afetam o tier, só o *tom* da
recomendação padrão de Inovação & P&D quando a síntese via LLM falha):
`core/llm_analysis.py` `_default_innovation_recommendation()` e
`reports/pdf_generator.py` `PDFReportGenerator._default_innovation_recommendation()`
— "alta" prioridade agora também exige o piso em cada componente, não só a
soma ≥ 10.

### Antes/depois no catálogo completo (35 ativos, mesma evidência bruta)

`scripts/tier_recalibration_report.py` reaplica as duas fórmulas (antiga e
nova) sobre a MESMA coleta ao vivo de 2026-08-19 (sem nova chamada de rede),
isolando o efeito das correções da flutuação natural do PubMed/EPO OPS
entre execuções. **4 de 35 ativos mudaram de tier** na classificação de
catálogo completo (`classify_precedence_tier`), todos na mesma direção
esperada — "Estrela Emergente" (indevido, ciência zerada) → "Monitoramento":

| Ativo | T_c | Patentes validadas | T_i antes → depois | Tier antes → depois |
|---|---:|---:|---|---|
| AT-005 Bidens Pilosa | 0.0 | 5 | 10.0 → 9.2 | Estrela Emergente → Monitoramento |
| AT-010 Semente de Uva | 0.0 | 4 | 10.0 → 8.3 | Estrela Emergente → Monitoramento |
| AT-011 Romã | 0.0 | 5 | 10.0 → 9.2 | Estrela Emergente → Monitoramento |
| AT-015 Cúrcuma | 0.0 | 5 | 10.0 → 9.2 | Estrela Emergente → Monitoramento |

Nenhum ativo com Tração Científica real (`sci >= 5.0`) mudou de tier — ex.:
AT-017 Calendula (`sci=5.0`, `ind=5.6` na fórmula nova) permanece "Estrela
Emergente" corretamente, é um caso genuíno de sinal duplo.

Na seleção real dos 8 ativos exibidos no PDF (`select_predictive_assets()`,
mesma coleta): **AT-010 Semente de Uva** deixa de ser selecionada como
"Emerging Stars" (não qualifica mais sozinha, e não há candidato com
evidência suficiente para preenchê-la nessa categoria); **AT-029 Cânhamo/CBD**
passa a preencher a vaga aberta no relatório de 8 ativos, via o mecanismo de
**preenchimento de última instância** já existente (`select_predictive_assets()`,
passo 4 — garante sempre exatamente 8 linhas no relatório completando com o
próximo melhor sinal combinado, mesmo abaixo do piso; é o único caso em que
um ativo sem os dois pisos ainda pode aparecer sob o rótulo "Emerging Stars"
no PDF, por não haver um badge visual dedicado a "preenchimento" — ressalva
pré-existente ao rótulo, não uma regressão desta correção, mas vale registrar
como candidato a melhoria futura de UX do relatório).

**Testes:** `tests/` (9 testes, incluindo os que travam Tração
Científica/Industrial em 0.0 na ausência de evidência) continuam passando
sem alteração após as duas correções.
a validação real de patentes via Google Patents (`patent_conn.validate_patent_batch`)
só era chamada em `main.py` na Fase 3, e só filtrava os `patent_ids`
**exibidos como citação** no relatório (as tags "PAT: ..." ao lado de cada
recomendação) — **sem realimentar** o cálculo de Tração Industrial, que
continuava sendo feito em cima de `_patent_traction_results` vindo direto da
base mock (`fetch_patents_mock`), nunca passado por `validate_patent()`.

Na prática, isso significava que o número "Tração Industrial: 8.0/10" podia
aparecer no relatório mesmo quando **nenhuma** das patentes que o compunham
sobrevivia à checagem real no Google Patents — foi exatamente o que aconteceu
com Chá Verde e Cúrcuma no rastro de cálculo capturado nesse dia (ver
[`docs/calculation_trace_2026-08-19.md`](docs/calculation_trace_2026-08-19.md),
histórico "antes da correção"): as 2 patentes mock de cada ativo falharam a
validação ao vivo (títulos reais de patentes reais, mas sobre assuntos
completamente diferentes — vidro óptico, impressão jato de tinta), e mesmo
assim os dois ativos exibiam 8.0/10 de Tração Industrial no PDF.

**Correção aplicada:** `main.py` (Fase 1) agora chama
`patent_conn.validate_patent_batch()` sobre os resultados da busca de
patentes de tração (12 meses) **antes** de montar `_patent_traction_results`,
e filtra para conter só as patentes que passaram na validação ao vivo. Tanto
`tracao_industrial` quanto `confianca_sinal` (que também soma `patent_data`
em `calculate_confidence_level`) agora derivam estritamente de evidência
confirmada — a validação da Fase 3 (citação exibida) tornou-se redundante e
foi removida, já que `item["patent_ids"]` chega pré-validado da Fase 1.

**Efeito colateral esperado e confirmado** (rastro pós-correção, mesmo
comando `python scripts/calculation_trace.py AT-009 AT-015`): como toda a
base de patentes (`connectors/patents.py` `_mock_database()`) é fabricada e
não corresponde a nenhuma patente real sobre os ativos do catálogo, a
Tração Industrial de Chá Verde e Cúrcuma caiu de 8.0/10 para **0.0/10** — e
o mesmo deve se repetir para a maioria/totalidade dos demais ativos em
execuções futuras, até que `connectors/patents.py` seja substituído por uma
fonte real de dados de patentes (EPO OPS ou equivalente, com credenciais).
Isso é o comportamento CORRETO diante da regra "nenhuma citação/score pode
depender de dado fabricado" — não é uma regressão.

## ✅ CORRIGIDO (2026-08-19): Preenchimento fabricado removido — o relatório agora tem tamanho dinâmico

**O achado, ao investigar o efeito das duas correções acima num relatório
real:** `core/predictive_ranking.py` `select_predictive_assets()` tinha um
4º passo ("Preenchimento") que completava a lista de ativos exibidos até
**exatamente 8**, mesmo quando não havia 8 candidatos genuínos — varria os
"próximos melhores por sinal combinado" entre os remanescentes (mesmo sem
evidência mínima verificada, como último recurso) e rotulava tudo como
"Emerging Stars", por não existir um badge dedicado a preenchimento.

Auditando os PDFs mais recentes já gerados localmente (`reports/output/`,
não versionado em git) para medir o estrago real, não hipotético:

| Relatório | Linhas "Estrela Emergente" | Das quais preenchimento fabricado |
|---|---:|---:|
| `relatorio_vanguard_pt_br.html` (mais recente) | 5 | **5 (100%)** |
| `relatorio_vanguard_pt_pt.html` (anterior) | 4 | **3 (75%)** |

Nenhuma das linhas de preenchimento identificadas batia sequer a regra de
soma antiga (`sci + ind >= 10.0`) — muito menos o piso por componente
introduzido na correção anterior. Isso incluía, por exemplo, Cúrcuma e
Centella Asiática rotuladas "Estrela Emergente" com Tração Científica
0.0/10 no relatório PT-PT, e Calendula (perfil de Dark Horse: `sci=5.0`,
`ind=0.0`) mal-rotulada "Estrela Emergente" no PT-BR.

**Correção:** o passo de preenchimento foi **removido sem substituto** — ver
`core/predictive_ranking.py` `select_predictive_assets()`. A partir de
agora:

- O relatório mostra exatamente os ativos que genuinamente qualificam em
  cada categoria (Risco Regulatório/Comercial, Estrela Emergente com piso
  em ambos os componentes, Dark Horse) — nunca menos rigoroso que isso.
- O total de linhas é **dinâmico**: pode ser 0 (nenhum ativo qualifica em
  nenhuma categoria nesta execução) até `high_risk_count + emerging_stars_count
  + dark_horses_count` (3+3+2=8 por padrão) — 8 deixa de ser uma garantia,
  passa a ser só o teto.
- Uma categoria sem nenhum candidato genuíno **não gera linha de tabela
  nenhuma** — decisão deliberada de NÃO criar uma 4ª categoria visual tipo
  "Dados Insuficientes para Classificação" como preenchimento de linha, por
  ser um mecanismo estruturalmente idêntico ao que acabou de ser removido
  (risco real de virar um novo "preenchimento disfarçado" no futuro).
  Em vez disso, `reports/pdf_generator.py` `generate_report()` computa
  quais categorias ficaram sem nenhum ativo nesta execução e imprime uma
  única nota textual curta logo abaixo da tabela (chave de tradução
  `no_qualifying_assets_note`, nas 3 línguas) listando-as — nunca uma linha
  de tabela fabricada. Se NENHUM ativo qualificar em nenhuma categoria, a
  tabela inteira é substituída por uma frase (`no_assets_at_all`) em vez de
  uma tabela vazia.

**Confirmação com execução real completa do catálogo (35 ativos, pipeline
corrigido de ponta a ponta - fórmula de patente recalibrada + regra de tier
em ambos os componentes + preenchimento removido), `python main.py`,
2026-08-19:**

Run ID `f6515246-8f06-4ec4-b0da-9ea97e418c3a` — os 3 idiomas (PT-BR/PT-PT/ES)
regeneraram com **exatamente 4 linhas cada** (antes: 8 fixas, artificial):

| Ativo | Categoria | T_c | T_i | Genuíno? |
|---|---|---:|---:|---|
| AT-033 Ácido Tranexâmico | High-Risk / Supply Alert | 4.3/10 | 0.0/10 | Sim — risco regulatório real |
| AT-019 Alcaçuz | High-Risk / Supply Alert | 0.0/10 | 3.6/10 | Sim — risco regulatório real |
| AT-026 Arbutin | High-Risk / Supply Alert | 0.0/10 | 0.0/10 | Sim — risco regulatório real |
| AT-017 Calendula | Emerging Stars | 5.0/10 | 5.6/10 | Sim — piso atingido nos dois componentes |

**Disruptive Dark Horses: 0 ativos genuínos nesta execução** — a categoria
não aparece na tabela; em seu lugar, a nota "Nenhum ativo qualificado nesta
categoria no período analisado: Sinal Científico sem Confirmação
Industrial." é exibida logo abaixo da tabela (as 3 línguas confirmadas).
Nenhuma das 4 linhas veio de preenchimento — confirmado tanto pelo código
(o passo 4 não existe mais) quanto inspecionando os valores: nenhuma
zeraria em T_c/T_i sem justificativa (High-Risk não exige evidência
científica por design; Calendula bate os dois pisos).

Classificação do catálogo completo (35 ativos, árvore de precedência,
auditoria/console - não é o que o PDF exibe): Dados Insuficientes/Não
Classificado: 17; Monitoramento: 7; Risco de Oferta: 6; Risco Regulatório:
4; Estrela Emergente: 1 (Calendula, a mesma que aparece no PDF).

**Testes automatizados:** `tests/test_predictive_ranking_regression.py`
(novo) trava especificamente esta classe de regressão - inclui um caso que
reproduz literalmente o catálogo em que o preenchimento antigo era
disparado (poucos candidatos genuínos) e confirma que a seleção retorna
só os genuínos, e um caso genérico que verifica, para toda linha
retornada por `select_predictive_assets()`, que a categoria atribuída é
sustentada por evidência mínima verificada e pelo piso de cada componente
que a categoria exige. Os 4 testes do arquivo foram verificados
manualmente contra o código ANTERIOR à correção (import direto do arquivo
na revisão do commit anterior) - os 4 falham nesse código antigo, inclusive
reproduzindo exatamente os casos reais da tabela acima (`sci=0.0`
rotulado "Emerging Stars").

**Outros lugares do pipeline com lógica parecida de "preencher até um
número fixo"?** Busca explícita (não assumida) em toda a árvore de
código-fonte por padrões de preenchimento (`while len(...) < N`,
`if len(...) < N`, fatiamento fixo `[:N]`, variáveis `target =`, `LIMIT`/
`TOP` em SQL, loops `range(N)` fixos, e as palavras
preench/completar/fallback/fill/pad em `core/`, `connectors/`, `reports/`,
`main.py`, `scripts/`) não encontrou nenhuma outra ocorrência deste padrão
específico. O único outro resultado fixo no pipeline é o teto de 8 do
próprio `select_predictive_assets()` (`high_risk_count=3 + emerging_stars_count=3
+ dark_horses_count=2`), que agora é só um LIMITE MÁXIMO por categoria, não
um piso — não preenche nada, só impede que uma categoria cresça sem limite.

Um achado ADJACENTE, mas de mecanismo diferente (não é preenchimento até um
número fixo, por isso não coberto pela correção acima): `high_risk_count`
limita a categoria de risco a no máximo 3 ativos exibidos: se houver um 4º
ativo com Risco Regulatório/Comercial real que não coube nesse teto, ele
continua elegível às categorias de oportunidade seguintes (Estrela
Emergente/Dark Horse) na mesma execução de `select_predictive_assets()` -
podendo aparecer rotulado como "Estrela Emergente" mesmo com risco
regulatório real. Isso é mitigado (mas não eliminado da exibição) pela
trava determinística pós-LLM em `main.py` Fase 3, que consulta o tier REAL
via `classify_precedence_tier()` (não o `predictive_category` exibido) para
decidir se libera uma recomendação executiva - mas o BADGE exibido na
tabela ainda poderia, em tese, mostrar "Estrela Emergente" para esse ativo.
Não foi corrigido nesta rodada por ser um mecanismo distinto do
preenchimento (é sobre teto de categoria, não sobre padding) - registrado
aqui para decisão separada.

## ✅ CORRIGIDO (2026-08-19): Separação categórica de risco corrigida (o achado acima, resolvido)

**Onde estava a trava parcial:** `main.py`, dentro do laço da Fase 3
(`for item in selected:`), linhas 445-450 (antes da correção):

```python
is_insufficient_data = (
    ranking_engine.classify_precedence_tier(item) == TIER_INSUFFICIENT_DATA
    or item["confianca_sinal"] == "BAIXA"
)
if is_insufficient_data:
    print(f"   🔒 Trava pós-LLM: {canonical_name} rebaixado para DADOS INSUFICIENTES ...")
```

**Por que era insuficiente:** essa trava só verifica DUAS condições -
`TIER_INSUFFICIENT_DATA` (evidência abaixo do mínimo) ou `confianca_sinal ==
"BAIXA"`. Ela **nunca checa `TIER_REGULATORY_RISK` nem `TIER_SUPPLY_RISK`**.
Um ativo com risco regulatório/comercial real, mas que não coubesse no teto
de `high_risk_count` (3 por padrão) em `select_predictive_assets()`, tinha
`is_insufficient_data = False` (porque seu tier real não é "Dados
Insuficientes", é "Risco Regulatório"/"Risco de Oferta" - condição
diferente da checada) — a LLM era chamada normalmente, uma recomendação
executiva completa era gerada, e `item["predictive_category"]` (que já
vinha errado de `select_predictive_assets()`, rotulado "Estrela Emergente")
era gravado sem nenhuma correção na linha 501 (`evaluations_by_lang[lang].append(...)`).
Ou seja: a trava protegia contra UM tipo de erro (evidência insuficiente)
mas não contra o outro (categoria de risco mal rotulada) - não era uma
separação categórica real entre risco e oportunidade, só um filtro de
qualidade de recomendação textual.

**A fonte do sinal de risco** (para responder objetivamente "qual risco"):
dois sinais independentes, cada um já teto-limitado a um sub-conjunto do
catálogo, nenhum deles vindo de julgamento da LLM:
- **Risco Regulatório** (`alerta_regulatorio == "ALERTA ALTO"`): vem de
  `connectors/regulatory_comex.py` `get_regulatory_matrix()`, que compara
  Anvisa/UE/FDA e usa o pior caso — fonte é `REGULATORY_REGISTRY`, uma base
  de conhecimento curada manualmente no código (ver aviso de proveniência
  no topo deste documento - **não é uma consulta em tempo real**).
- **Risco de Oferta comercial** (`sinal_comercial_comex` em `{"OFERTA
  CRÍTICA", "OFERTA LIMITADA"}`): vem de `connectors/regulatory_comex.py`/
  `connectors/trade_eurostat.py` `fetch_import_volume_mock()`/`fetch_trade_data()`
  — **mock determinístico** (seed SHA256 de asset_id+região), não uma
  chamada real à Comex Stat/Eurostat neste ambiente (sem credenciais em
  `.env` - mesmo aviso de proveniência).

**Correção implementada:** a separação foi movida para a ORIGEM do dado
exibido, não para mais uma checagem downstream. `core/predictive_ranking.py`
`select_predictive_assets()`, passos 2 (Emerging Stars) e 3 (Dark Horse),
agora excluem diretamente por `_is_regulatory_risk`/`_is_supply_risk`
(calculados dos mesmos dois sinais acima), não só por "já consumido no
passo 1" (`selected_ids`). Um ativo com sinal de risco real nunca mais
entra no pool de candidatos a Estrela Emergente/Dark Horse, esteja ou não
dentro do teto de `high_risk_count` — se não couber no teto, simplesmente
não aparece em nenhuma categoria nesta edição do relatório (mesmo
princípio "sem preenchimento, sem categoria emprestada" da correção
anterior). `item["predictive_category"]` e `classify_precedence_tier(item)`
agora são garantidamente consistentes para todo ativo selecionado - a
trava pós-LLM em `main.py` continua existindo, mas agora cobre só o eixo
evidência/confiança (seu propósito original), não precisa mais reconferir
risco.

**Teste de regressão:** `tests/test_predictive_ranking_regression.py`
adiciona 2 casos (risco regulatório e risco comercial, respectivamente)
que reproduzem literalmente a condição - 4 ativos com sinal de risco real,
todos também batendo o piso de Estrela Emergente/Dark Horse, com
`high_risk_count=3` (padrão). Verificado manualmente contra o código em
`HEAD` (estado do repositório antes de qualquer correção desta sessão,
já que nenhuma delas tinha sido commitada ainda): os 2 testes falham nesse
código antigo — o 4º ativo (`AT-704`/`AT-714`, o de menor Tração
Científica, excluído do teto por desempate) aparece rotulado "Emerging
Stars" em ambos os casos.

## ⚠️ PISO PROVISÓRIO (MIN_SCI_FOR_EMERGING_STAR/MIN_IND_FOR_EMERGING_STAR = 5.0 e INDUSTRIAL_TRACTION_N_REF = 6)

> **Este piso NÃO deve ser lido como um valor validado.** Foi calibrado com
> **N=1 caso confirmado** (Calendula/AT-017) dentro de um catálogo de 35
> ativos — o único ativo que, na execução completa de 2026-08-19, atingiu os
> dois componentes simultaneamente. Um único exemplo positivo não é uma
> amostra de validação independente: não existe, hoje, um segundo caso
> genuíno no catálogo para confirmar que 5.0 generaliza além deste
> ativo-fronteira específico (a diferença bruta acima do piso, antes de
> arredondar, foi de **+0.00085** — ver rastro completo abaixo). Tratar este
> piso como "confirmado pelos dados" seria uma leitura otimista demais do
> que uma amostra de tamanho 1 pode sustentar.
>
> **Status: PROVISÓRIO.** Revisão comprometida a cada execução completa do
> catálogo que acumule novos casos genuinamente próximos do piso (mesma
> cadência trimestral de `INDUSTRIAL_TRACTION_N_REF`, seção acima) — não
> antes de haver ao menos 2-3 casos reais para comparar.

Calendula (AT-017) foi o único ativo do catálogo de 35 a genuinamente
qualificar "Estrela Emergente" na execução completa de 2026-08-19
(`tracao_cientifica=5.0/10`, `tracao_industrial=5.6/10`) — muito perto do
piso mínimo de 5.0 nos dois componentes. Rastro de cálculo bruto completo
(quais PMIDs/patentes específicos entraram, e como cada componente da
fórmula chegou nesses números) em
[`docs/calculation_trace_calendula_2026-08-19.md`](docs/calculation_trace_calendula_2026-08-19.md)
— reexecutável a qualquer momento com `python -m scripts.calculation_trace AT-017`.

**Resumo do rastro:** T_c vem de 2 PMIDs reais verificados no NCBI
([42586652](https://pubmed.ncbi.nlm.nih.gov/42586652/),
[42465172](https://pubmed.ncbi.nlm.nih.gov/42465172/)), cada um com
`confidence_score=0.95`; o valor BRUTO antes de qualquer arredondamento é
**5.00085** (`+0.00085` acima do piso, não uma subida por arredondamento a
partir de um valor abaixo de 5.0). T_i vem de 2 patentes reais validadas ao
vivo no Google Patents ([CN121648039A](https://patents.google.com/patent/CN121648039A/en),
[MX2024003541A](https://patents.google.com/patent/MX2024003541A/en)),
`10×log(3)/log(7) = 5.6446` — confortavelmente acima do piso, sem margem
apertada nesse componente.

**A pergunta que importa: o piso 5.0 foi calibrado usando o catálogo que
inclui o Calendula, ou definido antes?** Definido ANTES, por raciocínio
estrutural, não ajustado a este exemplo:

1. O valor `5.0` foi proposto e aprovado numa resposta que explicava a
   regra de tier ANTES de `scripts/tier_recalibration_collect.py` (o
   script que rodou o catálogo completo, incluindo o Calendula pela
   primeira vez) sequer existir. A justificativa dada na época foi
   estrutural: 5.0 é o ponto médio da escala 0-10, e é literalmente o
   mesmo valor que o componente [G] de Tração Científica já usa como
   "neutro" quando não há linha de base (`core/score_engine.py`,
   `calculate_scientific_traction_breakdown`) - não foi escolhido olhando
   a distribuição real de nenhum ativo.
2. Depois que a coleta completa rodou e mostrou o Calendula bem perto
   desse piso, o valor **não foi ajustado** - nem para cima nem para baixo.
   O resultado foi reportado como está.

**Isso não é overfitting no sentido estrito (curva ajustada aos dados após
observá-los)** - mas seria dishonesto parar aí. Existe um problema
adjacente, real, que vale registrar sem retórica: com `N=1` (só um ativo
no catálogo inteiro passa nos dois pisos, por uma margem de 0.00085 num
dos dois componentes), **a robustez do piso 5.0 é essencialmente
não-testada**. Não há evidência de que 5.0 seja o valor "certo" além de
"não excluiu o único caso genuíno disponível" - o piso poderia ser 4.5 ou
5.4 e a única diferença observável no catálogo atual seria justamente
incluir ou excluir este único ativo-fronteira. Não há um segundo ou
terceiro caso genuíno no catálogo para confirmar que 5.0 generaliza bem
para além deste exemplo específico. Isso é uma limitação real do estado
atual do piso, registrada aqui deliberadamente sem tentar minimizá-la -
recomenda-se tratá-la com a mesma cadência de revisão trimestral já
definida para `INDUSTRIAL_TRACTION_N_REF` (seção acima), reavaliando à
medida que mais execuções completas do catálogo acumularem mais exemplos
de ativos genuinamente próximos do piso.

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

**Entradas:** `patent_matches` — patentes deduplicadas por família (INPADOC
family-id) na janela de 12 meses, vindas de `connectors/patents.py`
`fetch_patents()` (EPO OPS real, ver seção acima) e processadas por
`process_patent()` (correspondência de texto do título real contra o
`entity_resolver`), **já filtradas** para conter só as que sobreviveram à
validação ao vivo no Google Patents (`validate_patent_batch`, aplicada em
`main.py`/`scripts/calculation_trace.py`/`scripts/audit_report_single_asset.py`
antes de chamar `generate_assessment()` — ver correção de 2026-08-19 abaixo).

**Fórmula (log-comprimida, calibrada por N_ref — corrigida em 2026-08-19,
ver seção "Tração Industrial saturava..." acima para o histórico completo,
os dados de calibração e a tabela antes/depois):**
```
se total_patents == 0: T_i = 0.0
senão: T_i = min(10.0, 10 × log(1 + total_patents) / log(1 + INDUSTRIAL_TRACTION_N_REF))
```
`INDUSTRIAL_TRACTION_N_REF = 6` (calibrado em 2026-08-19 a partir do máximo
de patentes validadas observado numa execução real do catálogo completo de
35 ativos — não um número arbitrário; recalibrar trimestralmente, ver
`core/score_engine.py`).

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

## 4. Categoria de Triagem "High-Risk / Supply Alert" (o badge, não a coluna Risco de Oferta)

**Atenção:** esta é uma pergunta distinta da seção 3 acima. A coluna "Sinal
de Risco de Oferta Observado" (ALTO/MÉDIO/BAIXO RISCO) e o BADGE de
categoria "High-Risk / Supply Alert" exibido na tabela usam código
DIFERENTE, mesmo com inputs parecidos - documentado aqui separadamente
porque é fácil confundir os dois.

**Onde:** `core/predictive_ranking.py` → `select_predictive_assets()`, dentro
de `enriched` (não é `calculate_supply_risk()` da seção 3):

```python
"_is_regulatory_risk": e.get("alerta_regulatorio") == "ALERTA ALTO",
"_is_supply_risk": e.get("sinal_comercial_comex") in _SUPPLY_RISK_LEVELS,  # {"OFERTA CRÍTICA", "OFERTA LIMITADA"}
```

Um ativo qualifica para o badge "High-Risk / Supply Alert" se
`_is_regulatory_risk OR _is_supply_risk` for verdadeiro — **nenhum dos dois
sinais vem de julgamento da LLM**. A classificação da categoria (e de
`risco_oferta`, `alerta_regulatorio`, `sinal_comercial_comex`) é 100%
determinística, calculada por `core/score_engine.py`
ANTES de qualquer chamada à API Anthropic - a LLM só recebe esses valores já
prontos como contexto para gerar o TEXTO da recomendação
(`inovacao_pd`/`compras_procurement`), nunca decide a categoria/o score.

**As duas fontes de dado possíveis, nenhuma delas verificável em tempo real:**
- **Risco Regulatório** (`alerta_regulatorio == "ALERTA ALTO"`): vem de
  `connectors/regulatory_comex.py` `REGULATORY_REGISTRY` — um dicionário
  Python curado manualmente no código-fonte, com uma entrada por
  ativo/jurisdição. NÃO é uma consulta em tempo real a Anvisa/EU/FDA, não
  tem data de última revisão, não tem link para o texto oficial da norma
  (só uma string livre `alerts`) e não passa por nenhuma validação
  automática — se a norma mudar, a entrada só é atualizada se alguém
  editar o arquivo manualmente. **Isto é um ponto fraco genuíno**, não só
  uma limitação de protótipo: não há mecanismo de detecção de
  desatualização.
- **Risco de Oferta comercial** (`sinal_comercial_comex` em `{"OFERTA
  CRÍTICA", "OFERTA LIMITADA"}`): **mock determinístico** (seed SHA256 de
  asset_id+região, sem nenhuma base em dado comercial real) — ver aviso de
  proveniência no topo deste documento.

**Dado bruto usado para Arbutin (AT-026) especificamente**, extraído ao vivo
chamando `RegulatoryComexConnector.get_regulatory_matrix('AT-026')` e
`get_asset_dossier('AT-026', hs_code='2907.29.90', lang='PT-BR')`:

```json
// alerta_regulatorio (ANVISA, pior caso entre as 3 jurisdições - ESTE é o sinal que classificou Arbutin)
{
  "status": "USO_RESTRITO", "restriction_level": "ALTO", "max_concentration_allowed": "2.0%",
  "alerts": ["Precursor de hidroquinona - escrutínio regulatório elevado (clareadores)"]
}
// sinal_comercial_comex (MOCK) - NÃO foi o sinal que classificou Arbutin, está OK
{
  "volume_usd_annual": 1296717, "trend": "ESTAVEL", "suppliers_count": 5,
  "risk_score": "BAIXO_RISCO", "c_trade_concentration": 0.2
}
```

**Conclusão para Arbutin:** foi o sinal **regulatório**, não o comercial, que
classificou — as 3 jurisdições (Anvisa, Regulamento CE 1223/2009 Anexo III
entrada 77, FDA) restringem Alpha-Arbutin a no máximo 2,0% de concentração
por ser precursor de hidroquinona; o sinal comercial mock, isoladamente,
mostra "BAIXO_RISCO" (5 fornecedores, tendência estável) e não teria
classificado o ativo sozinho. `connectors/regulatory_comex.py` linha 116
(`REGULATORY_REGISTRY["EU_1223_2009"]["AT-026"]`) é a entrada literal
usada — ver trecho de código acima na seção "Fonte do sinal de risco" (achado
do item 2 da auditoria de 2026-08-19).

O fato em si (Alpha-Arbutin como precursor de hidroquinona sob restrição
regulatória) é plausível e corresponde a conhecimento real de regulação
cosmética — não é um dado inventado/alucinado. O ponto fraco não é "o dado
é falso", é "o dado é uma foto estática mantida à mão, sem data de
revisão, sem link de verificação, sem alerta de obsolescência" - o mesmo
risco de qualquer base de compliance mantida manualmente e nunca é
comparável, em confiabilidade, aos dados REAIS ao vivo (PubMed, EPO OPS)
que alimentam T_c/T_i.

## ✅ CORRIGIDO (2026-08-20): Rastreabilidade do REGULATORY_REGISTRY

O ponto fraco identificado acima (nenhuma citação de norma nem data de
revisão nas 49 entradas de `REGULATORY_REGISTRY`/`EU_REGULATORY_OVERRIDES`/
`FDA_REGULATORY_OVERRIDES`) foi endereçado, não eliminado — a base continua
sendo mantida manualmente, sem consulta em tempo real; o que mudou é que
agora isso é **auditável por fora do código**, e uma entrada nova sem os
dois campos abaixo **derruba a importação do módulo inteiro** em vez de
passar despercebida.

**2 campos obrigatórios em toda entrada**, validados na importação de
`connectors/regulatory_comex.py` (`_validate_regulatory_registries()`,
levanta `RegulatoryRegistryTraceabilityError` se qualquer entrada estiver
sem um dos dois):
- `source`: string não vazia citando a norma/resolução que embasa a
  entrada, OU declarando explicitamente que a classificação vem de
  avaliação interna sem um dispositivo legal específico citável — nunca
  omitida em silêncio.
- `last_verified`: data `YYYY-MM-DD` (formato validado por regex) da
  última confirmação manual de que a entrada ainda reflete a regulação
  vigente.

**Honestidade sobre o que `last_verified=2026-08-20` significa nas 49
entradas atuais:** essa data marca quando o CAMPO ESTRUTURADO foi
introduzido no código (bootstrap retroativo) — **não** é uma alegação de
que as 49 entradas foram individualmente refeitas/checadas contra o texto
da norma vigente nesta data. Só **Arbutin (AT-026)** foi de fato
reinvestigado a fundo nesta auditoria (seção acima). As revisões futuras de
`last_verified` devem refletir checagem individual genuína da entrada
correspondente, não uma atualização em lote.

**Composição do `source` das 49 entradas** (nenhuma citação nova foi
inventada — só reorganizadas as que já estavam no texto de `alerts`/
`max_concentration_allowed` da base pré-existente; entradas sem citação
específica já asserida no código foram marcadas honestamente como tal, em
vez de receber um número de norma inventado):

| Base | Entradas com citação específica | Entradas sem citação específica (avaliação interna genérica, declarada como tal) |
|---|---:|---:|
| `REGULATORY_REGISTRY` (Anvisa) | 1 de 35 (AT-032 — nota: cita `Regulamento (UE) 2019/831`, uma norma EUROPEIA dentro da base "Anvisa"; inconsistência de proveniência PRÉ-EXISTENTE ao código, mantida como estava, não corrigida nesta rodada por estar fora do escopo pedido) | 34 de 35 |
| `EU_REGULATORY_OVERRIDES` | 6 de 7 | 1 de 7 (AT-033) |
| `FDA_REGULATORY_OVERRIDES` | 0 de 7 | 7 de 7 (nenhuma citação de CFR/monografia OTC já existia no texto pré-existente — não fabricado agora) |

**Exposição fora do código:** o PDF final (`reports/pdf_generator.py`)
exibe uma evidence-tag `REG: {source} (verificado em {last_verified})` na
linha de recomendações de todo ativo cuja categoria de triagem é "High-Risk
/ Supply Alert" — mesmo padrão visual já usado para as citações `PMID:`/
`PAT:`. `main.py` passa `regulatory_source`/`regulatory_last_verified` (da
jurisdição de PIOR CASO, a mesma que decide a categoria) para cada
avaliação antes de gerar o relatório.

**Cadência de revisão: SEMESTRAL** (não trimestral, como
`INDUSTRIAL_TRACTION_N_REF`/`MIN_SCI_FOR_EMERGING_STAR` acima) — regulação
cosmética muda numa cadência muito mais lenta que publicação científica ou
depósito de patente; revisar com a mesma frequência seria esforço
desproporcional ao ritmo real de mudança da fonte. Próxima revisão
semestral agendada: 2027-02-20.

## ✅ CORRIGIDO (2026-08-20): Vazamento de português no relatório ES (3 pontos, sistêmico)

**O achado:** revisão do relatório ES gerado encontrou português vazando em
3 lugares distintos, apesar do texto gerado pela LLM estar corretamente
traduzido — sinal de que os 3 vinham de fontes FIXAS que nunca passavam
pelo pipeline de localização (a LLM só traduz o que ela mesma gera; texto
que chega pronto de outra fonte e é inserido direto no relatório não passa
por tradução nenhuma, a menos que o código explicitamente busque a versão
certa por idioma):

1. **Rótulo "Limitações:"** — hardcoded em português em TODOS os blocos de
   ressalva do documento espanhol (Inovação & P&D e Compras &
   Procurement), literal fixo em `core/llm_analysis.py`
   `_compose_recommendation_text()`.
2. **Disclaimer "REG: ..."** — o texto de `source` (rastreabilidade do
   `REGULATORY_REGISTRY`, introduzida na correção anterior) aparecia em
   português nos 3 ativos High-Risk do relatório ES, porque é dado que
   chega PRONTO ao gerador de PDF e é inserido direto na evidence-tag, sem
   passar pela LLM (ao contrário de `alerts`, que só chega ao leitor via
   prosa já traduzida pela síntese da LLM).
3. **Nomes de ativos não localizados** — "Alcaçuz" nunca virava "Regaliz"
   (nem na tabela, nem no corpo do texto); "Ácido Tranexâmico" na tabela
   usava grafia portuguesa (â) enquanto o corpo do texto, no MESMO PDF,
   usava corretamente "Tranexámico" (á, espanhol) — prova de que a tabela e
   o texto narrativo vinham de fontes diferentes (`canonical_name`, campo
   único da taxonomia, reaproveitado nos 3 idiomas, vs. a LLM traduzindo por
   conta própria, com sucesso desigual conforme o termo já era ou não
   internacionalmente padronizado).

### Correção 1 — "Limitações:" → `LIMITATIONS_LABEL` por idioma

**Onde:** `core/llm_analysis.py` `LIMITATIONS_LABEL` (novo dict, mesmo
padrão de `REGULATORY_BODY`/`REGION_CONTEXT` já existentes no arquivo) +
`_compose_recommendation_text()`, que agora recebe `lang_key` e busca o
rótulo certo (`"Limitações:"` PT-BR/PT-PT, `"Limitaciones:"` ES) em vez do
literal fixo.

**Por que não em `reports/pdf_generator.py` TRANSLATIONS:** esse dict já
existe e já cobre os elementos fixos do TEMPLATE do PDF (títulos de seção,
cabeçalhos de tabela, rótulos de coluna — confirmado, sistema de
localização de chrome do relatório já existia). "Limitações:" não é chrome
do template — é montado DENTRO do texto de `inovacao_pd`/`compras_procurement`
em `core/llm_analysis.py`, antes mesmo de chegar ao gerador de PDF, então
precisa de sua própria entrada de localização nesse módulo.

### Correção 2 — Disclaimer REG → `localize_source()` + `SOURCE_TRANSLATIONS`

**Onde:** `connectors/regulatory_comex.py` `SOURCE_TRANSLATIONS` (10
strings distintas — o número real de valores únicos de `source` nas 49
entradas das 3 bases, dado o reaproveitamento de templates genéricos) +
`localize_source(source_pt, lang)`, chamada por
`reports/pdf_generator.py` ao montar a evidence-tag REG.

**Validação estendida:** `_validate_regulatory_registries()` (a mesma
função que já falha a importação do módulo se `source`/`last_verified`
estiverem ausentes) agora TAMBÉM falha se o `source` de qualquer entrada
não tiver uma tradução ES registrada em `SOURCE_TRANSLATIONS` — uma
entrada nova com um `source` inédito, sem tradução, derruba a importação
do pacote inteiro em vez de vazar em português silenciosamente. Fail-loud
também em tempo de renderização: se `localize_source()` for chamada com um
texto sem tradução cadastrada (não deveria acontecer, dado o passo
anterior, mas defesa em profundidade), retorna
`"[TRADUÇÃO ES AUSENTE] {texto original}"` em vez de expor português sem
nenhum aviso.

**Sobre PT-PT (verificado, não assumido):** o texto de `source` já está em
português — que é o idioma de PT-PT também — então não há vazamento de
IDIOMA para PT-PT (diferente de ES). Existe, sim, uma questão distinta de
PROVENIÊNCIA: para 28 dos 35 ativos (os que não têm entrada em
`EU_REGULATORY_OVERRIDES`), o relatório PT-PT cai no fallback da base
Anvisa/Brasil, que às vezes cita explicitamente "RDC" (Resolução da
Diretoria Colegiada, um instrumento regulatório BRASILEIRO) num relatório
de Portugal. Isso é uma inconsistência de conteúdo/jurisdição, não de
tradução — registrada aqui para decisão separada, não corrigida nesta
rodada (fora do escopo do vazamento de idioma reportado).

### Correção 3 — Nomes de ativos: `canonical_name_es` na taxonomia

**Onde vem o nome canônico da tabela:** `data/taxonomy/ativos_mvp.json`,
campo `canonical_name` — um valor ÚNICO por ativo, lido diretamente em
`main.py` (`canonical_name = asset["canonical_name"]`) e reaproveitado nos
3 idiomas, tanto na tabela quanto no próprio PROMPT enviado à LLM (por
isso a LLM às vezes "acertava" a tradução na prosa por conhecimento
próprio — ex.: termos farmacêuticos internacionalmente padronizados como
"ácido tranexâmico" — e às vezes não, para nomes vernaculares menos
padronizados como "Alcaçuz").

**Correção:** novo campo opcional `canonical_name_es` na taxonomia,
adicionado a **14 dos 35 ativos** do catálogo (varredura completa dos 35,
não só os 4 que apareciam no relatório revisado — resultado abaixo).
`main.py` agora calcula um `localized_canonical_name` por idioma dentro do
laço de geração (usa `canonical_name_es` quando `lang=="ES"` e o campo
existe; senão usa o `canonical_name` base) e passa esse nome tanto para a
tabela (`evaluations_by_lang[lang]`) quanto para o PROMPT da LLM
(`generate_recommendations`) — a LLM deixa de precisar adivinhar/traduzir o
nome por conta própria.

**Varredura completa dos 35 ativos** (script ad-hoc, revisão manual
termo a termo — nenhuma tradução nova inventada sem confiança razoável;
nomes internacionais/binomiais latinos mantidos como estão, por serem já
válidos/neutros em espanhol):

| Categoria | Ativos | Ação |
|---|---:|---|
| Já idêntico ou válido em ES sem alteração (ex.: Resveratrol, Centella Asiática, Cúrcuma, Ácido Glicólico) | 21 | Nenhuma - `canonical_name` reaproveitado, correto como está |
| Divergência real PT→ES, `canonical_name_es` adicionado | 14 | Ver tabela abaixo |

| Ativo (asset_id) | PT (canonical_name) | ES (canonical_name_es) |
|---|---|---|
| AT-009 | Chá Verde | Té Verde |
| AT-010 | Semente de Uva | Semilla de Uva |
| AT-011 | Romã | Granada |
| AT-017 | Calendula | Caléndula |
| AT-018 | Camomila | Manzanilla |
| AT-019 | Alcaçuz | Regaliz |
| AT-022 | Figo da Índia | Higo Chumbo |
| AT-023 | Esqualano Vegetal | Escualano Vegetal |
| AT-025 | Aveia Coloidal | Avena Coloidal |
| AT-026 | Arbutin | Arbutina |
| AT-028 | Margarida | Margarita |
| AT-029 | Cânhamo / CBD | Cáñamo / CBD |
| AT-033 | Ácido Tranexâmico | Ácido Tranexámico |
| AT-034 | Ácido Lactobiônico | Ácido Lactobiónico |

Casos mantidos deliberadamente sem tradução (nomes internacionais/trade
names já usados como estão em literatura cosmética em espanhol, ou
binomiais latinos sem vernáculo único-padrão confiável): Bakuchiol,
Bidens Pilosa, Edelweiss, Ginkgo Biloba, Jambu, Kakadu Plum, Pycnogenol,
Boswellia, Tremella, Rosa Damascena, Camu-Camu, Aloe Vera, Rosa Mosqueta,
Café Verde, Fitoceramidas de Trigo (nenhum destes teve uma tradução real
identificada com confiança suficiente para registrar sem risco de
introduzir um erro — preferível manter o termo internacional a arriscar
uma tradução errada).

PT-BR e PT-PT continuam compartilhando o mesmo `canonical_name` — nenhuma
divergência de nomenclatura entre as duas variantes de português foi
identificada nos 35 ativos deste catálogo.

### 2 pontos menores confirmados nesta auditoria

- **Rodapé ES sem sufixo de país:** confirmado e corrigido -
  `reports/pdf_generator.py` TRANSLATIONS["ES"]["footer"] agora termina em
  "(España)", no mesmo padrão de "(Brasil)"/"(Portugal)" já usado em
  PT-BR/PT-PT.
- **Categoria "Disruptive Dark Horses" ausente da legenda:** investigado e
  **não é uma omissão** — a legenda (`methodology_html`, dentro de
  `<div class="methodology-box">`) sempre lista as 3 categorias possíveis,
  independente de haver alguma linha com aquele badge na tabela naquela
  edição; confirmado inspecionando os 3 HTMLs gerados (badge `cat-darkhorse`
  presente nos 3, mais a nota "Nenhum ativo qualificado nesta categoria..."
  citando "Sinal Científico sem Confirmação Industrial" pelo nome, já que
  nenhum ativo qualificou genuinamente para Dark Horse nesta execução - ver
  seção "Preenchimento fabricado removido" acima). Nenhuma alteração feita.

**Teste de regressão:** `tests/test_es_localization_regression.py` (4
testes) - trava os 3 achados especificamente: rótulo "Limitações:"
localizado por idioma, toda `source` regulatória com tradução ES real
(não uma cópia do PT-BR), a taxonomia com os 3 nomes ES corretos
(Alcaçuz→Regaliz, Calendula→Caléndula, Ácido Tranexâmico→Ácido
Tranexámico), e um teste de integração que gera um relatório ES completo
(dados sintéticos, sem chamada de rede/LLM) e escaneia o HTML final por um
blocklist de strings português-only. Verificado manualmente contra o
código anterior a esta correção (`git show HEAD:...`): os testes que
dependem de `localize_source()`/`canonical_name_es`/`lang_key` falham lá
(função inexistente, campo ausente, `TypeError` de assinatura),
confirmando que são regressões genuínas.

## 5. Confiança do Sinal

**Onde:** `core/score_engine.py` → `calculate_confidence_level()`

**Entrada:** `all_matches = pubmed_data + patent_data` (patentes reais do EPO
OPS, já validadas ao vivo — ver item 2 acima).

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
