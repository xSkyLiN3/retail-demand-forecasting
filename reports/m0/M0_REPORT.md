# Informe M0 — baseline de desarrollo

**Estado:** `PASS` para cerrar M0 y comenzar M1. El resultado demuestra un pipeline reproducible y
un baseline honesto; no demuestra que exista aún un modelo aprendido apto para demo.

## Identidad de la ejecución

| Campo | Valor |
|---|---|
| Run | `634d049d9d45373a` |
| Proyecto | `0.1.0` |
| Python | `3.12.0` |
| Modelo | `seasonal_naive_7d` |
| Tipo | baseline de desarrollo |
| SHA-256 del código ejecutable | `ef130e2107611f3a5e4768b3a3b1f6acc6073e485cbdd4e5ae14a1d6d0286c4c` |
| SHA-256 del panel | `6d39886a45da1da3a25e31c897aacaaa9017a0b88080250c443fc08daa02cf0d` |
| SHA-256 del manifiesto de cohorte | `50043991459d78fcae8006de381d5220682f5238d05a4a76008d0eb669b75513` |

El directorio aún no es un repositorio Git, por lo que el commit aparece como `null`. El hash del
árbol ejecutable cubre `src/`, `pyproject.toml` y constraints; una ejecución pública futura deberá
provenir además de un commit limpio.

## Protocolo ejecutado

- Cohorte: 20 SKU, congelada antes de 2010-12-01.
- Historia inicial: 365 días.
- Horizonte: 14 días.
- Separación entre cutoffs: 14 días; tests no solapados.
- Desarrollo: 20 folds, 5.600 predicciones.
- Primer cutoff: 2010-11-30.
- Última fecha evaluada en desarrollo: 2011-09-06.
- Ventana final reservada y no evaluada: 2011-09-17 a 2011-12-09, 84 días.

Los diez días entre la última ventana completa de desarrollo y la reserva final quedan sin puntuar:
el siguiente bloque de 14 días habría cruzado el límite. No se recortó un fold para mejorar el
resultado.

## Resultado global

| Métrica | Resultado | Lectura |
|---|---:|---|
| WAPE | 1,3678 | error absoluto equivalente al 136,78 % del volumen observado |
| MASE | 0,6854 | error escalado medio frente a la volatilidad histórica semanal |
| Bias normalizado | +0,0870 | sobrepredicción agregada del 8,70 % |
| MAE | 77,3211 | unidades absolutas por fila SKU-día |
| Unidades observadas | 316.560 | denominador de WAPE y bias |
| Unidades predichas | 344.116 | 27.556 por encima de lo observado |

El MASE inferior a uno no significa que el baseline “se supere a sí mismo”: su denominador es el
error estacional medio del historial disponible para cada SKU y fold. WAPE y MASE responden a
preguntas distintas y se publican juntos para evitar una lectura favorable selectiva.

## Variabilidad y fallos

- WAPE por fold varía entre 0,8487 (fold 14) y 2,2512 (fold 3).
- Solo dos folds tienen WAPE estrictamente inferior a 1; el baseline es débil e inestable.
- Los SKU con menor WAPE son `84755` (0,9971), `20725` (0,9979) y `84347` (1,1038).
- Los SKU con mayor WAPE son `84270` (2,8824), `21984` (2,0550) y `21982` (1,8401).
- `84270` tuvo 22.979 unidades durante la selección, pero solo 17 en las 280 fechas evaluadas. Su
  WAPE es extremo aunque su MAE sea apenas 0,175: es un ejemplo de producto que casi dejó de tener
  actividad y de por qué una sola métrica porcentual resulta insuficiente.
- Los horizontes 4 y 11 siempre caen en sábado por la cadencia de folds. Tanto actual como baseline
  son cero en sus 400 filas y WAPE/bias quedan correctamente como `null`, no como cero.

El patrón de sábado, la intermitencia y los cambios de actividad son señales que el modelo M1 debe
representar sin usar outcomes posteriores al cutoff.

## Reproducibilidad

La ejecución se repitió sin modificar inputs ni código. Coincidieron el ID y los tres hashes:

| Artefacto | SHA-256 |
|---|---|
| Folds | `3b7d5ba3699de9d16a4b65b9f69da77d8b7e05a261969bbba2444a818b4a8db8` |
| Métricas | `8fc51064f79018873ea7794d3c687cdf942b0f647f03a7f21aa60576fd143d70` |
| Predicciones no versionadas | `90f34e17dd52ef193b3a6a319d6b9321e7d2acade4b9de51d77ef267699268b6` |

La copia curada de [`run.json`](evidence/run.json) tiene SHA-256
`0d78e65b9255528570ce803c06c645910d25b49704a0ea4000183ef52818f301`. Los JSON curados son copias
byte a byte de la ejecución local. La tabla de predicciones se omite de Git por tamaño, pero se
regenera con:

```powershell
retail-forecast download
retail-forecast prepare
retail-forecast baseline
```

## Controles superados

- SHA fijado para ZIP y workbook.
- Solapamiento entre hojas resuelto mediante unión multiconjunto y auditado.
- Contrato de target y selección incluido en el manifiesto.
- Cohorte alineada exactamente con el primer fold.
- Folds cronológicos, enteros, completos, únicos y no solapados.
- Outcomes reconciliados contra el panel antes de calcular métricas.
- Métricas globales, por fold, SKU y horizonte.
- Ventana final excluida del comando de desarrollo.
- 32 pruebas, Ruff y `pip check` en verde, sin warnings.
- `sdist` y wheel construidos correctamente en entorno aislado.

## Decisión M0

M0 queda aprobado porque ya existe un piso reproducible y suficientemente difícil de manipular.
El WAPE alto no se oculta ni se presenta como éxito predictivo. M1 debe intentar superarlo con un
único modelo global directo multi-horizonte, features calculadas solo al cutoff y una comparación
idéntica por fold/SKU/horizonte.

M0 **no incluye** modelo aprendido, intervalos, API, PostgreSQL, monitoring, despliegue ni demo
pública. La ventana final permanece sin evaluar.
