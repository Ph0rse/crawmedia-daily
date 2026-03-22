"""
爆款排序算法
按互动数据计算爆款分数，支持自定义权重。
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def _parse_age_days(created_at: str) -> float:
    """计算内容发布距今的天数，用于衰减计算"""
    if not created_at:
        return 1.0  # 无时间信息时默认 1 天（不衰减）

    try:
        # 尝试多种时间格式
        for fmt in ["%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d"]:
            try:
                dt = datetime.strptime(created_at, fmt)
                break
            except ValueError:
                continue
        else:
            # 尝试 ISO 格式
            dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))

        now = datetime.now()
        if dt.tzinfo:
            now = datetime.now(timezone.utc)

        delta = (now - dt).total_seconds() / 86400  # 转为天数
        return max(delta, 0.1)  # 最少 0.1 天，避免除零
    except (ValueError, TypeError):
        return 1.0


def calculate_score(
    item: dict,
    weights: dict | None = None,
) -> float:
    """
    计算单条内容的爆款分数。

    公式: score = (likes * w_likes + collects * w_collects + comments * w_comments) / age_days
    抖音热榜内容（有 popularity 字段）直接使用热度值。

    Args:
        item: 标准化内容数据
        weights: 权重配置 {"likes": 1, "collects": 2, "comments": 3}

    Returns:
        爆款分数（越高越爆）
    """
    w = weights or {"likes": 1, "collects": 2, "comments": 3}

    likes = item.get("likes", 0)
    collects = item.get("collects", 0)
    comments = item.get("comments", 0)
    popularity = item.get("popularity", 0)

    # 抖音热榜有独立的 popularity 值，直接使用
    if popularity > 0 and item.get("platform") == "douyin":
        return float(popularity)

    # 小红书等平台：加权互动分数 / 发布天数
    age_days = _parse_age_days(item.get("created_at", ""))
    raw_score = (
        likes * w.get("likes", 1)
        + collects * w.get("collects", 2)
        + comments * w.get("comments", 3)
    )

    return raw_score / age_days


def rank_items(
    items: list[dict],
    weights: dict | None = None,
    top_n: int = 20,
) -> list[dict]:
    """
    对内容列表计算分数并排序。

    Args:
        items: 标准化的内容列表（可混合多平台）
        weights: 评分权重
        top_n: 返回 Top N 条

    Returns:
        按分数降序排列的内容列表，每条附带 "score" 和 "rank" 字段
    """
    logger.info("📊 开始爆款评分（共 %d 条内容）...", len(items))

    for item in items:
        item["score"] = calculate_score(item, weights)

    # 按分数降序
    sorted_items = sorted(items, key=lambda x: x["score"], reverse=True)

    # 取 Top N 并重新编号
    top_items = sorted_items[:top_n]
    for i, item in enumerate(top_items, 1):
        item["rank"] = i

    logger.info("📊 评分完成：Top %d（最高分: %.1f，最低分: %.1f）",
                len(top_items),
                top_items[0]["score"] if top_items else 0,
                top_items[-1]["score"] if top_items else 0)

    return top_items
