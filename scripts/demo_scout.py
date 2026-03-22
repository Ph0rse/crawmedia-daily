#!/usr/bin/env python3
"""
Scout 完整演示脚本
抓取抖音热榜 Top 10 + 上升热点（不过滤关键词），排序后保存 + 打印结果。
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from modules.scout.douyin import fetch_douyin_trends
from modules.scout.trend_ranker import rank_items

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("demo")


async def main():
    logger.info("🦞 CrawMedia Daily — Scout 演示")
    logger.info("=" * 65)

    # 准备数据目录
    data_dir = PROJECT_ROOT / "data" / "daily" / datetime.now().strftime("%Y-%m-%d")
    data_dir.mkdir(parents=True, exist_ok=True)

    # 1. 抓取热榜 + 上升热点（不过滤关键词），并下载封面图
    result = await fetch_douyin_trends(
        keywords=[],
        fetch_limit=20,
        top_n=20,
        save_covers=True,
        daily_dir=data_dir,
    )
    hot_items = result["hot_list"]
    trending_items = result["trending_list"]
    active_time = result["active_time"]

    if not hot_items:
        logger.error("❌ 未获取到数据")
        return

    # 2. 排序
    ranked = rank_items(hot_items, top_n=10)

    # 3. 打印热搜结果
    logger.info("")
    logger.info("🔥 抖音热搜 Top %d（最后更新: %s）", len(ranked), active_time or "未知")
    logger.info("-" * 65)
    for item in ranked:
        cover_tag = "📷" if item.get("cover_local") else ""
        logger.info(
            "  #%2d  %s  %s\n"
            "       热度: %s  |  视频数: %d  |  链接: %s",
            item["rank"],
            item["title"],
            cover_tag,
            f'{item["popularity"]:>12,}',
            item.get("video_count", 0),
            item["link"][:60] + "..." if len(item.get("link", "")) > 60 else item.get("link", ""),
        )
    logger.info("-" * 65)

    # 4. 打印上升热点
    if trending_items:
        logger.info("")
        logger.info("📈 实时上升热点 Top %d", min(len(trending_items), 5))
        logger.info("-" * 65)
        for item in trending_items[:5]:
            logger.info("  #%2d  %s", item["rank"], item["title"])
            logger.info("       视频数: %d  |  链接: %s",
                        item.get("video_count", 0),
                        item["link"][:60] + "..." if len(item.get("link", "")) > 60 else item.get("link", ""))
        logger.info("-" * 65)

    # 5. 保存到文件
    output_file = data_dir / "scout_demo.json"
    payload = {
        "stage": "scout_demo",
        "timestamp": datetime.now().isoformat(),
        "source": "douyin_hot_board",
        "active_time": active_time,
        "hot_count": len(ranked),
        "trending_count": len(trending_items),
        "hot_items": ranked,
        "trending_items": trending_items[:10],
    }
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    logger.info("")
    logger.info("💾 结果已保存: %s", output_file)
    if (data_dir / "covers").exists():
        cover_count = len(list((data_dir / "covers").iterdir()))
        logger.info("🖼️  封面图已缓存: %d 张 → %s", cover_count, data_dir / "covers")
    logger.info("✅ 演示完成")


if __name__ == "__main__":
    asyncio.run(main())
