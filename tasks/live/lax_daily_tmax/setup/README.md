# LAX-area daily maximum temperature

This target adapts the contributor's Open-Meteo past/future downloader to the repository's
host-only snapshot/truth/baseline contract. Each Task forecasts one scalar time series.
The initial task is maximum temperature; minimum temperature would be a separate task.

## Definition and baseline

Requested location: latitude 33.94, longitude -118.40; elevation fixed at 38 m;
cell_selection=land; timezone America/Los_Angeles. The provider's selected grid-cell
coordinates can differ from the requested coordinates. Retain them in raw responses.
Daily aggregation and elevation adjustment are defined by the provider. ERA5 and IFS
have different native grids; they are not interchangeable station observations.

Target: ERA5 temperature_2m_max through the Open-Meteo archive, with models=era5.
Baseline: temperature_2m_max with models=ecmwf_ifs025 through the forecast API.
Forecast dates: tomorrow through seven days after the run date, never today plus six.
No automatic best-match switching. Raw temperatures are Celsius; submitted and indexed
values are Kelvin (C + 273.15), compatible with the shared positive-value verifier.
Absolute errors and CRPS are invariant to this unit offset.

Training history starts 2024-01-01. ERA5 publication is delayed and can be revised.
Queries stop at run_date minus six days. Missing trailing archive values are left
unresolved; internal gaps fail explicitly. No interpolation, forecast backfill, or
replacement with another reanalysis. The pipeline retains first-seen values; this is
not an archive of guaranteed first-published ERA5 values. A six-day buffer is a
conservative policy, not a service-level guarantee. Baseline responses must contain
all seven dates and finish before 10:00 local time.

Recent target gaps remain empty, while calendar covariates are complete through the
forecast window. The TSFM must forecast across the archive gap before reaching the
seven requested dates. This is supported by the pipeline's last_observed convention.
The curated TSFM covariates are weekday and annual cycle only. Live agents can search
any internet sources and pick their own weather/news/marine covariates. The baseline
is itself publicly accessible; using its forecast is not prohibited.

## Keys, network and terms

The public non-commercial Open-Meteo endpoints normally require no API key. No secret
requirement is added, and no credential value belongs in Git, task.yaml or agents'
artifacts. Commercial or high-volume use requires separate review of current terms.
If a paid endpoint is needed later, follow the demo's host-only private .env/variable
pattern; do not embed a key or silently change URLs/models in the agent instructions.

Host HTTPS destinations: api.open-meteo.com, archive-api.open-meteo.com. Agents have
open networking in live mode; mock mode retains the generated archive-only rules.
Attribution: Open-Meteo, ECMWF IFS and Copernicus ERA5; preserve current license notices
when redistributing data. Complete API responses and retrieval times are archived.
The response generationtime_ms field is NOT a model issue timestamp; this task records
retrieval time and makes no claim about exact model initialization/cycle version.

## Repository fit and checks

Copied setup, verifier, oracle and image from the current ERCOT example, including its
continual variant. Added environment/weather in taxonomy.yaml because temperature is
not air quality, hydrology or natural hazards; the repository permits taxonomy changes
when a new leaf is necessary. No generated mock tasks or fetched datasets belong in PR.

From this target directory (stdlib, pandas, PyYAML):

    python -m unittest discover -s setup -v

From the sibling pipeline directory:

    uv run --project ale ale lint ../agentic-forecasting-tasks
    uv run python -m af snapshot lax_daily_tmax
    uv run python -m af truth lax_daily_tmax
    uv run python -m af baseline lax_daily_tmax "$(uv run python -m af today lax_daily_tmax)"
    scripts/ale validate ../agentic-forecasting-tasks/tasks/live/lax_daily_tmax

Snapshot/baseline commands require today before 10:00 America/Los_Angeles. The proposed
run starts 07:30, leaving time for the 90-minute agent episode. The maintainer should
choose the activation date and enable its timer after review; this PR changes no services.

## Initial validation status

Ten offline collector tests and repository-wide ALE lint passed. A synthetic-data
integration through the current pipeline verified snapshot manifests, baseline and
truth records, idempotent settlement, mock generation and database scoring of resolved
versus pending dates. Host execution with redirected sandbox paths accepted both
oracles and rejected invalid/empty submissions. These checks do not prove live access.

Live API requests were denied by this environment's proxy (403). Full `ale validate`
was attempted but failed extracting the unchanged upstream base image with Docker
`no space left on device`; no container oracle episodes completed. Revalidate on a
host with API access and sufficient Docker storage before marking the PR ready.
