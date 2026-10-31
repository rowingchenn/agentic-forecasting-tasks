Forecast how many people the US Transportation Security Administration (TSA) screens at
airport security checkpoints today. You have about 90 minutes.

## The target

The target is TSA's checkpoint travel number for the run date (${timezone}): the number of
travelers screened at TSA security checkpoints nationwide on that day, in whole passengers,
as published on TSA's Passenger Volumes page, https://www.tsa.gov/travel/passenger-volumes,
for example 2,450,000. A forecast is settled on the first number TSA publishes for the
date; later revisions are not used. TSA posts its numbers Monday to Friday by 9 a.m., so
the numbers for Friday to Sunday appear on Monday, and holiday weeks may be later. Counts
for single airports, airline passenger counts and other agencies' air travel statistics
measure different quantities.

## Inputs and environment

`/home/user/input/task.json` gives the run date, the target dates (the run date plus
${horizons} days), the submission cutoff (${cutoff} ${timezone} on the run date), and the
quantile levels ${quantile_levels}.

${environment_note}

${history_note}

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
  through "0.9"). Values are nonnegative numbers of passengers and must not decrease as
  the level increases.

Each forecast is scored after TSA publishes the number, with the continuous ranked
probability score (CRPS) of its quantiles, so both the center and the spread matter.

## Collected materials

${materials_note}
