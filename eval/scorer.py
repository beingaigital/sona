"""自动评分器 - 支持结构、字段、证据、数值一致性等多维度评分"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any, Union
from dataclasses import dataclass, field
from enum import Enum


class ScoreLevel(Enum):
    """评分等级"""
    EXCELLENT = "excellent"  # 90-100
    GOOD = "good"            # 80-89
    PASS = "pass"            # 60-79
    FAIL = "fail"            # 0-59


@dataclass
class ScoreItem:
    """单个评分项"""
    name: str
    score: float
    max_score: float
    passed: bool
    issues: List[str] = field(default_factory=list)
    details: Dict[str, Any] = field(default_factory=dict)
    
    @property
    def percentage(self) -> float:
        return (self.score / self.max_score * 100) if self.max_score > 0 else 0
    
    @property
    def level(self) -> ScoreLevel:
        p = self.percentage
        if p >= 90:
            return ScoreLevel.EXCELLENT
        elif p >= 80:
            return ScoreLevel.GOOD
        elif p >= 60:
            return ScoreLevel.PASS
        else:
            return ScoreLevel.FAIL


@dataclass
class ScoreResult:
    """评分结果"""
    case_id: str
    total_score: float
    max_score: float
    status: str  # "passed" or "failed"
    dimensions: Dict[str, ScoreItem]
    summary: str
    recommendations: List[str]
    
    @property
    def percentage(self) -> float:
        return (self.total_score / self.max_score * 100) if self.max_score > 0 else 0
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典格式"""
        return {
            "case_id": self.case_id,
            "score": self.total_score,
            "max_score": self.max_score,
            "percentage": round(self.percentage, 2),
            "status": self.status,
            "summary": self.summary,
            "recommendations": self.recommendations,
            "breakdown": {
                name: {
                    "score": item.score,
                    "max_score": item.max_score,
                    "percentage": round(item.percentage, 2),
                    "passed": item.passed,
                    "level": item.level.value,
                    "issues": item.issues,
                    "details": item.details
                }
                for name, item in self.dimensions.items()
            }
        }


class AutoScorer:
    """自动评分器"""
    
    # 默认评分权重配置
    DEFAULT_WEIGHTS = {
        "structure": 25,    # 结构完整性
        "fields": 25,       # 字段完整性
        "evidence": 25,     # 证据充分性
        "consistency": 25,  # 数值一致性
    }
    
    # 报告必需的结构章节
    REQUIRED_SECTIONS = [
        ("摘要", ["摘要", "概述", "简介", "总结"]),
        ("事件", ["事件", "背景", "回顾", "经过"]),
        ("舆情", ["舆情", "舆论", "反响", "反应", "评价"]),
        ("分析", ["分析", "解读", "研判"]),
        ("趋势", ["趋势", "预测", "展望", "走向"]),
        ("建议", ["建议", "对策", "措施", "方案"]),
    ]
    
    # 舆情报告必需的数据字段
    REQUIRED_FIELDS = [
        ("event_title", ["事件标题", "标题", "事件名称"]),
        ("time_range", ["时间范围", "时间", "时段", "周期"]),
        ("data_volume", ["数据量", "样本量", "信息量", "声量"]),
        ("sentiment", ["情感", "情绪", "态度", "评价"]),
        ("key_points", ["要点", "重点", "关键点", "核心"]),
        ("sources", ["来源", "渠道", "平台", "媒体"]),
    ]
    
    # 证据类型标记词
    EVIDENCE_MARKERS = {
        "data": ["数据显示", "据统计", "数据表明", "数据显示", "样本显示"],
        "quote": ["表示", "指出", "认为", "强调", "称", "说", "提到"],
        "source": ["据", "来自", "来源于", "引用自", "参考"],
        "example": ["例如", "比如", "如", "案例", "实例"],
    }
    
    def __init__(self, weights: Optional[Dict[str, float]] = None):
        """
        初始化评分器
        
        Args:
            weights: 各维度权重配置，默认使用 DEFAULT_WEIGHTS
        """
        self.weights = weights or self.DEFAULT_WEIGHTS.copy()
        self._validate_weights()
    
    def _validate_weights(self):
        """验证权重配置"""
        total = sum(self.weights.values())
        if abs(total - 100) > 0.01:
            # 自动归一化
            factor = 100 / total
            for key in self.weights:
                self.weights[key] *= factor
    
    def score(
        self,
        report_content: str,
        report_data: Optional[Dict[str, Any]] = None,
        case_config: Optional[Dict[str, Any]] = None
    ) -> ScoreResult:
        """
        对报告进行完整评分
        
        Args:
            report_content: 报告文本内容
            report_data: 报告结构化数据（JSON）
            case_config: 案例配置（期望字段、红线等）
            
        Returns:
            ScoreResult: 评分结果
        """
        case_id = case_config.get("case_id", "unknown") if case_config else "unknown"
        dimensions = {}
        
        # 1. 结构评分
        structure_item = self._score_structure(report_content, case_config)
        dimensions["structure"] = structure_item
        
        # 2. 字段评分
        fields_item = self._score_fields(report_content, report_data, case_config)
        dimensions["fields"] = fields_item
        
        # 3. 证据评分
        evidence_item = self._score_evidence(report_content, case_config)
        dimensions["evidence"] = evidence_item
        
        # 4. 数值一致性评分
        consistency_item = self._score_consistency(report_content, report_data, case_config)
        dimensions["consistency"] = consistency_item
        
        # 计算总分
        total_score = sum(item.score for item in dimensions.values())
        max_score = sum(self.weights.values())
        
        # 判断是否通过（总分>=60且各维度>=50%）
        passed = (
            total_score >= max_score * 0.6 and
            all(item.percentage >= 50 for item in dimensions.values())
        )
        
        # 生成总结和建议
        summary = self._generate_summary(dimensions, total_score, max_score)
        recommendations = self._generate_recommendations(dimensions)
        
        return ScoreResult(
            case_id=case_id,
            total_score=round(total_score, 2),
            max_score=max_score,
            status="passed" if passed else "failed",
            dimensions=dimensions,
            summary=summary,
            recommendations=recommendations
        )
    
    def _score_structure(
        self,
        report: str,
        case_config: Optional[Dict[str, Any]]
    ) -> ScoreItem:
        """
        结构完整性评分
        
        检查报告是否包含必需的章节结构
        """
        max_score = self.weights.get("structure", 25)
        issues = []
        details = {"found_sections": [], "missing_sections": []}
        
        # 自定义章节要求
        custom_sections = case_config.get("required_sections", []) if case_config else []
        sections_to_check = custom_sections if custom_sections else self.REQUIRED_SECTIONS
        
        score_per_section = max_score / len(sections_to_check)
        score = 0
        
        for section_name, keywords in sections_to_check:
            found = any(kw in report for kw in keywords)
            if found:
                score += score_per_section
                details["found_sections"].append(section_name)
            else:
                details["missing_sections"].append(section_name)
                issues.append(f"缺少必需章节: {section_name}")
        
        # 检查章节顺序（简单检查：摘要应该在前面）
        if "摘要" in report and "建议" in report:
            abstract_pos = report.find("摘要")
            suggestion_pos = report.find("建议")
            if abstract_pos > suggestion_pos:
                issues.append("章节顺序异常：摘要出现在建议之后")
                score -= score_per_section * 0.5
        
        # 检查报告长度
        char_count = len(report)
        details["char_count"] = char_count
        if char_count < 500:
            issues.append(f"报告内容过短 ({char_count} 字符)")
            score *= 0.5
        elif char_count < 1000:
            issues.append(f"报告内容偏短 ({char_count} 字符)")
            score *= 0.8
        
        return ScoreItem(
            name="structure",
            score=max(0, score),
            max_score=max_score,
            passed=score >= max_score * 0.6,
            issues=issues,
            details=details
        )
    
    def _score_fields(
        self,
        report: str,
        report_data: Optional[Dict[str, Any]],
        case_config: Optional[Dict[str, Any]]
    ) -> ScoreItem:
        """
        字段完整性评分
        
        检查报告是否包含必需的数据字段
        """
        max_score = self.weights.get("fields", 25)
        issues = []
        details = {"found_fields": [], "missing_fields": [], "field_coverage": {}}
        
        # 自定义字段要求
        custom_fields = case_config.get("required_fields", []) if case_config else []
        fields_to_check = custom_fields if custom_fields else self.REQUIRED_FIELDS
        
        score_per_field = max_score / len(fields_to_check)
        score = 0
        
        for field_name, keywords in fields_to_check:
            # 在文本中查找
            found_in_text = any(kw in report for kw in keywords)
            # 在结构化数据中查找
            found_in_data = False
            if report_data:
                found_in_data = (
                    field_name in report_data or
                    any(kw in str(report_data) for kw in keywords)
                )
            
            found = found_in_text or found_in_data
            coverage = 0
            
            if found_in_text:
                coverage += 50
            if found_in_data:
                coverage += 50
            
            details["field_coverage"][field_name] = coverage
            
            if found:
                # 根据覆盖度给分
                field_score = score_per_field * (coverage / 100)
                score += field_score
                details["found_fields"].append(field_name)
                
                if coverage < 100:
                    issues.append(f"字段 '{field_name}' 覆盖不完整 ({coverage}%)")
            else:
                details["missing_fields"].append(field_name)
                issues.append(f"缺少必需字段: {field_name}")
        
        # 检查数据完整性（如果有结构化数据）
        if report_data:
            empty_fields = [k for k, v in report_data.items() if v in [None, "", [], {}]]
            if empty_fields:
                issues.append(f"存在空值字段: {', '.join(empty_fields[:5])}")
                score *= 0.9
        
        return ScoreItem(
            name="fields",
            score=max(0, score),
            max_score=max_score,
            passed=score >= max_score * 0.6,
            issues=issues,
            details=details
        )
    
    def _score_evidence(
        self,
        report: str,
        case_config: Optional[Dict[str, Any]]
    ) -> ScoreItem:
        """
        证据充分性评分
        
        检查报告中的论据是否有数据、引用、来源支撑
        """
        max_score = self.weights.get("evidence", 25)
        issues = []
        details = {"evidence_stats": {}, "paragraph_analysis": []}
        
        # 统计各类证据标记
        evidence_counts = {}
        for ev_type, markers in self.EVIDENCE_MARKERS.items():
            count = sum(report.count(m) for m in markers)
            evidence_counts[ev_type] = count
        
        details["evidence_stats"] = evidence_counts
        
        # 分析段落结构
        paragraphs = [p.strip() for p in report.split('\n\n') if p.strip()]
        claim_count = 0
        evidence_supported = 0
        
        for para in paragraphs:
            # 识别论点（包含判断性词汇的句子）
            if any(kw in para for kw in ["是", "为", "有", "存在", "表明", "显示"]):
                claim_count += 1
                # 检查是否有证据支持
                has_evidence = any(
                    marker in para 
                    for markers in self.EVIDENCE_MARKERS.values() 
                    for marker in markers
                )
                if has_evidence:
                    evidence_supported += 1
                
                details["paragraph_analysis"].append({
                    "length": len(para),
                    "has_claim": True,
                    "has_evidence": has_evidence
                })
        
        details["claim_count"] = claim_count
        details["evidence_supported"] = evidence_supported
        
        # 计算证据支持率
        if claim_count > 0:
            support_rate = evidence_supported / claim_count
        else:
            support_rate = 0
        
        details["support_rate"] = support_rate
        
        # 评分计算
        score = 0
        
        # 数据引用（40%）
        data_score = min(evidence_counts.get("data", 0) / 3, 1) * max_score * 0.4
        score += data_score
        
        # 观点引用（30%）
        quote_score = min(evidence_counts.get("quote", 0) / 5, 1) * max_score * 0.3
        score += quote_score
        
        # 来源标注（20%）
        source_score = min(evidence_counts.get("source", 0) / 3, 1) * max_score * 0.2
        score += source_score
        
        # 证据支持率（10%）
        support_score = support_rate * max_score * 0.1
        score += support_score
        
        # 生成问题提示
        if evidence_counts.get("data", 0) < 2:
            issues.append("数据引用不足，建议增加统计数据支撑")
        if evidence_counts.get("source", 0) < 2:
            issues.append("来源标注不足，建议明确信息来源")
        if support_rate < 0.5:
            issues.append(f"论点证据支持率较低 ({support_rate*100:.1f}%)")
        
        return ScoreItem(
            name="evidence",
            score=round(score, 2),
            max_score=max_score,
            passed=score >= max_score * 0.6,
            issues=issues,
            details=details
        )
    
    def _score_consistency(
        self,
        report: str,
        report_data: Optional[Dict[str, Any]],
        case_config: Optional[Dict[str, Any]]
    ) -> ScoreItem:
        """
        数值一致性评分
        
        检查报告中的数值是否前后一致、是否符合逻辑
        """
        max_score = self.weights.get("consistency", 25)
        issues = []
        details = {"numbers_found": [], "inconsistencies": [], "checks": {}}
        
        # 提取报告中的所有数值
        numbers = self._extract_numbers(report)
        details["numbers_found"] = numbers[:20]  # 只记录前20个
        
        score = max_score
        
        # 1. 检查时间一致性
        time_check = self._check_time_consistency(report)
        details["checks"]["time"] = time_check
        if not time_check["passed"]:
            issues.extend(time_check["issues"])
            score -= max_score * 0.25
        
        # 2. 检查数值逻辑
        logic_check = self._check_number_logic(report, numbers)
        details["checks"]["logic"] = logic_check
        if not logic_check["passed"]:
            issues.extend(logic_check["issues"])
            score -= max_score * 0.25
        
        # 3. 检查文本与数据一致性
        if report_data:
            data_check = self._check_text_data_consistency(report, report_data)
            details["checks"]["data"] = data_check
            if not data_check["passed"]:
                issues.extend(data_check["issues"])
                score -= max_score * 0.25
        
        # 4. 检查百分比计算
        percent_check = self._check_percentage_calculation(report)
        details["checks"]["percentage"] = percent_check
        if not percent_check["passed"]:
            issues.extend(percent_check["issues"])
            score -= max_score * 0.25
        
        return ScoreItem(
            name="consistency",
            score=max(0, score),
            max_score=max_score,
            passed=score >= max_score * 0.6,
            issues=issues,
            details=details
        )
    
    def _extract_numbers(self, text: str) -> List[Dict[str, Any]]:
        """提取文本中的数值"""
        numbers = []
        
        # 匹配模式：数字 + 单位
        patterns = [
            (r'(\d+(?:\.\d+)?)\s*%', 'percentage'),
            (r'(\d+(?:\.\d+)?)\s*万', 'wan'),
            (r'(\d+(?:\.\d+)?)\s*亿', 'yi'),
            (r'(\d{4})\s*年', 'year'),
            (r'(\d+)\s*月', 'month'),
            (r'(\d+)\s*日', 'day'),
            (r'(\d+(?:\.\d+)?)', 'number'),
        ]
        
        for pattern, num_type in patterns:
            for match in re.finditer(pattern, text):
                numbers.append({
                    "value": match.group(1),
                    "type": num_type,
                    "context": text[max(0, match.start()-10):min(len(text), match.end()+10)],
                    "position": match.start()
                })
        
        return numbers
    
    def _check_time_consistency(self, report: str) -> Dict[str, Any]:
        """检查时间描述的一致性"""
        issues = []
        
        # 提取时间范围描述
        time_patterns = [
            r'(\d{4}[年/-]\d{1,2}[月/-]\d{1,2})',
            r'(\d{4}[年/-]\d{1,2})',
            r'(近?\d+天|近?\d+周|近?\d+个月?|近?\d+年)',
        ]
        
        time_refs = []
        for pattern in time_patterns:
            time_refs.extend(re.findall(pattern, report))
        
        # 检查是否有矛盾的时间描述
        if "近一周" in report and "近一个月" in report:
            issues.append("时间范围描述矛盾：同时出现'近一周'和'近一个月'")
        
        return {
            "passed": len(issues) == 0,
            "issues": issues,
            "time_refs": time_refs[:5]
        }
    
    def _check_number_logic(self, report: str, numbers: List[Dict]) -> Dict[str, Any]:
        """检查数值逻辑合理性"""
        issues = []
        
        # 检查百分比总和
        percentages = []
        for num in numbers:
            if num["type"] == "percentage":
                try:
                    percentages.append(float(num["value"]))
                except:
                    pass
        
        # 如果提到"占比"、"比例"等，检查百分比是否合理
        if any(kw in report for kw in ["占比", "比例", "分布", "构成"]):
            if percentages and sum(percentages) > 105:
                issues.append(f"百分比总和超过100%: {sum(percentages):.1f}%")
        
        # 检查数据量合理性
        data_volume_match = re.search(r'(\d+(?:\.\d+)?)\s*(万|亿)?\s*(?:条|篇|个)?', report)
        if data_volume_match:
            volume = float(data_volume_match.group(1))
            unit = data_volume_match.group(2) or ""
            if unit == "万":
                volume *= 10000
            elif unit == "亿":
                volume *= 100000000
            
            if volume < 10:
                issues.append(f"数据量过少 ({volume})，可能影响分析代表性")
        
        return {
            "passed": len(issues) == 0,
            "issues": issues,
            "percentages": percentages[:5]
        }
    
    def _check_text_data_consistency(
        self,
        report: str,
        report_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """检查文本描述与结构化数据的一致性"""
        issues = []
        
        # 检查数据量是否一致
        if "data_volume" in report_data:
            data_vol = report_data["data_volume"]
            # 在文本中查找数据量描述
            vol_patterns = [
                rf'{data_vol}',
                rf'{data_vol/10000:.1f}\s*万',
            ]
            found = any(re.search(p, report) for p in vol_patterns)
            if not found:
                issues.append(f"文本中未找到数据量 ({data_vol}) 的对应描述")
        
        # 检查情感倾向是否一致
        if "sentiment" in report_data:
            sentiment = report_data["sentiment"]
            sentiment_keywords = {
                "positive": ["积极", "正面", "乐观", "好评"],
                "negative": ["消极", "负面", "悲观", "差评"],
                "neutral": ["中性", "客观", "平稳"]
            }
            
            # 简单检查
            for sent_type, keywords in sentiment_keywords.items():
                if sentiment == sent_type:
                    if not any(kw in report for kw in keywords):
                        issues.append(f"情感倾向 ({sentiment}) 在文本中缺乏对应描述")
        
        return {
            "passed": len(issues) == 0,
            "issues": issues
        }
    
    def _check_percentage_calculation(self, report: str) -> Dict[str, Any]:
        """检查百分比计算是否正确"""
        issues = []
        
        # 查找 "A占B的X%" 或 "A为B的X%" 模式
        patterns = [
            r'(\d+(?:\.\d+)?)\s*[万亿]?\s*.*占.*(\d+(?:\.\d+)?)\s*[万亿]?\s*.*的\s*(\d+(?:\.\d+)?)%',
            r'占比.*?([\d\.]+)%',
        ]
        
        for pattern in patterns:
            matches = re.findall(pattern, report)
            # 这里可以添加更复杂的计算验证
        
        return {
            "passed": len(issues) == 0,
            "issues": issues,
            "checked_patterns": len(patterns)
        }
    
    def _generate_summary(self, dimensions: Dict[str, ScoreItem], total: float, max_total: float) -> str:
        """生成评分总结"""
        percentage = (total / max_total * 100) if max_total > 0 else 0
        
        if percentage >= 90:
            level = "优秀"
        elif percentage >= 80:
            level = "良好"
        elif percentage >= 60:
            level = "合格"
        else:
            level = "不合格"
        
        # 找出最弱的维度
        weakest = min(dimensions.items(), key=lambda x: x[1].percentage)
        strongest = max(dimensions.items(), key=lambda x: x[1].percentage)
        
        summary = f"报告质量评级：{level}（{percentage:.1f}%）。"
        summary += f"最强维度：{strongest[0]}（{strongest[1].percentage:.1f}%）；"
        summary += f"最弱维度：{weakest[0]}（{weakest[1].percentage:.1f}%）。"
        
        failed_dims = [name for name, item in dimensions.items() if not item.passed]
        if failed_dims:
            summary += f"未达标维度：{', '.join(failed_dims)}。"
        
        return summary
    
    def _generate_recommendations(self, dimensions: Dict[str, ScoreItem]) -> List[str]:
        """生成改进建议"""
        recommendations = []
        
        for name, item in dimensions.items():
            if not item.passed:
                if name == "structure":
                    recommendations.append("完善报告结构，确保包含摘要、事件、舆情、分析、趋势、建议等章节")
                elif name == "fields":
                    recommendations.append("补充关键数据字段，如事件标题、时间范围、数据量、情感倾向等")
                elif name == "evidence":
                    recommendations.append("增加数据引用和来源标注，提升论据可信度")
                elif name == "consistency":
                    recommendations.append("检查数值逻辑一致性，确保时间、百分比等描述准确无误")
            elif item.percentage < 80:
                # 虽然通过但仍有改进空间
                if name == "evidence" and item.details.get("evidence_stats", {}).get("data", 0) < 3:
                    recommendations.append("建议增加更多统计数据支撑论点")
        
        return recommendations


def score_report(
    report_content: str,
    report_data: Optional[Dict[str, Any]] = None,
    case_config: Optional[Dict[str, Any]] = None,
    weights: Optional[Dict[str, float]] = None
) -> Dict[str, Any]:
    """
    便捷函数：对报告进行评分
    
    Args:
        report_content: 报告文本内容
        report_data: 报告结构化数据
        case_config: 案例配置
        weights: 评分权重
        
    Returns:
        评分结果字典
    """
    scorer = AutoScorer(weights)
    result = scorer.score(report_content, report_data, case_config)
    return result.to_dict()
