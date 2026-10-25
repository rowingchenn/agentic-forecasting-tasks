# AAPL daily closing price

This target is a tradable-price control under `markets/equities`. It asks for nine
quantiles of Yahoo Finance AAPL closing prices on the next seven **calendar** days.
The latest regular-session close carries over weekends and exchange holidays. It
uses the existing pipeline's calendar-day horizons unchanged.

## Files and data flow

- `task.yaml`: target, sources, schedule, numeric inputs, and no-change reference.
- `instruction.md`: agent-facing definition and forecast/materials output contract.
- `setup/run.sh`: generic clock-based schedule; hides host collector code from agents.
- `setup/fetch.py`: host-only `snapshot`, `truth`, `baseline`; stdlib and pandas.
- `setup/test_fetch.py`: offline collector tests, using synthetic provider responses.
- `verify/`: existing shared-format verifier; no accuracy claim before settlement.
- `oracle/run.sh`: a format-only example, not an investment model.
- `image/Dockerfile`: existing pinned analysis-library image.

The collector follows the contributor's Yahoo OHLCV/Finnhub scripts, using Yahoo's
JSON chart endpoint directly to meet the stdlib+pandas host requirement. It does not
import yfinance or require a browser, Playwright, or publisher scraping. Adjusted
close is retained only for audit. Yahoo's close history can still change after splits.

Finnhub records include headlines, summaries, source URLs and publication timestamps.
Only items published within the seven-day lookback and no later than collection
start are retained. Vendor publication timestamps cannot prove original ingestion
time or absence of subsequent edits. Empty responses are retained as empty, not
claimed as complete coverage. No article body completeness is claimed.

Raw news is archived for audit; the current TSFM adapter consumes only price history
and calendar columns. Live agents independently acquire their information; the
pipeline gives mock agents materials collected by live agents, not these host-only
raw files. Supplying identical curated news to agents, or text embeddings to TSFMs,
would require a separate pipeline design change.

## Host requirements

Set `FINNHUB_API_KEY` securely in the pipeline host environment or its local `.env`.
Never commit it or include it in agent instructions. No Yahoo API key is required.
Allow outbound HTTPS to `query1.finance.yahoo.com` and `finnhub.io`; Finnhub plan
coverage and Yahoo availability/rate limits remain external requirements.

The baseline is a no-change reference, **not** an official/expert forecast. This is an
intentional departure from the existing ERCOT target: no comparable official short-term
AAPL price forecast is available. The maintainer should review that choice.

Known regular US equity holidays since 2024 and the 2025 Carter closure are handled.
Unexpected missing sessions fail explicitly; new exceptional exchange closures need
a calendar update. Early-close sessions still supply one daily bar.

## Checks

From this target directory, with pandas and PyYAML installed:

```bash
python -m unittest discover -s setup -v
```

From the sibling pipeline repository:

```bash
uv run --project ale ale lint ../agentic-forecasting-tasks/tasks/live/aapl_daily_close
uv run --project ale ale validate ../agentic-forecasting-tasks/tasks/live/aapl_daily_close
# Before 09:25 America/New_York, with live provider access and FINNHUB_API_KEY:
uv run python -m af snapshot aapl_daily_close
uv run python -m af truth aapl_daily_close
uv run python -m af baseline aapl_daily_close "$(uv run python -m af today aapl_daily_close)"
```

The collector refuses historical or late snapshots/baselines and rechecks the cutoff
when collection finishes. Truth excludes the current calendar day. The pipeline
retains the first value it observes; historical backfills are first-seen values at
backfill time, not point-in-time reconstructions of Yahoo's original publications.

The initial proposed schedule is daily at 07:45 New York time, cutoff 09:25, starting
2026-10-26. Enable scheduling and host credentials only after maintainer review.

## Initial contributor validation

ALE lint and eight offline collector tests passed. A synthetic-data integration run
through the sibling pipeline verified snapshot serialization, manifest hashes, truth
updates and baseline outputs. Full `ale validate` could not finish: extracting the
required ALE base image exhausted Docker storage. Live provider validation was also
blocked by egress restrictions, and no Finnhub key was available. These checks must
be completed on a host with sufficient Docker storage and provider access.
