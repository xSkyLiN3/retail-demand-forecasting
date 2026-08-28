# Operations

## Supported use

These instructions run an educational historical-data replay locally. They do not establish a
production forecasting service or authorize purchasing decisions. Never expose raw source data,
generated evaluation registries, database credentials or local report directories through a public
web server.

## Requirements

- Python `>=3.12,<3.13`;
- a virtual environment;
- npm is not required;
- Docker and Docker Compose are optional;
- PostgreSQL is optional unless the PostgreSQL repository is selected.

The repository includes `requirements/constraints-py312.txt` for the canonical Python 3.12
dependency set.

## Local installation

PowerShell:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install --constraint requirements/constraints-py312.txt ".[dev,api,postgres]"
```

Bash:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install --constraint requirements/constraints-py312.txt '.[dev,api,postgres]'
```

For analytical work without the API or PostgreSQL driver, install `.[dev]` instead.

## Verification

```bash
python -m pytest
ruff check src tests
ruff format --check src tests
python -m build
```

Run these checks from a clean checkout before producing canonical evidence. A passing synthetic test
suite does not evaluate the real final holdout.

## Preparing the historical dataset

```bash
retail-forecast download
retail-forecast prepare
retail-forecast baseline
```

Default generated paths live under `data/raw`, `data/processed` and `reports/generated`; their
contents are intentionally ignored by Git. The download command verifies the pinned source hash.
The prepare command produces the panel and cohort manifest used by later hash checks.

## M1 commands

Select one challenger using the tuning partition:

```bash
retail-forecast tune-model
```

Confirmation is a separate, deliberate command and requires the generated selection artifact:

```bash
retail-forecast validate-model --selection PATH_TO_SELECTION_JSON
```

Do not delete claims to repeat a completed or interrupted confirmation. Inspect the claim, run and
receipt together and document any operational interruption.

## M2 freeze and final holdout

Freeze interval calibration and monitoring policy using development only:

```bash
retail-forecast freeze-m2
```

This writes a contract under `reports/generated/m2/freeze/`. It does not evaluate final-window
outcomes. Review and preserve the contract, calibration, replay and run manifest before proceeding.

Final evaluation is intentionally explicit:

```bash
retail-forecast evaluate-holdout --contract PATH_TO_M2_CONTRACT_JSON
```

This command opens the final 84-day historical window and creates an exclusive claim next to the
processed panel. Run it only after code, thresholds, calibration and reporting policy are frozen.
Do not run it speculatively, from automated tests pointed at real processed data, or as a routine CI
step. A failed run after claim creation requires manual audit rather than deleting the claim and
retrying silently.

## Local batch replay

The product batch creates forecasts from observations available at an explicit cutoff and persists
them to exactly one backend.

JSON demo repository:

```bash
retail-forecast run-batch \
  --panel data/processed/daily_demand.csv \
  --contract PATH_TO_M2_CONTRACT_JSON \
  --cutoff YYYY-MM-DD \
  --demo-repository local/demo-repository.json
```

PostgreSQL:

```bash
retail-forecast run-batch \
  --panel data/processed/daily_demand.csv \
  --contract PATH_TO_M2_CONTRACT_JSON \
  --cutoff YYYY-MM-DD \
  --database-url "$DATABASE_URL"
```

Reconcile outcomes separately:

```bash
retail-forecast reconcile \
  --outcomes PATH_TO_OUTCOME_PANEL.csv \
  --run-id RUN_ID \
  --database-url "$DATABASE_URL"
```

Use `--demo-repository` instead of `--database-url` for the JSON backend. Do not provide both.

## Running the read-only API locally

Without `DATABASE_URL`, the service reads the configured JSON snapshot:

```bash
set DEMO_SNAPSHOT_PATH=demo/demo_snapshot.json
retail-forecast-api
```

On PowerShell use `$env:DEMO_SNAPSHOT_PATH = "demo/demo_snapshot.json"`. The default bind address is
`127.0.0.1:8000`. Check:

```text
http://127.0.0.1:8000/health
http://127.0.0.1:8000/
http://127.0.0.1:8000/docs
```

The versioned demo snapshot may be intentionally empty until reviewed evidence is approved for
publication.

## Docker Compose

Set a non-default database password before starting the stack:

PowerShell:

```powershell
$env:POSTGRES_PASSWORD = "replace-with-a-long-random-secret"
docker compose up --build
```

Bash:

```bash
export POSTGRES_PASSWORD='replace-with-a-long-random-secret'
docker compose up --build
```

The API is published only on host loopback by the supplied Compose file. PostgreSQL is not published
to the host. The application migrates its small schema at startup and, when `SEED_DEMO=1`, imports
the immutable demo snapshot idempotently.

Useful checks:

```bash
docker compose ps
docker compose logs api
docker compose exec postgres pg_isready -U retail_forecasting -d retail_forecasting
curl http://127.0.0.1:8000/health
```

Stop services without deleting the database volume:

```bash
docker compose down
```

Deleting the named volume destroys persisted demo data and must be a deliberate operation.

## Safe operation checklist

- Keep `.env`, DSNs and passwords outside version control.
- Replace the Compose fallback password for every non-ephemeral environment.
- Keep the supplied API bound to loopback unless a reverse proxy, TLS, access policy and resource
  limits have been reviewed.
- Treat the API as public read-only data if it is exposed; never seed confidential retail records.
- Use PostgreSQL for concurrent writers. The JSON backend is for a small local demo.
- Back up PostgreSQL before schema or deployment changes and test restoration separately.
- Preserve evaluation claims, receipts, contracts and their referenced outputs together.
- Do not describe `/health` as full production readiness; it verifies repository connectivity only.
- Do not schedule final-holdout evaluation in CI.

## Troubleshooting

- **Input hash mismatch:** do not bypass it. Confirm panel, cohort, M1 evidence, source tree and
  contract came from the same frozen run.
- **Existing claim without receipt:** inspect all generated files and logs. The claim intentionally
  prevents an automatic retry after outcomes may have been observed.
- **Run ID collision with different content:** stop and compare the persisted run, calibration and
  input panel; never overwrite it.
- **Empty dashboard:** verify the selected snapshot or PostgreSQL database contains reviewed runs.
  The repository's default snapshot can legitimately contain no records.
- **Storage unavailable:** verify the DSN, container health and database permissions. API `/health`
  returns `503` when repository access fails.
