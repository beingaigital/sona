"""CLI wrapper for Day1 evaluation harness."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run evaluation harness cases.")
    parser.add_argument("--target", choices=["workflow", "tool", "wiki"], help="Filter by target type.")
    parser.add_argument("--stage", help="Filter by stage name.")
    parser.add_argument("--case", dest="case_id", help="Run only one case by case id.")
    parser.add_argument("--suite", help="Suite selector (reserved for Day2).")
    parser.add_argument("--mode", choices=["live", "replay"], help="Filter by fixture mode.")
    parser.add_argument("--run-id", help="Optional fixed run id for deterministic diff/testing.")
    parser.add_argument(
        "--deterministic",
        action="store_true",
        help="Enable deterministic replay behavior (fixed time/random/latency).",
    )
    parser.add_argument(
        "--strict-warnings",
        action="store_true",
        help="Treat warning cases as CI failure (exit code 4).",
    )
    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parents[1]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

    try:
        from tests.evals.runner import run_evaluation
    except Exception as exc:  # pragma: no cover - import guard
        print(f"[ERROR] failed to import eval runner: {exc}")
        return 2

    env_mode = os.environ.get("EVAL_MODE")
    resolved_mode = args.mode or (env_mode if env_mode in {"live", "replay"} else None)
    env_det = os.environ.get("EVAL_DETERMINISTIC", "").strip().lower() in {"1", "true", "yes", "on"}
    resolved_deterministic = bool(args.deterministic or env_det)

    summary = run_evaluation(
        project_root=project_root,
        target=args.target,
        stage=args.stage,
        case_id=args.case_id,
        suite=args.suite,
        mode=resolved_mode,
        run_id=args.run_id,
        deterministic=resolved_deterministic,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if args.strict_warnings and int(summary.get("warning_cases") or 0) > 0:
        return 4
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

