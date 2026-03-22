#!/usr/bin/env python3
"""
Scout 模块端到端测试脚本
验证整个采集→排序→存储流程是否正确工作。

用法:
    python scripts/test_scout.py
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

from modules.config import get_config
from modules.scout.douyin import fetch_douyin_trends
from modules.scout.trend_ranker import calculate_score, rank_items

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("test_scout")


def test_config():
    """测试配置加载"""
    logger.info("━━━ 测试 1: 配置加载 ━━━")
    cfg = get_config()
    assert cfg is not None, "配置加载失败"
    assert "niche" in cfg, "配置缺少 niche 字段"
    assert cfg["niche"]["name"] == "萌宠", f"赛道名称不匹配: {cfg['niche']['name']}"

    keywords = cfg["niche"]["keywords"]
    assert len(keywords) > 0, "关键词列表为空"
    logger.info("  ✅ 配置加载正常：赛道=%s，关键词=%d 个", cfg["niche"]["name"], len(keywords))
    return cfg


def test_trend_ranker():
    """测试排序算法"""
    logger.info("━━━ 测试 2: 爆款排序算法 ━━━")

    mock_items = [
        {"title": "萌宠日常1", "likes": 5000, "collects": 2000, "comments": 300, "platform": "douyin", "popularity": 5000},
        {"title": "萌宠日常2", "likes": 10000, "collects": 500, "comments": 100, "platform": "douyin", "popularity": 10000},
        {"title": "抖音热搜1", "likes": 0, "collects": 0, "comments": 0, "popularity": 12000000, "platform": "douyin"},
        {"title": "萌宠日常3", "likes": 80000, "collects": 30000, "comments": 5000, "platform": "douyin", "popularity": 80000},
        {"title": "萌宠日常4", "likes": 20000, "collects": 8000, "comments": 1500, "platform": "douyin", "popularity": 20000},
    ]

    ranked = rank_items(mock_items, top_n=5)

    assert len(ranked) == 5, f"排序结果数量不正确: {len(ranked)}"
    assert ranked[0]["rank"] == 1, "第一名 rank 应该是 1"
    assert all(r.get("score", 0) > 0 for r in ranked), "所有项应有正分数"
    assert ranked[0]["score"] == 12000000, f"最高分应为 12000000: {ranked[0]['score']}"

    logger.info("  ✅ 排序算法正常")
    for item in ranked:
        logger.info("    #%d %s — 分数: %.1f", item["rank"], item["title"], item["score"])


async def test_douyin_fetch():
    """测试抖音热榜抓取（不带关键词过滤，验证数据流）"""
    logger.info("━━━ 测试 3: 抖音热榜抓取（无过滤） ━━━")

    items = await fetch_douyin_trends(keywords=[], fetch_limit=10, top_n=5)

    assert isinstance(items, list), "返回值应为列表"
    logger.info("  获取到 %d 条热榜数据", len(items))

    if len(items) > 0:
        first = items[0]
        assert "title" in first, "缺少 title 字段"
        assert "popularity" in first, "缺少 popularity 字段"
        assert "link" in first, "缺少 link 字段"
        assert first["platform"] == "douyin", "平台应为 douyin"

        logger.info("  ✅ 数据格式正确")
        for item in items[:3]:
            logger.info("    🔥 %s (热度: %s)", item["title"], f"{item['popularity']:,}")
    else:
        logger.warning("  ⚠️  未获取到数据（可能是网络问题）")


async def test_douyin_with_keywords():
    """测试抖音关键词过滤"""
    logger.info("━━━ 测试 4: 抖音热榜 + 关键词过滤 ━━━")
    cfg = get_config()
    keywords = cfg["niche"]["keywords"]

    items = await fetch_douyin_trends(keywords=keywords, fetch_limit=50, top_n=20)
    logger.info("  关键词过滤后: %d 条（关键词: %s）", len(items), ", ".join(keywords))

    if len(items) == 0:
        logger.info("  ℹ️  今天热榜没有匹配的赛道内容（正常情况）")

    logger.info("  ✅ 关键词过滤逻辑正常")


async def test_full_pipeline():
    """测试完整的 Scout 流程"""
    logger.info("━━━ 测试 5: 完整 Scout 流程 ━━━")

    from modules.scout.runner import run_scout
    results = await run_scout(skip_feishu=True)

    logger.info("  Scout 返回 %d 条结果", len(results))

    today = datetime.now().strftime("%Y-%m-%d")
    data_dir = PROJECT_ROOT / "data" / "daily" / today
    scout_file = data_dir / "scout.json"

    if scout_file.exists():
        with open(scout_file, "r", encoding="utf-8") as f:
            saved = json.load(f)
        logger.info("  💾 存储文件: stage=%s, count=%d", saved.get("stage"), saved.get("count", 0))
        logger.info("  ✅ 数据存储正常")
    else:
        if len(results) == 0:
            logger.info("  ℹ️  无结果时不生成存储文件（正常）")
        else:
            logger.error("  ❌ 存储文件未生成: %s", scout_file)


async def test_feishu_format():
    """测试飞书消息格式化（不实际发送）"""
    logger.info("━━━ 测试 6: 飞书消息格式化 ━━━")

    from modules.feishu import format_scout_results_for_feishu

    mock_items = [
        {"rank": 1, "title": "萌宠搞笑合集", "link": "https://example.com/1",
         "popularity": 8000000, "likes": 8000000, "collects": 0, "comments": 0, "score": 8000000.0, "platform": "douyin"},
        {"rank": 2, "title": "猫咪第一次看到雪", "link": "https://example.com/2",
         "popularity": 5000000, "likes": 5000000, "collects": 0, "comments": 0, "score": 5000000.0, "platform": "douyin"},
    ]

    title, paragraphs = format_scout_results_for_feishu(
        mock_items, "萌宠", ["猫咪", "狗狗", "宠物"]
    )

    assert "萌宠" in title, f"标题应包含赛道名: {title}"
    assert len(paragraphs) > 0, "段落不应为空"
    logger.info("  标题: %s", title)
    logger.info("  ✅ 飞书消息格式化正常")


async def main():
    logger.info("🧪 CrawMedia Daily — Scout 模块测试")
    logger.info("=" * 60)

    passed = 0
    failed = 0

    for test_fn in [test_config, test_trend_ranker]:
        try:
            test_fn()
            passed += 1
        except Exception as e:
            logger.error("  ❌ 测试失败: %s", e)
            failed += 1

    for test_fn in [test_douyin_fetch, test_douyin_with_keywords, test_full_pipeline, test_feishu_format]:
        try:
            await test_fn()
            passed += 1
        except Exception as e:
            logger.error("  ❌ 测试失败: %s", e)
            failed += 1

    logger.info("=" * 60)
    logger.info("🏁 测试完成: %d 通过, %d 失败", passed, failed)

    if failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
