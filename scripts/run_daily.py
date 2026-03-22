#!/usr/bin/env python3
"""
CrawMedia Daily — 每日 Pipeline 入口

用法:
    python scripts/run_daily.py                  # 执行完整 Pipeline（当前只有 scout）
    python scripts/run_daily.py --stage scout     # 只执行采集阶段
    python scripts/run_daily.py --skip-feishu     # 跳过飞书推送
"""

import argparse
import asyncio
import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from modules.scout.runner import run_scout
from modules.analyze.runner import run_analyze
from modules.remix.runner import run_remix
from modules.generate.runner import run_generate
from modules.publish.runner import run_publish

VALID_STAGES = ["scout", "analyze", "remix", "generate", "approve", "publish"]


def setup_logging(level: str = "INFO"):
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[logging.StreamHandler(sys.stdout)],
    )


async def main():
    parser = argparse.ArgumentParser(description="CrawMedia Daily Pipeline")
    parser.add_argument("--stage", type=str, default=None,
                       help="只执行指定阶段: scout, analyze, remix")
    parser.add_argument("--skip-feishu", action="store_true",
                       help="跳过飞书推送")
    parser.add_argument("--log-level", type=str, default="INFO",
                       help="日志级别: DEBUG, INFO, WARNING, ERROR")
    args = parser.parse_args()

    setup_logging(args.log_level)
    logger = logging.getLogger("crawmedia-daily")

    logger.info("🦞 CrawMedia Daily Pipeline 启动")

    stage = args.stage
    skip = args.skip_feishu

    # ── Scout ──
    if stage is None or stage == "scout":
        results = await run_scout(skip_feishu=skip)
        logger.info("Scout 阶段返回 %d 条结果", len(results))
        if stage == "scout":
            return

    # ── Analyze ──
    if stage is None or stage == "analyze":
        patterns = await run_analyze(skip_feishu=skip)
        logger.info("Analyze 阶段返回 %d 个模式", len(patterns))
        if stage == "analyze":
            return

    # ── Remix ──
    if stage is None or stage == "remix":
        ideas = await run_remix(skip_feishu=skip)
        logger.info("Remix 阶段返回 %d 个创意方案", len(ideas))
        if stage == "remix":
            return

    # ── Generate ──
    if stage is None or stage == "generate":
        items = await run_generate(skip_feishu=skip)
        logger.info("Generate 阶段返回 %d 个生成结果", len(items))
        if stage == "generate":
            return

    # ── Approve（发送审批卡片）──
    if stage is None or stage == "approve":
        approval = await run_publish(mode="approve", skip_feishu=skip)
        logger.info("Approve 阶段: 已发送 %d 条审批", len(approval))
        if stage == "approve":
            return

    # ── Publish（执行发布）──
    if stage is None or stage == "publish":
        results = await run_publish(mode="publish", skip_feishu=skip)
        logger.info("Publish 阶段: %d 条发布结果", len(results))
        if stage == "publish":
            return


if __name__ == "__main__":
    asyncio.run(main())
