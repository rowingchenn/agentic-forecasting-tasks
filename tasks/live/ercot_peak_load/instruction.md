Forecast the daily peak electricity demand of the ERCOT grid (Texas) for the next seven
days. You have about 90 minutes.

## The target

For each operating day (midnight to midnight, America/Chicago), the target is the largest
hourly value of ERCOT's total system load, in MW: the maximum over the day's hours of the
`total` column in ERCOT report NP6-345-CD, *Actual System Load by Weather Zone*, whose
values are averages over each hour ending. A forecast is settled on the value ERCOT first
publishes for that day. Instantaneous or 5-minute values, such as those on ERCOT's
real-time dashboards, measure a different quantity.

## Inputs and environment

`/home/user/input/task.json` gives the run date, the target dates (the run date plus
[1, 2, 3, 4, 5, 6, 7] days), the submission cutoff (09:25 America/Chicago on the run date), and the
quantile levels [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9].

You have unrestricted internet access. Apart from task.json, no data is provided: find the information you need yourself, using any sources, tools, software, or models.

You may have forecast this target on earlier run days in this same environment and session; if so, your earlier submissions are under `/home/user/history/<run date>/`. Compare them with the values published since, and use what you learn to improve how you forecast.

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

Save copies of the external materials you found useful for this forecast, such as data you downloaded, articles, reports, or forecasts by others, as regular files (not symbolic links) under `/home/user/output/collected/`. Save only what you obtained from outside this environment, not your own code, analyses, or forecasts. List each saved file in `/home/user/output/collected/index.jsonl`, one JSON object per line: `{"path": "<path relative to collected/>", "source": "<URL you obtained it from>", "note": "<why it is useful>"}`. Write an empty `index.jsonl` if you saved nothing.
