# M2 — Intervals, monitoring, and final evaluation

## Executive outcome

M2 was executed and closed. The inherited champion, `seasonal_naive_7d`, was evaluated exactly once
on the panel's final 84 days after freezing the code, cohort, M1 decision, split, calibration, and
guardrails.

The result is an **operational no-go**: the point forecast retains a high WAPE (`1.1565`), and the
intervals achieve only `77.02%` coverage against the nominal `90%` and the predeclared minimum of
`85%`. The system published `52` alerts and marked the run as
`degraded_with_published_alerts`. The method was not modified after observing the holdout.

The software is complete as a historical ML engineering demonstration: it reproduces the data,
separates selection from confirmation, prevents silent reopening, persists forecasts and outcomes,
and exposes unfavorable metrics through the API and dashboard.

## Identity and integrity

| Field | Value |
|---|---|
| Champion | `seasonal_naive_7d` |
| M2 contract | `15d47c1e177eff3d` |
| Internal contract SHA-256 | `15d47c1e177eff3d2092040f730786c18077217b6f895d5d6077ef863bfe3dcc` |
| Final evaluation | `c78eb14bc06ea484` |
| Run SHA-256 | `db3eb23931a5e0fe4b9f1b2bb425cf779e0b5ba8697510d0f182bd1fcba271f6` |
| Panel SHA-256 | `6d39886a45da1da3a25e31c897aacaaa9017a0b88080250c443fc08daa02cf0d` |
| Executable tree SHA-256 | `984cb0ed8f3d163f69434785ac77b6b21450947a507c35e3467184267586cfaa` |
| Holdout status | `evaluated_once_no_retuning` |

Before evaluation, the following were verified: panel, cohort, M1, and code hashes; champion; 20
development folds; six final folds; the ten-day gap; calibration across 14 horizons; and thresholds.
The exclusive claim and receipt were linked to the panel hash; the receipt was then cross-checked
against the run and its four outputs.

## Final split

- Cohort: 20 SKUs selected using only the initial period.
- M2 development: folds `0–19`, 14 days each.
- Prequential calibration: six warm-up folds; each replay uses only outcomes preceding its `as_of`.
- Unscored gap: ten days between development and holdout.
- Holdout: folds `20–25`, from `2011-09-17` through `2011-12-09`.
- Final rows: `1,680` (`20 SKUs × 14 horizons × 6 origins`).

The evaluation follows a rolling-origin design: each 14-day block may use history already observed
in earlier blocks, while interval calibration remains fixed exclusively on development data.

## Global metrics

| Metric | Result | Interpretation |
|---|---:|---|
| Actual units | 123,744 | denominator for WAPE and bias |
| Predicted units | 131,082 | aggregate overforecast of 7,338 |
| MAE | 85.1881 | absolute error per SKU-day row |
| WAPE | 1.1565 | absolute error equivalent to 115.65% of volume |
| Normalized bias | +0.0593 | passes the maximum absolute value of 0.10 |
| Coverage | **0.7702** | **fails** the 0.85 minimum and 0.90 nominal target |
| Mean width | 192.3692 | wide and still insufficient |
| Median width | 164.7085 | evidence of heterogeneity |
| Winkler | 1,105.4818 | penalizes width and misses |

The fact that WAPE does not trigger the provisional `2.00` threshold does not make the model good. An
error of `115.65%` remains too high for purchasing or replenishment. The threshold is retained
because it was declared before opening the holdout, but it is interpreted as a detector of extreme
degradation, not as a business approval criterion.

## Guardrails and alerts

| Guardrail | Threshold | Global observation | Result |
|---|---:|---:|:---:|
| Minimum coverage | 0.85 | 0.7702 | **fails** |
| Maximum coverage | 0.98 | 0.7702 | passes |
| Maximum absolute bias | 0.10 | 0.0593 | passes |
| Provisional maximum WAPE | 2.00 | 1.1565 | passes technically |

A total of 52 alerts were issued: 29 for coverage and 23 for absolute bias. One applies to the global
scope; the remainder identify failures by horizon or SKU.

Horizons 4 and 11 are the most severe cases: coverage of `12.50%` and `11.67%`, with a mean width
of zero. Horizons 1 and 8 have aggregate actual units equal to zero and coverage of `100%`; WAPE and
bias are correctly left as not evaluable. This combination demonstrates why coverage cannot be
interpreted without width, observed positive invoiced units, and its corresponding score.

![Final coverage by horizon](figures/holdout_coverage_by_horizon.svg)

At the SKU level, coverage ranges from `58.33%` for `22197` to `100%` for `84270`. The latter had
zero actual units throughout the holdout, so WAPE and bias are not evaluable: it is not presented as
a forecasting success.

## Final decision

- **Portfolio system:** complete and locally demonstrable.
- **Statistical champion:** retains its identity under the M1 gate; the challenger was not promoted.
- **Operational fitness:** rejected.
- **M2 intervals:** degraded; they do not satisfy the coverage contract.
- **Retuning on the holdout:** prohibited and not performed.

A future version may investigate adaptive intervals by regime, intermittent demand, or blocked
calibration, as well as new challengers. That research must use new temporal evidence or an external
dataset: this holdout is no longer valid for selection.

## Artifacts

The canonical manifests record the following outputs:

| Output | SHA-256 |
|---|---|
| M2 calibration | `28a36296dd11c3647b8f48b2b8c6bbe228b73da98a81aff8845ec7009999a830` |
| M2 contract | `9a7483f671f4b8f428aedb870ad3eeedc472e9370a59a7e9338844dfcf95de8d` |
| Prequential replay | `e328882b5c7de1a0c6bb327b0d563179e68ad042daddbf4377a5dc545a59590d` |
| Development predictions | `90f34e17dd52ef193b3a6a319d6b9321e7d2acade4b9de51d77ef267699268b6` |
| Holdout predictions | `2a0afda7eee987a61232462d4cd380c7c8636de2c40b611022d481992b120a05` |
| Final monitoring | `e32c68b31f2f0ef1e5e15b26a1bf6210dbd7b743a0c1750c95a500ca00d6a97a` |
| Final evaluation | `16d21e0ce68694c62ed3cf75ba71c73e1df4db2688f42befe0bd2a28b0bb3f29` |
| Demo snapshot | `6a1e418049eb2a2c5094be44c6cf722452a2d9c471ba2a230c5c9f0488f4caad` |

The [compact summary](evidence/evaluation_summary.json) is available for quick review; the hashes
and canonical manifests remain the integrity reference.
