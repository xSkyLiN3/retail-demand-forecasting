# M0 data audit

**Outcome:** `PASS` to build and evaluate the development baseline. This verdict does not validate
a learned model or open the final time window.

## Provenance and integrity

| Evidence | Observed value |
|---|---|
| Dataset | UCI Online Retail II |
| DOI | <https://doi.org/10.24432/C5CG6D> |
| License | CC BY 4.0 |
| ZIP | 45,622,418 bytes |
| ZIP SHA-256 | `572e36277c2390fbfde10664750731e0a86f55e33470d91919085f0408e67bfb` |
| Workbook | 45,622,278 bytes |
| Workbook SHA-256 | `bcbe73b35f5b7babf197fb0cb983a11f5d9ff929078d4aa53d171b1f2df2e980` |
| Verification | SHA-256, ZIP CRC, and workbook inspection |
| Audit date | August 26, 2026 |

The workbook contains two worksheets:

| Worksheet | Physical rows | Start | End |
|---|---:|---|---|
| `Year 2009-2010` | 525,461 | 2009-12-01 07:45 | 2010-12-09 20:01 |
| `Year 2010-2011` | 541,910 | 2010-12-01 08:26 | 2011-12-09 12:50 |

The 1,067,371 physical rows include an exact overlap from December 1 through December 9, 2010:

- 45,046 affected rows;
- 22,202 exact-value groups;
- 22,523 occurrences repeated across both worksheets;
- zero groups with unequal multiplicity between the official worksheets;
- 22,523 copies removed through a multiset union;
- 1,044,848 resulting logical rows.

The union retains repetitions within a worksheet. An additional 11,812 exact repetitions beyond
the first remain; without a line identifier, there is insufficient evidence to remove them.

## Target contract and funnel

All required validations passed, with no invalid dates, quantities, prices, or required identifiers.
There are 235,287 missing `customer_id` values and 4,275 missing descriptions; both fields are
optional and are not used by the model.

The target is `gross_positive_invoiced_units`. It requires a non-cancelled invoice, positive
quantity and price, and a standard code matching `^[0-9]{5}[A-Z]{0,2}$`.

| Rule or stage | Rows | Associated positive units |
|---|---:|---:|
| Logical input rows | 1,044,848 | — |
| Invoices with a cancellation prefix | 19,165 | reported outside the target |
| Non-positive quantity | 22,557 | 1,048,278 returned units in absolute value |
| Non-positive price | 6,029 | 250,775 |
| Non-standard code | 5,992 | 25,002 |
| Joint exclusion | 29,903 | do not sum categories: intersections exist |
| Eligible rows | 1,014,945 | 11,221,670 |

The initial audit found that a single-letter suffix excluded genuine variants such as `15056BL`,
`79323LP`, and `79323GR`. The pattern was expanded and is covered by a test. The remaining
high-volume non-standard codes include `POST`, `M`, `DOT`, `C2`, and `D`, which are consistent with
administrative charges or adjustments. Low-volume codes such as `DCGSSGIRL` or `PADS` could
represent special items; they remain outside this standard cohort and are declared as a limitation
of the heuristic.

## Time coverage and calendar

- Full range: 2009-12-01 to 2011-12-09, 739 calendar days.
- Days with at least one transaction in the ledger: 604.
- Days without any transaction: 135.
- Of the 105 Saturdays, 104 have no transactions; `source_observed_day` distinguishes this absence
  from an observed zero-sales day for a SKU.
- The panel does not use `source_observed_day` as a future feature.

## Frozen cohort

Selection uses only the first 365 days and ends before December 1, 2010. It requires 60 active days
and activity within the 56 days before the cutoff. The resulting 20 SKUs, ordered by training units,
are:

`21212`, `85123A`, `84077`, `85099B`, `17003`, `84879`, `84991`, `22197`, `21977`,
`21232`, `21213`, `21982`, `21980`, `84568`, `84755`, `84270`, `84347`, `21984`,
`84992`, `20725`.

All meet the recency requirement: their last observed training activity falls between November 28
and November 30, 2010. The manifest preserves active days, training units, selection rules, the
target contract, and hashes.

## Derived panel

| Property | Result |
|---|---:|
| Dimensions | 739 days × 20 SKUs = 14,780 rows |
| Rows with a zero target | 5,492 (37.16%) |
| Target units across the full panel | 1,120,119 |
| Duplicate `date, sku` rows | 0 |
| Null target values | 0 |
| Negative or fractional targets | 0 |
| Panel SHA-256 | `6d39886a45da1da3a25e31c897aacaaa9017a0b88080250c443fc08daa02cf0d` |

The final 84 days, from 2011-09-17 to 2011-12-09, are kept as the reserved final window. The panel
contains this period, but the M0 development commands do not generate predictions or metrics for it.

## Privacy and publication

The ZIP, workbook, panel, and prediction table are not versioned. The curated evidence contains only
aggregate statistics, dates, product SKUs, and hashes; it does not publish invoices or customer
identifiers. Reproducible artifacts are available in
[`reports/m0/evidence`](../reports/m0/evidence/).

## Limitations

- Positive invoiced sales are a proxy: stockouts, inventory, and latent demand are unavailable.
- Closure or absence of activity cannot always be distinguished from missing coverage.
- Standard-code classification is an explicit heuristic, not an official taxonomy.
- The holdout is reserved by procedure, not blinded by a third party.
