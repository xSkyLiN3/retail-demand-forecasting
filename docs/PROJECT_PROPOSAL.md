# Propuesta del proyecto

## Título

**Retail Demand Forecasting & Monitoring**

## Objetivo profesional

Construir una segunda evidencia fuerte para un perfil de AI/ML Engineering. El proyecto debe
complementar, no repetir, Machine Failure Risk Classifier.

La pieza anterior demuestra clasificación tabular, prevención de leakage, evaluación con holdout,
FastAPI, Docker y CI. Esta debe demostrar temporalidad, datos reales, forecasting multi-horizonte,
almacenamiento de resultados y monitorización una vez que el valor real se conoce.

## Pregunta del producto

Para un conjunto fijo de productos con historial suficiente, ¿cuántas unidades positivas
facturadas se observarán cada día durante los próximos 14 días?

La venta bruta observada se usa como proxy de demanda. No existen datos de inventario, quiebres de
stock, ventas perdidas ni confirmación de entrega. La salida debe ayudar a inspeccionar patrones y
errores; no se presentará como recomendación de compra ni como sistema validado para una tienda
real.

## Alcance del MVP

- Fuente: UCI Online Retail II, descargada con URL y SHA-256 fijados.
- Granularidad: SKU por día calendario.
- Cohorte: productos elegidos usando exclusivamente el primer año de datos.
- Horizonte: 14 días.
- Baseline obligatorio: seasonal naive semanal.
- Modelo aprendido: un modelo global con lags y variables de calendario.
- Evaluación: rolling origin, al menos seis ventanas.
- Métricas: WAPE, MASE, bias, MAE y cobertura de intervalos.
- Salida: forecast, intervalos y errores por producto, horizonte y fecha.
- Demo local: dashboard que muestre tanto aciertos como fallos.

## Fuera del MVP

- streaming en tiempo real;
- Kubernetes o una plataforma cloud de pago;
- autenticación multiusuario;
- optimización de inventario y costes de stockout;
- LLM, RAG o explicaciones generativas;
- predicciones para productos sin historia;
- afirmaciones de impacto comercial real.

## Fases

### M0 — Contrato y baseline

Descarga verificable, reglas de limpieza, cohorte sin leakage, panel diario, folds temporales y
seasonal naive. **Estado: completado y verificado localmente.**

### M1 — Modelo global

Modelo global directo para SKU y horizontes 1-14, con features disponibles al momento del forecast
y una búsqueda predeclarada de tres configuraciones. Los folds `0-13` seleccionan un único
candidato; los folds `14-19` lo confirman una vez contra seasonal naive mediante un gate objetivo.
Si falla, el baseline sigue siendo champion y el rechazo se documenta sin retuning. Los últimos 84
días permanecen reservados y excluidos de M1 hasta congelar también intervalos y monitorización. La
reserva es procedimental porque los outcomes permanecen físicamente en el panel local.

**Estado: completado y verificado localmente.** Poisson conservador mejoró WAPE de confirmación un
11.94 %, pero fue rechazado por bias positivo (`0.1477`), deterioro de bias y solo 10/20 SKU
ganados. Seasonal naive sigue siendo champion y el holdout final no se abrió.

### M2 — Incertidumbre y monitorización

Intervalos de predicción nominales al 90 %, calibrados únicamente con errores de desarrollo,
cobertura por horizonte, replay histórico y alertas de calidad/performance. M2 parte del champion
seasonal naive; no reutiliza la confirmación para rescatar el candidato rechazado.

### M3 — Producto local

Persistencia PostgreSQL, job batch idempotente, API de consulta y dashboard.

### M4 — Publicación

Docker Compose, CI, documentación, demo limitada en VPS, caso de estudio y actualización del
portafolio.

## Criterio de éxito

El modelo aprendido no gana por una única cifra agregada. Para avanzar debe superar al baseline en
WAPE en la mayoría de folds y productos elegibles, mantener bias interpretable y publicar los casos
en que pierde. Los intervalos deben mostrar cobertura observada por horizonte.
