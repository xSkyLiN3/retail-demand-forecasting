# Contrato de datos

## Fuente

- Dataset: **Online Retail II**.
- Autor: Daqing Chen.
- Repositorio: UCI Machine Learning Repository.
- DOI: <https://doi.org/10.24432/C5CG6D>.
- Licencia de datos: CC BY 4.0.
- Período declarado: 1 de diciembre de 2009 a 9 de diciembre de 2011.
- Archivo esperado dentro del ZIP: `online_retail_II.xlsx`.

El ZIP y el workbook no se versionan en Git. El descargador comprueba el SHA-256 del archivo y
`prepare` vuelve a comprobar el SHA-256 del workbook antes de procesarlo.

## Esquema normalizado

| Campo | Tipo lógico | Uso |
|---|---|---|
| `invoice_no` | texto no vacío | detectar cancelaciones; no es feature |
| `stock_code` | texto no vacío | identificador de serie |
| `description` | texto opcional | auditoría; no es feature |
| `quantity` | entero | construir demanda y contabilizar devoluciones |
| `invoice_date` | timestamp | ordenar y agregar por día |
| `unit_price` | decimal no negativo | filtro de calidad; no es feature del forecast inicial |
| `customer_id` | texto opcional | descartado; no se usa |
| `country` | texto no vacío | auditoría; no se usa en el MVP |

El loader admite los nombres históricos del workbook (`Invoice`, `Price`, `Customer ID`) y los
normaliza. Cualquier columna obligatoria ausente detiene el pipeline.

## Definición del objetivo

`units` es **venta bruta positiva observada**, usada como proxy de demanda. Es la suma diaria de
`quantity` para filas que cumplen simultáneamente:

- la factura no comienza con `C` (cancelación);
- `quantity > 0`;
- `unit_price > 0`;
- el código de producto cumple `^[0-9]{5}[A-Z]{0,2}$`.

El sufijo de hasta dos letras se fijó después de auditar variantes reales como `15056BL`,
`79323LP` y `79323GR`. Los códigos no estándar restantes se mantienen fuera del objetivo y se
publican por volumen para detectar falsos positivos o negativos de esta heurística.

El dataset no permite medir demanda latente, disponibilidad, stockouts ni fulfilment. Las
devoluciones se contabilizan por separado y no se retrotraen al día de venta, porque hacerlo usaría
información futura.

UCI describe la fuente como todas las transacciones del período. Bajo esa premisa, los días sin
filas elegibles se completan con cero para cada SKU. El panel añade `source_observed_day`: `true` si
hubo al menos una transacción de cualquier tipo en esa fecha y `false` si el ledger completo no
contiene filas. Este indicador permite mostrar la incertidumbre entre cero ventas, cierre y posible
ausencia de cobertura; no se utiliza como feature futura.

Las cancelaciones, devoluciones y códigos administrativos se excluyen del objetivo, pero sus
conteos y unidades se registran en el informe de calidad.

## Duplicados

- Las dos hojas oficiales se solapan entre el 1 y el 9 de diciembre de 2010. Para valores exactos
  repetidos entre hojas se aplica una unión multiconjunto: se conserva la multiplicidad máxima de
  una hoja y se prefiere la primera hoja en el orden del workbook. Así no se duplica el período,
  pero tampoco se colapsan dos líneas idénticas que ya coexistían dentro de una misma hoja.
- Repeticiones exactas dentro de una hoja se cuentan y se conservan. Sin un identificador de línea
  no es posible afirmar que sean duplicados erróneos en vez de dos líneas legítimas.
- El informe publica filas afectadas y retiradas, rango temporal, grupos, multiplicidades y
  cualquier desigualdad entre hojas antes de congelar el dataset.

## Cohorte sin leakage

La cohorte se selecciona dentro de los primeros 365 días observados:

1. filtrar productos con al menos 60 días activos;
2. exigir actividad positiva dentro de los 56 días anteriores al cutoff;
3. ordenar por unidades positivas totales;
4. conservar hasta 20 productos;
5. congelar esa lista y el hash del panel antes de crear folds de evaluación.

No se permite elegir productos usando actividad, ventas o errores del período futuro.

## Invariantes

- timestamps válidos y ordenables;
- cantidades finitas e integrales;
- precios finitos;
- una fila por `date, sku` después de agregar;
- panel completo y sin valores faltantes;
- `units >= 0`;
- días sin transacciones globales identificados, no confundidos silenciosamente con cobertura
  confirmada;
- cohorte no vacía y determinada antes del primer cutoff de evaluación;
- hash y conjunto de SKU del panel iguales a los del manifiesto de cohorte;
- ninguna fecha de forecast puede ser menor o igual a su cutoff.
