"""Wiki Markdown 编译器（Schema v2.0）

将 wiki markdown 文件编译为结构化 JSON，支持：
1. 解析 YAML frontmatter
2. 提取章节内容
3. 转换为 Schema v2.0 定义的8种知识类型
4. 自动生成唯一ID和统一元数据字段
"""

from __future__ import annotations

import json
import re
import uuid
import datetime as dt
from pathlib import Path
from typing import Any, Dict, List, Optional

import frontmatter
from .definitions import (
    Case,
    Methodology,
    DomainPlaybook,
    Actor,
    RiskPattern,
    ResponseTactic,
    Evidence,
    Scenario,
    generate_id,
    validate_and_parse,
)


def normalize_confidence(value: Any) -> float:
    """将旧版 high/medium/low 或数值字符串转换为 0-1 置信度。"""
    if value is None or value == "":
        return 0.8
    if isinstance(value, (int, float)):
        return max(0.0, min(1.0, float(value)))
    if isinstance(value, str):
        confidence_map = {
            "high": 0.9,
            "medium": 0.6,
            "low": 0.3,
        }
        normalized = value.strip().lower()
        if normalized in confidence_map:
            return confidence_map[normalized]
        try:
            return max(0.0, min(1.0, float(normalized)))
        except ValueError:
            return 0.8
    return 0.8


def parse_markdown(file_path: Path) -> Dict[str, Any]:
    """解析 markdown 文件，提取 frontmatter 和正文"""
    content = file_path.read_text(encoding='utf-8')
    post = frontmatter.loads(content)
    
    metadata = {}
    for key, value in post.metadata.items():
        if isinstance(value, dt.datetime):
            metadata[key] = value.isoformat()
        elif isinstance(value, (dt.date, dt.time)):
            metadata[key] = str(value)
        else:
            metadata[key] = value
    
    return {
        "metadata": metadata,
        "content": post.content,
        "filename": file_path.name,
        "file_path": str(file_path),
    }


def extract_sections(content: str) -> Dict[str, str]:
    """提取 markdown 章节（## 开头的二级标题）"""
    sections = {}
    pattern = r'^##\s+(.+?)$\n([\s\S]*?)(?=^##|\Z)'
    
    for match in re.finditer(pattern, content, re.MULTILINE):
        title = match.group(1).strip()
        body = match.group(2).strip()
        sections[title] = body
    
    return sections


def parse_list_field(sections: Dict, *keywords: str) -> List[str]:
    """从章节中解析列表字段，支持多个关键词匹配"""
    for keyword in keywords:
        if keyword in sections:
            content = sections[keyword]
            # 尝试解析列表项
            items = []
            # 匹配 - 或 * 开头的列表项
            for line in content.split('\n'):
                line = line.strip()
                if line.startswith('- ') or line.startswith('* ') or line.startswith('1. '):
                    items.append(line[2:].strip())
                elif line and not line.startswith('#'):
                    # 如果不是列表格式，作为单个项目
                    if not items:
                        items.append(line)
            return items[:10]  # 最多取10个
    return []


def classify_by_content(metadata: Dict, sections: Dict) -> str:
    """根据内容分类知识类型"""
    source_type = str(metadata.get('source_type', '')).lower()
    title = str(metadata.get('title', ''))
    
    if source_type == 'method':
        return 'Methodology'
    if '案例' in title or '事件' in title:
        return 'Case'
    if '指南' in title or '手册' in title:
        return 'DomainPlaybook'
    if '主体' in title or '机构' in title or '媒体' in title:
        return 'Actor'
    if '风险' in title:
        return 'RiskPattern'
    if '策略' in title or '建议' in title or '回应' in title:
        return 'ResponseTactic'
    
    return 'Case'


def extract_case_fields(metadata: Dict, sections: Dict) -> Dict[str, Any]:
    """从章节中提取 Case 特有的字段（支持旧章节到新字段的弱映射）"""
    return {
        "subdomain": metadata.get('subdomain'),
        "scenario": metadata.get('scenario'),
        "event_type": metadata.get('event_type'),
        "platforms": metadata.get('platforms', []),
        # 弱映射：关键事实 → trigger_points / key_claims 的候选来源
        "trigger_points": parse_list_field(sections, '触发点', '事件起因', '导火索', '关键事实'),
        "key_claims": parse_list_field(sections, '核心诉求', '网民质疑', '公众关切', '争议焦点', '关键事实'),
        "public_sentiment": sections.get('情绪与立场', sections.get('公众情绪', ''))[:500] if any(k in sections for k in ['情绪与立场', '公众情绪']) else None,
        # 弱映射：传播机制 → escalation_path
        "escalation_path": sections.get('升级路径', sections.get('传播路径', sections.get('传播机制', '')))[:500] if any(k in sections for k in ['升级路径', '传播路径', '传播机制']) else None,
        "turning_points": parse_list_field(sections, '转折点', '关键节点'),
        # 弱映射：可复用方法论 → lessons_learned
        "lessons_learned": parse_list_field(sections, '经验教训', '启示', '建议', '可复用方法论'),
        "official_response": sections.get('官方回应', sections.get('回应情况', ''))[:500] if any(k in sections for k in ['官方回应', '回应情况']) else None,
        "resolution": sections.get('事件结果', sections.get('处置结果', ''))[:500] if any(k in sections for k in ['事件结果', '处置结果']) else None,
        "severity": metadata.get('severity', 'medium'),
        "impact_scope": metadata.get('impact_scope'),
        "start_date": metadata.get('start_date'),
        "end_date": metadata.get('end_date'),
    }


def extract_risk_pattern_fields(metadata: Dict, sections: Dict) -> Dict[str, Any]:
    """从章节中提取 RiskPattern 特有的字段（支持旧章节到新字段的弱映射）"""
    return {
        "risk_category": metadata.get('risk_category'),
        "applicable_scenarios": parse_list_field(sections, '适用场景', '适用范围', '应用场景'),
        # 弱映射：风险点 → indicators / trigger_conditions / early_warning_signals
        "indicators": parse_list_field(sections, '风险指标', '指标', '信号指标', '风险点'),
        "trigger_conditions": parse_list_field(sections, '触发条件', '触发因素', '触发场景', '风险点'),
        "early_warning_signals": parse_list_field(sections, '预警信号', '早期信号', '前兆', '风险点'),
        # 弱映射：传播机制 → typical_escalation_path
        "typical_escalation_path": sections.get('传播机制', sections.get('升级路径', ''))[:500] if any(k in sections for k in ['传播机制', '升级路径']) else None,
        # 弱映射：情绪与立场 → related_emotions
        "related_emotions": parse_list_field(sections, '相关情绪', '情绪类型', '情绪与立场'),
        "severity": metadata.get('severity', 'medium'),
        # 弱映射：可复用方法论 → recommended_tactics
        "recommended_tactics": parse_list_field(sections, '应对策略', '推荐策略', '处置建议', '可复用方法论'),
        # 弱映射：相似议题线索 → example_cases
        "example_cases": parse_list_field(sections, '案例', '示例', '典型案例', '相似议题线索'),
    }


def extract_response_tactic_fields(metadata: Dict, sections: Dict) -> Dict[str, Any]:
    """从章节中提取 ResponseTactic 特有的字段（支持旧章节到新字段的弱映射）"""
    return {
        "tactic_type": metadata.get('tactic_type'),
        "applicable_scenarios": parse_list_field(sections, '适用场景', '适用范围'),
        "applicable_risk_patterns": parse_list_field(sections, '适用风险', '针对风险'),
        "applicable_actors": parse_list_field(sections, '适用主体', '适用对象'),
        "stage": metadata.get('stage'),
        "preconditions": parse_list_field(sections, '前置条件', '前提条件'),
        # 弱映射：可复用方法论 → actions
        "actions": parse_list_field(sections, '行动清单', '具体措施', '步骤', '可复用方法论'),
        "message_templates": parse_list_field(sections, '话术模板', '回应模板', '口径'),
        "do_and_dont": parse_do_and_dont(sections),
        "success_metrics": parse_list_field(sections, '成功指标', '评估指标', '效果指标'),
        # 弱映射：风险点 → failure_modes
        "failure_modes": parse_list_field(sections, '失败模式', '风险点', '注意事项'),
        # 弱映射：相似议题线索 → example_cases
        "example_cases": parse_list_field(sections, '案例', '示例', '应用案例', '相似议题线索'),
    }


def parse_do_and_dont(sections: Dict) -> Optional[Dict]:
    """解析可以做/不可以做字段"""
    do_items = []
    dont_items = []
    
    if '可以做' in sections:
        do_items = parse_list_field(sections, '可以做')
    if '不可以做' in sections:
        dont_items = parse_list_field(sections, '不可以做')
    if '禁忌' in sections:
        dont_items.extend(parse_list_field(sections, '禁忌'))
    if '注意事项' in sections and '失败模式' not in sections:
        dont_items.extend(parse_list_field(sections, '注意事项'))
    
    if do_items or dont_items:
        return {
            "do": do_items,
            "dont": dont_items
        }
    return None


def markdown_to_structured(
    file_path: Path,
    domain: Optional[str] = None
) -> Dict[str, Any]:
    """将单个 markdown 文件转换为结构化数据（Schema v2.0）"""
    parsed = parse_markdown(file_path)
    metadata = parsed['metadata']
    sections = extract_sections(parsed['content'])
    
    knowledge_type = classify_by_content(metadata, sections)
    
    # 生成唯一ID
    prefix_map = {
        'Case': 'case',
        'Methodology': 'meth',
        'DomainPlaybook': 'play',
        'Actor': 'act',
        'RiskPattern': 'risk',
        'ResponseTactic': 'tactic',
        'Evidence': 'evid',
        'Scenario': 'scen',
    }
    obj_id = generate_id(prefix_map.get(knowledge_type, 'kb'))
    
    # 统一元数据字段
    result = {
        "id": obj_id,
        "type": knowledge_type,
        "domain": domain or metadata.get('domain', ''),
        "tags": metadata.get('tags', []),
        "source_path": parsed['file_path'],
        "source_url": metadata.get('source', ''),
        "evidence_refs": [],
        "confidence": normalize_confidence(metadata.get('confidence', 0.8)),
        "status": metadata.get('status', 'candidate'),
        "created_at": metadata.get('created_at', ''),
        "updated_at": metadata.get('updated_at', ''),
        "last_verified_at": None,
    }
    
    title = str(metadata.get('title', file_path.stem))

    # 类型特有字段
    if knowledge_type == 'Case':
        result.update({
            "title": title,
            "summary": sections.get('事件概述', sections.get('摘要', ''))[:500] if any(k in sections for k in ['事件概述', '摘要']) else '',
            "actors": metadata.get('entities', []),
            "timeline": sections.get('事件概述', sections.get('时间线', ''))[:500] if any(k in sections for k in ['事件概述', '时间线']) else '',
            **extract_case_fields(metadata, sections),
        })
    elif knowledge_type == 'Methodology':
        result.update({
            "name": title,
            "description": sections.get('可复用方法论', '')[:500] if '可复用方法论' in sections else '',
            "keywords": metadata.get('tags', []),
        })
    elif knowledge_type == 'DomainPlaybook':
        result.update({
            "subdomains": [],
            "scenario_type": None,
            "risk_level": "medium",
            "response_stages": [],
            "stakeholders": [],
            "recommended_tactics": [],
            "do_and_dont": None,
            "example_cases": [],
            "evaluation_metrics": [],
            "references": [],
        })
    elif knowledge_type == 'Actor':
        result.update({
            "name": title,
            "aliases": [],
            "actor_type": metadata.get('actor_type', 'organization'),
            "description": '',
            "domain_relevance": [],
            "region": None,
            "platforms": [],
            "influence_level": "medium",
            "credibility": "medium",
        })
    elif knowledge_type == 'RiskPattern':
        result.update({
            "name": title,
            "description": sections.get('风险点', sections.get('描述', ''))[:500] if any(k in sections for k in ['风险点', '描述']) else '',
            **extract_risk_pattern_fields(metadata, sections),
        })
    elif knowledge_type == 'ResponseTactic':
        result.update({
            "name": title,
            "description": sections.get('可复用方法论', sections.get('描述', ''))[:500] if any(k in sections for k in ['可复用方法论', '描述']) else '',
            **extract_response_tactic_fields(metadata, sections),
        })
    
    return result


def generate_field_coverage_report(output_dir: Path) -> Dict[str, Any]:
    """统计增强字段覆盖率，辅助判断旧资料映射和新模板填写质量。"""
    output_dir = Path(output_dir)
    ignored_files = {
        "compile_report.json",
        "compile_errors.json",
        "field_coverage_report.json",
    }
    enhanced_fields = {
        "Case": [
            "trigger_points",
            "key_claims",
            "public_sentiment",
            "escalation_path",
            "turning_points",
            "lessons_learned",
            "official_response",
            "resolution",
        ],
        "RiskPattern": [
            "trigger_conditions",
            "early_warning_signals",
            "typical_escalation_path",
            "recommended_tactics",
        ],
        "ResponseTactic": [
            "actions",
            "failure_modes",
            "example_cases",
            "message_templates",
            "success_metrics",
        ],
    }

    type_counts: Dict[str, int] = {}
    field_stats: Dict[str, Dict[str, Dict[str, Any]]] = {
        knowledge_type: {
            field: {
                "exists_count": 0,
                "nonempty_count": 0,
                "nonempty_rate": 0.0,
            }
            for field in fields
        }
        for knowledge_type, fields in enhanced_fields.items()
    }
    invalid_files = []

    for json_file in sorted(output_dir.glob("*.json")):
        if json_file.name in ignored_files:
            continue
        try:
            data = json.loads(json_file.read_text(encoding="utf-8"))
        except Exception as exc:
            invalid_files.append({
                "path": str(json_file),
                "error": str(exc),
            })
            continue

        knowledge_type = data.get("type", "Unknown")
        type_counts[knowledge_type] = type_counts.get(knowledge_type, 0) + 1
        if knowledge_type not in field_stats:
            continue

        for field, stats in field_stats[knowledge_type].items():
            if field in data:
                stats["exists_count"] += 1
            if data.get(field):
                stats["nonempty_count"] += 1

    for knowledge_type, fields in field_stats.items():
        total = type_counts.get(knowledge_type, 0)
        for stats in fields.values():
            stats["nonempty_rate"] = round(stats["nonempty_count"] / total, 4) if total else 0.0

    report = {
        "output_dir": str(output_dir),
        "type_counts": type_counts,
        "field_stats": field_stats,
        "invalid_files": invalid_files,
        "notes": [
            "exists_count 表示字段结构是否存在。",
            "nonempty_count 表示字段是否有实际内容。",
            "旧 wiki 文档弱映射填充率有限，新领域资料应按 domains/template 模板显式填写。",
        ],
    }
    report_path = output_dir / "field_coverage_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def compile_wiki_directory(
    input_dir: Path,
    output_dir: Path,
    domain: Optional[str] = None,
    clean: bool = False,
) -> List[str]:
    """编译整个 wiki 目录，并输出可核验的编译报告。

    Args:
        input_dir: Markdown 输入目录。
        output_dir: JSON 输出目录。
        domain: 可选领域标记。
        clean: 是否先清理输出目录中的旧 JSON，避免重复编译残留。
    """
    input_dir = Path(input_dir)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    if clean:
        for old_json in output_dir.glob("*.json"):
            old_json.unlink()
    
    md_files = sorted(input_dir.rglob("*.md"))
    compiled_files = []
    errors = []
    type_counts: Dict[str, int] = {}
    
    for md_file in md_files:
        try:
            structured = markdown_to_structured(md_file, domain)
            validate_and_parse(structured)
            output_file = output_dir / f"{structured['id']}.json"
            output_file.write_text(json.dumps(structured, ensure_ascii=False, indent=2), encoding='utf-8')
            compiled_files.append(str(output_file))
            knowledge_type = structured.get("type", "Unknown")
            type_counts[knowledge_type] = type_counts.get(knowledge_type, 0) + 1
        except Exception as e:
            errors.append({
                "source_path": str(md_file),
                "filename": md_file.name,
                "error": str(e),
            })

    report = {
        "input_dir": str(input_dir),
        "output_dir": str(output_dir),
        "source_markdown_count": len(md_files),
        "compiled_count": len(compiled_files),
        "failed_count": len(errors),
        "type_counts": type_counts,
        "clean_before_compile": clean,
        "compiled_files": compiled_files,
    }
    report_path = output_dir / "compile_report.json"
    errors_path = output_dir / "compile_errors.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    errors_path.write_text(json.dumps(errors, ensure_ascii=False, indent=2), encoding='utf-8')
    coverage_report = generate_field_coverage_report(output_dir)
    coverage_path = output_dir / "field_coverage_report.json"
    
    if errors:
        print(f"编译完成，{len(compiled_files)} 成功，{len(errors)} 失败")
        print(f"编译报告: {report_path}")
        print(f"错误报告: {errors_path}")
        print(f"字段覆盖率报告: {coverage_path}")
        for err in errors[:20]:
            print(f"  - {err['filename']}: {err['error']}")
        if len(errors) > 20:
            print(f"  ... 还有 {len(errors) - 20} 条错误，详见 {errors_path}")
    else:
        print(f"编译完成，全部 {len(compiled_files)} 个文件成功")
        print(f"编译报告: {report_path}")
        print(f"错误报告: {errors_path}")
        print(f"字段覆盖率报告: {coverage_path}")
    
    return compiled_files


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="编译 wiki markdown 为 Schema v2.0 JSON")
    parser.add_argument("input_dir", type=Path, help="输入 markdown 目录")
    parser.add_argument("output_dir", type=Path, help="输出 JSON 目录")
    parser.add_argument("domain", nargs="?", default=None, help="可选领域，例如 health/transport/panda")
    parser.add_argument("--clean", action="store_true", help="编译前清理输出目录中的旧 JSON")
    args = parser.parse_args()

    compile_wiki_directory(args.input_dir, args.output_dir, args.domain, clean=args.clean)
