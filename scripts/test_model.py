"""测试模型工厂：询问模型是哪一款模型。"""

from __future__ import annotations

import sys
import argparse
from pathlib import Path

# 保证项目根在 path 中
_root = Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from langchain_core.messages import HumanMessage
from model.factory import ModelFactory


def main() -> None:
    """测试模型工厂，询问模型是哪一款模型。"""
    parser = argparse.ArgumentParser(
        description="测试模型工厂：询问模型是哪一款模型",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 使用默认 main profile
  python scripts/test_model.py

  # 指定 profile
  python scripts/test_model.py --profile tools

  # 指定 provider 和 model
  python scripts/test_model.py --provider openai --model gpt-4o-mini

  # 指定 provider 和 model（覆盖 profile 配置）
  python scripts/test_model.py --profile main --provider gemini --model gemini-1.5-flash
        """
    )
    parser.add_argument(
        "--profile",
        type=str,
        default="main",
        help="配置 profile（main/tools/report），默认: main"
    )
    parser.add_argument(
        "--provider",
        type=str,
        default=None,
        help="覆盖 provider（openai/gemini/qwen/deepseek）"
    )
    parser.add_argument(
        "--model",
        type=str,
        default=None,
        help="覆盖 model（如 gpt-4o-mini, qwen-plus 等）"
    )
    
    args = parser.parse_args()
    
    try:
        # 使用模型工厂创建模型实例
        print(f"正在创建模型实例...")
        print(f"  Profile: {args.profile}")
        if args.provider:
            print(f"  Provider: {args.provider} (覆盖配置)")
        if args.model:
            print(f"  Model: {args.model} (覆盖配置)")
        print()
        
        llm = ModelFactory.create(
            profile=args.profile,
            provider=args.provider,
            model=args.model
        )
        
        # 获取实际使用的模型信息
        model_name = getattr(llm, "model_name", None) or getattr(llm, "model", None) or "未知"
        provider_name = getattr(llm, "_llm_type", None) or llm.__class__.__name__ or "未知"
        
        print(f"✓ 模型实例创建成功")
        print(f"  模型名称: {model_name}")
        print(f"  模型类型: {provider_name}")
        print()
        
        # 询问模型
        print("正在询问模型: 你是哪一款模型？")
        print("-" * 60)
        
        messages = [HumanMessage(content="你是哪一款模型？")]
        response = llm.invoke(messages)
        
        # 打印回答
        content = response.content if hasattr(response, "content") else str(response)
        print(f"模型回答:")
        print(content)
        print("-" * 60)
        
    except ValueError as e:
        print(f"❌ 配置错误: {e}", file=sys.stderr)
        sys.exit(1)
    except ImportError as e:
        print(f"❌ 导入错误: {e}", file=sys.stderr)
        print("提示: 请确保已安装所需的依赖包", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"❌ 发生错误: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
