"""运行舆情专题监测与预警。"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run monitor tasks once.")
    parser.add_argument("--config", help="Config file path, default config/config.yaml")
    parser.add_argument("--task-id", help="Run one specific monitor task")
    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parents[1]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

    module_path = project_root / "tools" / "monitoring.py"
    spec = importlib.util.spec_from_file_location("sona_monitoring", module_path)
    if spec is None or spec.loader is None:
        print("[ERROR] failed to load monitoring module")
        return 2
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    run_monitoring = module.run_monitoring

    result = run_monitoring(config_path=args.config, task_id=args.task_id)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
