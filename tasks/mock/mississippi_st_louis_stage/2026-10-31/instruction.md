Forecast the daily mean stage of the Mississippi River at St. Louis for the next 13 days.
You have about 90 minutes.

## The target

For each calendar day (midnight to midnight, America/Chicago), the target is the mean, in feet,
of the gauge height readings at *Mississippi River at St. Louis, MO*: USGS site 07010000,
parameter 00065, which the National Weather Service lists as gauge EADM7 and shows with the
same readings. Readings come every 30 minutes; a day's value is the mean of all its
readings, and a day counts once it has readings in at least 12 of its hours. A forecast is
settled on the value first published for that day, computed from the readings as posted
by the next day. Stage is the height of the water surface above the gauge's reference
point, not the depth of the river, and it can be negative. Discharge (flow), water-surface
elevation and once-a-day readings, such as a 7 a.m. stage, measure different quantities.

## Inputs and environment

`/home/user/input/task.json` gives the run date, the target dates (the run date plus
[1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13] days), the submission cutoff (18:40 America/Chicago on the run date), and the
quantile levels [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9].

This is an offline replay of the run date: the network is cut and web search is disabled. Base your forecast on the materials in `/home/user/input/archive/`, which agents gathered from the internet on the run date: one folder per agent, each with an `index.jsonl` giving every file's source URL and why it was saved. Apart from task.json and the archive, no data is provided. You may use any software, methods, or models available in this environment. Forecast as of the run date in task.json, whatever the system clock shows.

## Output

Write `/home/user/output/forecast.json` in this form:

```text
{
  "run_date": "<run_date from task.json>",
  "forecasts": [
    {"target_date": "YYYY-MM-DD",
     "quantiles": {"0.1": <ft>, "0.2": <ft>, "0.3": <ft>, "0.4": <ft>, "0.5": <ft>,
                   "0.6": <ft>, "0.7": <ft>, "0.8": <ft>, "0.9": <ft>}},
    ...
  ]
}
```

- Include each date in `target_dates` exactly once.
- `quantiles` uses exactly the levels in `quantile_levels`, written as shown ("0.1"
  through "0.9"). Values are numbers in feet, negative ones included, and must not
  decrease as the level increases.

Each forecast is scored after the readings are published, with the continuous ranked
probability score (CRPS) of its quantiles, so both the center and the spread matter.

## Collected materials

A replay has no outside sources, so there is nothing to collect: write an empty `/home/user/output/collected/index.jsonl`.
