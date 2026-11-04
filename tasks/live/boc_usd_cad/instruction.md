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

You have unrestricted internet access. Apart from task.json, no data is provided: find the information you need yourself, using any sources, tools, software, or models.

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

Save copies of the external materials you found useful for this forecast, such as data you downloaded, articles, reports, or forecasts by others, as regular files (not symbolic links) under `/home/user/output/collected/`. Save only what you obtained from outside this environment, not your own code, analyses, or forecasts. List each saved file in `/home/user/output/collected/index.jsonl`, one JSON object per line: `{"path": "<path relative to collected/>", "source": "<URL you obtained it from>", "note": "<why it is useful>"}`. Write an empty `index.jsonl` if you saved nothing.
