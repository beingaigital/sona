"""CLI wrapper for Day1 evaluation harness."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run evaluation harness cases.")
    parser.add_argument("--target", choices=["workflow", "tool", "wiki"], help="Filter by target type.")
    parser.add_argument("--stage", help="Filter by stage name.")
    parser.add_argument("--case", dest="case_id", help="Run only one case by case id.")
    parser.add_argument("--suite", help="Suite selector (reserved for Day2).")
    parser.add_argument("--mode", choices=["live", "replay"], help="Filter by fixture mode.")
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

    summary = run_evaluation(
        project_root=project_root,
        target=args.target,
        stage=args.stage,
        case_id=args.case_id,
        suite=args.suite,
        mode=args.mode,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

