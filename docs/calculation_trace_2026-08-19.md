# Rastro de Cálculo — Chá Verde (AT-009) e Cúrcuma (AT-015)

Gerado em 2026-08-19 via `python scripts/calculation_trace.py AT-009 AT-015`
(execução ao vivo, PubMed real, validação real via Google Patents). O script
chama literalmente `core.score_engine.ScoreEngine.generate_assessment()` —
a mesma função de produção usada por `main.py` — com os dados coletados; os
números abaixo não são reimplementados nem recalculados manualmente.

Reexecutável a qualquer momento: `python scripts/calculation_trace.py AT-XXX`.
Os números de PubMed variam de execução para execução (janela móvel de 15/
365 dias relativa à data de hoje) — isso é esperado e correto.

---

## Chá Verde (AT-009)

Query: `Camellia sinensis` · Exclusões: `dietary supplement`, `beverage consumption`, `weight loss pill`
HS Code (comércio exterior): `1302.19.99`

### [1] PubMed — janela de Tração Científica (12 meses, dados ao vivo do NCBI)

Query exata enviada: `Camellia sinensis NOT "dietary supplement" NOT "beverage consumption" NOT "weight loss pill"`
Janela: `2025-08-18` a `2026-08-18` · Contagem total reportada pelo NCBI (`esearch.count`): **732**

| PMID | Link | Resultado | Título (NCBI, bruto) |
|---|---|---|---|
| 42607373 | [pubmed.ncbi.nlm.nih.gov/42607373](https://pubmed.ncbi.nlm.nih.gov/42607373/) | REJEITADO | CsNAC083 and CsVND1 are associated with tea plant leaf angle through xylem development and lignin deposition. |
| 42605290 | [pubmed.ncbi.nlm.nih.gov/42605290](https://pubmed.ncbi.nlm.nih.gov/42605290/) | REJEITADO | Effects of Botanical Supplementation on Benzene-Induced Hematological... (rato, não dermocosmético) |
| **42596856** | [pubmed.ncbi.nlm.nih.gov/42596856](https://pubmed.ncbi.nlm.nih.gov/42596856/) | **VERIFICADO** | Exposure-Based Toxicological Evaluation of Lead and Elemental Impurities in Encapsulated Green Tea Dietary Supplements... (match_type=BOTANICAL_NAME, confidence_score=0.95, relevance_level=2) |
| 42589454 | [pubmed.ncbi.nlm.nih.gov/42589454](https://pubmed.ncbi.nlm.nih.gov/42589454/) | REJEITADO | Influence of Light Quality During Spreading on the Chemical and Aromatic Profile of Summer Green Tea. |
| 42589428 | [pubmed.ncbi.nlm.nih.gov/42589428](https://pubmed.ncbi.nlm.nih.gov/42589428/) | REJEITADO | Medicinal Value of ... (título truncado pelo NCBI) |
| 42589150 | [pubmed.ncbi.nlm.nih.gov/42589150](https://pubmed.ncbi.nlm.nih.gov/42589150/) | REJEITADO | To Explore the Utility of Leaf Morphological, Color, and Chlorophyll Traits... |
| 42588413 | [pubmed.ncbi.nlm.nih.gov/42588413](https://pubmed.ncbi.nlm.nih.gov/42588413/) | REJEITADO | Identification and Comparison of Aroma-Active Compounds in Different Chinese Dark Teas. |
| 42588008 | [pubmed.ncbi.nlm.nih.gov/42588008](https://pubmed.ncbi.nlm.nih.gov/42588008/) | REJEITADO | Ultrasonic Degradation Improves the In Vitro Utilization of Oolong Tea Pectic Polysaccharides... |
| 42584152 | [pubmed.ncbi.nlm.nih.gov/42584152](https://pubmed.ncbi.nlm.nih.gov/42584152/) | REJEITADO | Theabrownin from Fu Brick Tea Ameliorates Obesity via Modulating Tryptophan Metabolism... |
| 42565943 | [pubmed.ncbi.nlm.nih.gov/42565943](https://pubmed.ncbi.nlm.nih.gov/42565943/) | REJEITADO | Integrating Metabolomics Data, Network Pharmacology, and Molecular Docking... |

**Total verificado que entra na fórmula: 1**

### [2] Linha de base histórica (36 meses, componente [G])
Janela: `2022-08-19` a `2025-08-18` · Contagem bruta (`esearch.count`, sem efetch): **1762**

### [3] Patentes (12 meses) — ⚠️ base MOCK, nunca uma API real

| Patente | Título mock (usado na Entity Resolution local) | Validação AO VIVO no Google Patents |
|---|---|---|
| [EP4189012A1](https://patents.google.com/patent/EP4189012A1/en) | "Fermented Camellia sinensis extract for sensitive skin formulations" | **REJEITADA** — título real da patente não confirma "Chá Verde" |
| [US20220331678A1](https://patents.google.com/patent/US20220331678A1/en) | "Camellia sinensis leaf extract combined with niacinamide for brightening" | **REJEITADA** — título real da patente não confirma "Chá Verde" |

**Patentes que entram na fórmula de T_i: 2 (mock) · Sobreviveriam à validação real: 0**

### [5] Score final (chamada literal a `generate_assessment()`)

```
Componentes V/G/A/Q: {'V': 2.09, 'G': 0.01, 'A': 10.0, 'Q': 9.5}
Pesos:                {'V': 0.25, 'G': 0.35, 'A': 0.2, 'Q': 0.2}

V = min(10, 0.95 × 2.2)                         = 2.09
G = 5 + 5·clamp((1/365 − 1762/1095)/(1762/1095), −1, 1) = 0.01
A = 10 × (1 match nível≥2 / 1 verificado)       = 10.0
Q = 10 × (0.95 / 1)                             = 9.5

T_c = 0.25·2.09 + 0.35·0.01 + 0.2·10.0 + 0.2·9.5 = 4.4/10  ✅ TRAÇÃO CIENTÍFICA EXIBIDA NO PDF
```

```
T_i: total_patents=2 (MOCK, 0 validadas ao vivo), assignees únicos=2
     base_score = min(8.0, 2×3.0) = 6.0
     diversity_bonus = min(2.0, 2×1.0) = 2.0
     T_i = min(10.0, 6.0+2.0) = 8.0/10  ⚠️ TRAÇÃO INDUSTRIAL EXIBIDA NO PDF — deriva de 2 patentes
     que a validação ao vivo rejeitou (ver METHODOLOGY.md, limitação conhecida)
```

Comércio exterior (mock, Comex Stat BR): volume bruto **USD 933.314** → faixa exibida "Entre USD 840 mil e USD 1,0 Mi"; 4 fornecedores; tendência ESTÁVEL.
Alerta Regulatório: NENHUM (Anvisa). → **Risco de Oferta: MEDIO RISCO** (Sinal Comercial = OFERTA LIMITADA, por causa de 4 < 5 fornecedores).
Confiança do Sinal: **ALTA** (3 evidências verificadas — 1 PMID real + 2 patentes mock —, confidence_score médio ≥ 0.95).

---

## Cúrcuma (AT-015)

Query: `Curcuma longa` · Exclusões: `culinary spice`, `oral supplement`, `dietary curcumin capsule`
HS Code: `1302.19.99`

### [1] PubMed — janela de Tração Científica (12 meses)

Query exata: `Curcuma longa NOT "culinary spice" NOT "oral supplement" NOT "dietary curcumin capsule"`
Janela: `2025-08-18` a `2026-08-18` · Contagem total (`esearch.count`): **577**

| PMID | Link | Resultado | Título (NCBI, bruto) |
|---|---|---|---|
| 42597400 | [pubmed.ncbi.nlm.nih.gov/42597400](https://pubmed.ncbi.nlm.nih.gov/42597400/) | REJEITADO | Effects of ... (título truncado) |
| 42595837 | [pubmed.ncbi.nlm.nih.gov/42595837](https://pubmed.ncbi.nlm.nih.gov/42595837/) | REJEITADO | Chemically modified curcumin in periodontal therapy: a scoping review. |
| 42591746 | [pubmed.ncbi.nlm.nih.gov/42591746](https://pubmed.ncbi.nlm.nih.gov/42591746/) | REJEITADO | Evolutionary analysis... CDPK gene family in turmeric (genética de cultivo, não dermocosmético) |
| 42589357 | [pubmed.ncbi.nlm.nih.gov/42589357](https://pubmed.ncbi.nlm.nih.gov/42589357/) | REJEITADO | Enhanced Oral Delivery of Turmeric Extract via Spray-Dried Microparticles... |
| 42588054 | [pubmed.ncbi.nlm.nih.gov/42588054](https://pubmed.ncbi.nlm.nih.gov/42588054/) | REJEITADO | Curcumol Alleviates Obesity-Related Insulin Resistance... Skeletal Muscle |
| 42587861 | [pubmed.ncbi.nlm.nih.gov/42587861](https://pubmed.ncbi.nlm.nih.gov/42587861/) | REJEITADO | Hydrophobic Deep Eutectic Solvent-Derived Curcuminoids... Colloidal Delivery System |
| 42580785 | [pubmed.ncbi.nlm.nih.gov/42580785](https://pubmed.ncbi.nlm.nih.gov/42580785/) | REJEITADO | A historical perspective of radioprotection studies in India... |
| 42575332 | [pubmed.ncbi.nlm.nih.gov/42575332](https://pubmed.ncbi.nlm.nih.gov/42575332/) | REJEITADO | Ethnopharmacological insights into ayurvedic and siddha medicine in cancer... |
| 42564758 | [pubmed.ncbi.nlm.nih.gov/42564758](https://pubmed.ncbi.nlm.nih.gov/42564758/) | REJEITADO | Assembly and comparative analysis of the multipartite mitochondrial genome... |
| 42562546 | [pubmed.ncbi.nlm.nih.gov/42562546](https://pubmed.ncbi.nlm.nih.gov/42562546/) | REJEITADO | Revealing quality formation in turmeric... microwave vacuum drying |

**Total verificado que entra na fórmula: 0** — nenhum dos 10 candidatos atingiu Nível ≥ 2 de relevância tópica dermocosmética.

### [2] Linha de base histórica (36 meses)
Janela: `2022-08-19` a `2025-08-18` · Contagem bruta: **1426**

### [3] Patentes (12 meses) — ⚠️ base MOCK

| Patente | Título mock (Entity Resolution local) | Validação AO VIVO no Google Patents (reconfirmada após retry limpo) |
|---|---|---|
| [JP2024056789A](https://patents.google.com/patent/JP2024056789A/en) | "Curcuma longa fermented extract for skin barrier enhancement" | **REJEITADA** — patente real é sobre "Optical glass, optical element..." (vidro óptico) |
| [KR20230045678A](https://patents.google.com/patent/KR20230045678A/en) | "Curcuma longa and centella asiatica synergistic complex for redness relief" | **REJEITADA** — patente real é sobre "Inkjet printing facilities... display panel" (impressão jato de tinta) |

**Patentes que entram na fórmula de T_i: 2 (mock) · Sobreviveriam à validação real: 0**

### [5] Score final

```
Componentes V/G/A/Q: {'V': 0.0, 'G': 5.0, 'A': 0.0, 'Q': 0.0}   (verified_count=0 → piso explícito, ver METHODOLOGY.md)

T_c = 0.0/10  ✅ TRAÇÃO CIENTÍFICA EXIBIDA NO PDF — exatamente zero, evidência real inexistente
```

```
T_i: total_patents=2 (MOCK, 0 validadas ao vivo), assignees únicos=2
     base_score = min(8.0, 2×3.0) = 6.0
     diversity_bonus = min(2.0, 2×1.0) = 2.0
     T_i = min(10.0, 6.0+2.0) = 8.0/10  ⚠️ mesmo problema do Chá Verde acima
```

Comércio exterior (mock, Comex Stat BR): volume bruto **USD 543.225** → faixa exibida "Entre USD 489 mil e USD 598 mil"; 12 fornecedores; tendência CRESCENTE.
Alerta Regulatório: BAIXO (Anvisa). → **Risco de Oferta: BAIXO RISCO** (Sinal Comercial = OFERTA SAUDÁVEL, 12 ≥ 5 fornecedores).
Confiança do Sinal: **ALTA** (2 evidências verificadas — só as 2 patentes mock, já que 0 PMIDs passaram — confidence_score médio ≥ 0.95).

**Nota:** aqui fica visível outra consequência do gap documentado em `METHODOLOGY.md`: a "Confiança do Sinal: ALTA" de Cúrcuma depende inteiramente de 2 patentes fabricadas (0 PMIDs reais passaram); se `calculate_confidence_level` também fosse recalculada só com evidência real validada, o resultado provavelmente cairia para "BAIXA" (lista vazia de matches).

---

## JSON bruto retornado por `generate_assessment()` para os dois ativos

```json
[
  {
    "model_version": "2.3.0",
    "tracao_cientifica": "4.4/10",
    "tracao_cientifica_componentes": {
      "score": 4.4,
      "components": {"V": 2.09, "G": 0.01, "A": 10.0, "Q": 9.5},
      "weights": {"V": 0.25, "G": 0.35, "A": 0.2, "Q": 0.2}
    },
    "tracao_industrial": "8.0/10",
    "alerta_regulatorio": "SEM ALERTA",
    "sinal_comercial_comex": "OFERTA LIMITADA",
    "risco_oferta": "MEDIO RISCO",
    "confianca_sinal": "ALTA",
    "total_evidencias": 12,
    "evidencias_verificadas": 3,
    "nivel_evidencia_maximo": 3
  },
  {
    "model_version": "2.3.0",
    "tracao_cientifica": "0.0/10",
    "tracao_cientifica_componentes": {
      "score": 0.0,
      "components": {"V": 0.0, "G": 5.0, "A": 0.0, "Q": 0.0},
      "weights": {"V": 0.25, "G": 0.35, "A": 0.2, "Q": 0.2}
    },
    "tracao_industrial": "8.0/10",
    "alerta_regulatorio": "ALERTA BAIXO",
    "sinal_comercial_comex": "OFERTA SAUDÁVEL",
    "risco_oferta": "BAIXO RISCO",
    "confianca_sinal": "ALTA",
    "total_evidencias": 12,
    "evidencias_verificadas": 2,
    "nivel_evidencia_maximo": 2
  }
]
```
