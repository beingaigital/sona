"""Aggregate eval runs and render static regression dashboard."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Build regression dashboard from eval_results.")
    p.add_argument("--eval-root", default="eval_results", help="Eval results root directory.")
    p.add_argument("--history-file", default="eval_results/history.json", help="Output history json path.")
    p.add_argument("--dashboard-file", default="eval_results/dashboard.md", help="Output dashboard markdown path.")
    return p


def main() -> int:
    args = _parser().parse_args()
    project_root = Path(__file__).resolve().parents[1]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

    from workflow.regression_dashboard import build_diff, collect_run_summaries, render_markdown

    eval_root = Path(args.eval_root).resolve()
    history_file = Path(args.history_file).resolve()
    dashboard_file = Path(args.dashboard_file).resolve()

    history = collect_run_summaries(eval_root)
    history_payload = [h.to_dict() for h in history]

    history_file.parent.mkdir(parents=True, exist_ok=True)
    dashboard_file.parent.mkdir(parents=True, exist_ok=True)
    history_file.write_text(json.dumps(history_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    current = history[-1] if history else None
    previous = history[-2] if len(history) >= 2 else None
    diff = build_diff(current, previous) if current else {"has_previous": False, "message": "No runs found."}
    dashboard_md = render_markdown(history, diff) if current else "# Eval Regression Dashboard\n\nNo runs found.\n"
    dashboard_file.write_text(dashboard_md, encoding="utf-8")

    print(
        json.dumps(
            {
                "runs": len(history),
                "history_file": str(history_file),
                "dashboard_file": str(dashboard_file),
                "latest_run_id": current.run_id if current else "",
                "diff": diff,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

