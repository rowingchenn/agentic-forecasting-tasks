# agentic-forecasting-tasks

ALE Tasks for live forecasting by general agents. Each forecasting target has two Tasks:

```text
tasks/
├── live/<target>/   the real-time Task: open internet, sudo, the run day's structured input
└── mock/<target>/   offline replay of one archived live day: no internet, no hosted search,
                     the same structured input plus every live agent's saved materials
```

Both follow the [ALE Task format](https://github.com/AgentsLastExam/ale/blob/main/docs/specs/task-authoring.md)
(`core/v1`). `instruction.md`, `verify/` and `oracle/` are identical between the two; only
`task.yaml`, `image/` and `setup/` differ.

## What a target Task contains

- `tasks/live/<target>/task.yaml` carries the target definition under
  `extras.agentic-forecasting`: target y and its settlement source, run schedule and cutoff,
  horizons H, quantile levels, the structured input X with its sources and history range,
  and the expert baseline B with where it comes from. The pipeline and the website read it
  from here.
- `setup/run.sh` installs the run day's data, which the pipeline stages into
  `setup/assets/` before each run (ignored by Git):
  - live: `x/` = X_d → `/home/user/input/x/` (read-only)
  - mock: `x/` and `archive/<system>/` (materials each live agent saved) →
    `/home/user/input/{x,archive}/`
- The agent writes `/home/user/output/forecast.json` (one quantile set per target date) and
  `/home/user/output/collected/` with an `index.jsonl` of the materials it found useful.
- `verify/` checks only that the submission is well-formed. Accuracy is scored after the
  real values are published, outside ALE, by the run repository.
- The mock image writes `/etc/codex/managed_config.toml` (`web_search = "disabled"`) and
  `/etc/claude-code/managed-settings.json` (deny `WebSearch`, `WebFetch`). Hosted search runs
  at the model provider, so a blocked network alone would not stop it.

## Targets

| Target | y | Run interval | Horizons | Baseline B |
|---|---|---|---|---|
| `ercot_peak_load` | ERCOT daily system peak load (MW) | daily, cutoff 10:00 CT | 1–7 days | ERCOT seven-day load forecast |

## Adding a target

1. Copy `tasks/live/ercot_peak_load` to `tasks/live/<target>` and rewrite `task.yaml`
   (keep the Task name `<target with dashes>-live` and the `extras` keys),
   `instruction.md`, and the oracle.
2. Copy the mock Task the same way, keeping the shared files identical.
3. Add `af/targets/<target>.py` and a timer in the run repository.

`just lint` checks the Tasks with the engine from the sibling run repository
(`../agentic-forecasting/ale`).
