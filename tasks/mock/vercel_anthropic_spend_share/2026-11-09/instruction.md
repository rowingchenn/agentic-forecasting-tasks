Forecast Anthropic's share of all spend on Vercel's AI Gateway for each of the seven UTC days
after the run date. You have about 90 minutes.

## The target

Each target date is a UTC day, from 00:00 to 24:00 UTC; the first, the day after the run date,
starts at 19:00 Eastern Standard Time (20:00 Eastern Daylight Time) on the run date, after the
cutoff. The target is Anthropic's share, in percent, of all spend through Vercel's AI Gateway
on that day, as Vercel's AI Gateway leaderboard of labs publishes it on its Spend chart,
"Share of AI Gateway spend across the top labs" (https://vercel.com/ai-gateway/leaderboards/labs).
Its exact value is in Vercel's open-data export of the leaderboard,
https://vercel.com/api/ai/leaderboard-export?dataset=labs&modality=all&format=json, documented
at https://vercel.com/docs/ai-gateway/leaderboards: the field `share_percent` of the row whose
`date` is the target date, `name` is `anthropic` and `metric` is `spend`, for example 31.7541.
Every lab's share of the day is listed there, and they sum to 100. Vercel documents neither
what its spend measures nor the time zone of its days; that they are UTC days is inferred, not
documented. The export's latest row is the current, incomplete day. A forecast is settled on
the first value recorded for the date: the export's value read once a full day has passed
since the date ended, normally at 16:45 Eastern time two days after the date. Vercel changes
past values slightly afterwards; later changes are not used. Shares of tokens or requests,
other modalities (such as `text`), other labs and individual models measure different
quantities.

## Inputs and environment

There is a run day every day. `/home/user/input/task.json` gives the run date, the target
dates (the run date plus [1, 2, 3, 4, 5, 6, 7] days), the submission cutoff (18:30
America/New_York on the run date), and the quantile levels [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9].

This is an offline replay of the run date: the network is cut and web search is disabled. Base your forecast on the materials in `/home/user/input/archive/`, which agents gathered from the internet on the run date: one folder per agent, each with an `index.jsonl` giving every file's source URL and why it was saved. Apart from task.json and the archive, no data is provided. You may use any software, methods, or models available in this environment. Forecast as of the run date in task.json, whatever the system clock shows.

## Output

Write `/home/user/output/forecast.json` in this form:

```text
{
  "run_date": "<run_date from task.json>",
  "forecasts": [
    {"target_date": "YYYY-MM-DD",
     "quantiles": {"0.1": <percent>, "0.2": <percent>, "0.3": <percent>,
                   "0.4": <percent>, "0.5": <percent>, "0.6": <percent>,
                   "0.7": <percent>, "0.8": <percent>, "0.9": <percent>}},
    ...
  ]
}
```

- Include each date in `target_dates` exactly once.
- `quantiles` uses exactly the levels in `quantile_levels`, written as shown ("0.1"
  through "0.9"). Values are positive numbers, Anthropic's share in percent (31.75 for
  31.75%), and must not decrease as the level increases.

Each forecast is scored after Vercel publishes the day's share, with the continuous ranked
probability score (CRPS) of its quantiles, so both the center and the spread matter.

## Collected materials

A replay has no outside sources, so there is nothing to collect: write an empty `/home/user/output/collected/index.jsonl`.
