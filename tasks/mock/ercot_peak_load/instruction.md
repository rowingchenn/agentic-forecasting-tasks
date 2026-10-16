Forecast the daily peak electricity demand of the ERCOT grid (Texas) for the next seven
days. ${environment_note} You have about 90 minutes.

## Inputs

`/home/user/input/x/` (read-only) holds the structured data for this run:

- `task.json`: the run date, the seven target dates, the required quantile levels, the
  exact target definition, and descriptions of every column.
- `series.csv`: one row per day. `target` is the observed daily peak load in MW and is
  empty after `last_observed`. The other columns are covariates whose values are known
  for the forecast window: daily maximum and minimum temperature forecasts for one city
  in each ERCOT weather zone, day of week, and US federal holidays.
- `raw/`: the hourly load by weather zone and the hourly temperature forecast that
  `series.csv` was derived from.

## Output

Write `/home/user/output/forecast.json` in this form:

```text
{
  "run_date": "<run_date from task.json>",
  "forecasts": [
    {"target_date": "YYYY-MM-DD",
     "quantiles": {"0.1": <MW>, "0.2": <MW>, "0.3": <MW>, "0.4": <MW>, "0.5": <MW>,
                   "0.6": <MW>, "0.7": <MW>, "0.8": <MW>, "0.9": <MW>}},
    ...
  ]
}
```

- Include each date in `target_dates` exactly once.
- `quantiles` uses exactly the levels in `quantile_levels`, written as shown ("0.1"
  through "0.9"). Values are positive numbers in MW and must not decrease as the level
  increases.

Each forecast is scored after ERCOT publishes the actual value, with the continuous
ranked probability score (CRPS) of its quantiles, so both the center and the spread
matter.

## Collected materials

Save copies of the external materials you found useful for this forecast, such as
articles, reports, forecasts by others, or data you downloaded, as regular files (not
symbolic links) under `/home/user/output/collected/`. Save only what you obtained from
outside this environment: not the provided input files, and not your own code, analyses,
or forecasts. List each saved file in `/home/user/output/collected/index.jsonl`, one JSON
object per line:

```json
{"path": "<path relative to collected/>", "source": "<URL you obtained it from>", "note": "<why it is useful>"}
```

Write an empty `index.jsonl` if you saved nothing.
