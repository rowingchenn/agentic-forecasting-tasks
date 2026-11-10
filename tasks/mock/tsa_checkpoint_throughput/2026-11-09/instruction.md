Forecast how many people the US Transportation Security Administration (TSA) screens at
airport security checkpoints nationwide on each of the seven days after the run date. You
have about 90 minutes.

## The target

For each date, the target is TSA's checkpoint travel number for that calendar day: the
number of travelers screened at TSA security checkpoints nationwide on that day, in whole
passengers, as published under that date in the Date/Numbers table of TSA's Passenger
Volumes page, https://www.tsa.gov/travel/passenger-volumes, for example 2,450,000. A
forecast is settled on the first number TSA publishes for the date; later revisions, such
as those on TSA's archive page of a past year, are not used. TSA posts its numbers on
weekday mornings, usually by 10:00 Eastern time, adding the previous day; Monday's posting
adds Friday to Sunday, and holiday weeks may be later. Counts for single airports, airline
passenger counts and other agencies' air travel statistics measure different quantities.

Runs are daily. Forecasts are due by 13:00 Eastern time (America/New_York) on the run
date, for each of the seven dates after it.

## Inputs and environment

`/home/user/input/task.json` gives the run date, the target dates (the run date plus
[1, 2, 3, 4, 5, 6, 7] days), the submission cutoff (13:00 America/New_York on the run date), and the
quantile levels [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9].

This is an offline replay of the run date: the network is cut and web search is disabled. Base your forecast on the materials in `/home/user/input/archive/`, which agents gathered from the internet on the run date: one folder per agent, each with an `index.jsonl` giving every file's source URL and why it was saved. Apart from task.json and the archive, no data is provided. You may use any software, methods, or models available in this environment. Forecast as of the run date in task.json, whatever the system clock shows.

## Output

Write `/home/user/output/forecast.json` in this form:

```text
{
  "run_date": "<run_date from task.json>",
  "forecasts": [
    {"target_date": "YYYY-MM-DD",
     "quantiles": {"0.1": <passengers>, "0.2": <passengers>, "0.3": <passengers>,
                   "0.4": <passengers>, "0.5": <passengers>, "0.6": <passengers>,
                   "0.7": <passengers>, "0.8": <passengers>, "0.9": <passengers>}},
    ...
  ]
}
```

- Include each date in `target_dates` exactly once.
- `quantiles` uses exactly the levels in `quantile_levels`, written as shown ("0.1"
  through "0.9"). Values are positive numbers of passengers and must not decrease as
  the level increases.

Each forecast is scored after TSA publishes the number, with the continuous ranked
probability score (CRPS) of its quantiles, so both the center and the spread matter.

## Collected materials

A replay has no outside sources, so there is nothing to collect: write an empty `/home/user/output/collected/index.jsonl`.
