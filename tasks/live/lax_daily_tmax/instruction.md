Forecast the daily maximum 2-metre air temperature near Los Angeles International
Airport (33.94° N, 118.40° W) for the next seven days. You have about 90 minutes.

## Exact target and settlement

The target is Open-Meteo Historical Weather API's `temperature_2m_max`, with
`models=era5`, latitude `33.94`, longitude `-118.40`, elevation `38` metres,
`cell_selection=land`, and timezone `${timezone}`. It is the provider's local-calendar
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

`/home/user/input/task.json` gives the run date, target dates (run date plus ${horizons}
calendar days), cutoff (${cutoff} ${timezone} on the run date), and quantile levels
${quantile_levels}. Forecast each entire target day, not only a daytime period.
Use information available before submission and never after the cutoff.

${environment_note}

In live runs, you may consult any accessible weather service, model output, satellite
or marine information, public news, or other relevant internet source, and choose your
own covariates. Open-Meteo is not your only allowed source. Its public non-commercial
APIs normally need no key, subject to usage terms and limits; no private credential is
provided inside the episode. Follow the offline restrictions above in mock replays.

The comparison baseline is ECMWF IFS 0.25° (`ecmwf_ifs025`) through Open-Meteo at the
same requested location, elevation and timezone. Its native grid differs from ERA5.
The task's target and settlement definition remain ERA5, regardless of sources used.

${history_note}

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

${materials_note}
