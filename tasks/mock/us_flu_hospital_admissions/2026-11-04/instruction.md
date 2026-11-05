Forecast how many people will be admitted to hospitals in the United States with
laboratory-confirmed influenza in each of four weeks: the week that ends this Saturday and
the three weeks after it. You have about 90 minutes.

## The target

Each target date is a Saturday, the last day of an MMWR epidemiological week (Sunday through
Saturday). For each, the target is the number of new hospital admissions of patients with
laboratory-confirmed influenza in the United States during that week, as hospitals report
them to CDC's National Healthcare Safety Network (NHSN): the field `totalconfflunewadm` for
jurisdiction `USA` in the preliminary release of NHSN's Weekly Hospital Respiratory Data
(HRD) Metrics by Jurisdiction on data.cdc.gov (dataset `mpgq-jmmr`), in the row whose
`weekendingdate` is the target date. A forecast is settled on the first value published for
its week, normally in the release on the Wednesday after the week ends. NHSN revises a
week's value after that, usually upward as late reports arrive; those revisions, and NHSN's
official release on Fridays (dataset `ua7e-t2fy`), are not used. Single states, rates per
100,000, and other surveillance systems such as FluSurv-NET or ILINet measure different
quantities.

NHSN's release on the run date, normally late in the morning Eastern time, adds the week
that ended the Saturday before. The first target week, ending this Saturday, is therefore
already under way at the cutoff, and no part of its count is published until the
Wednesday after it ends.

## Inputs and environment

Runs are on Wednesdays. `/home/user/input/task.json` gives the run date, the target dates
(the run date plus [3, 10, 17, 24] days: the Saturday that ends this week and the three
Saturdays after it), the submission cutoff (14:05 America/New_York on the run date), and
the quantile levels [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9].

This is an offline replay of the run date: the network is cut and web search is disabled. Base your forecast on the materials in `/home/user/input/archive/`, which agents gathered from the internet on the run date: one folder per agent, each with an `index.jsonl` giving every file's source URL and why it was saved. Apart from task.json and the archive, no data is provided. You may use any software, methods, or models available in this environment. Forecast as of the run date in task.json, whatever the system clock shows.

## Output

Write `/home/user/output/forecast.json` in this form:

```text
{
  "run_date": "<run_date from task.json>",
  "forecasts": [
    {"target_date": "YYYY-MM-DD",
     "quantiles": {"0.1": <admissions>, "0.2": <admissions>, "0.3": <admissions>,
                   "0.4": <admissions>, "0.5": <admissions>, "0.6": <admissions>,
                   "0.7": <admissions>, "0.8": <admissions>, "0.9": <admissions>}},
    ...
  ]
}
```

- Include each date in `target_dates` exactly once.
- `quantiles` uses exactly the levels in `quantile_levels`, written as shown ("0.1"
  through "0.9"). Values are positive numbers of admissions in the week ending on the
  target date and must not decrease as the level increases.

Each forecast is scored after NHSN publishes the week's first value, with the continuous
ranked probability score (CRPS) of its quantiles, so both the center and the spread matter.

## Collected materials

A replay has no outside sources, so there is nothing to collect: write an empty `/home/user/output/collected/index.jsonl`.
