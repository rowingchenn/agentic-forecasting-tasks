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

You have unrestricted internet access. Apart from task.json, no data is provided: find the information you need yourself, using any sources, tools, software, or models.

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

Save copies of the external materials you found useful for this forecast, such as data you downloaded, articles, reports, or forecasts by others, as regular files (not symbolic links) under `/home/user/output/collected/`. Save only what you obtained from outside this environment, not your own code, analyses, or forecasts. List each saved file in `/home/user/output/collected/index.jsonl`, one JSON object per line: `{"path": "<path relative to collected/>", "source": "<URL you obtained it from>", "note": "<why it is useful>"}`. Write an empty `index.jsonl` if you saved nothing.
