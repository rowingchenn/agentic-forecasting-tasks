Forecast the daily peak electricity demand of the ERCOT grid (Texas) for the next seven
days. You have about 90 minutes.

## The target

For each operating day (midnight to midnight, ${timezone}), the target is the largest
hourly value of ERCOT's total system load, in MW: the maximum over the day's hours of the
`total` column in ERCOT report NP6-345-CD, *Actual System Load by Weather Zone*, whose
values are averages over each hour ending. A forecast is settled on the value ERCOT first
publishes for that day. Instantaneous or 5-minute values, such as those on ERCOT's
real-time dashboards, measure a different quantity.

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

${materials_note}
