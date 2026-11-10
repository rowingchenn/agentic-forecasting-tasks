Forecast the average US retail price of regular gasoline that the US Energy Information
Administration (EIA) will first publish for each of the next two Mondays. You have about 90
minutes.

## The target

Each target date is a Monday. For each, the target is EIA's weekly US regular gasoline
retail price, all formulations, in dollars per gallon including taxes, for that Monday: series
`EMM_EPMR_PTE_NUS_DPG`, "Weekly U.S. Regular All Formulations Retail Gasoline Prices", of
EIA's Gasoline and Diesel Fuel Update (https://www.eia.gov/petroleum/gasdiesel/; every week
since 1990 is listed at
https://www.eia.gov/dnav/pet/hist/LeafHandler.ashx?n=PET&s=EMM_EPMR_PTE_NUS_DPG&f=W). EIA
surveys about 1,000 retail outlets for their self-serve cash price at 8:00 a.m. local time on
the Monday and publishes the average weighted by the outlets' sales volumes, to three decimal
places. Midgrade, premium and all-grades prices, diesel, regional, state and city prices, and
the daily averages of other publishers such as AAA or GasBuddy measure different quantities.

A forecast is settled on the value EIA first publishes for the Monday, normally at about
10:00 a.m. Eastern on the Tuesday after it, or on the Wednesday when the Monday is a federal
holiday. Later revisions are not used.

EIA's release on the Tuesday before the run date gives the price of the Monday three days
before it. The first target date is the next Monday, so no part of either target is
published by the cutoff.

## Inputs and environment

Runs are on Thursdays. `/home/user/input/task.json` gives the run date, the target dates (the
run date plus [4, 11] days: the next Monday and the Monday after it), the submission cutoff
(13:50 America/New_York on the run date), and the quantile levels [0.1, 0.2, 0.3, 0.4, 0.5,
0.6, 0.7, 0.8, 0.9].

You have unrestricted internet access. Apart from task.json, no data is provided: find the information you need yourself, using any sources, tools, software, or models.

## Output

Write `/home/user/output/forecast.json` in this form:

```text
{
  "run_date": "<run_date from task.json>",
  "forecasts": [
    {"target_date": "YYYY-MM-DD",
     "quantiles": {"0.1": <USD/gal>, "0.2": <USD/gal>, "0.3": <USD/gal>,
                   "0.4": <USD/gal>, "0.5": <USD/gal>, "0.6": <USD/gal>,
                   "0.7": <USD/gal>, "0.8": <USD/gal>, "0.9": <USD/gal>}},
    ...
  ]
}
```

- Include each date in `target_dates` exactly once.
- `quantiles` uses exactly the levels in `quantile_levels`, written as shown ("0.1"
  through "0.9"). Values are positive prices in dollars per gallon, such as 3.125, and must
  not decrease as the level increases.

Each forecast is scored after EIA publishes the Monday's price, with the continuous ranked
probability score (CRPS) of its quantiles, so both the center and the spread matter.

## Collected materials

Save copies of the external materials you found useful for this forecast, such as data you downloaded, articles, reports, or forecasts by others, as regular files (not symbolic links) under `/home/user/output/collected/`. Save only what you obtained from outside this environment, not your own code, analyses, or forecasts. List each saved file in `/home/user/output/collected/index.jsonl`, one JSON object per line: `{"path": "<path relative to collected/>", "source": "<URL you obtained it from>", "note": "<why it is useful>"}`. Write an empty `index.jsonl` if you saved nothing.
