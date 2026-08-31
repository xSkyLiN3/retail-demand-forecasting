# Architecture

## Purpose and boundary

Retail Demand Forecasting & Monitoring is an evidence-first educational project for forecasting
daily positive invoiced units by SKU. It uses the historical UCI Online Retail II dataset and treats
gross positive invoiced sales as a proxy for demand. It is not an inventory optimizer, purchasing
recommendation system, or production retail platform.

The architecture separates model development and final evaluation from the product-facing replay.
This matters because the source panel contains historical outcomes, including the final window, even
when a command is not allowed to evaluate them.

## Milestone flow

```text
Pinned UCI archive
        |
        v
M0: contract, cohort, daily panel, rolling folds, seasonal-naive baseline
        |
        v
M1: frozen global-model search -> one confirmation -> promotion decision
        |
        v
M2: frozen champion, interval calibration, prequential monitoring replay
        |
        +---- development-only freeze
        |
        `---- separate, explicitly invoked final-holdout evaluation
        |
        v
Batch replay -> JSON or PostgreSQL -> read-only API and dashboard
```

### M0 — data contract and baseline

`source.py` downloads the pinned archive and verifies its SHA-256. `dataset.py` normalizes the
workbook, applies the target rules, selects the SKU cohort using only the initial training window,
and writes a complete daily SKU panel plus manifests. `backtesting.py` constructs chronological,
non-overlapping folds and produces the mandatory seven-day seasonal-naive baseline.

The target is gross positive invoiced units. Cancelled invoices, non-positive quantities and
non-positive prices are excluded according to the documented contract. Because inventory and lost
sales are unavailable, observed sales must not be described as unconstrained demand.

### M1 — learned challenger and governance

`features.py` builds causal features in long format: one row per forecast origin, SKU and horizon.
`modeling.py` fits a small, frozen grid of global histogram gradient-boosting models. Training labels
for a fold are limited to target dates at or before its cutoff.

Selection and confirmation are separate commands. Development folds `0-13` select one candidate;
folds `14-19` confirm it once against the seasonal baseline. `comparison.py` applies the frozen gate
across aggregate error, bias, folds and SKUs. M1 preserves the baseline as champion when any required
criterion fails. The detailed, already-produced decision belongs in `reports/m1/`; this document does
not restate or manufacture evaluation results.

### M2 — uncertainty and monitoring

`intervals.py` calibrates nominal 90% intervals by horizon from signed residuals. Residuals are
scaled using each SKU's seasonal scale computed only from history available at that row's cutoff.
When a causal positive scale is unavailable, calibration and application use the explicitly stored
raw-residual fallback for that horizon. Bounds are anchored around the point forecast and clipped at
zero where required by the non-negative target.

`m2_workflow.py` freezes:

- the seasonal-naive champion inherited from M1;
- 20 development folds and six final folds covering exactly the final 84 calendar days;
- interval configuration and calibration;
- monitoring thresholds and a prequential replay;
- hashes for panel, cohort manifest, M1 evidence and source tree.

The replay uses six warm-up folds. Each later development fold is evaluated with calibration based
only on earlier folds, and records its `as_of` boundary. `m2_cli.py` keeps freezing and final-holdout
evaluation as different operations. Preparing a holdout plan does not generate predictions. Opening
the final window requires the explicit `evaluate-holdout` command and an exclusive claim/receipt.

## Product-facing components

### Batch

`batch.py` creates a deterministic seasonal-naive forecast from a supplied cutoff. Only observations
at or before the cutoff enter the point forecast, seasonal scale and run fingerprint. The frozen M2
calibration supplies horizon-specific interval offsets. A repeated run with identical inputs has the
same `run_id` and is expected to be idempotent at the repository boundary.

Outcome reconciliation is separate. It stores monitoring rows only for forecast dates whose outcomes
are supplied and match a persisted forecast. Forecast generation and outcome observation therefore
remain distinct states.

### Storage

`storage.py` exposes one repository contract with three implementations:

- `JsonForecastRepository`: an atomic-file backend for a small mutable local demo;
- `ImmutableJsonForecastRepository`: a startup-loaded, write-rejecting view of the reviewed public
  snapshot;
- `PostgresForecastRepository`: relational persistence for runs, forecasts and monitoring rows.

Both validate forecast dates, horizons, non-negative finite bounds, run identity and monitoring
reconciliation. PostgreSQL is the intended multi-process backend. The JSON backend is not a
concurrent production database.

### API and dashboard

`service.py` provides a read-only FastAPI application:

- `GET /health` checks the configured repository;
- `GET /api/forecasts` lists persisted forecasts;
- `GET /api/monitoring` lists reconciled outcomes and errors;
- `GET /` renders a compact educational dashboard;
- `GET /docs` exposes generated API documentation in the local development runtime only.

There are no HTTP mutation routes. Batch and reconciliation are deliberate command-line operations.
The public runtime disables generated documentation, restricts accepted hosts and loads the reviewed
snapshot through `ImmutableJsonForecastRepository`. The dashboard labels the data as historical
educational replay and does not claim live operations.

## Dependency direction

```text
source -> dataset -> backtesting
                     |       |
                     |       +-> intervals -> monitoring
                     +-> features -> modeling -> comparison

m2_workflow -> backtesting + intervals + monitoring
m2_cli      -> m2_workflow + evidence/claim handling
batch       -> storage
web         -> storage -> service
```

Model-development modules do not depend on FastAPI or PostgreSQL. API and database dependencies are
optional extras, keeping the analytical core testable without running a service.

## Evidence and reproducibility

Development and evaluation artifacts use canonical JSON, SHA-256 manifests and explicit temporal
partitions. Raw and generated data are excluded from version control; compact evidence and reports
are versioned separately. A canonical public run should come from a clean Git commit and a pinned
Python 3.12 environment.

The final outcomes are physically present in the historical panel. Hashes, separate commands and
exclusive claims reduce accidental reuse, but they do not provide external blinding. Methodological
honesty remains part of the system boundary.
