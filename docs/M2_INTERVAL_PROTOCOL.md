# M2 — Interval and Monitoring Protocol

## Inherited Decision

M2 starts exclusively from `seasonal_naive_7d`, the champion retained by M1. The rejected Poisson
model is not corrected, recalibrated, or compared again using its confirmation results. M2 evidence
records the hashes of the panel, cohort manifest, M1 confirmation summary, and code tree.

## Frozen Temporal Split

- Development: 20 previously observed folds, indices `0-19`, 14 days each.
- Final holdout: six new blocks, indices `20-25`, covering exactly the final 84 calendar days of
  the panel.
- Any possible gap between the last development fold and the start of the holdout is recorded; it
  is never filled by shifting the holdout or reusing outcomes.
- Development and holdout do not overlap. Holdout preparation returns only a plan containing dates
  and hashes; it does not generate predictions or metrics.

## Intervals

Nominal coverage is 90%. Final calibration uses only champion errors from folds `0-19` and produces
separate parameters by horizon. For every row, it calculates the signed residual
`(actual - prediction) / escala_estacional_causal`: each SKU's scale is estimated using only the
history available through that row's cutoff. The lower and upper quantiles are anchored so that
they always include zero, and the forecast's lower bound is clipped at zero.

When a series has no positive, finite causal seasonal scale, that row explicitly uses the signed
residual in original units. Calibration retains raw quantiles by horizon to apply the same fallback
at inference time. It is not calibrated by SKU: twenty observations per SKU and horizon would be
too unstable to support that granularity.

Coverage and width are reported together. An excessively wide interval is not presented as a
success merely because it achieves coverage, and windows with zero actual demand retain
non-evaluable metrics where appropriate.

## Prequential Replay

Replay begins after six warm-up folds. To evaluate each fold `i`, calibration is fitted using only
folds before `i`. The artifact records:

- `as_of`: cutoff of the evaluated fold;
- folds used for calibration;
- maximum outcome date used in calibration;
- metrics and alerts for the window.

The maximum calibration date must be less than or equal to `as_of`. This replay measures historical
operational behavior; it does not replace independent holdout evaluation.

## Frozen Thresholds

Before opening the holdout, the following values are fixed:

- minimum coverage: `0.85`;
- maximum coverage: `0.98`;
- maximum absolute normalized bias: `0.10`;
- maximum WAPE: `2.00`.

These are explicit guardrails, not values optimized on the holdout. Data-quality alerts (missing,
duplicate, or invalid rows, or unavailable outcomes) must be distinguished from performance
alerts.

## Final Opening

Final evaluation is a separate operation. It must verify the complete hash of the M2 contract, the
champion, calibration, thresholds, all six temporal boundaries, and input hashes before creating
an exclusive claim. Only then may it reconstruct the baseline forecast, apply intervals, and
reconcile outcomes. The run publishes results once and does not authorize subsequent changes to
the method.

## Result After Opening

The preceding protocol was frozen before observing the holdout. The canonical opening was run once
over `2011-09-17` through `2011-12-09` and produced 1,680 rows. Coverage was `77.02%`, below the
`85%` minimum; WAPE was `1.1565`, and normalized bias was `+0.0593`. The final status is
`degraded_with_published_alerts`, and operational use is rejected: **no-go**.

Intervals and thresholds were not recalibrated after the result. Metrics, slices, alerts, and
canonical identities are in [the M2 report](../reports/m2/M2_REPORT.md) and its associated
evidence. No retuning or post-holdout method changes were performed.
