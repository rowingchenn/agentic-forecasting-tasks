Forecast the daily maximum of the planetary Kp index of geomagnetic activity, as NOAA
estimates it, for tomorrow and the day after (UTC days). The Task runs daily, and forecasts
are due at 12:20 UTC on the run date. You have about 90 minutes.

## The target

For each target date, a calendar day in UTC, the target is the highest of the eight
three-hourly values of the estimated planetary K index (Kp) that NOAA's Space Weather
Prediction Center (SWPC) publishes for that day in its product
`https://services.swpc.noaa.gov/products/noaa-planetary-k-index.json`: the `Kp` field of the
rows whose `time_tag`, the start of a three-hour block in UTC, falls on the target date (00:00
through 21:00). Kp runs from 0 to 9 in thirds, which SWPC writes as decimals: 2- is 1.67, 2o
is 2.00 and 2+ is 2.33. A day counts once all eight values are posted. A forecast is settled
on the first published value: the eight values as SWPC's product shows them at about 10:15
UTC the day after the target date. A day not complete then is settled at the first later
daily reading, with any value the product never shows taken from SWPC's Daily Space Weather
Indices for that day. SWPC's later corrections are not used. GFZ Potsdam's official Kp
(nowcast or definitive), SWPC's one-minute Kp estimates, the Ap index, and local K indices
such as Boulder's measure different quantities.

## Inputs and environment

`/home/user/input/task.json` gives the run date, the target dates (the run date plus
[1, 2] days), the submission cutoff (12:20 UTC on the run date), and the
quantile levels [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9].

This is an offline replay of the run date: the network is cut and web search is disabled. Base your forecast on the materials in `/home/user/input/archive/`, which agents gathered from the internet on the run date: one folder per agent, each with an `index.jsonl` giving every file's source URL and why it was saved. Apart from task.json and the archive, no data is provided. You may use any software, methods, or models available in this environment. Forecast as of the run date in task.json, whatever the system clock shows.

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

A replay has no outside sources, so there is nothing to collect: write an empty `/home/user/output/collected/index.jsonl`.
