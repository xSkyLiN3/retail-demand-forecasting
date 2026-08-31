# M0 report — development baseline

**Status:** `PASS` to close M0 and begin M1. The outcome demonstrates a reproducible pipeline and an
honest baseline; it does not yet demonstrate a learned model suitable for a demo.

## Run identity

| Field | Value |
|---|---|
| Run | `634d049d9d45373a` |
| Project | `0.1.0` |
| Python | `3.12.0` |
| Model | `seasonal_naive_7d` |
| Type | development baseline |
| Executable code SHA-256 | `ef130e2107611f3a5e4768b3a3b1f6acc6073e485cbdd4e5ae14a1d6d0286c4c` |
| Panel SHA-256 | `6d39886a45da1da3a25e31c897aacaaa9017a0b88080250c443fc08daa02cf0d` |
| Cohort manifest SHA-256 | `50043991459d78fcae8006de381d5220682f5238d05a4a76008d0eb669b75513` |

The directory was not yet a Git repository, so the commit is recorded as `null`. The executable tree
hash covers `src/`, `pyproject.toml`, and the constraints; a future public run must also originate
from a clean commit.

## Executed protocol

- Cohort: 20 SKUs, frozen before 2010-12-01.
- Initial history: 365 days.
- Horizon: 14 days.
- Separation between cutoffs: 14 days; non-overlapping tests.
- Development: 20 folds, 5,600 predictions.
- First cutoff: 2010-11-30.
- Last date evaluated in development: 2011-09-06.
- Reserved, unevaluated final window: 2011-09-17 to 2011-12-09, 84 days.

The ten days between the last complete development window and the final reserve remain unscored:
the next 14-day block would have crossed the boundary. No fold was truncated to improve the result.

## Overall result

| Metric | Result | Interpretation |
|---|---:|---|
| WAPE | 1.3678 | absolute error equal to 136.78% of the observed volume |
| MASE | 0.6854 | mean error scaled against historical weekly volatility |
| Normalized bias | +0.0870 | aggregate overprediction of 8.70% |
| MAE | 77.3211 | absolute units per SKU-day row |
| Observed units | 316,560 | denominator for WAPE and bias |
| Predicted units | 344,116 | 27,556 above the observed value |

A MASE below one does not mean that the baseline “outperforms itself”: its denominator is the mean
seasonal error from the history available for each SKU and fold. WAPE and MASE answer different
questions and are published together to avoid selective favorable interpretation.

## Variability and failures

- Fold-level WAPE ranges from 0.8487 (fold 14) to 2.2512 (fold 3).
- Only two folds have WAPE strictly below 1; the baseline is weak and unstable.
- The SKUs with the lowest WAPE are `84755` (0.9971), `20725` (0.9979), and `84347` (1.1038).
- The SKUs with the highest WAPE are `84270` (2.8824), `21984` (2.0550), and `21982` (1.8401).
- `84270` had 22,979 units during selection, but only 17 across the 280 evaluated dates. Its WAPE is
  extreme even though its MAE is only 0.175: this is an example of a product that nearly ceased to
  be active and why a single percentage metric is insufficient.
- Horizons 4 and 11 always fall on a Saturday because of the fold cadence. Both actual and baseline
  values are zero across their 400 rows, and WAPE/bias are correctly recorded as `null`, not zero.

The Saturday pattern, intermittency, and changes in activity are signals that the M1 model must
represent without using outcomes after the cutoff.

## Reproducibility

The run was repeated without modifying inputs or code. The ID and all three hashes matched:

| Artifact | SHA-256 |
|---|---|
| Folds | `3b7d5ba3699de9d16a4b65b9f69da77d8b7e05a261969bbba2444a818b4a8db8` |
| Metrics | `8fc51064f79018873ea7794d3c687cdf942b0f647f03a7f21aa60576fd143d70` |
| Unversioned predictions | `90f34e17dd52ef193b3a6a319d6b9321e7d2acade4b9de51d77ef267699268b6` |

The curated copy of [`run.json`](evidence/run.json) has SHA-256
`0d78e65b9255528570ce803c06c645910d25b49704a0ea4000183ef52818f301`. The curated JSON files are
byte-for-byte copies of the local run. The prediction table is omitted from Git because of its size,
but can be regenerated with:

```powershell
retail-forecast download
retail-forecast prepare
retail-forecast baseline
```

## Passed controls

- Pinned SHA for the ZIP and workbook.
- Overlap between worksheets resolved through a multiset union and audited.
- Target contract and selection included in the manifest.
- Cohort aligned exactly with the first fold.
- Chronological, integer, complete, unique, and non-overlapping folds.
- Outcomes reconciled against the panel before metric calculation.
- Global, fold-level, SKU-level, and horizon-level metrics.
- Final window excluded from the development command.
- 32 tests, Ruff, and `pip check` passing without warnings.
- `sdist` and wheel built successfully in an isolated environment.

## M0 decision

M0 is approved because a reproducible baseline that is sufficiently difficult to manipulate now
exists. The high WAPE is neither hidden nor presented as predictive success. M1 must attempt to beat
it with a single global direct multi-horizon model, features computed only at the cutoff, and an
identical comparison by fold/SKU/horizon.

M0 **does not include** a learned model, intervals, an API, PostgreSQL, monitoring, deployment, or a
public demo. The final window remains unevaluated.
