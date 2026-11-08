# Data definition and operational notes

USDA AMS report 2756: https://mymarketnews.ams.usda.gov/viewReport/2756 .
Current PDF: https://www.ams.usda.gov/mnreports/AMS_2756.pdf .
Use these rather than the stale pywretailchicken.pdf endpoint.

The national price includes Alaska and Hawaii. Prices are advertised promotions,
weighted by advertising store count, not consumer expenditure or sales volumes.
Do not sum prices, average regions equally, or mix regular and value packs.

## History verification (8 November 2026)

The proposed history has 306 consecutive Friday labels, 2021-01-01 to 2026-11-06.
305 original PDFs were checked for report dates. All 301 overlapping structured-data
prices and store counts agree exactly with the PDFs. All 302 structured National
counts reconcile to their regional totals; reconstructed weighted prices agree
within a cent. Exact duplicate source records were removed, never conflicting ones.

The checked 2021-2023 bootstrap is included as host-only tests/historical.csv,
with verification provenance and original PDF URLs. Four API gaps were recovered
from PDFs: 2022-04-01, 2022-09-16, 2022-12-02 and 2023-01-13. The missing archive
issue for 2023-08-11 was checked against the following report's previous-week
column. The January 2021 first label uses a report ending 2020-12-31. These are
downloaded historical vintages, not reconstructed first publications.

The host fetches 2024 onward dynamically. The endpoint silently caps results at
1,500 rows; every batch must reconcile returnedRows, totalRows and actual length.
Truncated date intervals are recursively split. The exact legacy product label
through August 2024 maps to the separate product/Value package fields from
September 2024. Conflicts, invalid units/counts, or incomplete snapshots stop the
run. There is no guessed live PDF parser or automatic substitution for missing
live rows; a persistent gap needs maintainer review and a verified correction.

## Timing and settlement

Run Fridays 14:00 Eastern, cutoff 15:50, target the next Friday (+7 days), normally
the Saturday-Friday period beginning after cutoff. Fifty of the 53 archive issues
since November 2025 uploaded Friday, two Thursday and one Tuesday; latest observed
upload clock time was 16:48:56 UTC (12:48 Eastern that day). The current week's
National observation must actually exist before the run starts, including holiday
weeks. The Monday header report_date is not the reporting-week label.

The pipeline truth store retains the first captured observation. Later revisions
cannot replace it. Capture promptly after release; archive upload metadata alone
are not proof of original publication timestamps. No retroactive snapshot is
claimed by these historical checks. Maintainer sets first_run when enabling the timer.

## Four-week reference and expert baseline

The simple reference is the arithmetic mean of the last four consecutive observed
weekly prices, implemented by tests/fetch.py:reference_forecast. In a retrospective
comparison on 149 common weeks from January 2024, its MAE was $0.1775/lb, versus
$0.2250 for last week and $0.3511 for the 52-week lag. This used downloaded vintages,
not historical as-of snapshots, and does not establish live performance.

There is no verified matching public weekly expert forecast. Per repository rules,
no metadata baseline table is declared and baseline() returns no expert forecasts.
The helper is available for reference evaluation; the generic pipeline does not
automatically call target-specific reference helpers. Wiring it into a scored
reference system would require a separate pipeline change. The live solution
script is only a format oracle and does not claim forecast accuracy.

USDA's general reuse policy: https://www.usda.gov/policies-and-links . Most USDA
information is public domain, subject to third-party exceptions; attribute USDA.
This task uses USDA numerical aggregates, not retailer advertising artwork.
