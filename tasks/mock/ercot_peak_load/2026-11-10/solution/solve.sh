#!/usr/bin/env bash
# Reference forecast from X_d (assets/x/, never shown to an agent): weekly seasonal naive
# with spread from historical changes over seven days (daily or weekly rows alike): ratios
# for a positive series, differences for one that can be zero or negative, so the
# quantiles always increase.
set -euo pipefail

python3 - assets/x <<'PY'
import json
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

x = Path(sys.argv[1])
task = json.loads((x / "task.json").read_text())
target = pd.read_csv(x / "series.csv", index_col="timestamp", parse_dates=True)["target"]
last = date.fromisoformat(task["last_observed"])
observed = target.dropna()
week_before = observed.shift(freq="7D").reindex(observed.index)
positive = bool((observed > 0).all())
changes = (observed / week_before if positive else observed - week_before).dropna()
forecasts = []
for day in task["target_dates"]:
    anchor = date.fromisoformat(day)
    while anchor > last or pd.isna(target.get(pd.Timestamp(anchor))):  # sources skip days
        anchor -= timedelta(days=7)
    base = float(target[pd.Timestamp(anchor)])
    values = sorted(
        base * changes.quantile(q) if positive else base + changes.quantile(q)
        for q in task["quantile_levels"]
    )
    quantiles = {str(q): float(f"{v:.6g}") for q, v in zip(task["quantile_levels"], values)}
    forecasts.append({"target_date": day, "quantiles": quantiles})

out = Path("/home/user/output")
(out / "collected").mkdir(parents=True, exist_ok=True)
(out / "collected" / "index.jsonl").write_text("")
(out / "forecast.json").write_text(json.dumps({"run_date": task["run_date"], "forecasts": forecasts}, indent=2))
PY
