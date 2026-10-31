Forecast Apple Inc. (AAPL) closing prices for the dates in task.json. The Task runs
weekly on Mondays and asks for Tuesday through Friday and the following Monday.
You have about 90 minutes.

## The target

For each target date in ${timezone}, the target is Yahoo Finance's AAPL daily chart
`indicators.quote[0].close`, in USD per share: the regular-session close on or before
that date. If the exchange is closed, use the previous session's close. This is not
`adjclose`, an intraday quote or an extended-hours trade. Yahoo can retrospectively
revise historical prices for splits; the series is not permanently split-unadjusted.

Settlement uses the first completed-calendar-day value observed and retained by the
pipeline. This is not necessarily the first value published by the exchange. Subsequent
vendor revisions do not overwrite already settled values.

## Inputs and environment

`/home/user/input/task.json` gives the run date, the target dates (the run date plus
${horizons} days), the submission cutoff (${cutoff} ${timezone} on the run date), and the
quantile levels ${quantile_levels}.

${environment_note}

${history_note}

## Output

Write `/home/user/output/forecast.json` in this form:

```text
{
  "run_date": "<run_date from task.json>",
  "forecasts": [
    {"target_date": "YYYY-MM-DD",
     "quantiles": {"0.1": <USD>, "0.2": <USD>, "0.3": <USD>, "0.4": <USD>, "0.5": <USD>,
                   "0.6": <USD>, "0.7": <USD>, "0.8": <USD>, "0.9": <USD>}},
    ...
  ]
}
```

- Include each date in `target_dates` exactly once.
- `quantiles` uses exactly the levels in `quantile_levels`, written as shown ("0.1"
  through "0.9"). Values are positive numbers in USD per share and must not decrease
  as the level increases.

Each forecast is scored after the actual value is published, with the continuous
ranked probability score (CRPS) of its quantiles, so both the center and spread matter.

## Collected materials

${materials_note}
