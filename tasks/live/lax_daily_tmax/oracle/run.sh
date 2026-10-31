#!/usr/bin/env bash
# A well-formed submission for `ale validate`; the live verifier checks the format only.
# The reference forecast from X_d is the oracle of each run day's mock Task.
set -euo pipefail

python3 - <<'PY'
import json
from pathlib import Path

task = json.loads(Path("/home/user/input/task.json").read_text())
flat = {str(q): 295.0 for q in task["quantile_levels"]}
out = Path("/home/user/output")
(out / "collected").mkdir(parents=True, exist_ok=True)
(out / "collected" / "index.jsonl").write_text("")
forecasts = [{"target_date": day, "quantiles": flat} for day in task["target_dates"]]
(out / "forecast.json").write_text(json.dumps({"run_date": task["run_date"], "forecasts": forecasts}))
PY
