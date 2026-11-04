Forecast the official closing price of Apple stock (AAPL) for the coming week. The Task
runs on Mondays after the stock market closes and asks for Tuesday through Friday and the
following Monday; forecasts are due at 18:35 Eastern Time (America/New_York) on the run
date. You have about 90 minutes.

## The target

For each target date, the target is the official closing price of Apple Inc. common stock
(ticker AAPL) for that date's regular trading session on Nasdaq (09:30 to 16:00 ET), in US
dollars per share with cents, as published that day: it is not adjusted for later stock
splits or dividends. On a date without a regular session (weekends and exchange holidays),
the target is the previous session's closing price. The run date's own close is published
before this task starts. A forecast is settled on the first closing price published for
that date. Pre-market and after-hours trades, intraday prices and a split- or
dividend-adjusted close are different quantities.

## Inputs and environment

`/home/user/input/task.json` gives the run date, the target dates (the run date plus
[1, 2, 3, 4, 7] days), the submission cutoff (18:35 America/New_York on the run date), and the
quantile levels [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9].

You have unrestricted internet access. Apart from task.json, no data is provided: find the information you need yourself, using any sources, tools, software, or models.

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

Save copies of the external materials you found useful for this forecast, such as data you downloaded, articles, reports, or forecasts by others, as regular files (not symbolic links) under `/home/user/output/collected/`. Save only what you obtained from outside this environment, not your own code, analyses, or forecasts. List each saved file in `/home/user/output/collected/index.jsonl`, one JSON object per line: `{"path": "<path relative to collected/>", "source": "<URL you obtained it from>", "note": "<why it is useful>"}`. Write an empty `index.jsonl` if you saved nothing.
