Forecast how much gas Great Britain's National Transmission System (NTS) will deliver in
the next gas day. You have about 90 minutes.

## The target

A gas day runs from 05:00 to 05:00 UK local time and is named after the day it starts on,
so the gas day of the target date starts at 05:00 on the morning after the run date. When
the clocks change, the gas day that contains the change has 23 hours (spring) or 25 hours
(autumn). The target is National Gas's *Demand Actual, NTS, D+1* for that gas day (data
item PUBOB637 on the National Gas Data Portal, data.nationalgas.com), in million standard
cubic metres (mcm): NTS direct loads (industry, power stations, storage injection), LDZ
offtakes to the regional distribution networks, NTS shrinkage and interconnector exports.

A forecast is settled on the first value National Gas publishes for the gas day, normally
at about 12:20 UK time the day after it. The portal also posts partial values that
morning (often 0 or about 20 mcm) and, rarely, erroneous values that are corrected later,
so a value counts only if it lies within a factor of 1.25 of the median of every version of
National Gas's forecast for that gas day (*Demand Forecast, NTS*, PUBOB623) published
before it; a value with no forecast version before it counts as it is. A value or version
counts as published at the later of its two time stamps on the portal, *Applicable At* and
*Generated Time* (UK local time), and the first value published that counts is used.
National Gas's later revisions, for up to five days after the gas day, are not used, and
neither are other measures such as demand in energy units (kWh), single LDZs, or the D+6
value.

## Inputs and environment

`/home/user/input/task.json` gives the run date, the target dates (the run date plus
[1] days), the submission cutoff (12:55 Europe/London on the run date), and the
quantile levels [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9].

You have unrestricted internet access. Apart from task.json, no data is provided: find the information you need yourself, using any sources, tools, software, or models.

## Output

Write `/home/user/output/forecast.json` in this form:

```text
{
  "run_date": "<run_date from task.json>",
  "forecasts": [
    {"target_date": "YYYY-MM-DD",
     "quantiles": {"0.1": <mcm>, "0.2": <mcm>, "0.3": <mcm>, "0.4": <mcm>, "0.5": <mcm>,
                   "0.6": <mcm>, "0.7": <mcm>, "0.8": <mcm>, "0.9": <mcm>}},
    ...
  ]
}
```

- Include each date in `target_dates` exactly once.
- `quantiles` uses exactly the levels in `quantile_levels`, written as shown ("0.1"
  through "0.9"). Values are positive numbers in mcm for the gas day and must not decrease
  as the level increases.

Each forecast is scored after National Gas publishes the gas day's first value, with the
continuous ranked probability score (CRPS) of its quantiles, so both the center and the
spread matter.

## Collected materials

Save copies of the external materials you found useful for this forecast, such as data you downloaded, articles, reports, or forecasts by others, as regular files (not symbolic links) under `/home/user/output/collected/`. Save only what you obtained from outside this environment, not your own code, analyses, or forecasts. List each saved file in `/home/user/output/collected/index.jsonl`, one JSON object per line: `{"path": "<path relative to collected/>", "source": "<URL you obtained it from>", "note": "<why it is useful>"}`. Write an empty `index.jsonl` if you saved nothing.
