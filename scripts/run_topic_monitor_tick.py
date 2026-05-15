#!/usr/bin/env python3
"""专题监测定时轮询入口（供 cron / systemd 每 N 小时调用）。

默认：列出内存或 Postgres 中的**活跃**专题，对每个专题执行 ``run_monitoring_cycle``。

若设置 ``SONA_TOPIC_MONITOR_USE_OPINION_NETINSIGHT=1`` 且已配置 ``SONA_OPINION_SYSTEM_ROOT``、
``NETINSIGHT_USER`` / ``NETINSIGHT_PASS``，则通过 ``workflow/topic_netinsight_adapter`` 动态加载
opinion-system 的 ``src.netinsight.client``，按与 opinion-system worker 相同的多平台计数 +
配额拉取 + 去重逻辑采集数据，再写入 Sona 专题监测流水线。

用法::

    cd /path/to/sona-master && python3 scripts/run_topic_monitor_tick.py

cron（每 6 小时）::

    0 */6 * * * cd /path/to/sona-master && /usr/bin/python3 scripts/run_topic_monitor_tick.py >> /var/log/sona_topic_monitor.log 2>&1
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from workflow.topic_monitoring_pipeline import TopicMonitoringPipeline  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="专题监测定时 tick")
    parser.add_argument(
        "--topic-id",
        action="append",
        dest="topic_ids",
        default=None,
        help="仅处理指定专题 ID（可重复传入）；缺省则处理全部活跃专题",
    )
    args = parser.parse_args()

    pipeline = TopicMonitoringPipeline()
    topics = pipeline.db.list_monitor_topics(is_active=True)
    want = {str(x).strip() for x in (args.topic_ids or []) if str(x).strip()}
    if want:
        ids = [str(t.get("id")) for t in topics if str(t.get("id")) in want]
    else:
        ids = [str(t.get("id")) for t in topics if t.get("id")]

    if not ids:
        print(json.dumps({"ok": True, "message": "无活跃专题，跳过", "processed": []}, ensure_ascii=False))
        return 0

    from workflow.topic_netinsight_adapter import (
        build_opinion_netinsight_search_func,
        topic_monitor_use_opinion_netinsight,
    )

    search_func = None
    if topic_monitor_use_opinion_netinsight():
        try:
            search_func = build_opinion_netinsight_search_func(pipeline)
        except Exception as exc:  # noqa: BLE001
            print(json.dumps({"ok": False, "error": f"构建 NetInsight search_func 失败: {exc}"}, ensure_ascii=False))
            return 2

    out = pipeline.run_monitoring_cycle(ids, search_func=search_func)
    print(json.dumps({"ok": True, "topic_ids": ids, "netinsight": bool(search_func), "results": out.get("results", [])}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
