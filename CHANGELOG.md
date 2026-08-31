# Changelog

All notable changes to this project are documented in this file.

## [Unreleased]

### Added

- Hardened, read-only public demo manifest and VPS release procedure.
- Immutable in-memory JSON repository with startup SHA-256 verification.
- Explicit no-go dashboard, coherent run/SKU explorer and covered/missed ledger.
- First-party CSS and JavaScript compatible with a strict Content Security Policy.
- Public smoke tests and container-isolation checks in CI.

### Changed

- Public API input bounds and a reduced maximum query limit of 2,000 rows.
- Public health output now hides storage implementation details.
- Runtime image is multi-stage and installs no PostgreSQL client for the public build.

The model, frozen holdout, forecast rows and monitoring outcomes were not changed or reopened.

[Unreleased]: https://github.com/xSkyLiN3/retail-demand-forecasting/compare/v1.0.0...HEAD
