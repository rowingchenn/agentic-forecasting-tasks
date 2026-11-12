Forecast how many seasonally adjusted initial claims for unemployment insurance the US
Department of Labor (DOL) will first report for each of four weeks: the week that ends this
Saturday and the three weeks after it. You have about 90 minutes.

## The target

Each target date is a Saturday, the last day of a claims week (Sunday through Saturday). For
each, the target is the seasonally adjusted number of initial claims for unemployment
insurance in the United States in that week, as DOL first publishes it: the advance figure in
the sentence "In the week ending [date], the advance figure for seasonally adjusted initial
claims was [number]" of DOL's Unemployment Insurance Weekly Claims news release
(https://www.dol.gov/ui/data.pdf; past releases are listed at
https://oui.doleta.gov/unemploy/claims_arch.asp and stored by release date, such as
https://oui.doleta.gov/press/2026/102926.pdf). DOL rounds the figure to the nearest 1,000.

A forecast is settled on that first figure, normally published at 8:30 a.m. Eastern on the
Thursday after the week ends, or on the Wednesday in a week when Thursday is a holiday or a
federal office closure. DOL revises the figure a week later and revises its seasonal factors
every year; those revisions are not used. If DOL publishes no release for a week, as in a lapse
in appropriations, the forecast is settled on FRED's series ICSA for that week, read once DOL's
releases have resumed and the week is three weeks old. Unadjusted claims, continued claims, the
four-week average and state figures measure different quantities.

DOL's release at 8:30 a.m. Eastern on the run date, or on the day before when that Thursday is
a holiday, gives the week that ended the Saturday before. The first target week, ending this
Saturday, is therefore already under way at the cutoff, and no part of its count is published
until the Thursday after it ends.

## Inputs and environment

Runs are on Thursdays. `/home/user/input/task.json` gives the run date, the target dates (the
run date plus [2, 9, 16, 23] days: this Saturday and the three Saturdays after it), the
submission cutoff (10:45 America/New_York on the run date), and the quantile levels [0.1, 0.2,
0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9].

This is an offline replay of the run date: the network is cut and web search is disabled. Base your forecast on the materials in `/home/user/input/archive/`, which agents gathered from the internet on the run date: one folder per agent, each with an `index.jsonl` giving every file's source URL and why it was saved. Apart from task.json and the archive, no data is provided. You may use any software, methods, or models available in this environment. Forecast as of the run date in task.json, whatever the system clock shows.

## Output

Write `/home/user/output/forecast.json` in this form:

```text
{
  "run_date": "<run_date from task.json>",
  "forecasts": [
    {"target_date": "YYYY-MM-DD",
     "quantiles": {"0.1": <claims>, "0.2": <claims>, "0.3": <claims>,
                   "0.4": <claims>, "0.5": <claims>, "0.6": <claims>,
                   "0.7": <claims>, "0.8": <claims>, "0.9": <claims>}},
    ...
  ]
}
```

- Include each date in `target_dates` exactly once.
- `quantiles` uses exactly the levels in `quantile_levels`, written as shown ("0.1"
  through "0.9"). Values are positive numbers of claims in the week ending on the target date,
  in claims and not in thousands, and must not decrease as the level increases.

Each forecast is scored after DOL publishes the week's first figure, with the continuous ranked
probability score (CRPS) of its quantiles, so both the center and the spread matter.

## Collected materials

A replay has no outside sources, so there is nothing to collect: write an empty `/home/user/output/collected/index.jsonl`.
