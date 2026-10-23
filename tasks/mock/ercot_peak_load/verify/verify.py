"""Submission checks. Accuracy is scored later, once ERCOT publishes the actual values."""

import json
import math
from pathlib import Path

from ale_verify import write

TASK = json.loads(Path("/home/user/input/x/task.json").read_text())
OUTPUT = Path("/home/user/output")
COLLECTED = OUTPUT / "collected"


def forecast_problems(data: object) -> list[str]:
    if not isinstance(data, dict):
        return ["forecast.json is not an object"]
    problems = []
    if data.get("run_date") != TASK["run_date"]:
        problems.append(f"run_date must be {TASK['run_date']}")
    entries = data.get("forecasts")
    if not isinstance(entries, list) or not all(isinstance(e, dict) for e in entries):
        return [*problems, "forecasts must be a list of objects"]
    dates = [entry.get("target_date") for entry in entries]
    if sorted(map(str, dates)) != sorted(TASK["target_dates"]):
        problems.append(f"target dates must be exactly {TASK['target_dates']}, got {dates}")
    levels = [str(level) for level in TASK["quantile_levels"]]
    for entry in entries:
        quantiles = entry.get("quantiles")
        if not isinstance(quantiles, dict) or sorted(quantiles) != sorted(levels):
            problems.append(f"{entry.get('target_date')}: quantile keys must be {levels}")
            continue
        values = [quantiles[level] for level in levels]
        if not all(
            isinstance(v, int | float) and not isinstance(v, bool) and math.isfinite(v) and v > 0
            for v in values
        ):
            problems.append(f"{entry.get('target_date')}: values must be positive numbers")
        elif any(low > high for low, high in zip(values, values[1:], strict=False)):
            problems.append(f"{entry.get('target_date')}: quantiles decrease")
    return problems


def forecast_valid() -> dict:
    try:
        data = json.loads((OUTPUT / "forecast.json").read_text())
    except (OSError, ValueError) as exc:
        return {"score": 0.0, "diagnostic": f"forecast.json is unreadable: {exc}"}
    problems = forecast_problems(data)
    return {
        "score": float(not problems),
        "diagnostic": "; ".join(problems)[:2000] or "valid forecast",
    }


def materials() -> tuple[dict, int]:
    index = COLLECTED / "index.jsonl"
    try:
        lines = index.read_text().splitlines()
    except OSError as exc:
        return {"score": 0.0, "diagnostic": f"collected/index.jsonl is unreadable: {exc}"}, 0
    root = COLLECTED.resolve()
    count = 0
    for number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            return {"score": 0.0, "diagnostic": f"index.jsonl line {number} is not JSON"}, count
        path, source = entry.get("path"), entry.get("source")
        if not isinstance(path, str) or not isinstance(source, str) or not source.strip():
            return {
                "score": 0.0,
                "diagnostic": f"index.jsonl line {number} needs path and source",
            }, count
        target = (root / path).resolve()
        if root not in target.parents or not target.is_file():
            return {
                "score": 0.0,
                "diagnostic": f"line {number}: {path} is not a file in collected/",
            }, count
        count += 1
    return {"score": 1.0, "diagnostic": f"{count} materials listed"}, count


forecast_result = forecast_valid()
materials_result, material_count = materials()
write(
    {
        "overall": (forecast_result["score"] + materials_result["score"]) / 2,
        "forecast_valid": forecast_result,
        "materials_valid": materials_result,
        "materials": material_count,
    }
)
