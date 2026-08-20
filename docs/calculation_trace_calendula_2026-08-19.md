# Rastro de Cálculo — Calendula (AT-017)

Gerado em 2026-08-19 via `python -m scripts.calculation_trace AT-017`
(execução ao vivo: PubMed/NCBI real, EPO OPS real, validação real via
Google Patents). O script chama literalmente
`core.score_engine.ScoreEngine.generate_assessment()` — a mesma função de
produção usada por `main.py` — com os dados coletados; os números abaixo
não são reimplementados nem recalculados manualmente.

**Motivo deste rastro:** Calendula (AT-017) foi o único ativo do catálogo de
35 a genuinamente qualificar para "Estrela Emergente" na execução completa
de 2026-08-19 (`tracao_cientifica=5.0/10`, `tracao_industrial=5.6/10`), com
`tracao_cientifica` muito perto do piso mínimo (`MIN_SCI_FOR_EMERGING_STAR
= 5.0`). Este documento existe para confirmar, com números brutos
auditáveis, que isso é coincidência genuína dos dados reais coletados — não
um threshold calibrado sobre este exemplo específico. Ver `METHODOLOGY.md`,
seção "Rastro de cálculo do Calendula e cronologia do piso", para a
discussão completa.

Reexecutável a qualquer momento: `python -m scripts.calculation_trace
AT-017`. Os números variam de execução para execução (janela móvel relativa
à data de hoje, novos artigos/patentes publicados) — isso é esperado.

---

Query: `Calendula officinalis` · Exclusões: nenhuma
HS Code (comércio exterior): `1302.19.99`

## [1] PubMed — janela de Tração Científica (12 meses, dados ao vivo do NCBI)

Query exata enviada ao NCBI: `Calendula officinalis`
Janela: 2025-08-19 a 2026-08-19 · Contagem total reportada pelo NCBI: 68

| PMID | Status | Título (bruto, NCBI) |
|---|---|---|
| [42587194](https://pubmed.ncbi.nlm.nih.gov/42587194/) | REJEITADO | Unraveling the neuroprotective and anti-seizure potential of Calendula officinalis L. extracts in PTZ-induced experimental epilepsy in rats. |
| [42586652](https://pubmed.ncbi.nlm.nih.gov/42586652/) | **VERIFICADO** | Cationic curdlan-based hydrogels loaded with Calendula officinalis-decorated silver nanoparticles: Synthesis, characterization and in vitro... |
| [42515784](https://pubmed.ncbi.nlm.nih.gov/42515784/) | REJEITADO | Development and Characterization of Honey- and Essential Oil-Based Structured Systems for Skin Applications. |
| [42471005](https://pubmed.ncbi.nlm.nih.gov/42471005/) | REJEITADO | Natural colorants from edible flowers: exploring pigment profiles, phenolic richness, and antioxidant potential. |
| [42465172](https://pubmed.ncbi.nlm.nih.gov/42465172/) | **VERIFICADO** | Comparative Evaluation of Calendula officinalis and Povidone-Iodine in Facial Wound Healing. |
| 42460685, 42451683, 42449941, 42416386, 42396086 | REJEITADO | (fora de escopo tópico/aplicado - agronomia, microbioma, outros ativos) |

**2 PMIDs VERIFICADOS entram na fórmula de T_c** — os dois com
`match_type=BOTANICAL_NAME`, `confidence_score=0.95`, `relevance_level=2`
(Entity Resolution, `core/entity_resolver.py`).

Linha de base histórica (36 meses, componente [G]): janela 2022-08-20 a
2025-08-19, contagem bruta reportada pelo NCBI = **188**.

## [2] Patentes — janela de Tração Industrial (12 meses, dados ao vivo do EPO OPS)

Query CQL exata: `(ti="Calendula officinalis" or ab="Calendula officinalis") and pd within "20250819,20260819"`
Total reportado pelo EPO OPS: **19** · Após dedup por família INPADOC: **19**

Das 19 patentes retornadas, **2 sobreviveram à validação ao vivo no Google Patents**:

| Patente | Titular | Título (EPO OPS, bruto) |
|---|---|---|
| [CN121648039A](https://patents.google.com/patent/CN121648039A/en) | UNIV HUBEI SCIENCE & TECHNOLOGY | Sweet-scented osmanthus and calendula officinalis child face cream for relieving allergy, moistening and repairing skin barrier and preparation method... |
| [MX2024003541A](https://patents.google.com/patent/MX2024003541A/en) | UNIV AUTONOMA DE NUEVO LEON [MX] | CHITOSAN MATRICES FUNCTIONALISED WITH HYDROALCOHOLIC EXTRACT OF CALENDULA OFFICINALIS AND USE THEREOF FOR CELL SCAFFOLDING |

As outras 17 falharam a validação — a maioria por título não confirmar a
entidade "Calendula" (patentes reais sobre outros ativos que apareceram na
busca por texto livre) ou por HTTP 404 no Google Patents (documento ainda
não indexado publicamente).

## [3] Cálculo — chamada literal a `ScoreEngine.generate_assessment()`

**Tração Científica:**
```
V = min(10, soma(confidence_score dos 2 PMIDs verificados) × 2.2) = min(10, (0.95+0.95)×2.2) = 4.18
G = 5 + 5×clamp((taxa_atual − taxa_base)/taxa_base, −1, 1)
    taxa_atual = 2/365 = 0.005479
    taxa_base  = 188/1095 = 0.171689
    G = 5 + 5×(−0.968084) = 0.1595744...  (sem arredondamento)
A = 10 × (2 matches com relevance_level≥2 / 2 verificados) = 10.0
Q = 10 × média(0.95, 0.95) = 9.5

T_c (SEM arredondamento) = 0.25×4.18 + 0.35×0.1595744... + 0.2×10.0 + 0.2×9.5
                         = 1.045 + 0.0558510... + 2.0 + 1.9
                         = 5.0008510638...
T_c (arredondado, round(x,1), valor exibido e usado na comparação de tier) = 5.0
```

**Diferença bruta acima do piso `MIN_SCI_FOR_EMERGING_STAR=5.0`, antes de
qualquer arredondamento: `+0.00085`.** Genuinamente acima, não uma subida
por arredondamento a partir de um valor abaixo de 5.0 (ex.: 4.95 não
arredondaria para "acima do piso" nesta comparação, que usa o valor já
arredondado — mas mesmo o valor bruto pré-arredondamento já estava acima).

**Tração Industrial:**
```
T_i = min(10.0, 10 × log(1 + total_patents) / log(1 + INDUSTRIAL_TRACTION_N_REF))
    = 10 × log(1+2) / log(1+6)
    = 10 × log(3) / log(7)
    = 10 × 1.098612.../1.945910...
    = 5.6446...
    → round(x,1) = 5.6
```

## [4] Resultado final

```json
{
  "tracao_cientifica": "5.0/10",
  "tracao_industrial": "5.6/10",
  "risco_oferta": "BAIXO RISCO",
  "confianca_sinal": "ALTA",
  "evidencias_verificadas": 4
}
```

Alerta Regulatório: SEM ALERTA (ANVISA) · Sinal Comercial: OFERTA SAUDÁVEL
(mock — 6 fornecedores, tendência estável, ver ressalva de proveniência em
`METHODOLOGY.md`).
