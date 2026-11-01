Forecast Anthropic's share of all spend on Vercel's AI Gateway for one day: the UTC day after
the run date. You have about 90 minutes.

## The target

The target date is a UTC day, from 00:00 to 24:00 UTC; the one after the run date starts at
19:00 Eastern Standard Time (20:00 Eastern Daylight Time) on the run date. The target is
Anthropic's share, in percent, of all spend through Vercel's AI Gateway on that day, as
Vercel's AI Gateway leaderboard of labs publishes it on its Spend chart, "Share of AI Gateway
spend across the top labs" (https://vercel.com/ai-gateway/leaderboards/labs). Its exact value
is in Vercel's open-data export of the leaderboard,
https://vercel.com/api/ai/leaderboard-export?dataset=labs&modality=all&format=json, documented
at https://vercel.com/docs/ai-gateway/leaderboards: the field `share_percent` of the row whose
`date` is the target date, `name` is `anthropic` and `metric` is `spend`, for example 31.7541.
Every lab's share of the day is listed there, and they sum to 100. A forecast is settled on
the first value the export shows for the date once a full day has passed since the date
ended; later changes are not used. The export's latest row is the current, incomplete day.
Shares of tokens or requests, other modalities (such as `text`), other labs and individual
models measure different quantities.

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

${materials_note}
