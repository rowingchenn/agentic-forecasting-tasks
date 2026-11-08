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
a Thursday, and the forecast is settled on the first figure posted. Estimates of daily
slaughter, such as those in USDA's daily report 3208, and counts that include plants not
under federal inspection are different quantities.

## Inputs and environment

`/home/user/input/task.json` gives the run date, the target date (the run date plus 1
day), the submission cutoff (13:50 America/New_York on the run date), and the quantile
levels [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9].

You have unrestricted internet access. Apart from task.json, no data is provided: find the information you need yourself, using any sources, tools, software, or models.

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
ranked probability score (CRPS) of its quantiles, so both the center and spread matter.

## Collected materials

Save copies of the external materials you found useful for this forecast, such as data you downloaded, articles, reports, or forecasts by others, as regular files (not symbolic links) under `/home/user/output/collected/`. Save only what you obtained from outside this environment, not your own code, analyses, or forecasts. List each saved file in `/home/user/output/collected/index.jsonl`, one JSON object per line: `{"path": "<path relative to collected/>", "source": "<URL you obtained it from>", "note": "<why it is useful>"}`. Write an empty `index.jsonl` if you saved nothing.
