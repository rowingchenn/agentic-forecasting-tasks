Forecast the structural liquidity of the Norwegian banking system, as Norges Bank publishes
it, for Tuesday to Friday of this week and Monday of next week; the run date is a Monday.
You have about 90 minutes.

## The target

For each calendar day, the target is Norges Bank's *structural liquidity*, in NOK million:
the reserves that banks hold overnight at Norges Bank, excluding the effect of Norges Bank's
market operations (F-loans, F-deposits and central bank certificates). It is the series
`STAT_S` (Structural liquidity) of the dataflow `LIQUIDITY_FORECAST` in Norges Bank's data
warehouse, and the historical line in the charts on Norges Bank's page *Liquidity in the
banking system*. Norges Bank publishes it with each liquidity forecast, normally every
Monday and Thursday at 12:00 Europe/Oslo, with values up to the day before; around holidays
a release can move or be skipped, and occasionally an extra one comes out on another day.
Weekends and holidays repeat the previous business day's value, and the value can be
negative. A forecast is settled on the value first published for that day; the last one to
four days of each year are never published and are not scored. Total liquidity (`STAT_T`),
banks' total reserves (sight deposits) at Norges Bank with the market operations included,
which stays near Norges Bank's NOK 35 billion target, Norges Bank's forecast of structural
liquidity including central bank certificates (`FCAST_CBC`), and the cumulative effects of
single factors in Norges Bank's Bank liquidity statistics measure different quantities.

The run starts at 12:30 Europe/Oslo on the run date, after Norges Bank's 12:00 release of
that day, which adds the values for the previous Thursday to Sunday, and the forecast is
due at 14:15 Europe/Oslo. The latest published values normally run to the Sunday before
the run date; on a Monday without a release (a holiday), only to the day before the
previous release, normally the Wednesday before. The
targets are first published later: Tuesday's and Wednesday's values with the Thursday
release, Thursday's and Friday's with the next Monday's, and next Monday's with the
Thursday release after it.

## Inputs and environment

`/home/user/input/task.json` gives the run date, the target dates (the run date plus
[1, 2, 3, 4, 7] days), the submission cutoff (14:15 Europe/Oslo on the run date), and the
quantile levels [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9].

This is an offline replay of the run date: the network is cut and web search is disabled. Base your forecast on the materials in `/home/user/input/archive/`, which agents gathered from the internet on the run date: one folder per agent, each with an `index.jsonl` giving every file's source URL and why it was saved. Apart from task.json and the archive, no data is provided. You may use any software, methods, or models available in this environment. Forecast as of the run date in task.json, whatever the system clock shows.

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

A replay has no outside sources, so there is nothing to collect: write an empty `/home/user/output/collected/index.jsonl`.
