# Model Card — Seasonal-Naive Retail Demand Forecast

## Model details

| Field | Value |
|---|---|
| Project | Retail Demand Forecasting & Monitoring |
| Current champion | Seven-day seasonal naive |
| Model identifier | `seasonal_naive_7d` |
| Task | Daily SKU-level multi-horizon regression |
| Horizon | 14 calendar days |
| Interval target | Nominal 90% coverage |
| License for project code | MIT |
| Status | Educational historical-data system; not approved for purchasing decisions |

The champion repeats the most recent observed seven-day pattern for each SKU across the next 14
days. It is deterministic, non-negative and recalculated from data available at the forecast cutoff.

M1 also evaluates a learned global challenger under a frozen temporal protocol. Promotion is not
based on one aggregate metric: error, normalized bias, fold breadth and SKU breadth are evaluated
together. The current champion identity must be taken from the versioned M1 decision evidence, not
inferred from this summary alone.

## Intended use

Appropriate uses:

- demonstrate chronological forecasting evaluation and leakage controls;
- inspect seasonal-naive forecasts, uncertainty intervals and historical errors;
- exercise deterministic batch, storage, reconciliation and a read-only API;
- teach why a simpler baseline can remain champion after a learned model is evaluated;
- support a professional portfolio case study with explicit limitations.

Out-of-scope uses:

- purchasing, replenishment or inventory allocation;
- staffing, pricing or financial commitments;
- live production forecasting for a retailer;
- estimation of lost sales, stockouts or unconstrained demand;
- new-SKU forecasting without history;
- causal claims about promotions, price, customers or market behavior;
- automated decisions affecting people or access to services.

## Training and evaluation data

The source is UCI Online Retail II, a historical transactional dataset distributed under CC BY 4.0.
The project pins the archive URL and SHA-256. The model target is daily gross positive invoiced units
per selected SKU.

The target is a sales proxy. The dataset does not provide reliable inventory position, stockout
events, lost demand, fulfilment confirmation or the operational context of a modern deployment.
Zero observed units can mean no positive invoiced sale; they do not prove zero customer demand.

The SKU cohort is frozen using only an initial training window. A complete daily panel represents
missing positive sales as zero according to the data contract. Evaluation uses chronological
rolling-origin blocks rather than a random split.

## Evaluation design

The forecasting unit is `(cutoff, SKU, horizon)`, where horizons span 1–14 days. Each fold trains or
conditions only on data available through its cutoff and evaluates the immediately following block.

The project separates evidence into:

- M0: data contract and seasonal baseline;
- M1 tuning: development folds `0-13`;
- M1 confirmation: development folds `14-19`;
- M2 interval calibration and prequential development replay;
- one final 84-day historical holdout, opened only by a separate explicit command.

Metrics include WAPE, MASE, MAE and normalized bias. Interval reporting includes empirical coverage,
mean and median width, and Winkler score, globally and by relevant slices. WAPE and normalized bias
are non-evaluable when observed aggregate units are zero; the implementation preserves null rather
than substituting a favorable zero.

Existing numerical results are reported in the versioned M0/M1 evidence and reports. This model card
does not duplicate them because doing so risks separating claims from their hashes and evaluation
context. Final-holdout results must not be claimed until a canonical receipt and reviewed evidence
exist.

## Prediction intervals

M2 calibrates signed residual quantiles separately by horizon. For rows with an evaluable seasonal
scale, the residual is:

```text
(actual - point forecast) / causal seasonal scale
```

The scale is computed per SKU from history ending at that row's cutoff. If no positive finite scale
exists, the row uses signed raw-residual quantiles for the same horizon. Lower and upper residual
quantiles are anchored to contain zero; forecast bounds are constrained to contain the point forecast
and the lower bound cannot be negative.

The intervals have nominal 90% coverage. They are empirical historical intervals, not a guarantee
for future retail data. Coverage must be reported together with width: a very wide interval is not a
useful success merely because it contains most outcomes.

## Monitoring

The historical replay monitors:

- interval coverage below `0.85` or above `0.98`;
- absolute normalized bias above `0.10`;
- WAPE above the provisional explicit guardrail `2.0`;
- interval width and proper interval score as diagnostic values;
- per-horizon and per-SKU slices, including unevaluable zero-demand metrics.

Forecast generation and outcome reconciliation are separate. A forecast can remain pending until a
matching outcome is supplied. Monitoring rows must reconcile exactly with persisted run, SKU,
horizon, date, point forecast and interval.

These thresholds are educational guardrails. They are not service-level objectives agreed with a
retail operator and have not been optimized for a purchasing policy.

## Ethical considerations and limitations

- Historical behavior may not represent future seasonality, assortment or customer behavior.
- High-volume SKUs dominate aggregate WAPE; per-SKU evidence is therefore required.
- The simple champion cannot model promotions, holidays beyond repeated weekly structure, trend
  breaks, price changes or product substitution.
- Sparse and intermittent demand can produce unstable percentage metrics and wide intervals.
- The cohort excludes products without adequate initial history, so results do not generalize to
  cold-start items.
- The project does not quantify business cost, stockout risk or inventory carrying cost.
- Public demonstrations must use only the reviewed historical/derived data allowed by the source
  license and must not add private customer or retailer data.

## Reproducibility and governance

The project records input, code, configuration and output hashes. M1 model selection, M1 confirmation,
M2 freezing and final evaluation are separate operations. Exclusive claims and receipts are intended
to prevent silent repetition after outcomes may have been observed.

A portfolio-grade canonical result should satisfy all of the following:

- run from Python 3.12 with pinned constraints;
- originate from a clean, identifiable Git commit;
- pass tests, lint, formatting and package build;
- preserve panel, cohort, M1, M2 and output hashes;
- publish unfavorable slices and alerts as well as favorable aggregate values;
- retain the educational-use disclaimer in the API and dashboard.

## Operational ownership

The project author is responsible for reviewing data provenance, frozen contracts, claims and public
language before release. Users of the demo are responsible for treating outputs as historical
educational evidence only. There is no production support commitment, monitoring service or response
SLA.
