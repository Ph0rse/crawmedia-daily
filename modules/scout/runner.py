"""
Scout 阶段主入口
编排：抖音热榜采集 → 爆款排序 → 存储 → 飞书通知
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

from ..config import get_config
from ..feishu import format_scout_results_for_feishu, send_feishu_rich_text
from .douyin import fetch_douyin_trends
from .trend_ranker import rank_items

logger = logging.getLogger(__name__)


def _get_daily_dir() -> Path:
    """创建并返回当天的数据目录"""
    cfg = get_config()
    data_dir = Path(cfg.get("output", {}).get("data_dir", "./data"))
    today = datetime.now().strftime("%Y-%m-%d")
    daily_dir = data_dir / "daily" / today
    daily_dir.mkdir(parents=True, exist_ok=True)
    return daily_dir


def _save_results(
    hot_items: list[dict],
    trending_items: list[dict],
    active_time: str | None,
    daily_dir: Path,
) -> Path:
    """保存采集结果到 scout.json（包含热搜 + 上升热点）"""
    output_path = daily_dir / "scout.json"
    payload = {
        "stage": "scout",
        "timestamp": datetime.now().isoformat(),
        "active_time": active_time,
        "hot_count": len(hot_items),
        "trending_count": len(trending_items),
        "hot_items": hot_items,
        "trending_items": trending_items,
    }
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    logger.info("💾 采集结果已保存: %s", output_path)
    return output_path


async def run_scout(
    skip_feishu: bool = False,
) -> list[dict]:
    """
    执行完整的 Scout 阶段。

    Returns:
        排序后的 Top N 爆款列表（hot_list 部分）
    """
    cfg = get_config()
    niche = cfg.get("niche", {})
    niche_name = niche.get("name", "未命名赛道")
    keywords = niche.get("keywords", [])
    scout_cfg = cfg.get("scout", {})

    logger.info("=" * 60)
    logger.info("🚀 Scout 阶段启动 — 赛道: %s", niche_name)
    logger.info("   关键词: %s", ", ".join(keywords) or "全部")
    logger.info("=" * 60)

    daily_dir = _get_daily_dir()

    # ── 1. 抖音热榜 + 上升热点采集 ──
    dy_cfg = scout_cfg.get("douyin", {})
    try:
        result = await fetch_douyin_trends(
            keywords=keywords,
            fetch_limit=dy_cfg.get("fetch_limit", 50),
            top_n=dy_cfg.get("top_n", 20),
            save_covers=True,
            daily_dir=daily_dir,
        )
        hot_items = result["hot_list"]
        trending_items = result["trending_list"]
        active_time = result["active_time"]
        logger.info("📱 抖音采集完成: 热搜 %d 条，上升热点 %d 条", len(hot_items), len(trending_items))
    except Exception as e:
        logger.error("❌ 抖音采集失败: %s", e)
        hot_items, trending_items, active_time = [], [], None

    if not hot_items:
        logger.warning("⚠️  采集无结果")
        return []

    # ── 2. 爆款排序 ──
    ranking_cfg = scout_cfg.get("ranking", {})
    weights = ranking_cfg.get("weights", {"likes": 1, "collects": 2, "comments": 3})
    top_n = dy_cfg.get("top_n", 20)
    ranked_hot = rank_items(hot_items, weights=weights, top_n=top_n)

    # ── 3. 保存结果（热搜 + 上升热点一起存）──
    _save_results(ranked_hot, trending_items, active_time, daily_dir)

    # ── 4. 飞书通知 ──
    if not skip_feishu:
        try:
            title, paragraphs = format_scout_results_for_feishu(
                ranked_hot, niche_name, keywords
            )
            await send_feishu_rich_text(title, paragraphs)
            logger.info("📨 飞书推送完成")
        except Exception as e:
            logger.error("❌ 飞书推送失败: %s", e)

    # ── 汇总 ──
    logger.info("=" * 60)
    logger.info("✅ Scout 阶段完成")
    logger.info("   热搜: %d 条  |  上升热点: %d 条  |  封面图: %s",
                len(ranked_hot), len(trending_items), daily_dir / "covers")
    logger.info("   数据目录: %s", daily_dir)
    logger.info("=" * 60)

    return ranked_hot
