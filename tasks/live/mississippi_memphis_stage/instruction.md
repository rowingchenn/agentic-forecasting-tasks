Forecast the daily mean stage of the Mississippi River at Memphis for the next 13 days.
You have about 90 minutes.

## The target

For each calendar day (midnight to midnight, ${timezone}), the target is the mean, in feet,
of the hourly stage readings the National Weather Service publishes for its gauge MEMT1,
*Mississippi River at Memphis, TN*: the observed values its gauge page and data service
show. A day's value is the mean of all its readings, and a day counts once it has readings
in at least 12 of its hours. A forecast is settled on the value first published for that
day, computed from the readings as posted by the next day. Stage is the height of the
water surface above the gauge's reference point, not the depth of the river, and it can be
negative. Discharge (flow), water-surface elevation and once-a-day readings, such as a
7 a.m. stage, measure different quantities.

## Inputs and environment

`/home/user/input/task.json` gives the run date, the target dates (the run date plus
${horizons} days), the submission cutoff (${cutoff} ${timezone} on the run date), and the
quantile levels ${quantile_levels}.

${environment_note}

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

${materials_note}
