# M2 — Protocolo de intervalos y monitorización

## Decisión heredada

M2 parte exclusivamente de `seasonal_naive_7d`, el champion conservado por M1. El modelo Poisson
rechazado no se corrige, recalibra ni vuelve a comparar usando su confirmación. La evidencia M2
registra los hashes del panel, manifiesto de cohorte, resumen de confirmación M1 y árbol de código.

## Partición temporal congelada

- Desarrollo: 20 folds ya observados, índices `0-19`, 14 días cada uno.
- Holdout final: seis bloques nuevos, índices `20-25`, que cubren exactamente los últimos 84 días
  calendario del panel.
- La posible separación entre el último fold de desarrollo y el comienzo del holdout se registra;
  nunca se rellena desplazando el holdout ni reutilizando outcomes.
- Desarrollo y holdout no se solapan. La preparación del holdout devuelve solamente un plan con
  fechas y hashes; no genera predicciones ni métricas.

## Intervalos

La cobertura nominal es 90 %. La calibración final usa únicamente errores del champion en folds
`0-19` y produce parámetros separados por horizonte. Para cada fila calcula el residuo firmado
`(actual - prediction) / escala_estacional_causal`: la escala de cada SKU se estima solo con la
historia disponible hasta el cutoff de esa fila. Los cuantiles inferior y superior se anclan para
que siempre incluyan cero, y el límite inferior del forecast se recorta a cero.

Cuando una serie no tiene una escala estacional causal positiva y finita, esa fila usa explícitamente
el residuo firmado en unidades originales. La calibración conserva cuantiles raw por horizonte para
aplicar el mismo fallback en inferencia. No se calibra por SKU: veinte observaciones por SKU y
horizonte serían demasiado inestables para sostener esa granularidad.

Se publican cobertura y anchura conjuntamente. Un intervalo muy ancho no se presenta como éxito por
alcanzar cobertura, y ventanas de demanda real cero conservan métricas no evaluables cuando
corresponde.

## Replay prequential

El replay comienza tras seis folds de calentamiento. Para evaluar cada fold `i`, la calibración se
ajusta solo con folds anteriores a `i`. El artefacto registra:

- `as_of`: cutoff del fold evaluado;
- folds usados para calibrar;
- máxima fecha de outcome usada en calibración;
- métricas y alertas de la ventana.

La máxima fecha de calibración debe ser menor o igual a `as_of`. Este replay mide comportamiento
histórico operativo; no reemplaza la evaluación independiente del holdout.

## Thresholds congelados

Antes de abrir el holdout quedan fijados:

- cobertura mínima: `0.85`;
- cobertura máxima: `0.98`;
- bias normalizado absoluto máximo: `0.10`;
- WAPE máximo: `2.00`.

Son guardrails explícitos, no valores optimizados con el holdout. Las alertas de calidad de datos
(filas ausentes, duplicadas, inválidas o outcomes no disponibles) deben distinguirse de las alertas
de performance.

## Apertura final

La evaluación final será otra operación. Debe verificar el hash íntegro del contrato M2, el champion,
la calibración, los thresholds, las seis fronteras temporales y los hashes de inputs antes de crear
una claim exclusiva. Solo entonces podrá reconstruir el forecast baseline, aplicar intervalos y
reconciliar outcomes. La ejecución publicará resultados una vez y no autorizará cambios posteriores
del método.

## Resultado posterior a la apertura

El protocolo anterior se congeló antes de observar el holdout. La apertura canónica se ejecutó una
sola vez sobre `2011-09-17` a `2011-12-09` y produjo 1.680 filas. La cobertura fue `77,02 %`, por
debajo del mínimo de `85 %`; WAPE fue `1,1565` y bias normalizado `+0,0593`. El estado final es
`degraded_with_published_alerts` y el uso operativo queda rechazado.

No se recalibraron intervalos ni thresholds después del resultado. Las métricas, slices, alertas e
identidades canónicas están en [el informe M2](../reports/m2/M2_REPORT.md) y su evidencia asociada.
