# Evaluation protocol

## Unit and horizon

- Series unit: SKU.
- Frequency: calendar day.
- Horizon: 14 days.
- Primary seasonality: 7 days.

## Rolling-origin backtesting

Each fold contains:

- `train`: all dates through the cutoff, inclusive;
- `test`: the 14 days immediately after it;
- spacing between cutoffs: 14 days, generating non-overlapping future blocks;
- initial minimum: 365 days of history;
- public minimum: six complete folds.

The SKU cohort is frozen before the first fold. Transformations, imputations, lags, scaling,
feature selection, and calibration must be fitted using data available through the cutoff of each
fold.

The last 84 days form a **reserved final time window**, equivalent to six 14-day blocks.
Development commands do not generate predictions or metrics for it, although its outcomes remain
physically present in the local panel; the protection is therefore procedural, not external
blinding. Earlier folds may be used to choose one model and a small search. Features,
hyperparameters, the interval method, and thresholds are frozen before the final window is
evaluated. Its results are published once and are not used for retuning.

## Frozen M1 protocol

The 20 development folds are split once, chronologically:

- folds `0-13`: fitting and selection among predeclared configurations;
- folds `14-19`: procedural confirmation of the single selected candidate;
- last 84 days: final holdout still reserved, outside M1.

Selection and confirmation are separate commands. The first command persists the feature
contract, partition, grid, seed, input and code hashes, and winning configuration before results are
calculated for folds `14-19`. The second command rejects any selection whose contract or hashes no
longer match. The selection file is not modified during confirmation.

The candidate is a single global direct model in long format. Each row represents
`(origin_date, sku, horizon) -> units_at_origin_plus_horizon`, with horizons `1-14`. To train a fold
with cutoff `c`, only labels with `target_date <= c` are allowed; for inference, every row starts
from `origin_date = c`. There is no recursive forecasting, and outcomes from days 1-13 are not used
to predict later horizons.

The frozen features are:

- SKU and horizon as native categorical features;
- known calendar features for the target date: day of week, month, weekend, and annual sine/cosine;
- seasonal naive available at the origin;
- demand aligned with the target date at 14, 21, 28, and 35 days;
- demand observed at the origin and lags of 1, 7, 14, and 28 days;
- rolling means of 7, 14, 28, and 56 days, plus 28-day standard deviation and maximum;
- proportion of active days over 28 and 56 days, days since the last positive demand, and two trend
  measures between 7-day and 28-day windows.

Price, customer, country, description, `source_observed_day`, global target encoding, and features
calculated using the complete panel are not used. Historical origins are daily and require 56 days
of causal context.

The grid is limited to three `HistGradientBoostingRegressor` configurations, all with
`learning_rate=0.05`, `max_iter=250`, `l2_regularization=1`, `early_stopping=False`, and seed 42:

1. conservative Poisson: 7 leaves and a minimum of 80 samples per leaf;
2. medium Poisson: 15 leaves and a minimum of 40 samples per leaf;
3. squared error control: 15 leaves and a minimum of 40 samples per leaf.

The lowest aggregate WAPE over folds `0-13` is selected; a tie favors lower absolute bias and then
the simpler configuration. The search is not expanded after confirmation is observed.

## Baselines

1. **Seasonal naive (required):** repeats the pattern of the last seven days.
2. **28-day rolling mean (secondary reference):** will be added only if it provides an
   interpretable comparison.

The seasonal naive is published first on the development folds. In the final window, the baseline
and learned model will be evaluated and published together, using exactly the same dates, so that
final outcomes are not revealed before the candidate is frozen.

## Metrics

- **WAPE:** aggregate absolute error divided by aggregate observed demand.
- **MASE:** each error is scaled by the seasonal-naive denominator of its own SKU and fold; the
  aggregate MASE is the mean of evaluable scaled errors, never a denominator mixed across SKUs.
- **Normalized bias:** sum of `forecast - actual` divided by observed demand.
- **MAE:** absolute units per row.
- **Interval coverage:** percentage of observations between the lower and upper bounds.
- **Interval width:** must be reported alongside coverage.

Global, per-fold, per-SKU, and per-horizon values are published. Series with a zero denominator are
marked as not evaluable for the corresponding metric; they are never silently replaced with zero.

The intervals will have 90% nominal coverage. The method will be calibrated using only residuals
from the development folds; coverage and width by horizon will be published for the final holdout.

## Learned-model gate

To replace the baseline as the candidate model, it must:

- reduce aggregate WAPE by at least 5% relative to the baseline;
- improve WAPE in at least 4 of the 6 confirmation folds;
- improve WAPE for at least 11 of the 20 SKUs; a SKU with non-evaluable WAPE does not count as a
  win;
- achieve an aggregate MASE lower than the baseline;
- not hide deterioration through a single weighted average;
- maintain `abs(normalized_bias) <= 0.10` and not worsen the baseline's absolute bias by more than
  0.02;
- produce only finite, non-negative predictions;
- pass all temporal-leakage tests.

Performance is also reported separately for horizons `1-7` and `8-14`. If any criterion fails,
seasonal naive retains the champion role, and M1 documents the rejected candidate without
retuning. Confirmation determines whether the candidate proceeds to M2; it does not constitute the
final result. The main public result will come from the time holdout once the interval method and
monitoring have also been frozen.

## M2 closure

After the method was frozen, the holdout was opened once. The champion achieved WAPE `1.1565`,
normalized bias `+0.0593`, and interval coverage `77.02%` against the nominal `90%`. Coverage
failed the predeclared minimum of `85%`; the result was marked as degraded, and the model was not
approved for operational decisions. The [M2 report](../reports/m2/M2_REPORT.md) preserves the
complete interpretation and evidence hashes.

Confirmation creates an exclusive claim and a receipt indexed by the panel hash alongside the
data manifest. As a result, changing the reports directory or regenerating an equivalent selection
does not silently repeat folds `14-19`. An interrupted claim is audited manually; it is not deleted
to retry after partial results may have been observed.

## Evidence

Each run records configuration, data range, SKU list, cutoffs, dependency versions, metrics, and
hashes of inputs, code, and outputs. If a Git repository exists, it also records the commit and
worktree state; before publication, a canonical run must come from a clean commit. The record is
sufficient to reproduce a comparison without turning the project into a tracking platform.
