# Auditoría de datos M0

**Resultado:** `PASS` para construir y evaluar el baseline de desarrollo. Este veredicto no valida
un modelo aprendido ni abre la ventana temporal final.

## Procedencia e integridad

| Evidencia | Valor observado |
|---|---|
| Dataset | UCI Online Retail II |
| DOI | <https://doi.org/10.24432/C5CG6D> |
| Licencia | CC BY 4.0 |
| ZIP | 45.622.418 bytes |
| SHA-256 ZIP | `572e36277c2390fbfde10664750731e0a86f55e33470d91919085f0408e67bfb` |
| Workbook | 45.622.278 bytes |
| SHA-256 workbook | `bcbe73b35f5b7babf197fb0cb983a11f5d9ff929078d4aa53d171b1f2df2e980` |
| Verificación | SHA-256, CRC del ZIP e inspección del workbook |
| Fecha de auditoría | 26 de agosto de 2026 |

El workbook contiene dos hojas:

| Hoja | Filas físicas | Inicio | Fin |
|---|---:|---|---|
| `Year 2009-2010` | 525.461 | 2009-12-01 07:45 | 2010-12-09 20:01 |
| `Year 2010-2011` | 541.910 | 2010-12-01 08:26 | 2011-12-09 12:50 |

Las 1.067.371 filas físicas incluyen un solapamiento exacto del 1 al 9 de diciembre de 2010:

- 45.046 filas afectadas;
- 22.202 grupos de valores exactos;
- 22.523 ocurrencias repetidas en ambas hojas;
- cero grupos con multiplicidad desigual entre las hojas oficiales;
- 22.523 copias retiradas mediante unión multiconjunto;
- 1.044.848 filas lógicas resultantes.

La unión conserva repeticiones dentro de una hoja. Permanecen 11.812 repeticiones exactas más allá
de la primera; sin identificador de línea no existe evidencia suficiente para eliminarlas.

## Contrato y funnel del objetivo

Las validaciones obligatorias pasaron sin fechas, cantidades, precios ni identificadores requeridos
inválidos. Hay 235.287 `customer_id` ausentes y 4.275 descripciones ausentes; ambos campos son
opcionales y no entran al modelo.

El objetivo es `gross_positive_invoiced_units`. Se exige factura no cancelada, cantidad y precio
positivos, y código estándar `^[0-9]{5}[A-Z]{0,2}$`.

| Regla o etapa | Filas | Unidades positivas asociadas |
|---|---:|---:|
| Filas lógicas de entrada | 1.044.848 | — |
| Facturas con prefijo de cancelación | 19.165 | se reportan fuera del target |
| Cantidad no positiva | 22.557 | 1.048.278 unidades devueltas en valor absoluto |
| Precio no positivo | 6.029 | 250.775 |
| Código no estándar | 5.992 | 25.002 |
| Exclusión conjunta | 29.903 | no sumar categorías: existen intersecciones |
| Filas elegibles | 1.014.945 | 11.221.670 |

La primera auditoría detectó que un sufijo de una sola letra excluía variantes reales como
`15056BL`, `79323LP` y `79323GR`. El patrón se amplió y quedó cubierto por una prueba. Entre los
códigos no estándar de mayor volumen restantes aparecen `POST`, `M`, `DOT`, `C2` y `D`, coherentes
con cargos o ajustes administrativos. Códigos de bajo volumen como `DCGSSGIRL` o `PADS` podrían
representar artículos especiales; se mantienen fuera de esta cohorte estándar y quedan declarados
como limitación de la heurística.

## Cobertura temporal y calendario

- Rango completo: 2009-12-01 a 2011-12-09, 739 días calendario.
- Días con alguna transacción en el ledger: 604.
- Días sin ninguna transacción: 135.
- De los 105 sábados, 104 no tienen transacciones; `source_observed_day` permite distinguir esta
  ausencia de un cero de ventas observado para un SKU.
- El panel no usa `source_observed_day` como feature futura.

## Cohorte congelada

La selección usa exclusivamente los primeros 365 días y termina antes del 1 de diciembre de 2010.
Exige 60 días activos y actividad dentro de los 56 días previos al cutoff. Los 20 SKU resultantes,
ordenados por unidades de training, son:

`21212`, `85123A`, `84077`, `85099B`, `17003`, `84879`, `84991`, `22197`, `21977`,
`21232`, `21213`, `21982`, `21980`, `84568`, `84755`, `84270`, `84347`, `21984`,
`84992`, `20725`.

Todos cumplen recencia: la última actividad observada en training está entre el 28 y el 30 de
noviembre de 2010. El manifiesto conserva días activos, unidades de training, reglas de selección,
contrato del target y hashes.

## Panel derivado

| Propiedad | Resultado |
|---|---:|
| Dimensiones | 739 días × 20 SKU = 14.780 filas |
| Filas con target cero | 5.492 (37,16 %) |
| Unidades del target en todo el panel | 1.120.119 |
| Duplicados `date, sku` | 0 |
| Nulos en target | 0 |
| Targets negativos o fraccionarios | 0 |
| SHA-256 del panel | `6d39886a45da1da3a25e31c897aacaaa9017a0b88080250c443fc08daa02cf0d` |

Los últimos 84 días, 2011-09-17 a 2011-12-09, se mantienen como ventana final reservada. El panel
la contiene, pero los comandos M0 de desarrollo no generan predicciones ni métricas sobre ella.

## Privacidad y publicación

El ZIP, workbook, panel y tabla de predicciones no se versionan. La evidencia curada contiene solo
estadísticas agregadas, fechas, SKU de producto y hashes; no publica facturas ni identificadores de
clientes. Los artefactos reproducibles están en [`reports/m0/evidence`](../reports/m0/evidence/).

## Limitaciones

- Las ventas facturadas positivas son un proxy: no existen stockouts, inventario ni demanda latente.
- El cierre o ausencia de actividad no siempre puede distinguirse de falta de cobertura.
- La clasificación de códigos estándar es una heurística explícita, no una taxonomía oficial.
- El holdout está reservado por procedimiento, no cegado por un tercero.
