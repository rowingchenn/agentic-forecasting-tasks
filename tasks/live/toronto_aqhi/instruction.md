Forecast the daytime maximum of Toronto's Air Quality Health Index (AQHI) for today and
tomorrow. You have about 90 minutes.

## The target

For each calendar day (${timezone}), the target is the highest hourly AQHI that Environment
and Climate Change Canada (ECCC) observes for its AQHI community Toronto (code FEUZB) during
the day: the maximum of the hourly values stamped 07:00 to 18:00 local time, which cover
ECCC's daytime forecast period of 06:00 to 18:00. Values count as ECCC publishes them, with
two decimals, and "10+" counts as 11; a day counts once at least 9 of its 12 hours have
values. A forecast is settled on the first published value: the hourly observations in
ECCC's realtime file on its MSC Datamart the day after. Later backfill revisions are not
used. ECCC's own AQHI forecasts are whole numbers, but the observations are not rounded.
Values for single monitoring stations or other communities, and other indices such as the
US Air Quality Index, measure different quantities.

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
     "quantiles": {"0.1": <AQHI>, "0.2": <AQHI>, "0.3": <AQHI>, "0.4": <AQHI>, "0.5": <AQHI>,
                   "0.6": <AQHI>, "0.7": <AQHI>, "0.8": <AQHI>, "0.9": <AQHI>}},
    ...
  ]
}
```

- Include each date in `target_dates` exactly once.
- `quantiles` uses exactly the levels in `quantile_levels`, written as shown ("0.1"
  through "0.9"). Values are positive numbers on the AQHI scale and must not decrease as
  the level increases.

Each forecast is scored after ECCC publishes the observations, with the continuous ranked
probability score (CRPS) of its quantiles, so both the center and the spread matter.

## Collected materials

${materials_note}
