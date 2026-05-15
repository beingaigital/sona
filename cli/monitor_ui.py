"""CLI bridge for /monitor command（专题监测）。"""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Tuple

import jieba
from rich.prompt import Prompt

from cli.display import console
from workflow.topic_monitoring_pipeline import TopicMonitoringPipeline, run_high_speed_rail_demo

_MONITOR_SUBCOMMANDS = frozenset(
    {"help", "h", "?", "demo", "list", "create", "status", "report"}
)


def _print_help() -> None:
    console.print("[cyan]/monitor[/cyan]  - 专题监测命令")
    console.print("  [cyan]/monitor help[/cyan]                 - 显示命令帮助")
    console.print("  [cyan]/monitor demo[/cyan]                 - 运行高铁舆情内存演示")
    console.print("  [cyan]/monitor list[/cyan]                 - 列出当前进程内/外部库专题")
    console.print("  [cyan]/monitor create 名称|领域|关键词1,关键词2[/cyan]  （结构化）")
    console.print("  [dim]  自然语言新建（整句即可）：[/dim][cyan]/monitor 帮我建立重大交通事故的舆情监测专题[/cyan]")
    console.print("  [cyan]/monitor status <topic_id>[/cyan]    - 查询专题状态")
    console.print("  [cyan]/monitor report <topic_id> [daily|weekly][/cyan] - 生成日报/周报")
    console.print("  [dim]  环境变量：SONA_MONITOR_SKIP_EXTRACT=1 跳过 extract 精炼；SONA_TOPIC_MONITOR_INTERVAL_HOURS 默认采集间隔。[/dim]")
    console.print("  [dim]  网察：SONA_TOPIC_MONITOR_USE_OPINION_NETINSIGHT=1 + SONA_OPINION_SYSTEM_ROOT + NETINSIGHT_USER/PASS 时，定时脚本可走 opinion-system 采集链。[/dim]")


def _parse_create_args(args: str) -> tuple[str, str, list[str]]:
    parts = [p.strip() for p in args.split("|")]
    name = parts[0] if len(parts) > 0 else ""
    domain = parts[1] if len(parts) > 1 else ""
    keywords = [k.strip() for k in parts[2].split(",")] if len(parts) > 2 else []
    return name, domain, [k for k in keywords if k]


def _looks_like_create_intent(text: str) -> bool:
    """判断整句是否像「新建监测专题」的自然语言，而非误拼的子命令。"""
    s = text.strip()
    if not s:
        return False
    if any(v in s for v in ("建立", "创建", "新建", "开通")) and any(
        x in s for x in ("专题", "监测", "舆情")
    ):
        return True
    if any(x in s for x in ("监测专题", "舆情监测", "舆情专题")):
        return True
    if "专题" in s and "舆情" in s:
        return True
    return False


def _infer_domain(core: str) -> str:
    if any(x in core for x in ("交通", "事故", "高速", "道路", "地铁", "铁路", "物流", "航运", "民航")):
        return "交通"
    if any(x in core for x in ("医", "药", "院", "疾控", "健康", "疫情", "控烟")):
        return "公共卫生"
    if any(x in core for x in ("教育", "学校", "大学", "招生")):
        return "教育"
    if any(x in core for x in ("环保", "污染", "碳", "气候")):
        return "生态环境"
    return "综合舆情"


def _keywords_from_core(core: str, domain: str) -> List[str]:
    """从主题核心短语拆出监测关键词（去重、保留顺序）。"""
    kws: List[str] = []
    seen: set[str] = set()
    noise = frozenset(
        {
            "建立",
            "创建",
            "新建",
            "开通",
            "专题",
            "监测",
            "舆情",
            "的",
            "和",
            "与",
            "一个",
            "帮我",
            "我要",
            "我想",
        }
    )

    def push(w: str) -> None:
        w = w.strip()
        if len(w) < 2 or w in seen or w in noise:
            return
        seen.add(w)
        kws.append(w)

    push(core)
    try:
        for w in jieba.lcut(core):
            push(w)
    except Exception:
        pass
    if domain == "交通":
        for extra in ("交通事故", "交通安全", "追尾"):
            if extra in core:
                push(extra)
    return kws[:16]


def _try_parse_natural_create(text: str) -> Optional[Tuple[str, str, List[str]]]:
    """
    从自然语言中解析专题名称、领域、关键词。

    例如：「帮我建立 重大交通事故的舆情监测专题」「帮重大交通事故的舆情监测专题」
    """
    s = text.strip()
    if not s or not _looks_like_create_intent(s):
        return None

    core = s
    suffixes = (
        "的舆情监测专题",
        "舆情监测专题",
        "的舆情专题",
        "舆情专题",
        "专题监测",
        "监测专题",
        "的专题",
        "的监测",
        "舆情监测",
    )
    for suf in suffixes:
        if core.endswith(suf):
            core = core[: -len(suf)].strip()
            break

    prefixes = (
        "请帮我",
        "能不能帮我",
        "麻烦帮我",
        "帮我",
        "请",
        "我想",
        "我要",
        "给建立一个",
        "给我建立",
        "给建立",
        "建立一个",
        "建立",
        "创建一个",
        "创建",
        "新建一个",
        "新建",
        "开通",
        "搞一个",
        "做一个",
    )
    changed = True
    while changed:
        changed = False
        for pre in sorted(prefixes, key=len, reverse=True):
            if core.startswith(pre):
                core = core[len(pre) :].strip()
                changed = True
                break

    # 口语里单独的「帮」+ 主题
    if core.startswith("帮") and len(core) > 1 and core[1] not in "忙":
        core = core[1:].strip()

    core = re.sub(r"[\s　，,。]+", "", core)
    core = core.strip("的")
    if len(core) < 2:
        return None

    domain = _infer_domain(core)
    if "舆情" in core or "监测" in core:
        name = f"{core}专题" if len(core) <= 28 else f"{core[:26]}…专题"
    else:
        name = f"{core}舆情监测"
        if len(name) > 40:
            name = f"{core[:32]}…监测"

    keywords = _keywords_from_core(core, domain)
    if not keywords:
        return None
    return name, domain, keywords


def _finalize_create_topic(
    pipeline: TopicMonitoringPipeline,
    *,
    name: str,
    domain: str,
    keywords: List[str],
    description: str,
    source_label: str,
    narrative_query: str | None = None,
) -> None:
    if not name or not domain or not keywords:
        console.print("[red]专题名称、领域和关键词都不能为空。[/red]")
        return

    from workflow.topic_monitoring_workflow import (
        build_default_topic_config,
        format_monitor_workflow_hints,
        refine_monitor_keywords,
    )

    final_keywords = list(keywords)
    merged_plan: dict | None = None
    nq = (narrative_query or "").strip()
    if nq and os.environ.get("SONA_MONITOR_SKIP_EXTRACT", "").strip().lower() not in ("1", "true", "yes"):
        console.print("[dim]调用 extract_search_terms（与事件分析 Step1 同源）精炼监测关键词…[/dim]")
        pack = refine_monitor_keywords(user_text=nq, seed_keywords=final_keywords)
        merged_plan = pack.get("search_plan") if isinstance(pack.get("search_plan"), dict) else {}
        if pack.get("merged_keywords"):
            final_keywords = list(pack["merged_keywords"])
        if not pack.get("used_extract"):
            err = str(pack.get("extract_error") or "").strip()
            if err:
                console.print(f"[yellow]关键词精炼未使用模型输出（{err}），已保留种子词。[/yellow]")

    topic = pipeline.create_topic(
        name=name,
        domain=domain,
        keywords=final_keywords,
        description=description,
    )
    tid = str(topic.get("id") or "")
    console.print("[green]已创建专题[/green]")
    console.print(f"名称: [bold]{name}[/bold]")
    console.print(f"领域: {domain}")
    console.print(f"关键词: {', '.join(final_keywords)}")
    console.print(f"ID: [cyan]{tid}[/cyan]")
    console.print(f"[dim]来源: {source_label}[/dim]")

    cfg = build_default_topic_config(merged_search_plan=merged_plan or {})
    cfg["monitoring_started_at"] = datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds")
    pipeline.patch_topic_config(tid, cfg)
    console.print("[dim]── 监测编排（默认全平台 + 建议间隔）──[/dim]")
    for ln in format_monitor_workflow_hints(cfg).split("\n"):
        console.print(f"[dim]{ln}[/dim]")

    pipeline.run_monitoring_cycle([tid])
    console.print(
        "[dim]已执行首轮监测周期（未注入 search_func / NetInsight 时无外链帖子，快照可能为「无新数据」占位）。[/dim]"
    )
    console.print(
        "[yellow]定时抓取：[/yellow]请用系统调度每 N 小时执行 "
        "``python3 scripts/run_topic_monitor_tick.py``（默认处理全部活跃专题），"
        "或在编排层注入 ``run_monitoring_cycle(..., search_func=...)`` 对接网察拉数。"
    )
    console.print(
        f"[cyan]下一步：[/cyan] `/monitor status {tid}` 查看状态；"
        f"`/monitor report {tid} daily` 生成舆情专报（Markdown）。"
    )


def run_monitor_command(raw_query: str | None = None) -> None:
    query = str(raw_query or "").strip()
    if not query:
        query = Prompt.ask("请输入 monitor 子命令").strip()
    if not query:
        _print_help()
        return

    parts = query.split(maxsplit=1)
    command = parts[0].lower()
    rest = parts[1].strip() if len(parts) > 1 else ""

    try:
        pipeline = TopicMonitoringPipeline()

        if command in ("help", "h", "?"):
            _print_help()
            return

        if command == "demo":
            console.print("[cyan]开始运行高铁舆情专题监测演示...[/cyan]")
            result = run_high_speed_rail_demo()
            console.print("[green]完成[/green]")
            console.print(f"专题 ID: [cyan]{result['topic'].get('id')}[/cyan]")
            console.print(f"报告路径: [cyan]{result['report']['report_path']}[/cyan]")
            return

        if command == "list":
            topics = pipeline.db.list_monitor_topics()
            if not topics:
                console.print("[yellow]当前暂无专题。可先运行 /monitor demo 或 /monitor create。[/yellow]")
                return
            console.print(f"[bold]已创建专题 ({len(topics)})[/bold]")
            for topic in topics:
                console.print(f"- {topic.get('id', '')} | {topic.get('name', '')} | {topic.get('domain', '')}")
            return

        if command == "create":
            name, domain, keywords = "", "", []
            if rest and "|" in rest:
                name, domain, keywords = _parse_create_args(rest)
            elif rest:
                parsed = _try_parse_natural_create(rest)
                if parsed:
                    name, domain, keywords = parsed
                else:
                    name, domain, keywords = _parse_create_args(rest)
            if not name:
                name = Prompt.ask("请输入专题名称").strip()
            if not domain:
                domain = Prompt.ask("请输入专题领域").strip()
            if not keywords:
                raw_keywords = Prompt.ask("请输入关键词，逗号分隔").strip()
                keywords = [k.strip() for k in raw_keywords.split(",") if k.strip()]
            desc = "由 /monitor create 创建"
            narrative = (rest or "").strip() or f"{name} {domain} {' '.join(keywords)}"
            _finalize_create_topic(
                pipeline,
                name=name,
                domain=domain,
                keywords=keywords,
                description=desc,
                source_label="/monitor create",
                narrative_query=narrative,
            )
            return

        if command == "status":
            topic_id = rest or Prompt.ask("请输入专题 ID").strip()
            if not topic_id:
                console.print("[red]需要指定专题 ID。[/red]")
                return
            status = pipeline.get_topic_status(topic_id)
            if status.get("error"):
                console.print(f"[red]{status['error']}[/red]")
                return
            topic = status.get("topic") or {}
            console.print(f"[bold]专题状态：{topic.get('name','')} ({topic_id})[/bold]")
            console.print(f"领域：{topic.get('domain','')}  描述：{topic.get('description','')}")
            console.print(f"关键词：{', '.join(k.get('keyword','') for k in status.get('keywords') or [])}")
            console.print(f"最新快照：{status.get('latest_snapshot')}")
            console.print(f"未解决告警：{len(status.get('active_alerts') or [])}")
            return

        if command == "report":
            if not rest:
                console.print("[red]/monitor report 需要专题 ID，可选 daily 或 weekly。[/red]")
                return
            args = rest.split()
            topic_id = args[0]
            period = args[1] if len(args) > 1 else "daily"
            output_dir = Path(__file__).resolve().parents[1] / "topic_monitoring_reports"
            result = pipeline.generate_periodic_report(topic_id, period=period, output_dir=output_dir)
            console.print("[green]已生成报告[/green]")
            console.print(f"报告路径: [cyan]{result['report_path']}[/cyan]")
            return

        if command not in _MONITOR_SUBCOMMANDS:
            parsed = _try_parse_natural_create(query)
            if parsed:
                name, domain, keywords = parsed
                desc = f"CLI 自然语言创建｜{query[:240]}"
                _finalize_create_topic(
                    pipeline,
                    name=name,
                    domain=domain,
                    keywords=keywords,
                    description=desc,
                    source_label="/monitor（自然语言）",
                    narrative_query=query,
                )
                return

        console.print(f"[yellow]未知 /monitor 子命令: {command}[/yellow]")
        _print_help()
    except Exception as exc:
        console.print(f"[red]/monitor 执行失败: {exc}[/red]")
        console.print(
            "[yellow]若要接 Supabase/Postgres，请配置 SUPABASE_URL/SUPABASE_KEY "
            "或 DATABASE_URL/POSTGRES_URL，并先执行 workflow/topic_monitoring_schema.sql。[/yellow]"
        )
