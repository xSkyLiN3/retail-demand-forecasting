# Protocolo de evaluación

## Unidad y horizonte

- Unidad de serie: SKU.
- Frecuencia: día calendario.
- Horizonte: 14 días.
- Estacionalidad primaria: 7 días.

## Rolling-origin backtesting

Cada fold contiene:

- `train`: todas las fechas hasta el cutoff, inclusive;
- `test`: los 14 días inmediatamente posteriores;
- separación entre cutoffs: 14 días, generando bloques futuros no solapados;
- mínimo inicial: 365 días de historia;
- mínimo público: seis folds completos.

La cohorte de SKU se congela antes del primer fold. Transformaciones, imputaciones, lags, escalado,
selección de variables y calibración deben ajustarse con datos disponibles hasta el cutoff de cada
fold.

Los últimos 84 días forman una **ventana temporal final reservada**, equivalente a seis bloques de
14 días. Los comandos de desarrollo no generan predicciones ni métricas sobre ella, aunque sus
outcomes permanecen físicamente en el panel local; por tanto, la protección es procedimental, no
un cegado externo. Los folds anteriores pueden usarse para elegir un único modelo y una búsqueda
pequeña. Features, hiperparámetros, método de intervalo y thresholds se congelan antes de evaluar
la ventana final. Sus resultados se publican una vez y no se usan para retuning.

## Protocolo congelado de M1

Los 20 folds de desarrollo se dividen una sola vez, de forma cronológica:

- folds `0-13`: ajuste y selección entre configuraciones predeclaradas;
- folds `14-19`: confirmación procedimental del único candidato seleccionado;
- últimos 84 días: holdout final todavía reservado, fuera de M1.

La selección y la confirmación son comandos distintos. El primer comando persiste el contrato de
features, la partición, la cuadrícula, la semilla, los hashes de entradas y código, y la
configuración ganadora antes de calcular resultados sobre los folds `14-19`. El segundo comando
rechaza cualquier selección cuyo contrato o hashes ya no coincidan. El archivo de selección no se
modifica durante la confirmación.

El candidato es un único modelo global directo en formato largo. Cada fila representa
`(origin_date, sku, horizon) -> units_at_origin_plus_horizon`, con horizontes `1-14`. Para entrenar
un fold con cutoff `c`, solo se admiten etiquetas con `target_date <= c`; para inferencia, todas las
filas parten de `origin_date = c`. No existe predicción recursiva ni se usan outcomes de los días
1-13 para anticipar horizontes posteriores.

Las variables congeladas son:

- SKU y horizonte como categóricas nativas;
- calendario conocido de la fecha objetivo: día de semana, mes, fin de semana y seno/coseno anual;
- seasonal naive disponible en el origen;
- demanda alineada con la fecha objetivo a 14, 21, 28 y 35 días;
- demanda observada en el origen y lags de 1, 7, 14 y 28 días;
- medias móviles de 7, 14, 28 y 56 días, más desviación y máximo de 28 días;
- proporción de días activos en 28 y 56 días, días desde la última demanda positiva y dos medidas
  de tendencia entre ventanas de 7 y 28 días.

No se usan precio, cliente, país, descripción, `source_observed_day`, codificación target global ni
variables calculadas con el panel completo. Los orígenes históricos son diarios y requieren 56
días de contexto causal.

La cuadrícula se limita a tres configuraciones de `HistGradientBoostingRegressor`, todas con
`learning_rate=0.05`, `max_iter=250`, `l2_regularization=1`, `early_stopping=False` y semilla 42:

1. Poisson conservadora: 7 hojas y mínimo 80 muestras por hoja;
2. Poisson media: 15 hojas y mínimo 40 muestras por hoja;
3. control squared error: 15 hojas y mínimo 40 muestras por hoja.

Se selecciona el menor WAPE agregado en folds `0-13`; un empate favorece menor bias absoluto y
luego la configuración más simple. No se amplía la búsqueda después de observar la confirmación.

## Baselines

1. **Seasonal naive (obligatorio):** repite el patrón de los últimos siete días.
2. **Media móvil de 28 días (referencia secundaria):** se añadirá solo si aporta una comparación
   interpretable.

El seasonal naive se publica primero sobre los folds de desarrollo. En la ventana final, baseline
y modelo aprendido se evaluarán y publicarán juntos, usando exactamente las mismas fechas, para no
revelar outcomes finales antes de congelar el candidato.

## Métricas

- **WAPE:** error absoluto agregado dividido por demanda observada agregada.
- **MASE:** cada error se escala con el denominador seasonal-naive de su propio SKU y fold; el MASE
  agregado es la media de errores escalados evaluables, nunca un denominador mezclado entre SKU.
- **Bias normalizado:** suma de `forecast - actual` dividida por demanda observada.
- **MAE:** unidades absolutas por fila.
- **Cobertura de intervalo:** porcentaje de observaciones entre límites inferior y superior.
- **Anchura de intervalo:** debe reportarse junto a cobertura.

Se publican valores globales, por fold, por SKU y por horizonte. Las series con denominador cero se
marcan como no evaluables para la métrica correspondiente; nunca se sustituyen silenciosamente por
cero.

Los intervalos tendrán cobertura nominal del 90 %. El método se calibrará solo con residuos de los
folds de desarrollo; se publicarán cobertura y anchura por horizonte en el holdout final.

## Gate del modelo aprendido

Para reemplazar al baseline como modelo candidato debe:

- reducir el WAPE agregado al menos un 5 % frente al baseline;
- mejorar WAPE en al menos 4 de los 6 folds de confirmación;
- mejorar WAPE para al menos 11 de los 20 SKU; un SKU con WAPE no evaluable no cuenta como
  victoria;
- obtener un MASE agregado inferior al baseline;
- no ocultar deterioros mediante una única media ponderada;
- mantener `abs(normalized_bias) <= 0.10` y no empeorar el bias absoluto del baseline en más de
  0.02;
- producir únicamente predicciones finitas y no negativas;
- superar todas las pruebas de leakage temporal.

Además se reporta el rendimiento separado para horizontes `1-7` y `8-14`. Si cualquier criterio
falla, seasonal naive conserva el rol de champion y M1 documenta el candidato rechazado sin
retuning. La confirmación permite decidir si el candidato continúa a M2; no constituye el resultado
final. El resultado público principal será el del holdout temporal una vez congelados también el
método de intervalos y la monitorización.

## Cierre M2

Después de congelar el método, el holdout se abrió una sola vez. El champion obtuvo WAPE `1,1565`,
bias normalizado `+0,0593` y cobertura de intervalos `77,02 %` frente al `90 %` nominal. La
cobertura incumplió el mínimo predeclarado de `85 %`; el resultado quedó marcado como degradado y el
modelo no se aprobó para decisiones operativas. El [informe M2](../reports/m2/M2_REPORT.md) conserva
la lectura completa y los hashes de evidencia.

La confirmación crea una claim exclusiva y un recibo indexado por el hash del panel junto al
manifiesto de datos. Así, cambiar el directorio de reportes o regenerar una selección equivalente no
repite silenciosamente los folds `14-19`. Una claim interrumpida se audita manualmente; no se borra
para reintentar después de haber podido observar resultados parciales.

## Evidencia

Cada ejecución registra configuración, rango de datos, lista de SKU, cutoffs, versiones de
dependencias, métricas y hashes de entradas, código y salidas. Si existe un repositorio Git también
registra commit y estado del worktree; antes de publicar, una ejecución canónica deberá provenir de
un commit limpio. El registro es suficiente para reproducir una comparación sin convertir el
proyecto en una plataforma de tracking.
