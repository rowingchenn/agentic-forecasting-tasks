Forecast the daily maximum of the planetary Kp index of geomagnetic activity, as NOAA
estimates it, for tomorrow and the day after. You have about 90 minutes.

## The target

For each target date, a calendar day in UTC, the target is the highest of the eight
three-hourly values of the estimated planetary K index (Kp) that NOAA's Space Weather
Prediction Center (SWPC) publishes for that day in its product
`https://services.swpc.noaa.gov/products/noaa-planetary-k-index.json`: the `Kp` field of the
rows whose `time_tag`, the start of a three-hour block in UTC, falls on the target date (00:00
through 21:00). Kp runs from 0 to 9 in thirds, which SWPC writes as decimals: 2- is 1.67, 2o
is 2.00 and 2+ is 2.33. A day counts once all eight values are posted. A forecast is settled
on the first published value: the eight values as SWPC's product shows them at about 10:35
UTC the day after the target date. A day not complete then is settled at the first later
daily reading, with any value the product never shows taken from SWPC's Daily Space Weather
Indices for that day. SWPC's later corrections are not used. GFZ Potsdam's official Kp
(nowcast or definitive), SWPC's one-minute Kp estimates, the Ap index, and local K indices
such as Boulder's measure different quantities.

## Inputs and environment

`/home/user/input/task.json` gives the run date, the target dates (the run date plus
[1, 2] days), the submission cutoff (12:20 UTC on the run date), and the
quantile levels [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9].

You have unrestricted internet access. Apart from task.json, no data is provided: find the information you need yourself, using any sources, tools, software, or models.

## Output

Write `/home/user/output/forecast.json` in this form:

```text
{
  "run_date": "<run_date from task.json>",
  "forecasts": [
    {"target_date": "YYYY-MM-DD",
     "quantiles": {"0.1": <Kp>, "0.2": <Kp>, "0.3": <Kp>, "0.4": <Kp>, "0.5": <Kp>,
                   "0.6": <Kp>, "0.7": <Kp>, "0.8": <Kp>, "0.9": <Kp>}},
    ...
  ]
}
```

- Include each date in `target_dates` exactly once.
- `quantiles` uses exactly the levels in `quantile_levels`, written as shown ("0.1"
  through "0.9"). Values are nonnegative numbers on the Kp scale and must not decrease as
  the level increases.

Each forecast is scored after SWPC publishes the day's eight values, with the continuous
ranked probability score (CRPS) of its quantiles, so both the center and the spread matter.

## Collected materials

Save copies of the external materials you found useful for this forecast, such as data you downloaded, articles, reports, or forecasts by others, as regular files (not symbolic links) under `/home/user/output/collected/`. Save only what you obtained from outside this environment, not your own code, analyses, or forecasts. List each saved file in `/home/user/output/collected/index.jsonl`, one JSON object per line: `{"path": "<path relative to collected/>", "source": "<URL you obtained it from>", "note": "<why it is useful>"}`. Write an empty `index.jsonl` if you saved nothing.
