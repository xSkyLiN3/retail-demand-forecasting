# Project Proposal

## Title

**Retail Demand Forecasting & Monitoring**

## Professional Objective

Build a second strong piece of evidence for an AI/ML Engineering profile. The project should
complement, rather than repeat, Machine Failure Risk Classifier.

The previous project demonstrates tabular classification, leakage prevention, holdout evaluation,
FastAPI, Docker, and CI. This project should demonstrate temporality, real data, multi-horizon
forecasting, result storage, and monitoring once the actual value becomes known.

## Product Question

For a fixed set of products with sufficient history, how many units with positive invoiced
quantities will be observed each day over the next 14 days?

Observed gross sales are used as a proxy for demand. The dataset contains no inventory, stockout,
lost-sales, or delivery-confirmation data. The output should support inspection of patterns and
errors; it will not be presented as a purchasing recommendation or as a system validated for a
real store.

## MVP Scope

- Source: UCI Online Retail II, downloaded using a pinned URL and SHA-256.
- Granularity: SKU by calendar day.
- Cohort: products selected using only the first year of data.
- Horizon: 14 days.
- Required baseline: weekly seasonal naive.
- Learned model: a global model with lags and calendar features.
- Evaluation: rolling origin, with at least six windows.
- Metrics: WAPE, MASE, bias, MAE, and interval coverage.
- Output: forecasts, intervals, and errors by product, horizon, and date.
- Local demo: dashboard showing both successes and failures.

## Outside the MVP

- real-time streaming;
- Kubernetes or a paid cloud platform;
- multi-user authentication;
- inventory and stockout-cost optimization;
- LLMs, RAG, or generative explanations;
- forecasts for products without history;
- claims of real commercial impact.

## Milestones

### M0 — Contract and Baseline

Verifiable download, cleaning rules, leakage-free cohort, daily panel, temporal folds, and seasonal
naive. **Status: completed and verified locally.**

### M1 — Global Model

A direct global model for SKUs and horizons 1–14, with features available at forecast time and a
predeclared search over three configurations. Folds `0-13` select one candidate; folds `14-19`
confirm it once against seasonal naive using an objective gate. If it fails, the baseline remains
the champion, and the rejection is documented with no retuning. The final 84 days remain reserved
and excluded from M1 until intervals and monitoring are also frozen. The reservation is procedural
because outcomes remain physically present in the local panel.

**Status: completed and verified locally.** Conservative Poisson improved confirmation WAPE by
11.94%, but it was rejected because of positive bias (`0.1477`), bias deterioration, and wins on
only 10/20 SKUs. Seasonal naive remains the champion, and the final holdout was not opened during
M1. The M1 promotion decision is an explicit **no-go**, with **no retuning** after confirmation.

### M2 — Uncertainty and Monitoring

Prediction intervals with 90% nominal coverage, calibrated only on development errors, coverage by
horizon, historical replay, and data-quality and performance alerts. M2 starts from the seasonal
naive champion; it does not reuse confirmation to rescue the rejected candidate.

### M3 — Local Product

PostgreSQL persistence, idempotent batch job, query API, and dashboard.

**Status: completed.** The PostgreSQL implementation is retained as persistence evidence and is
validated in CI through migration, idempotent seeding, restart, and complete row counts.

### M4 — Publication

Docker Compose, CI, documentation, limited public demo on the VPS, case study, and portfolio
update.

**Status: prepared for release `v1.0.1`.** The public demo uses the reviewed, immutable snapshot,
without PostgreSQL or write routes, behind TLS and explicit limits. The canonical endpoint is
`https://retail.nightstrike.cloud` and must be verified after every deployment.

## Success Criterion

The learned model does not win on a single aggregate figure. To advance, it must outperform the
baseline on WAPE across most folds and eligible products, maintain interpretable bias, and publish
the cases in which it loses. Intervals must show observed coverage by horizon.
