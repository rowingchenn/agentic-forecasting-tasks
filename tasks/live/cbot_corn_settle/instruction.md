Forecast the daily settlement price of CBOT corn futures for the coming week. The Task
runs on Mondays after the corn market settles and asks for Tuesday through Friday and the
following Monday; forecasts are due at 18:35 Eastern Time (America/New_York) on the run
date. You have about 90 minutes.

## The target

For each target date, the target is the settlement price published by CBOT (CME Group)
for that date's session of the nearby corn futures contract (ticker ZC), in US cents per
bushel. The nearby contract is, among the listed months March, May, July, September and
December, the one nearest to expiry whose last trading day is on or after the target
date; a contract's last trading day is the business day before the 15th calendar day of
its contract month. On the session after a contract's last trading day the target moves
to the next contract. On a date without a session (weekends and exchange holidays), the
target is the previous session's settlement price. The run date's own settlement is
published before this task starts. A forecast is settled on the first settlement price
published for that date. Intraday prices, the last traded price and prices of other
contract months are different quantities.

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
     "quantiles": {"0.1": <cents>, "0.2": <cents>, "0.3": <cents>, "0.4": <cents>, "0.5": <cents>,
                   "0.6": <cents>, "0.7": <cents>, "0.8": <cents>, "0.9": <cents>}},
    ...
  ]
}
```

- Include each date in `target_dates` exactly once.
- `quantiles` uses exactly the levels in `quantile_levels`, written as shown ("0.1"
  through "0.9"). Values are positive numbers in US cents per bushel (for example 441.5
  for $4.415 a bushel) and must not decrease as the level increases.

Each forecast is scored after the actual value is published, with the continuous
ranked probability score (CRPS) of its quantiles, so both the center and spread matter.

## Collected materials

Save copies of the external materials you found useful for this forecast, such as data you downloaded, articles, reports, or forecasts by others, as regular files (not symbolic links) under `/home/user/output/collected/`. Save only what you obtained from outside this environment, not your own code, analyses, or forecasts. List each saved file in `/home/user/output/collected/index.jsonl`, one JSON object per line: `{"path": "<path relative to collected/>", "source": "<URL you obtained it from>", "note": "<why it is useful>"}`. Write an empty `index.jsonl` if you saved nothing.
