Forecast tomorrow's peak electricity demand in the Tennessee Valley Authority (TVA)
balancing authority. You have about 90 minutes.

## The target

For each day (hour ending 01:00 to hour ending 24:00, ${timezone}), the target is the
largest hourly demand, in MW, that TVA reports to the U.S. Energy Information
Administration on Form EIA-930, as shown in EIA's Hourly Electric Grid Monitor. Only the
values TVA reports count, not the ones EIA imputes where a report is missing. An hour that
is missing, zero or negative, or more than 30% above the hours on both sides of it (alone
or as a pair) is left out, and a day with more than one such hour has no value. A forecast
is settled on the value published two days after the day: balancing authorities revise
the previous day's hours by 7:00 ET the next morning, and a few more within two days.
EIA-930's values are hourly averages; instantaneous values, such as some record peaks in
the news, measure a different quantity.

## Inputs and environment

`/home/user/input/task.json` gives the run date, the target date (the run date plus
${horizons} day, that is, the next day), the submission cutoff (${cutoff} ${timezone} on
the run date), and the quantile levels ${quantile_levels}.

${environment_note}

## Output

Write `/home/user/output/forecast.json` in this form:

```text
{
  "run_date": "<run_date from task.json>",
  "forecasts": [
    {"target_date": "YYYY-MM-DD",
     "quantiles": {"0.1": <MW>, "0.2": <MW>, "0.3": <MW>, "0.4": <MW>, "0.5": <MW>,
                   "0.6": <MW>, "0.7": <MW>, "0.8": <MW>, "0.9": <MW>}}
  ]
}
```

- Include each date in `target_dates` exactly once.
- `quantiles` uses exactly the levels in `quantile_levels`, written as shown ("0.1"
  through "0.9"). Values are positive numbers in MW and must not decrease as the level
  increases.

Each forecast is scored after EIA publishes the actual value, with the continuous ranked
probability score (CRPS) of its quantiles, so both the center and the spread matter.

## Collected materials

${materials_note}
