Forecast the Bank of Canada's daily average USD/CAD exchange rate for the coming week. The
Task runs on Mondays and asks for Tuesday through Friday and the following Monday;
forecasts are due at 19:00 Eastern Time (America/Toronto) on the run date. You have about
90 minutes.

## The target

For each target date, the target is the Bank of Canada's daily average exchange rate for
the US dollar, series `FXUSDCAD` in the Bank's Valet API: Canadian dollars per one US
dollar, published with four decimals (1.3994 means 1 USD = 1.3994 CAD). The Bank collects
indicative mid-market quotes every minute from 08:00 to 16:00 ET, drops the highest and
lowest 2.5%, and averages the rest (a trimmed mean); it publishes the rate by 16:30 ET on
Bank business days, when its Ottawa head office is open. On weekends and Bank holidays no
rate is published, and the target for such a date is the previous published rate. The run
date's own rate is published before this task starts. A forecast is settled on the first
rate the Bank publishes for that date; on weekends and Bank holidays, the previous rate.
The inverse rate (USD per CAD), an intraday or closing quote, another provider's fixing
and the Bank's former noon rate are different quantities.

## Inputs and environment

`/home/user/input/task.json` gives the run date, the target dates (the run date plus
[1, 2, 3, 4, 7] days), the submission cutoff (19:00 America/Toronto on the run date), and the
quantile levels [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9].

This is an offline replay of the run date: the network is cut and web search is disabled. Base your forecast on the materials in `/home/user/input/archive/`, which agents gathered from the internet on the run date: one folder per agent, each with an `index.jsonl` giving every file's source URL and why it was saved. Apart from task.json and the archive, no data is provided. You may use any software, methods, or models available in this environment. Forecast as of the run date in task.json, whatever the system clock shows.

## Output

Write `/home/user/output/forecast.json` in this form:

```text
{
  "run_date": "<run_date from task.json>",
  "forecasts": [
    {"target_date": "YYYY-MM-DD",
     "quantiles": {"0.1": <CAD per USD>, "0.2": <CAD per USD>, "0.3": <CAD per USD>,
                   "0.4": <CAD per USD>, "0.5": <CAD per USD>, "0.6": <CAD per USD>,
                   "0.7": <CAD per USD>, "0.8": <CAD per USD>, "0.9": <CAD per USD>}},
    ...
  ]
}
```

- Include each date in `target_dates` exactly once.
- `quantiles` uses exactly the levels in `quantile_levels`, written as shown ("0.1"
  through "0.9"). Values are positive numbers in CAD per USD and must not decrease as the
  level increases.

Each forecast is scored after the rates are published, with the continuous ranked
probability score (CRPS) of its quantiles, so both the center and the spread matter.

## Collected materials

A replay has no outside sources, so there is nothing to collect: write an empty `/home/user/output/collected/index.jsonl`.
