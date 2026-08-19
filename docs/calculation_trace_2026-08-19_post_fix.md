# Rastro de Cálculo (PÓS-CORREÇÃO) — Chá Verde (AT-009) e Cúrcuma (AT-015)

Gerado em 2026-08-19, logo após a correção descrita em `METHODOLOGY.md`
("Tração Industrial agora só usa patentes validadas ao vivo"). Complementa
[`docs/calculation_trace_2026-08-19.md`](calculation_trace_2026-08-19.md)
(rastro capturado ANTES da correção, mantido como registro histórico da
auditoria) — a seção de PubMed é idêntica (mesmo dia, mesma janela
determinística), só a seção de Patentes/Tração Industrial mudou.

Comando: `python scripts/calculation_trace.py AT-009 AT-015`

## Chá Verde (AT-009)

| Patente | Validação AO VIVO no Google Patents |
|---|---|
| [EP4189012A1](https://patents.google.com/patent/EP4189012A1/en) | REJEITADA — título real não confirma "Chá Verde" |
| [US20220331678A1](https://patents.google.com/patent/US20220331678A1/en) | REJEITADA — título real não confirma "Chá Verde" |

**Patentes que entram em T_i agora: 0 de 2 mock retornadas.**

```
ANTES da correção:  Tração Industrial = 8.0/10
DEPOIS da correção: Tração Industrial = 0.0/10
Tração Científica (inalterada, já era baseada em PubMed real): 4.4/10
Confiança do Sinal: ALTA (inalterada - baseada em 1 PMID real com confidence_score=0.95)
Risco de Oferta: MEDIO RISCO (inalterado - não depende de patentes)
```

## Cúrcuma (AT-015)

| Patente | Validação AO VIVO no Google Patents |
|---|---|
| [JP2024056789A](https://patents.google.com/patent/JP2024056789A/en) | REJEITADA — patente real é sobre vidro óptico |
| [KR20230045678A](https://patents.google.com/patent/KR20230045678A/en) | REJEITADA — patente real é sobre impressão jato de tinta |

**Patentes que entram em T_i agora: 0 de 2 mock retornadas.**

```
ANTES da correção:  Tração Industrial = 8.0/10 | Confiança do Sinal = ALTA
DEPOIS da correção: Tração Industrial = 0.0/10 | Confiança do Sinal = BAIXA
Tração Científica (inalterada, já era 0/10 - nenhum PMID real): 0.0/10
Risco de Oferta: BAIXO RISCO (inalterado - não depende de patentes)
```

A queda de Confiança do Sinal de ALTA para BAIXA em Cúrcuma é uma consequência
esperada e correta: antes, as 2 patentes mock inflavam artificialmente a
contagem de evidências (`evidencias_verificadas: 2`); agora que nenhuma
evidência real existe para este ativo (nem PMID, nem patente), o sistema
reflete isso honestamente como confiança baixa, em vez de "ALTA" apoiada em
dado fabricado.

## Conclusão da auditoria

A correção elimina o gap identificado: **nenhum score exibido no relatório
depende mais de patente não verificada.** O efeito colateral é que, com a
base de patentes inteiramente mock/fabricada, a Tração Industrial e (por
consequência) a Confiança do Sinal de praticamente todo o catálogo devem
cair — isso é o comportamento correto até `connectors/patents.py` ser
substituído por uma fonte real (EPO OPS ou equivalente).
