Forecast the daily maximum 2-metre air temperature near Los Angeles International
Airport (33.94° N, 118.40° W) for the next seven days. You have about 90 minutes.

## Exact target and settlement

The target is Open-Meteo Historical Weather API's `temperature_2m_max`, with
`models=era5`, latitude `33.94`, longitude `-118.40`, elevation `38` metres,
`cell_selection=land`, and timezone `America/Los_Angeles`. It is the provider's local-calendar
daily maximum for its gridded, elevation-adjusted reanalysis series, NOT a measured
LAX station maximum and NOT whichever temperature a forecast website later displays.

Submit temperatures in **Kelvin (K)**: `K = °C + 273.15`. For example, 25°C is 298.15 K.
Kelvin keeps below-freezing temperatures compatible with this benchmark's positive
value checks; temperature differences have the same numerical size in K and °C.

Outcomes settle on the first finite ERA5 archive value the pipeline observes after a
six-day publication buffer. Reanalysis is delayed and may later be revised; previous
settlements are retained. Recent unavailable outcomes remain unresolved. Forecasts
are never used as observed truth.

## Inputs and timing

`/home/user/input/task.json` gives the run date, target dates (run date plus [1, 2, 3, 4, 5, 6, 7]
calendar days), cutoff (10:00 America/Los_Angeles on the run date), and quantile levels
[0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]. Forecast each entire target day, not only a daytime period.
Use information available before submission and never after the cutoff.

You have unrestricted internet access. Apart from task.json, no data is provided: find the information you need yourself, using any sources, tools, software, or models.

In live runs, you may consult any accessible weather service, model output, satellite
or marine information, public news, or other relevant internet source, and choose your
own covariates. Open-Meteo is not your only allowed source. Its public non-commercial
APIs normally need no key, subject to usage terms and limits; no private credential is
provided inside the episode. Follow the offline restrictions above in mock replays.

The comparison baseline is ECMWF IFS 0.25° (`ecmwf_ifs025`) through Open-Meteo at the
same requested location, elevation and timezone. Its native grid differs from ERA5.
The task's target and settlement definition remain ERA5, regardless of sources used.

## Output

Write `/home/user/output/forecast.json`:

```text
{
  "run_date": "<run_date from task.json>",
  "forecasts": [
    {"target_date": "YYYY-MM-DD",
     "quantiles": {"0.1": <K>, "0.2": <K>, "0.3": <K>, "0.4": <K>,
                   "0.5": <K>, "0.6": <K>, "0.7": <K>, "0.8": <K>, "0.9": <K>}}
  ]
}
```

Include every target date exactly once. Use exactly the requested quantile levels,
with finite, positive Kelvin values, nondecreasing as the quantile level increases.
Accuracy is evaluated after settlement with quantile CRPS, median absolute error and
80% interval coverage. The episode verifier checks submission format, not accuracy.

## Collected materials

Save copies of the external materials you found useful for this forecast, such as data you downloaded, articles, reports, or forecasts by others, as regular files (not symbolic links) under `/home/user/output/collected/`. Save only what you obtained from outside this environment, not your own code, analyses, or forecasts. List each saved file in `/home/user/output/collected/index.jsonl`, one JSON object per line: `{"path": "<path relative to collected/>", "source": "<URL you obtained it from>", "note": "<why it is useful>"}`. Write an empty `index.jsonl` if you saved nothing.
