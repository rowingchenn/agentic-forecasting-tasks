Forecast how much corn USDA will first report as inspected for export from the United States
in the week that starts the day after the run date. You have about 90 minutes.

## The target

The target date is a Thursday, the last day of a week that runs from the Friday before it
through that Thursday. The target is the corn inspected and/or weighed for export under the
US Grain Standards Act in that week, in metric tons, over all port areas and destinations, as
USDA first reports it: the CORN figure in the column for the week in USDA's weekly report
WA_GR101, *Grains Inspected and/or Weighed for Export*
(https://www.ams.usda.gov/mnreports/wa_gr101.txt). The same number is the sum of `mt` over
the rows with `grain` equal to `CORN` and `date` (Week Ending Date) equal to the target date
in the Grain Inspections dataset on USDA's AgTransport,
https://agtransport.usda.gov/Exports/Grain-Inspections/sruw-w49i.

USDA publishes the report on the Monday after the week ends, or on the Tuesday when Monday is
a federal holiday, and adds the week to the dataset the same day. A forecast is settled on
that first figure. The next Monday's report revises it: upward in all 40 weeks checked from
2024 to 2026, by 2% at the median and up to 19%. The revised figure, which the dataset and
later reports show for every earlier week, is not used. Export sales, Census export
statistics, bushels and marketing-year totals measure different quantities.

At the cutoff, the latest published week is normally the one that ended seven days before
the run date; the week ending on the run date is not yet published, and the target week
starts the day after the run date.

## Inputs and environment

Runs are on Thursdays. `/home/user/input/task.json` gives the run date, the target date (the
run date plus [7] days: next Thursday), the submission cutoff (15:50 America/New_York on the
run date), and the quantile levels [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9].

This is an offline replay of the run date: the network is cut and web search is disabled. Base your forecast on the materials in `/home/user/input/archive/`, which agents gathered from the internet on the run date: one folder per agent, each with an `index.jsonl` giving every file's source URL and why it was saved. Apart from task.json and the archive, no data is provided. You may use any software, methods, or models available in this environment. Forecast as of the run date in task.json, whatever the system clock shows.

## Output

Write `/home/user/output/forecast.json` in this form:

```text
{
  "run_date": "<run_date from task.json>",
  "forecasts": [
    {"target_date": "YYYY-MM-DD",
     "quantiles": {"0.1": <t>, "0.2": <t>, "0.3": <t>, "0.4": <t>, "0.5": <t>,
                   "0.6": <t>, "0.7": <t>, "0.8": <t>, "0.9": <t>}}
  ]
}
```

- Include each date in `target_dates` exactly once.
- `quantiles` uses exactly the levels in `quantile_levels`, written as shown ("0.1"
  through "0.9"). Values are non-negative numbers of metric tons in the week ending on the
  target date, and must not decrease as the level increases.

Each forecast is scored after USDA publishes the week's first figure, with the continuous
ranked probability score (CRPS) of its quantiles, so both the center and the spread matter.

## Collected materials

A replay has no outside sources, so there is nothing to collect: write an empty `/home/user/output/collected/index.jsonl`.
