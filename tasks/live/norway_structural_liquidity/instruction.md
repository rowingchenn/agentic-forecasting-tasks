Forecast the structural liquidity of the Norwegian banking system, as Norges Bank publishes
it, for Tuesday to Friday of this week and Monday of next week. You have about 90 minutes.

## The target

For each calendar day, the target is Norges Bank's *structural liquidity*, in NOK million:
the reserves that banks hold overnight at Norges Bank, excluding the effect of Norges Bank's
market operations (F-loans, F-deposits and central bank certificates). It is the series
`STAT_S` (Structural liquidity) of the dataflow `LIQUIDITY_FORECAST` in Norges Bank's data
warehouse, and the historical line in the charts on Norges Bank's page *Liquidity in the
banking system*. Norges Bank publishes it with each liquidity forecast, normally every
Monday and Thursday at 12:00 ${timezone}, with values up to the day before; around holidays
a release can move or be skipped, and occasionally an extra one comes out on another day.
Weekends and holidays repeat the previous business day's value, and the value can be
negative. A forecast is settled on the value first published for that day; the last one to
four days of each year are never published and are not scored. Total liquidity (`STAT_T`),
banks' total reserves (sight deposits) at Norges Bank with the market operations included,
which stays near Norges Bank's NOK 35 billion target, Norges Bank's forecast of structural
liquidity including central bank certificates (`FCAST_CBC`), and the cumulative effects of
single factors in Norges Bank's Bank liquidity statistics measure different quantities.

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
     "quantiles": {"0.1": <NOK million>, "0.2": <NOK million>, "0.3": <NOK million>,
                   "0.4": <NOK million>, "0.5": <NOK million>, "0.6": <NOK million>,
                   "0.7": <NOK million>, "0.8": <NOK million>, "0.9": <NOK million>}},
    ...
  ]
}
```

- Include each date in `target_dates` exactly once.
- `quantiles` uses exactly the levels in `quantile_levels`, written as shown ("0.1"
  through "0.9"). Values are numbers in NOK million, negative ones included, and must not
  decrease as the level increases.

Each forecast is scored after Norges Bank publishes the actual value, with the continuous
ranked probability score (CRPS) of its quantiles, so both the center and the spread
matter.

## Collected materials

${materials_note}
