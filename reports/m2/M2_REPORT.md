# M2 — Intervalos, monitoring y evaluación final

## Resultado ejecutivo

M2 quedó ejecutado y cerrado. El champion heredado, `seasonal_naive_7d`, se evaluó una sola vez
sobre los últimos 84 días del panel después de congelar código, cohort, decisión M1, partición,
calibración y guardrails.

El resultado es un **no-go operativo**: el forecast puntual conserva un WAPE alto (`1,1565`) y los
intervalos alcanzan solo `77,02 %` de cobertura frente al `90 %` nominal y al mínimo predeclarado de
`85 %`. El sistema publicó `52` alertas y marcó la ejecución como
`degraded_with_published_alerts`. No se modificó el método después de observar el holdout.

El software sí queda completo como demostración histórica de ML engineering: reproduce datos,
separa selección y confirmación, bloquea reaperturas silenciosas, persiste forecasts/outcomes y
expone métricas desfavorables en API y dashboard.

## Identidad e integridad

| Campo | Valor |
|---|---|
| Champion | `seasonal_naive_7d` |
| Contrato M2 | `15d47c1e177eff3d` |
| SHA-256 interno del contrato | `15d47c1e177eff3d2092040f730786c18077217b6f895d5d6077ef863bfe3dcc` |
| Evaluación final | `c78eb14bc06ea484` |
| SHA-256 del run | `db3eb23931a5e0fe4b9f1b2bb425cf779e0b5ba8697510d0f182bd1fcba271f6` |
| SHA-256 del panel | `6d39886a45da1da3a25e31c897aacaaa9017a0b88080250c443fc08daa02cf0d` |
| SHA-256 del árbol ejecutable | `984cb0ed8f3d163f69434785ac77b6b21450947a507c35e3467184267586cfaa` |
| Estado del holdout | `evaluated_once_no_retuning` |

Antes de evaluar se verificaron: hashes de panel/cohorte/M1/código, champion, 20 folds de desarrollo,
seis folds finales, gap de diez días, calibración de 14 horizontes y thresholds. La claim y el
receipt exclusivos quedaron asociados al hash del panel; el receipt se volvió a contrastar con el
run y sus cuatro outputs.

## Partición final

- Cohorte: 20 SKU seleccionados solo con el período inicial.
- Desarrollo M2: folds `0–19`, 14 días cada uno.
- Calibración prequential: seis folds de warm-up; cada replay usa solo outcomes anteriores a su
  `as_of`.
- Gap sin puntuar: diez días entre desarrollo y holdout.
- Holdout: folds `20–25`, desde `2011-09-17` hasta `2011-12-09`.
- Filas finales: `1.680` (`20 SKU × 14 horizontes × 6 orígenes`).

La evaluación es rolling-origin: cada bloque de 14 días puede utilizar historia ya conocida de los
bloques anteriores, pero la calibración de intervalos permanece fijada exclusivamente con
desarrollo.

## Métricas globales

| Métrica | Resultado | Lectura |
|---|---:|---|
| Unidades reales | 123.744 | denominador de WAPE y bias |
| Unidades predichas | 131.082 | sobrepronóstico agregado de 7.338 |
| MAE | 85,1881 | error absoluto por fila SKU-día |
| WAPE | 1,1565 | error absoluto equivalente al 115,65 % del volumen |
| Bias normalizado | +0,0593 | pasa el máximo absoluto de 0,10 |
| Cobertura | **0,7702** | **falla** mínimo 0,85 y nominal 0,90 |
| Anchura media | 192,3692 | amplia y aun así insuficiente |
| Anchura mediana | 164,7085 | evidencia heterogeneidad |
| Winkler | 1.105,4818 | penaliza anchura y misses |

Que WAPE no dispare el threshold provisional de `2,00` no vuelve bueno al modelo. Un error de
`115,65 %` sigue siendo demasiado alto para compras o reposición. El threshold se conserva porque
fue declarado antes de abrir el holdout, pero se interpreta como detector de degradación extrema,
no como criterio de aprobación empresarial.

## Guardrails y alertas

| Guardrail | Umbral | Observado global | Resultado |
|---|---:|---:|:---:|
| Cobertura mínima | 0,85 | 0,7702 | **falla** |
| Cobertura máxima | 0,98 | 0,7702 | pasa |
| Bias absoluto máximo | 0,10 | 0,0593 | pasa |
| WAPE máximo provisional | 2,00 | 1,1565 | pasa técnicamente |

Se emitieron 52 alertas: 29 de cobertura y 23 de bias absoluto. Una corresponde al scope global;
las demás señalan fallos por horizonte o SKU.

Los horizontes 4 y 11 son los casos más graves: cobertura de `12,50 %` y `11,67 %`, con anchura
media igual a cero. Los horizontes 1 y 8 tienen unidades reales agregadas iguales a cero y cobertura
del `100 %`; WAPE y bias quedan correctamente como no evaluables. Esta combinación muestra por qué
la cobertura no puede leerse sin anchura, demanda observada y score propio.

![Cobertura final por horizonte](figures/holdout_coverage_by_horizon.svg)

A nivel SKU, la cobertura varía desde `58,33 %` para `22197` hasta `100 %` para `84270`. Este último
tuvo cero unidades reales en todo el holdout, por lo que WAPE y bias no son evaluables: no se presenta
como una victoria de forecasting.

## Decisión final

- **Sistema de portfolio:** terminado y demostrable localmente.
- **Champion estadístico:** conserva identidad por el gate M1; no se promovió el challenger.
- **Aptitud operativa:** rechazada.
- **Intervalos M2:** degradados; no satisfacen el contrato de cobertura.
- **Retuning sobre holdout:** prohibido y no realizado.

Una futura versión puede investigar intervalos adaptativos por régimen, demanda intermitente o
calibración bloqueada, además de nuevos challengers. Esa investigación debe usar nueva evidencia
temporal o un dataset externo: este holdout ya dejó de ser válido para selección.

## Artefactos

Los manifests canónicos registran los siguientes outputs:

| Output | SHA-256 |
|---|---|
| Calibración M2 | `28a36296dd11c3647b8f48b2b8c6bbe228b73da98a81aff8845ec7009999a830` |
| Contrato M2 | `9a7483f671f4b8f428aedb870ad3eeedc472e9370a59a7e9338844dfcf95de8d` |
| Replay prequential | `e328882b5c7de1a0c6bb327b0d563179e68ad042daddbf4377a5dc545a59590d` |
| Predicciones de desarrollo | `90f34e17dd52ef193b3a6a319d6b9321e7d2acade4b9de51d77ef267699268b6` |
| Predicciones de holdout | `2a0afda7eee987a61232462d4cd380c7c8636de2c40b611022d481992b120a05` |
| Monitoring final | `e32c68b31f2f0ef1e5e15b26a1bf6210dbd7b743a0c1750c95a500ca00d6a97a` |
| Evaluación final | `16d21e0ce68694c62ed3cf75ba71c73e1df4db2688f42befe0bd2a28b0bb3f29` |
| Snapshot de demo | `6a1e418049eb2a2c5094be44c6cf722452a2d9c471ba2a230c5c9f0488f4caad` |

El [resumen compacto](evidence/evaluation_summary.json) existe para lectura rápida; los hashes y
manifests canónicos son la referencia de integridad.
