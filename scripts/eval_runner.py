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
    parser.add_argument(
        "--exit-zero",
        action="store_true",
        help="Always exit with status 0 even when cases fail (default: exit 1 on any failure).",
    )
    parser.add_argument(
        "--strict-warnings",
        action="store_true",
        help="Treat scorer warnings as failures (exit 4 if any case status is warning).",
    )
    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    env_mode = os.getenv("EVAL_MODE")
    if args.mode is None and env_mode in {"live", "replay"}:
        args.mode = env_mode

    env_strict = os.getenv("EVAL_STRICT_WARNINGS", "").strip().lower() in {"1", "true", "yes"}
    strict_warnings = bool(args.strict_warnings or env_strict)

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

    if args.suite and int(summary.get("total_cases") or 0) == 0:
        print(
            "[ERROR] eval gate: no cases matched the requested suite "
            f"{args.suite!r} (misconfiguration or missing case tags).",
            file=sys.stderr,
        )
        return 3

    failures = int(summary.get("fail_cases") or 0)
    if failures > 0 and not args.exit_zero:
        ci = summary.get("ci_report") if isinstance(summary.get("ci_report"), dict) else {}
        blockers = ci.get("blockers") if isinstance(ci.get("blockers"), list) else []
        print(
            "[ERROR] eval gate failed: "
            f"{failures} case(s) with status=fail. See ci_report.blockers in JSON output "
            f"or eval_results/{summary.get('run_id', '')}/ci_report.json.",
            file=sys.stderr,
        )
        for b in blockers:
            if not isinstance(b, dict):
                continue
            cid = b.get("case_id")
            reasons = b.get("fail_reasons") or []
            print(f"  - {cid}: {reasons}", file=sys.stderr)
        return 1

    warn_count = int(summary.get("warning_cases") or 0)
    if warn_count > 0 and strict_warnings and not args.exit_zero:
        print(
            "[ERROR] eval gate: strict-warnings enabled but "
            f"{warn_count} case(s) ended with status=warning. "
            "See per-case fail_reasons in metrics (warning reasons) or summary.results.",
            file=sys.stderr,
        )
        for item in summary.get("results") or []:
            if not isinstance(item, dict):
                continue
            if item.get("status") != "warning":
                continue
            print(f"  - {item.get('case_id')}: {item.get('fail_reasons')}", file=sys.stderr)
        return 4

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

