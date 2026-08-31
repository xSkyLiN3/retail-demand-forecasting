# M1 — Global Model and Promotion Decision

## Outcome

The global `poisson_conservative` model **was not promoted**. Although it reduced confirmation
WAPE from `1.2365` to `1.0889` (a relative improvement of `11.94%`) and also improved MASE, it
failed three predeclared criteria: absolute bias, bias deterioration relative to the baseline, and
the breadth of improvement across SKUs. `seasonal_naive_7d` retains the champion role. The
promotion decision is therefore an explicit **no-go**.

No retuning was performed after observing confirmation, and the final 84 days remain reserved,
with no predictions or metrics.

## Design Frozen Before Confirmation

- Direct global model in long format: one row per origin, SKU, and horizon.
- Horizon: 14 days, all predicted from the same cutoff.
- Causal features with 56 days of history; every training label satisfies
  `target_date <= cutoff`.
- Three fixed `HistGradientBoostingRegressor` configurations.
- `early_stopping=False`, 250 iterations, and seed 42 for every candidate.
- Folds `0-13` for selection; folds `14-19` for a single confirmation.
- Confirmation gate persisted before reading its results.

The exact feature contract is in
[feature_contract.json](evidence/feature_contract.json), and the complete search is in
[model_grid.json](evidence/model_grid.json).

## Selection on Folds 0–13

| Model | WAPE | Improvement vs. baseline | MASE | Bias | Folds won |
|---|---:|---:|---:|---:|---:|
| Seasonal naive | 1.4250 | — | 0.6859 | +0.1294 | — |
| Conservative Poisson | **1.2196** | **14.42%** | **0.6290** | +0.2206 | 11/14 |
| Medium Poisson | 1.2993 | 8.82% | 0.6761 | +0.2565 | 12/14 |
| Squared-error control | 1.4164 | 0.60% | 0.7363 | +0.4237 | 7/14 |

The primary rule was aggregate WAPE, so conservative Poisson was selected. Tuning bias already
indicated a risk, but it was not a license to change the criterion after seeing the results. The
immutable selection was recorded as `88eb90b8563a7568`, with SHA-256
`4988769d144732e3c8154f310ab989670dc6abe3e423bec3a19e503796119856`.

## Confirmation on Folds 14–19

| Metric | Candidate | Baseline | Outcome |
|---|---:|---:|---|
| WAPE | **1.0889** | 1.2365 | 11.94% improvement |
| MASE | **0.6043** | 0.6843 | improvement |
| MAE | **62.23** | 70.67 | improvement |
| Normalized bias | +0.1477 | **−0.0103** | candidate overforecasts |
| Forecast units | 110,205.4 | 95,030.0 | actual: 96,020 |
| Folds with lower WAPE | 4/6 | 2/6 | meets minimum |
| SKUs with lower WAPE | 10/20 | — | falls short of 11 |

The improvement appeared in both segments: `13.05%` for horizons 1–7 and `10.89%` for horizons
8–14. It does not depend solely on the first forecast days.

![Confirmation WAPE by fold](figures/confirmation_wape_by_fold.svg)

### Predeclared Gate

| Criterion | Threshold | Observed | Pass |
|---|---:|---:|:---:|
| Relative WAPE improvement | ≥ 5% | 11.94% | yes |
| Folds won | ≥ 4/6 | 4/6 | yes |
| SKUs won | ≥ 11/20 | 10/20 | **no** |
| MASE below baseline | < 0.6843 | 0.6043 | yes |
| Absolute bias | ≤ 0.10 | 0.1477 | **no** |
| Absolute bias deterioration | ≤ 0.02 | 0.1374 | **no** |
| Finite, non-negative predictions | required | yes | yes |

SKU `84270` had aggregate actual demand equal to zero during confirmation, so its WAPE is not
evaluable and it does not count as a win. Among the evaluable SKUs, the candidate won on 10 and
lost on 9. The decision is not changed by reinterpreting that case after observing it.

## Failure Analysis

The candidate learns enough structure to reduce absolute error, but shifts total volume upward: it
forecasts 14,185 more units than were observed. The pattern is consistent with a Poisson loss with
a logarithmic link, which produces positive values even for combinations with nearly zero
historical demand. This is an explanatory hypothesis, not demonstrated causality.

Horizons 4 and 11 had zero aggregate actual units. The weekly baseline forecast zero, while the
candidate produced a small but non-zero MAE (`0.247` and `0.255`). This reveals a ledger regularity
—no Sunday activity in those blocks—that the baseline captures naturally. The largest
deteriorations are concentrated in some low-volume SKUs, especially `84347` and `21982`, while the
strongest improvements appear for `84992`, `85099B`, and `21977`.

## Engineering Controls

- Complete 20-SKU × 14-horizon grid in every fold.
- 14/14 tuning fits and 6/6 confirmation fits used exactly their cutoff as the latest label.
- Zero raw negative predictions from Poisson.
- Mutating outcomes after the cutoff does not change the origin's features or forecasts.
- Exact paired comparison by fold, cutoff, date, SKU, and horizon.
- Contracts, inputs, environment, code, and outputs protected with SHA-256.
- A panel-hash receipt blocks another accidental confirmation with a different `report-dir`.
- The decision is reconciled against the comparison file; it does not rely on an isolated flag.

The confirmation run is `de2cb9a17c8762ec`. The complete prediction tables remain outside the
repository because of their size; their hashes and the verifiable summary are in
[confirmation_summary.json](evidence/confirmation_summary.json). Selection is summarized in
[tuning_summary.json](evidence/tuning_summary.json).

## Decision and Next Milestone

The holdout will not be opened for this candidate, and no corrections will be tested using the
folds already observed. M2 must build uncertainty and monitoring around the seasonal champion,
including coverage by horizon, bias detection, and explicit treatment of zero-demand windows. It
would make sense to evaluate the final 84 days once only after that protocol has been frozen.

This outcome does not demonstrate commercial impact or the general superiority of Poisson. It does
demonstrate a defensible ML Engineering decision: a model with a better aggregate metric was
rejected because its bias and product-level coverage did not meet the standard defined before
evaluation. The promotion decision remains **no-go**, with **no retuning** after confirmation.
