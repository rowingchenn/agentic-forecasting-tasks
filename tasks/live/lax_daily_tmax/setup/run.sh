#!/usr/bin/env bash
# The agent gets the day's schedule and nothing else: task.json with the run date, target
# dates, cutoff and quantile levels, from the clock and the Task's params. fetch.py beside
# this file is the pipeline's host-side code; the agent must not read it, so this stage
# directory ends up root-only (ALE uploads it world-readable).
set -euo pipefail

# A continuing episode (ale run --continue-from) runs in the sandbox an earlier run day
# kept. Move that day's submission to /home/user/history/<its run date>/ and start a clean
# output directory, so today's artifacts hold only today's work.
if [ -f /home/user/input/task.json ] && [ -n "$(ls -A /home/user/output 2>/dev/null)" ]; then
  previous=$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))["run_date"])' /home/user/input/task.json)
  mkdir -p /home/user/history
  rm -rf "/home/user/history/$previous"
  mv /home/user/output "/home/user/history/$previous"
  install -d -o user -g user /home/user/output
  chown -R user:user /home/user/history
fi

mkdir -p /home/user/input
python3 - "$ALE_PARAMS_JSON" > /home/user/input/task.json <<'PY'
import json
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

params = json.load(open(sys.argv[1]))
zone = ZoneInfo(params["timezone"])
day = datetime.now(zone).date()
hour, minute = map(int, params["cutoff"].split(":"))
task = {
    "run_date": day.isoformat(),
    "cutoff": datetime(day.year, day.month, day.day, hour, minute, tzinfo=zone).isoformat(),
    "target_dates": [(day + timedelta(days=h)).isoformat() for h in params["horizons"]],
    "quantile_levels": params["quantile_levels"],
}
print(json.dumps(task, indent=2))
PY
chmod 0644 /home/user/input/task.json
chmod -R go-rwx "$ALE_STAGE_DIR"
