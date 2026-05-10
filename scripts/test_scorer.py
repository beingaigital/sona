"""测试自动评分器功能"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).resolve().parents[1]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from eval.scorer import AutoScorer, score_report


def test_basic_scoring():
    """测试基础评分功能"""
    print("=" * 60)
    print("测试 1: 基础评分功能")
    print("=" * 60)
    
    # 测试报告内容
    report_content = """
# 舆情分析报告：测试事件

## 摘要
本报告针对测试事件进行舆情分析。据统计，相关讨论量达1.2万条。

## 事件回顾
事件起因于2024年1月15日，某品牌发布新产品，引发网友热议。

## 舆情分析
数据显示，正面评价占比45%，负面评价占比30%，中性评价占比25%。
专家表示，该产品设计创新，但定价偏高。

## 趋势判断
预测未来一周舆情将持续发酵，建议密切关注。

## 建议
1. 加强信息监测
2. 及时回应公众关切
3. 做好风险预案
"""
    
    report_data = {
        "event_title": "测试事件",
        "time_range": "2024-01-15 至 2024-01-22",
        "data_volume": 12000,
        "sentiment": "neutral"
    }
    
    case_config = {
        "case_id": "test_case_001",
        "domain": "测试领域"
    }
    
    # 运行评分
    result = score_report(report_content, report_data, case_config)
    
    # 打印结果
    print(f"\n案例 ID: {result['case_id']}")
    print(f"总分: {result['score']}/{result['max_score']} ({result['percentage']}%)")
    print(f"状态: {result['status']}")
    print(f"\n总结: {result['summary']}")
    
    print("\n各维度评分:")
    for dim_name, dim_data in result['breakdown'].items():
        print(f"  {dim_name}:")
        print(f"    得分: {dim_data['score']}/{dim_data['max_score']} ({dim_data['percentage']}%)")
        print(f"    状态: {'通过' if dim_data['passed'] else '未通过'}")
        if dim_data['issues']:
            print(f"    问题: {dim_data['issues']}")
    
    print("\n改进建议:")
    for rec in result['recommendations']:
        print(f"  - {rec}")
    
    return result


def test_low_quality_report():
    """测试低质量报告评分"""
    print("\n" + "=" * 60)
    print("测试 2: 低质量报告评分")
    print("=" * 60)
    
    # 低质量报告（缺少多个章节）
    report_content = """
这是一个简短的报告。

事件发生了，很多人讨论。
结束。
"""
    
    report_data = {}
    
    case_config = {
        "case_id": "test_case_002",
        "domain": "测试领域"
    }
    
    result = score_report(report_content, report_data, case_config)
    
    print(f"\n案例 ID: {result['case_id']}")
    print(f"总分: {result['score']}/{result['max_score']} ({result['percentage']}%)")
    print(f"状态: {result['status']}")
    
    print("\n各维度评分:")
    for dim_name, dim_data in result['breakdown'].items():
        print(f"  {dim_name}: {dim_data['score']}/{dim_data['max_score']} ({dim_data['percentage']}%)")
    
    print("\n发现的问题:")
    for dim_name, dim_data in result['breakdown'].items():
        if dim_data['issues']:
            print(f"  [{dim_name}]")
            for issue in dim_data['issues']:
                print(f"    - {issue}")
    
    return result


def test_custom_weights():
    """测试自定义权重"""
    print("\n" + "=" * 60)
    print("测试 3: 自定义权重")
    print("=" * 60)
    
    report_content = """
## 摘要
测试报告摘要。

## 事件
事件描述。

## 舆情
舆情分析。

## 分析
深度分析。

## 趋势
趋势预测。

## 建议
建议内容。
"""
    
    # 自定义权重：更重视结构
    custom_weights = {
        "structure": 40,
        "fields": 20,
        "evidence": 20,
        "consistency": 20
    }
    
    case_config = {
        "case_id": "test_case_003",
        "domain": "测试领域"
    }
    
    scorer = AutoScorer(weights=custom_weights)
    result = scorer.score(report_content, {}, case_config)
    result_dict = result.to_dict()
    
    print(f"\n自定义权重: {custom_weights}")
    print(f"总分: {result_dict['score']}/{result_dict['max_score']}")
    
    print("\n各维度权重分布:")
    for dim_name, dim_data in result_dict['breakdown'].items():
        weight = custom_weights.get(dim_name, 25)
        print(f"  {dim_name}: 权重 {weight}%, 得分 {dim_data['percentage']:.1f}%")
    
    return result_dict


def test_runner_integration():
    """测试与 runner 集成"""
    print("\n" + "=" * 60)
    print("测试 4: Runner 集成")
    print("=" * 60)
    
    from eval.runner import run_evaluation
    
    results_dir = project_root / "eval" / "results"
    
    summary = run_evaluation(
        project_root=project_root,
        output_dir=results_dir
    )
    
    print(f"\n评测完成!")
    print(f"总案例数: {summary['total_cases']}")
    print(f"通过: {summary['passed_cases']}")
    print(f"失败: {summary['failed_cases']}")
    print(f"错误: {summary['error_cases']}")
    print(f"平均分: {summary['average_score']}")
    
    # 列出生成的文件
    if results_dir.exists():
        print(f"\n生成的文件:")
        for f in sorted(results_dir.glob("*.json")):
            print(f"  - {f.name}")
    
    return summary


def main():
    """运行所有测试"""
    print("\n" + "=" * 60)
    print("Sona 自动评分器测试")
    print("=" * 60)
    
    try:
        # Test 1: 基础评分
        result1 = test_basic_scoring()
        
        # Test 2: 低质量报告
        result2 = test_low_quality_report()
        
        # Test 3: 自定义权重
        result3 = test_custom_weights()
        
        # Test 4: Runner 集成
        result4 = test_runner_integration()
        
        print("\n" + "=" * 60)
        print("所有测试完成!")
        print("=" * 60)
        
        # 保存测试结果
        test_results = {
            "basic_scoring": result1,
            "low_quality": result2,
            "custom_weights": result3,
            "runner_summary": result4
        }
        
        output_file = project_root / "eval" / "results" / "test_results.json"
        output_file.parent.mkdir(parents=True, exist_ok=True)
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(test_results, f, ensure_ascii=False, indent=2)
        
        print(f"\n测试结果已保存: {output_file}")
        
    except Exception as e:
        print(f"\n[ERROR] 测试失败: {e}")
        import traceback
        traceback.print_exc()
        return 1
    
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
