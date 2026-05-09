"""Day9: CI quality gate for eval results."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _latest_run(eval_root: Path) -> Path:
    candidates = [x for x in eval_root.iterdir() if x.is_dir() and (x / "summary.json").exists()]
    if not candidates:
        raise FileNotFoundError("No eval run found under eval_results/")
    return sorted(candidates)[-1]


def _filter_by_suite(summary: Dict[str, Any], suite: str) -> Dict[str, Any]:
    results = summary.get("results") if isinstance(summary.get("results"), list) else []
    filtered: List[Dict[str, Any]] = []
    suite_norm = suite.strip().lower()
    for item in results:
        if not isinstance(item, dict):
            continue
        case_id = str(item.get("case_id") or "").strip().lower()
        if case_id.startswith(suite_norm):
            filtered.append(item)
    total_cases = len(filtered)
    pass_cases = sum(1 for item in filtered if str(item.get("status") or "") == "pass")
    warning_cases = sum(1 for item in filtered if str(item.get("status") or "") == "warning")
    fail_cases = sum(1 for item in filtered if str(item.get("status") or "") == "fail")
    fallback_rate = float(warning_cases) / float(total_cases) if total_cases else 0.0
    pass_rate = float(pass_cases) / float(total_cases) if total_cases else 0.0
    merged = dict(summary)
    merged["results"] = filtered
    merged["total_cases"] = total_cases
    merged["pass_cases"] = pass_cases
    merged["warning_cases"] = warning_cases
    merged["fail_cases"] = fail_cases
    merged["pass_rate"] = pass_rate
    merged["fallback_rate"] = fallback_rate
    return merged


def _collect_blockers(summary: Dict[str, Any], *, min_pass_rate: float, max_fallback_rate: float) -> List[str]:
    blockers: List[str] = []
    pass_rate = float(summary.get("pass_rate") or 0.0)
    fallback_rate = float(summary.get("fallback_rate") or 0.0)
    total_cases = int(summary.get("total_cases") or 0)

    if total_cases <= 0:
        blockers.append("B_NO_CASES: total_cases=0")
    if pass_rate < min_pass_rate:
        blockers.append(f"B_PASS_RATE_LOW: {pass_rate:.3f} < {min_pass_rate:.3f}")
    if fallback_rate > max_fallback_rate:
        blockers.append(f"B_FALLBACK_RATE_HIGH: {fallback_rate:.3f} > {max_fallback_rate:.3f}")

    results = summary.get("results") if isinstance(summary.get("results"), list) else []
    for item in results:
        if not isinstance(item, dict):
            continue
        status = str(item.get("status") or "")
        case_id = str(item.get("case_id") or "unknown")
        fail_reasons = item.get("fail_reasons") if isinstance(item.get("fail_reasons"), list) else []
        if status == "fail":
            blockers.append(f"B_CASE_FAILED: {case_id}")
        for reason in fail_reasons:
            if isinstance(reason, str) and ("required field" in reason.lower() or "missing" in reason.lower()):
                blockers.append(f"B_SCHEMA_BREAK: {case_id}: {reason}")
    return blockers


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate gate for latest run or specified run.")
    parser.add_argument("--run-id", help="Specific run id under eval_results/")
    parser.add_argument("--latest", action="store_true", help="Gate on latest run under eval_results/")
    parser.add_argument("--suite", help="Optional suite prefix filter by case_id (e.g. workflow)")
    parser.add_argument("--min-pass-rate", type=float, default=0.85)
    parser.add_argument("--max-fallback-rate", type=float, default=0.20)
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    eval_root = root / "eval_results"
    if args.run_id and args.latest:
        print(json.dumps({"ok": False, "error": "Use either --run-id or --latest, not both."}, ensure_ascii=False))
        return 2
    run_dir = eval_root / args.run_id if args.run_id else _latest_run(eval_root)
    summary = _read_json(run_dir / "summary.json")
    if not summary:
        print(json.dumps({"ok": False, "error": "summary.json missing or invalid", "run_dir": str(run_dir)}, ensure_ascii=False))
        return 2
    if args.suite:
        summary = _filter_by_suite(summary, args.suite)

    blockers = _collect_blockers(
        summary,
        min_pass_rate=float(args.min_pass_rate),
        max_fallback_rate=float(args.max_fallback_rate),
    )
    report = {
        "ok": len(blockers) == 0,
        "run_id": str(summary.get("run_id") or run_dir.name),
        "run_dir": str(run_dir),
        "suite": str(args.suite or ""),
        "pass_rate": float(summary.get("pass_rate") or 0.0),
        "fallback_rate": float(summary.get("fallback_rate") or 0.0),
        "total_cases": int(summary.get("total_cases") or 0),
        "blockers": blockers,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
