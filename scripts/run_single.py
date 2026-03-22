#!/usr/bin/env python3
"""
CrawMedia Daily — 单阶段执行入口

用法:
    python scripts/run_single.py scout              # 执行采集
    python scripts/run_single.py scout --no-feishu   # 不推送飞书
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


def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[logging.StreamHandler(sys.stdout)],
    )


async def main():
    parser = argparse.ArgumentParser(description="CrawMedia Daily — 单阶段执行")
    parser.add_argument("stage", type=str,
                       help="阶段名: scout, analyze, remix, generate, approve, publish, status")
    parser.add_argument("--no-feishu", action="store_true", help="不推送飞书")
    parser.add_argument("--ids", type=str, default=None,
                       help="审批通过的条目索引（逗号分隔），如: --ids 0,1,2")
    parser.add_argument("--preview", action="store_true",
                       help="发布预览模式（填写表单但不点发布）")
    parser.add_argument("--now", type=str, default=None,
                       help="覆盖当前时间（HH:MM，测试用）")
    parser.add_argument("--force-publish", action="store_true",
                       help="强制发布（跳过全部审批，直接发布所有内容）")
    args = parser.parse_args()

    setup_logging()
    logger = logging.getLogger("crawmedia-daily")
    skip = args.no_feishu

    if args.stage == "scout":
        results = await run_scout(skip_feishu=skip)
        logger.info("✅ Scout 完成，共 %d 条结果", len(results))
    elif args.stage == "analyze":
        patterns = await run_analyze(skip_feishu=skip)
        logger.info("✅ Analyze 完成，共 %d 个创意模式", len(patterns))
    elif args.stage == "remix":
        ideas = await run_remix(skip_feishu=skip)
        logger.info("✅ Remix 完成，共 %d 个创意方案", len(ideas))
    elif args.stage == "generate":
        items = await run_generate(skip_feishu=skip)
        logger.info("✅ Generate 完成，共 %d 个生成结果", len(items))

    # ── Publish 相关阶段 ──
    elif args.stage == "approve":
        # 发送审批卡片
        approve_ids = None
        if args.ids:
            approve_ids = [int(x.strip()) for x in args.ids.split(",") if x.strip().isdigit()]
        result = await run_publish(
            mode="approve",
            skip_feishu=skip,
            approve_ids=approve_ids,
        )
        logger.info("✅ 审批卡片已发送，共 %d 条", len(result))

    elif args.stage == "publish":
        # 执行发布
        approve_ids = None
        if args.ids:
            approve_ids = [int(x.strip()) for x in args.ids.split(",") if x.strip().isdigit()]
        if args.force_publish:
            result = await run_publish(
                mode="full",
                skip_feishu=skip,
                preview=args.preview,
            )
        else:
            result = await run_publish(
                mode="publish",
                skip_feishu=skip,
                preview=args.preview,
                approve_ids=approve_ids,
                now_override=args.now,
            )
        success = sum(1 for r in result if r.get("publish_status") == "success")
        logger.info("✅ Publish 完成: %d/%d 成功", success, len(result))

    elif args.stage == "status":
        result = await run_publish(mode="status", skip_feishu=True)
        logger.info("📋 审批状态查询完成，共 %d 条", len(result))

    else:
        logger.error("❌ 阶段 '%s' 尚未实现", args.stage)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
