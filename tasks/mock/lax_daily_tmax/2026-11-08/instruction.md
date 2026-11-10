Forecast the highest temperature at Los Angeles International Airport on each of the next six
days. You have about 90 minutes.

## The target

For each day, the target is the maximum temperature, in whole degrees Fahrenheit, in the
National Weather Service's Daily Climate Report for Los Angeles International Airport (product
CLILAX, issued by the NWS office in Los Angeles/Oxnard): the "MAXIMUM" line under
"TEMPERATURE (F)". The report covers the climate day from midnight to midnight Pacific
Standard Time; the climate day stays on standard time all year, so while daylight saving time
is in effect it runs from 1 a.m. to 1 a.m. local time. The maximum can occur at any hour of
the climate day, at night as well; a forecast "daytime high" for 6 a.m. to 6 p.m. covers only
part of it and can differ. A forecast is settled on the first final report for the day that
gives a maximum ("...CLIMATE SUMMARY FOR <date>...", normally issued within a few hours after
the climate day ends); a day without one is not scored. The preliminary report issued around
5 p.m. ("VALID TODAY AS OF 0500 PM LOCAL TIME") and later corrections do not count. Readings
from other stations, other sources' daily maxima for the airport, and hourly observations
measure different quantities.

## Inputs and environment

There is a run every day. `/home/user/input/task.json` gives the run date, the target dates
(the run date plus [1, 2, 3, 4, 5, 6] days), the submission cutoff (18:20 UTC on the run date,
which is 10:20 a.m. PST or 11:20 a.m. PDT in Los Angeles), and the quantile levels
[0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]. The run date is the UTC date, which during the
run is also the date in Los Angeles; each target date is a climate day as defined above.

This is an offline replay of the run date: the network is cut and web search is disabled. Base your forecast on the materials in `/home/user/input/archive/`, which agents gathered from the internet on the run date: one folder per agent, each with an `index.jsonl` giving every file's source URL and why it was saved. Apart from task.json and the archive, no data is provided. You may use any software, methods, or models available in this environment. Forecast as of the run date in task.json, whatever the system clock shows.

## Output

Write `/home/user/output/forecast.json` in this form:

```text
{
  "run_date": "<run_date from task.json>",
  "forecasts": [
    {"target_date": "YYYY-MM-DD",
     "quantiles": {"0.1": <°F>, "0.2": <°F>, "0.3": <°F>, "0.4": <°F>, "0.5": <°F>,
                   "0.6": <°F>, "0.7": <°F>, "0.8": <°F>, "0.9": <°F>}},
    ...
  ]
}
```

- Include each date in `target_dates` exactly once.
- `quantiles` uses exactly the levels in `quantile_levels`, written as shown ("0.1"
  through "0.9"). Values are numbers in degrees Fahrenheit and must not decrease as the
  level increases.

Each forecast is scored after the climate reports are published, with the continuous ranked
probability score (CRPS) of its quantiles, so both the center and the spread matter.

## Collected materials

A replay has no outside sources, so there is nothing to collect: write an empty `/home/user/output/collected/index.jsonl`.
