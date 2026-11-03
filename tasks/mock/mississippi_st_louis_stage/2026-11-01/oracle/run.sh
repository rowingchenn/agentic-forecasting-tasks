#!/usr/bin/env bash
# Reference forecast from X_d (assets/x/, never shown to an agent): weekly seasonal naive
# with spread from historical week-over-week ratios.
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
ratios = (target / target.shift(7)).dropna()
forecasts = []
for day in task["target_dates"]:
    anchor = date.fromisoformat(day)
    while anchor > last:
        anchor -= timedelta(days=7)
    base = target[pd.Timestamp(anchor)]
    quantiles = {str(q): round(float(base * ratios.quantile(q)), 1) for q in task["quantile_levels"]}
    forecasts.append({"target_date": day, "quantiles": quantiles})

out = Path("/home/user/output")
(out / "collected").mkdir(parents=True, exist_ok=True)
(out / "collected" / "index.jsonl").write_text("")
(out / "forecast.json").write_text(json.dumps({"run_date": task["run_date"], "forecasts": forecasts}, indent=2))
PY
