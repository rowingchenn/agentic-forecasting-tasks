Forecast the daily electricity generation from wind on the Southwest Power Pool (SPP) grid
for the next seven days. The Task runs every day at 12:00 America/Chicago and asks for the
seven days after the run date; forecasts are due at 13:50 America/Chicago on the run date.
You have about 90 minutes.

## The target

The target is the net generation from wind that SPP reports to the US Energy Information
Administration on Form EIA-930, as published in the six-month BALANCE files of EIA's
Hourly Electric Grid Monitor (https://www.eia.gov/electricity/gridmonitor/, files
`EIA930_BALANCE_<year>_Jan_Jun.csv` and `EIA930_BALANCE_<year>_Jul_Dec.csv`). Take the
rows with `Balancing Authority` = `SWPP` and the column `Net Generation (MW) from Wind
without Integrated Battery Storage` (fuel code WND). Each hourly value is the hour's
energy in MWh. For each day from midnight to midnight America/Chicago, the target is the
sum of that day's 23, 24 or 25 hourly values, in MWh; the hour ending at midnight
(`UTC Time at End of Hour` converted to Central time) belongs to the day that ends, as in
the file's `Data Date`. A day counts only when every hour has a value.

Wind with integrated battery storage (WNB), the imputed and adjusted columns and other
fuels are not part of the target. EIA's API and the Grid Monitor's charts show the same
hourly values, but a forecast is settled on the first complete daily total in the
six-month file, which EIA rewrites once a day, at about 10:00 Central, adding the previous
day; later revisions do not replace it.

## Inputs and environment

`/home/user/input/task.json` gives the run date, the target dates (the run date plus
[1, 2, 3, 4, 5, 6, 7] days), the submission cutoff (13:50 America/Chicago on the run date), and the
quantile levels [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9].

This is an offline replay of the run date: the network is cut and web search is disabled. Base your forecast on the materials in `/home/user/input/archive/`, which agents gathered from the internet on the run date: one folder per agent, each with an `index.jsonl` giving every file's source URL and why it was saved. Apart from task.json and the archive, no data is provided. You may use any software, methods, or models available in this environment. Forecast as of the run date in task.json, whatever the system clock shows.

## Output

Write `/home/user/output/forecast.json` in this form:

```text
{
  "run_date": "<run_date from task.json>",
  "forecasts": [
    {"target_date": "YYYY-MM-DD",
     "quantiles": {"0.1": <MWh>, "0.2": <MWh>, "0.3": <MWh>, "0.4": <MWh>, "0.5": <MWh>,
                   "0.6": <MWh>, "0.7": <MWh>, "0.8": <MWh>, "0.9": <MWh>}},
    ...
  ]
}
```

- Include each date in `target_dates` exactly once.
- `quantiles` uses exactly the levels in `quantile_levels`, written as shown ("0.1"
  through "0.9"). Values are non-negative numbers in MWh and must not decrease as the level
  increases.

Each forecast is scored after EIA publishes the actual value, with the continuous
ranked probability score (CRPS) of its quantiles, so both the center and the spread
matter.

## Collected materials

A replay has no outside sources, so there is nothing to collect: write an empty `/home/user/output/collected/index.jsonl`.
