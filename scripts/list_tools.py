"""查看当前 Agent 工具注册表：名称、描述、参数。"""

from __future__ import annotations

import sys
from pathlib import Path

# 保证项目根在 path 中
_root = Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from agent.reactagent import AGENT_TOOLS


def main() -> None:
    print("当前工具注册表（Agent 使用的工具列表）\n")
    print("-" * 60)
    for i, t in enumerate(AGENT_TOOLS, 1):
        name = getattr(t, "name", t.__class__.__name__)
        desc = getattr(t, "description", "") or ""
        args_schema = getattr(t, "args_schema", None)
        args_str = ""
        if args_schema:
            try:
                schema = None
                if hasattr(args_schema, "model_json_schema"):
                    schema = args_schema.model_json_schema()
                elif hasattr(args_schema, "schema"):
                    schema = args_schema.schema()
                if isinstance(schema, dict):
                    props = schema.get("properties", {})
                    args_str = ", ".join(f"{k}: {v.get('type', 'any')}" for k, v in props.items())
            except Exception:
                args_str = "(无法解析)"
        if not args_str and args_schema:
            args_str = str(args_schema)[:80]
        print(f"[{i}] {name}")
        print(f"    描述: {desc.strip()[:200]}{'...' if len(desc) > 200 else ''}")
        if args_str:
            print(f"    参数: {args_str}")
        print("-" * 60)
    print(f"共 {len(AGENT_TOOLS)} 个工具。")


if __name__ == "__main__":
    main()
