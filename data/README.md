# Data directory

Raw and processed datasets are intentionally excluded from Git.

- `raw/`: checksum-verified UCI archive and extracted workbook.
- `processed/`: daily demand panel, frozen cohort and quality summary.

Run `retail-forecast download` and `retail-forecast prepare` after installing the project. Data
license and transformation decisions are documented in `docs/DATA_CONTRACT.md`.
