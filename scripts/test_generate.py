#!/usr/bin/env python3
"""
Generate 模块测试 — 取 remixed.json 第一条创意，生成视频 + 文案。
用法:
    python scripts/test_generate.py                # 标准模式，生成 1 条
    python scripts/test_generate.py --dry-run       # 只测试文案生成，不调视频 API
    python scripts/test_generate.py --mode draft    # 样片预览模式
"""
import argparse
import asyncio
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from modules.config import get_config
from modules.generate.copy_gen import generate_copy
from modules.generate.volcengine_video import generate_video, MODE_DEFAULT, MODE_DRAFT, MODE_FLEX


def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        handlers=[logging.StreamHandler(sys.stdout)],
    )


def load_first_idea() -> dict:
    cfg = get_config()
    data_dir = Path(cfg.get("output", {}).get("data_dir", "./data"))
    today = datetime.now().strftime("%Y-%m-%d")
    remix_path = data_dir / "daily" / today / "remixed.json"
    if not remix_path.exists():
        print(f"❌ 未找到 {remix_path}")
        sys.exit(1)
    with open(remix_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    ideas = data.get("ideas", [])
    if not ideas:
        print("❌ remixed.json 中没有创意方案")
        sys.exit(1)
    return ideas[0]


async def test_copy(idea: dict):
    """测试文案生成"""
    print("\n" + "=" * 50)
    print("✍️  测试文案生成...")
    print("=" * 50)
    result = await generate_copy(idea, platforms=["douyin", "xiaohongshu"])
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


async def test_video(idea: dict, mode: str = MODE_DEFAULT):
    """测试视频生成"""
    prompt_result = idea.get("prompt_result", {})
    prompt = prompt_result.get("seedance_prompt", "")
    duration = prompt_result.get("duration_seconds", 5)

    if not prompt:
        print("❌ 创意方案缺少 seedance_prompt")
        return None

    print("\n" + "=" * 50)
    print(f"📹 测试视频生成 [模式: {mode}]")
    print(f"   Prompt: {prompt[:80]}...")
    print(f"   时长: {duration}s")
    print("=" * 50)

    cfg = get_config()
    data_dir = Path(cfg.get("output", {}).get("data_dir", "./data"))
    today = datetime.now().strftime("%Y-%m-%d")
    videos_dir = data_dir / "daily" / today / "videos"

    result = await generate_video(
        prompt,
        videos_dir,
        filename="test_video.mp4",
        duration=min(5, duration),
        ratio="9:16",
        resolution="480p",
        generate_audio=True,
        poll_interval=10,
        poll_timeout=600,
        mode=mode,
    )

    print("\n📋 生成结果:")
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return result


async def main():
    parser = argparse.ArgumentParser(description="Generate 模块测试")
    parser.add_argument("--dry-run", action="store_true", help="只测试文案，不调视频 API")
    parser.add_argument("--mode", type=str, default="default", help="生成模式: default/draft/flex")
    args = parser.parse_args()

    setup_logging()

    idea = load_first_idea()
    print(f"📂 加载创意: {idea.get('title', '?')}")
    print(f"   策略: {idea.get('strategy', '?')}")
    print(f"   标签: {', '.join(idea.get('tags', []))}")

    # 1. 文案生成（快速，先测这个）
    copy_result = await test_copy(idea)

    # 2. 视频生成
    if not args.dry_run:
        video_result = await test_video(idea, mode=args.mode)
    else:
        print("\n⏭️  --dry-run 模式，跳过视频生成")

    print("\n✅ 测试完成!")


if __name__ == "__main__":
    asyncio.run(main())
