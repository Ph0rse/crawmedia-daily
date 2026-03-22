#!/usr/bin/env python3
"""
Week 2 端到端测试
读取 scout_demo.json → Analyze（创意模式提取）→ Remix（创意组合 + Seedance Prompt）

用法:
    python scripts/test_week2.py                       # 完整测试（需要 LLM API）
    python scripts/test_week2.py --stage analyze       # 只测 Analyze
    python scripts/test_week2.py --stage remix         # 只测 Remix（需要先有 analysis.json）
    python scripts/test_week2.py --dry-run             # 不调用 LLM，只验证数据流
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
from modules.analyze.runner import run_analyze
from modules.analyze.pattern_db import PatternDB
from modules.remix.runner import run_remix


def setup_logging():
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        handlers=[logging.StreamHandler(sys.stdout)],
    )


def test_dry_run():
    """不调用 LLM，只验证配置加载和数据流"""
    logger = logging.getLogger("test")
    logger.info("🧪 Dry Run 测试开始")

    # 1. 配置加载
    cfg = get_config()
    assert cfg.get("niche", {}).get("name"), "赛道名未配置"
    assert cfg.get("analyze", {}).get("max_items"), "analyze.max_items 未配置"
    logger.info("✅ 配置加载正常: 赛道=%s", cfg["niche"]["name"])

    # 2. Scout 数据加载
    data_dir = Path(cfg.get("output", {}).get("data_dir", "./data"))
    today = datetime.now().strftime("%Y-%m-%d")
    daily_dir = data_dir / "daily" / today

    scout_files = [daily_dir / "scout.json", daily_dir / "scout_demo.json"]
    found = None
    for f in scout_files:
        if f.exists():
            found = f
            break
    assert found, f"未找到 Scout 数据文件: {[str(f) for f in scout_files]}"

    with open(found, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    items = data.get("hot_items", [])
    assert len(items) > 0, "Scout 数据为空"
    logger.info("✅ Scout 数据加载正常: %s (%d 条)", found.name, len(items))

    # 3. PatternDB 初始化
    db = PatternDB()
    logger.info("✅ PatternDB 初始化正常: %s (累计 %d 条)", db.db_path, db.count())

    # 4. 模板加载
    from modules.remix.prompt_generator import TEMPLATES_DIR
    templates = list(TEMPLATES_DIR.glob("*.yaml"))
    assert len(templates) > 0, "未找到 Prompt 模板"
    logger.info("✅ Prompt 模板加载正常: %d 个模板", len(templates))

    # 5. 策略注册
    from modules.remix.strategy import STRATEGIES
    assert len(STRATEGIES) == 4, f"期望 4 种策略，实际 {len(STRATEGIES)}"
    logger.info("✅ 创意策略注册正常: %s", list(STRATEGIES.keys()))

    logger.info("🎉 Dry Run 全部通过！")


async def test_analyze():
    """测试 Analyze 阶段"""
    logger = logging.getLogger("test")
    logger.info("🧪 测试 Analyze 阶段...")

    patterns = await run_analyze(skip_feishu=True)

    assert len(patterns) > 0, "Analyze 未返回任何模式"
    logger.info("📊 返回 %d 个创意模式", len(patterns))

    for p in patterns:
        assert "pattern_id" in p, f"模式缺少 pattern_id: {p}"
        assert "hook" in p, f"模式缺少 hook: {p.get('pattern_id')}"
        logger.info("   ✅ %s: %s", p["pattern_id"], p.get("source", {}).get("title", ""))

    # 验证数据库写入
    db = PatternDB()
    assert db.count() > 0, "PatternDB 为空"
    logger.info("📊 PatternDB 总记录: %d", db.count())

    # 验证文件输出
    cfg = get_config()
    today = datetime.now().strftime("%Y-%m-%d")
    analysis_path = Path(cfg["output"]["data_dir"]) / "daily" / today / "analysis.json"
    assert analysis_path.exists(), f"analysis.json 未生成: {analysis_path}"
    logger.info("✅ analysis.json 已生成")

    return patterns


async def test_remix(patterns=None):
    """测试 Remix 阶段"""
    logger = logging.getLogger("test")
    logger.info("🧪 测试 Remix 阶段...")

    ideas = await run_remix(skip_feishu=True, patterns=patterns)

    assert len(ideas) > 0, "Remix 未返回任何创意"
    logger.info("🎨 返回 %d 个创意方案", len(ideas))

    for idea in ideas:
        logger.info("   %s [%s] %s",
                     "✅" if idea.get("prompt_result") else "⚠️",
                     idea.get("strategy", "?"),
                     idea.get("title", "未命名"))
        if idea.get("prompt_result"):
            pr = idea["prompt_result"]
            prompt_preview = pr.get("seedance_prompt", "")[:80]
            logger.info("      Prompt: %s...", prompt_preview)
            logger.info("      时长: %ds, 风格: %s",
                        pr.get("duration_seconds", 0),
                        ", ".join(pr.get("style_tags", [])))

    # 验证文件输出
    cfg = get_config()
    today = datetime.now().strftime("%Y-%m-%d")
    remixed_path = Path(cfg["output"]["data_dir"]) / "daily" / today / "remixed.json"
    assert remixed_path.exists(), f"remixed.json 未生成: {remixed_path}"
    logger.info("✅ remixed.json 已生成")

    return ideas


async def main():
    parser = argparse.ArgumentParser(description="Week 2 端到端测试")
    parser.add_argument("--stage", type=str, default=None,
                       help="只测指定阶段: analyze, remix")
    parser.add_argument("--dry-run", action="store_true",
                       help="不调用 LLM，只验证数据流和配置")
    args = parser.parse_args()

    setup_logging()
    logger = logging.getLogger("test")

    if args.dry_run:
        test_dry_run()
        return

    logger.info("🚀 Week 2 端到端测试开始")
    logger.info("=" * 60)

    if args.stage is None or args.stage == "analyze":
        patterns = await test_analyze()
        logger.info("")

    if args.stage is None or args.stage == "remix":
        await test_remix()

    logger.info("")
    logger.info("=" * 60)
    logger.info("🎉 Week 2 测试完成！")


if __name__ == "__main__":
    asyncio.run(main())
