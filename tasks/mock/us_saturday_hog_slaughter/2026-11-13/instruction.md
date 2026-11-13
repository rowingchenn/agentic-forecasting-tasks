Forecast how many hogs will be slaughtered under federal inspection in the United States
this Saturday. The Task runs on Fridays at midday and asks for the next day, Saturday;
forecasts are due at 13:50 Eastern Time (America/New_York) on the run date. You have about
90 minutes.

## The target

The target is the number of hogs, in head, slaughtered under federal inspection in the
United States on the target Saturday, as reported by the US Department of Agriculture's
Agricultural Marketing Service in report 3658, "Actual Slaughter Under Federal
Inspection": the Hogs figure in the row for that Saturday, in the Final issue for the week
(Sunday to Saturday) that ends on the target date. A dash in the row means zero. USDA
posts that issue on its My Market News site about 12 days after the Saturday, normally on
a Thursday at about 12:00 Eastern Time, and the forecast is settled on the first figure
posted. Every issue is listed at
https://mymarketnews.ams.usda.gov/filerepo/reports?field_slug_id_value=3658. Estimates
of daily slaughter, such as those in USDA's daily report 3208, and counts that include
plants not under federal inspection are different quantities.

## Inputs and environment

`/home/user/input/task.json` gives the run date, the target date (the run date plus 1
day), the submission cutoff (13:50 America/New_York on the run date), and the quantile
levels [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9].

This is an offline replay of the run date: the network is cut and web search is disabled. Base your forecast on the materials in `/home/user/input/archive/`, which agents gathered from the internet on the run date: one folder per agent, each with an `index.jsonl` giving every file's source URL and why it was saved. Apart from task.json and the archive, no data is provided. You may use any software, methods, or models available in this environment. Forecast as of the run date in task.json, whatever the system clock shows.

## Output

Write `/home/user/output/forecast.json` in this form:

```text
{
  "run_date": "<run_date from task.json>",
  "forecasts": [
    {"target_date": "YYYY-MM-DD",
     "quantiles": {"0.1": <head>, "0.2": <head>, "0.3": <head>, "0.4": <head>, "0.5": <head>,
                   "0.6": <head>, "0.7": <head>, "0.8": <head>, "0.9": <head>}}
  ]
}
```

- Include each date in `target_dates` exactly once.
- `quantiles` uses exactly the levels in `quantile_levels`, written as shown ("0.1"
  through "0.9"). Values are numbers of hogs, zero or more, and must not decrease as the
  level increases.

Each forecast is scored after the actual value is published, with the continuous
ranked probability score (CRPS) of its quantiles, so both the center and the spread
matter.

## Collected materials

A replay has no outside sources, so there is nothing to collect: write an empty `/home/user/output/collected/index.jsonl`.
