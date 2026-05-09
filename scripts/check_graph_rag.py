"""Graph RAG 自检脚本：检查配置读取、Neo4j 连通性与基础数据可用性。"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.graph_rag_query import _get_neo4j_driver, _get_neo4j_settings  # noqa: E402


def _safe_count(tx_result: Any) -> int:
    try:
        record = tx_result.single()
        if not record:
            return 0
        return int(record.get("count", 0) or 0)
    except Exception:
        return 0


def main() -> None:
    """打印 Graph RAG 当前可用性诊断结果。"""
    uri, user, password = _get_neo4j_settings()
    masked_password = "*" * len(password) if password else ""

    print("=" * 72)
    print("Graph RAG 自检")
    print("=" * 72)
    print(f"项目根目录: {PROJECT_ROOT}")
    print(f"Neo4j URI: {uri}")
    print(f"Neo4j User: {user}")
    print(f"Neo4j Password: {masked_password}")
    print("-" * 72)

    driver = _get_neo4j_driver()
    if driver is None:
        print("❌ Neo4j 驱动不可用，请先安装 neo4j Python 包。")
        sys.exit(1)

    try:
        with driver.session() as session:
            ping = session.run("RETURN 1 AS ok")
            ping_record = ping.single()
            ok = int(ping_record.get("ok", 0) or 0) if ping_record else 0
            print(f"✅ 数据库连接成功: RETURN 1 -> {ok}")

            counts: Dict[str, int] = {
                "all_nodes": _safe_count(session.run("MATCH (n) RETURN count(n) AS count")),
                "all_relationships": _safe_count(session.run("MATCH ()-[r]->() RETURN count(r) AS count")),
                "case_candidates": _safe_count(
                    session.run(
                        "MATCH (n) WHERE any(lbl IN labels(n) WHERE lbl IN $labels) RETURN count(n) AS count",
                        {"labels": ["Case", "PublicOpinionCase", "EventCase", "Incident", "CaseStudy"]},
                    )
                ),
                "theory_candidates": _safe_count(
                    session.run(
                        "MATCH (n) WHERE any(lbl IN labels(n) WHERE lbl IN $labels) RETURN count(n) AS count",
                        {"labels": ["Theory", "Framework", "Methodology", "Rule"]},
                    )
                ),
                "indicator_candidates": _safe_count(
                    session.run(
                        "MATCH (n) WHERE any(lbl IN labels(n) WHERE lbl IN $labels) RETURN count(n) AS count",
                        {
                            "labels": [
                                "AnalysisMethod",
                                "Indicator",
                                "Metric",
                                "Dimension",
                                "Signal",
                                "Feature",
                                "Index",
                            ]
                        },
                    )
                ),
            }

            print("基础数据统计:")
            print(json.dumps(counts, ensure_ascii=False, indent=2))

            if counts["all_nodes"] == 0:
                print("⚠️ Neo4j 已启动，但数据库是空的；Graph RAG 仍然查不到结果。")
            elif counts["case_candidates"] == 0 and counts["theory_candidates"] == 0 and counts["indicator_candidates"] == 0:
                print("⚠️ Neo4j 有数据，但未发现 Graph RAG 约定的案例/理论/指标节点标签。")
            else:
                print("✅ Graph RAG 所需基础数据已存在，可以继续在 Sona 中调用。")

    except Exception as e:
        print(f"❌ Graph RAG 连接/查询失败: {e}")
        sys.exit(2)
    finally:
        try:
            driver.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
