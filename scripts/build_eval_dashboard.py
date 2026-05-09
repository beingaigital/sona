"""Build static regression dashboard from eval_results."""

from __future__ import annotations

import json
from pathlib import Path
from statistics import median
from typing import Any, Dict, List


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _collect_runs(eval_root: Path) -> List[Dict[str, Any]]:
    runs: List[Dict[str, Any]] = []
    for d in sorted([x for x in eval_root.iterdir() if x.is_dir()]):
        summary = _read_json(d / "summary.json")
        if not summary:
            continue
        results = summary.get("results") if isinstance(summary.get("results"), list) else []
        latencies = []
        for item in results:
            if not isinstance(item, dict):
                continue
            metrics = item.get("metrics") if isinstance(item.get("metrics"), dict) else {}
            lat = metrics.get("latency_ms", 0)
            try:
                latencies.append(int(lat))
            except Exception:
                pass
        p50 = int(median(latencies)) if latencies else 0
        p95 = sorted(latencies)[max(0, int(len(latencies) * 0.95) - 1)] if latencies else 0
        runs.append(
            {
                "run_id": str(summary.get("run_id") or d.name),
                "timestamp": str(summary.get("timestamp") or ""),
                "total_cases": int(summary.get("total_cases") or 0),
                "pass_rate": float(summary.get("pass_rate") or 0.0),
                "fallback_rate": float(summary.get("fallback_rate") or 0.0),
                "p50_latency_ms": p50,
                "p95_latency_ms": int(p95),
                "budget_trigger_count": int(
                    ((summary.get("budget_summary") if isinstance(summary.get("budget_summary"), dict) else {}).get("trigger_count") or 0)
                ),
            }
        )
    return runs


def _write_markdown(path: Path, rows: List[Dict[str, Any]]) -> None:
    lines = [
        "# Eval Regression Dashboard",
        "",
        "| run_id | timestamp | total | pass_rate | fallback_rate | p50(ms) | p95(ms) | budget_triggers |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows[-30:]:
        lines.append(
            f"| {row['run_id']} | {row['timestamp']} | {row['total_cases']} | {row['pass_rate']:.2f} | {row['fallback_rate']:.2f} | {row['p50_latency_ms']} | {row['p95_latency_ms']} | {row['budget_trigger_count']} |"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    project_root = Path(__file__).resolve().parents[1]
    eval_root = project_root / "eval_results"
    if not eval_root.exists():
        print("eval_results not found")
        return 1
    rows = _collect_runs(eval_root)
    out_root = project_root / "eval_results" / "_dashboard"
    out_root.mkdir(parents=True, exist_ok=True)
    (out_root / "dashboard.json").write_text(json.dumps({"runs": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_markdown(out_root / "dashboard.md", rows)
    print(str(out_root / "dashboard.md"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
