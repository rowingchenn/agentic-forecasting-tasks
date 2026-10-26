Forecast Apple Inc. (AAPL) daily closing prices in USD per share for the next seven
calendar days. You have about 90 minutes.

## Target and settlement

The target is Yahoo Finance's AAPL daily chart `indicators.quote[0].close`, not
`adjclose`, an intraday quote, or an extended-hours trade. Use the close of the last
regular trading session ending on or before each target date in ${timezone}. On
weekends and exchange holidays, carry forward the prior session's close. Do not assume
that every weekday is a trading day. Yahoo can retrospectively revise historical prices
for splits; do not describe this series as permanently split-unadjusted.

Settlement uses the first completed-calendar-day value observed and retained by the
pipeline. This is not necessarily the first value published by the exchange. Subsequent
vendor revisions do not overwrite already settled values.

## Inputs and timing

`/home/user/input/task.json` supplies the run date, target dates (the run date plus
${horizons} calendar days), submission cutoff (${cutoff} ${timezone} on the run date),
and quantile levels ${quantile_levels}. Submit before that cutoff. Use only information
available before your submission and never after the cutoff. Daily OHLCV history must
exclude the current, incomplete session. Finnhub company news is a suggested contextual
source; summaries are not guaranteed full article bodies. No private API key is supplied
inside the episode. In live runs, you may use any publicly accessible news website,
company investor-relations page, regulatory filing, or other relevant source; you are
not restricted to Finnhub. Finnhub access is optional for the agent. Follow the replay
environment restrictions below when running offline.

${environment_note}

## Output

Write `/home/user/output/forecast.json`:

```text
{
  "run_date": "<run_date from task.json>",
  "forecasts": [
    {"target_date": "YYYY-MM-DD",
     "quantiles": {"0.1": <USD>, "0.2": <USD>, "0.3": <USD>,
                   "0.4": <USD>, "0.5": <USD>, "0.6": <USD>,
                   "0.7": <USD>, "0.8": <USD>, "0.9": <USD>}}
  ]
}
```

Include every target date exactly once and exactly the quantile keys requested.
Values must be finite, positive numbers in USD per share, nondecreasing as the quantile
level increases. Accuracy is scored later using quantile CRPS, median absolute error,
and 80% interval coverage. The episode verifier checks format, not future accuracy.

## Collected materials

${materials_note}
