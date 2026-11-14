Forecast the national average advertised price of boneless skinless chicken breast in value
packs that the US Department of Agriculture (USDA) will report for each of the next two
weeks. You have about 90 minutes.

## The target

Each target date is a Friday, and its target is a price in US dollars per pound from USDA
Agricultural Marketing Service report 2756, *Weekly Grocery Store Chicken Feature
Activity* (https://mymarketnews.ams.usda.gov/viewReport/2756), in the issue whose week ends
on the target date: in the report's Report Details data, `report_end_date` is the target
date and `report_begin_date` the Saturday six days before it (the PDF is titled with the
Monday of that week). Take the row with `region` NATIONAL, `section` Parts, `type`
"Breast, Boneless/Skinless", `package_size` Value, `environment` Conventional, `condition`
Fresh and `price_unit` Per lb, and use its `price_avg`. In the PDF it is the line
"Breast, Boneless/Skinless, Value", Conventional, Fresh of the NATIONAL table, column CW Wtd
Avg. Before September 2024 the data name the same item with the `type` "BREAST,
BONELESS/SKINLESS, VALUE PACK" and no package size.

USDA collects the prices that major grocery retailers advertise in their weekly circulars
and digital promotions, mostly from the retailers' websites. `price_avg` is the average of
the advertised prices of the item, weighted by the number of stores each ad covers, over
the eight regions of the report, Alaska and Hawaii included. It is a promotional price,
not the average shelf or checkout price. Regular packs, packs of 10 lbs or more, tenders,
marinated or thin-sliced breast, and antibiotic-free or organic breast are other items.

USDA posts each issue on the Friday that ends its week, normally between 10:00 and 13:30
Eastern, or earlier in the week when the Friday is a federal holiday. A forecast is
settled on the value USDA first publishes; later revisions are not used. A week without an
issue is not scored.

At the cutoff, the latest issue is normally the one for the week ending on the run date,
posted that morning; the issues for the two target weeks come out on the target dates.

## Inputs and environment

Runs are on Fridays. `/home/user/input/task.json` gives the run date, the target dates (the
run date plus [7, 14] days: the next two Fridays), the submission cutoff (17:15
America/New_York on the run date), and the quantile levels [0.1, 0.2, 0.3, 0.4, 0.5, 0.6,
0.7, 0.8, 0.9].

You have unrestricted internet access. Apart from task.json, no data is provided: find the information you need yourself, using any sources, tools, software, or models.

## Output

Write `/home/user/output/forecast.json` in this form:

```text
{
  "run_date": "<run_date from task.json>",
  "forecasts": [
    {"target_date": "YYYY-MM-DD",
     "quantiles": {"0.1": <USD/lb>, "0.2": <USD/lb>, "0.3": <USD/lb>,
                   "0.4": <USD/lb>, "0.5": <USD/lb>, "0.6": <USD/lb>,
                   "0.7": <USD/lb>, "0.8": <USD/lb>, "0.9": <USD/lb>}},
    ...
  ]
}
```

- Include each date in `target_dates` exactly once.
- `quantiles` uses exactly the levels in `quantile_levels`, written as shown ("0.1"
  through "0.9"). Values are positive prices in dollars per pound, such as 2.49, and must
  not decrease as the level increases.

Each forecast is scored after USDA publishes the week's price, with the continuous ranked
probability score (CRPS) of its quantiles, so both the center and the spread matter.

## Collected materials

Save copies of the external materials you found useful for this forecast, such as data you downloaded, articles, reports, or forecasts by others, as regular files (not symbolic links) under `/home/user/output/collected/`. Save only what you obtained from outside this environment, not your own code, analyses, or forecasts. List each saved file in `/home/user/output/collected/index.jsonl`, one JSON object per line: `{"path": "<path relative to collected/>", "source": "<URL you obtained it from>", "note": "<why it is useful>"}`. Write an empty `index.jsonl` if you saved nothing.
