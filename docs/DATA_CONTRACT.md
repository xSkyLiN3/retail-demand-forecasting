# Data contract

## Source

- Dataset: **Online Retail II**.
- Author: Daqing Chen.
- Repository: UCI Machine Learning Repository.
- DOI: <https://doi.org/10.24432/C5CG6D>.
- Data license: CC BY 4.0.
- Declared period: December 1, 2009 to December 9, 2011.
- Expected file inside the ZIP: `online_retail_II.xlsx`.

The ZIP and workbook are not versioned in Git. The downloader verifies the file's SHA-256, and
`prepare` verifies the workbook's SHA-256 again before processing it.

## Normalized schema

| Field | Logical type | Use |
|---|---|---|
| `invoice_no` | non-empty text | detect cancellations; not a feature |
| `stock_code` | non-empty text | series identifier |
| `description` | optional text | audit; not a feature |
| `quantity` | integer | construct demand and account for returns |
| `invoice_date` | timestamp | sort and aggregate by day |
| `unit_price` | non-negative decimal | quality filter; not a feature of the initial forecast |
| `customer_id` | optional text | discarded; not used |
| `country` | non-empty text | audit; not used in the MVP |

The loader supports the workbook's historical column names (`Invoice`, `Price`, `Customer ID`) and
normalizes them. Any missing required column stops the pipeline.

## Target definition

`units` is **observed positive gross sales**, used as a proxy for demand. It is the daily sum of
`quantity` for rows that simultaneously meet these conditions:

- the invoice does not begin with `C` (cancellation);
- `quantity > 0`;
- `unit_price > 0`;
- the product code matches `^[0-9]{5}[A-Z]{0,2}$`.

The suffix of up to two letters was established after auditing real variants such as `15056BL`,
`79323LP`, and `79323GR`. The remaining non-standard codes are kept outside the target and reported
by volume to detect false positives or false negatives from this heuristic.

The dataset cannot measure latent demand, availability, stockouts, or fulfilment. Returns are
accounted for separately and are not backdated to the day of sale, because doing so would use future
information.

UCI describes the source as all transactions in the period. Under that premise, days with no
eligible rows are completed with zero for each SKU. The panel adds `source_observed_day`: `true` if
there was at least one transaction of any type on that date, and `false` if the complete ledger has
no rows. This indicator makes the uncertainty between zero sales, closure, and possible missing
coverage visible; it is not used as a future feature.

Cancellations, returns, and administrative codes are excluded from the target, but their counts and
units are recorded in the quality report.

## Duplicates

- The two official worksheets overlap between December 1 and 9, 2010. For exact values repeated
  across worksheets, a multiset union is applied: the maximum multiplicity from one worksheet is
  retained, and the first worksheet in workbook order is preferred. This avoids duplicating the
  period without collapsing two identical lines that already coexisted within the same worksheet.
- Exact repetitions within a worksheet are counted and retained. Without a line identifier, it is
  not possible to assert that they are erroneous duplicates rather than two legitimate lines.
- The report publishes affected and removed rows, time range, groups, multiplicities, and any
  inequality between worksheets before the dataset is frozen.

## Leakage-free cohort

The cohort is selected within the first 365 observed days:

1. filter products with at least 60 active days;
2. require positive activity within the 56 days before the cutoff;
3. rank by total positive units;
4. retain up to 20 products;
5. freeze that list and the panel hash before creating evaluation folds.

Products may not be selected using activity, sales, or errors from the future period.

## Invariants

- valid, sortable timestamps;
- finite, integral quantities;
- finite prices;
- one row per `date, sku` after aggregation;
- complete panel with no missing values;
- `units >= 0`;
- days with no global transactions identified, rather than silently confused with confirmed
  coverage;
- non-empty cohort determined before the first evaluation cutoff;
- panel hash and SKU set equal to those in the cohort manifest;
- no forecast date may be earlier than or equal to its cutoff.
