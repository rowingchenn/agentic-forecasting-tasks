# US weekly corn inspected/weighed for export

Source webpage: https://agtransport.usda.gov/Exports/Grain-Inspections/sruw-w49i
Primary annual CSVs: https://fgisonline.ams.usda.gov/ExportGrainReport/default.aspx
Workbook webpage: https://www.ams.usda.gov/services/transportation-analysis/gtr-datasets
Current weekly text report: https://www.ams.usda.gov/mnreports/wa_gr101.txt

## Definition and verification

USDA FGIS grain CORN, national sum of MT, grouped by the source Week Ending Date.
Each Thursday labels Friday through Thursday. No country or port exclusions; no
mixing wheat/soybeans, pounds/bushels, marketing-year cumulative totals, sales
contracts, customs export dollar values or corn futures prices.

On 8 November 2026, checked 148 consecutive weeks, 2024-01-04 through 2026-10-29,
with 20,937 source corn records, no null or negative amounts and no missing or
duplicate weekly labels. All 148 API totals match the workbook national totals,
the workbook detailed rows and the primary FGIS annual CSV sums exactly. Latest
884,766 metric tons also matches the current WA_GR101 text report exactly.
These are reconciliations of currently downloadable vintages, not a reconstruction
of original weekly publications.

The first week includes 2023-12-29 through 2024-01-04. Reading only the 2024 annual
CSV would omit 513,842 tons from December: 578,520 instead of 1,092,362. The host
therefore groups the full structured dataset by its existing Thursday label,
without filtering certification year or reassigning weeks at New Year.

Public JSON requests need no key and use only stdlib and pandas. The weekly
aggregate is checked against an independent source record/distinct-week count.
Counts reconcile to prevent silent response truncation. Missing MT, negative or
fractional amounts, duplicate/non-Thursday labels, unit/schema changes and dataset
updates during acquisition all fail closed. No source record deduplication or
interpolation is performed. Source counts stay in raw data, not future covariates.

## Timing, lag and settlement

Example: report dated Monday 2026-11-02 covers the week ending Thursday 2026-10-29.
The GTR PDF dated Thursday 2026-11-05 still labels its table October 29. Use the
observation label, not the report issue date.

Runs start Thursdays 14:00 Eastern and cut off 15:50 (110 minutes later), forecasting
the following Thursday, a wholly future Friday–Thursday period. The latest expected
published observation is the previous Thursday, run date minus seven days. Both
the run-date week (still unpublished) and next week's target stay empty in X_d.
A missing expected latest observation or interior published week stops the run.
This guards holiday/publication delays without assuming a specific Monday clock
time. Maintainer sets first_run to a Thursday when enabling the timer.

Truth is collected no earlier than the Monday after the target Thursday. The
pipeline retains the first captured nonmissing value; later revisions cannot
replace it. Older annual files do change: CY2024.csv was updated June 2026, and
the source text explicitly lists past corrections. Snapshot history is the vintage
available at acquisition, not the original historical release. Preserve first-seen
truth and source evidence prospectively; the current API cannot reconstruct an
arbitrary past as-of snapshot. Missing weeks remain unsettled, never zero.

## Four-observation reference

`reference_forecast()` computes the arithmetic mean of the latest four consecutive
observed weeks from a snapshot's target column. Because of the release lag, the
latest observation is two weekly labels before the forecast target. Do not use
the as-yet-unpublished intervening week. On 96 common retrospective target weeks
from 2025, MAE was 197,151 metric tons, versus 248,298 for persistence and 370,977
for a 52-week lag. This uses downloaded vintages and is not a vintage-correct
backtest or proof of live performance.

No matching public expert weekly forecast was verified. Following repository
rules, the expert baseline table is absent and baseline() returns no forecasts.
The generic pipeline does not automatically call a target-specific simple-reference
helper; scoring this reference requires separate pipeline wiring. The solution
script is a format oracle, not an accuracy claim.

## Reuse and scope

The metadata has no dedicated license identifier. USDA's general policy says most
USDA information is public domain, subject to third-party exceptions; attribute
USDA FGIS / AMS: https://www.usda.gov/policies-and-links . This task uses USDA
numerical aggregates. CBOT corn futures PR #17 measures a price; hog slaughter
PR #18 measures another kind of production. Neither duplicates this trade volume.
