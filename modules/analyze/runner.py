"""
Analyze 阶段主入口
编排：读取 Scout 产出 → LLM 创意模式提取 → 存入模式库 → 飞书通知
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

from ..config import get_config
from ..feishu import send_feishu_rich_text
from .creative_extractor import extract_patterns_batch
from .pattern_db import PatternDB

logger = logging.getLogger(__name__)


def _get_daily_dir() -> Path:
    cfg = get_config()
    data_dir = Path(cfg.get("output", {}).get("data_dir", "./data"))
    today = datetime.now().strftime("%Y-%m-%d")
    daily_dir = data_dir / "daily" / today
    daily_dir.mkdir(parents=True, exist_ok=True)
    return daily_dir


def _load_scout_results(daily_dir: Path) -> list[dict]:
    """从当天目录加载 Scout 阶段产出（优先 scout.json，其次 scout_demo.json）"""
    for filename in ("scout.json", "scout_demo.json"):
        path = daily_dir / filename
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            items = data.get("hot_items", [])
            logger.info("📂 加载 Scout 数据: %s (%d 条)", path.name, len(items))
            return items

    logger.warning("⚠️  未找到 Scout 数据文件")
    return []


def _save_analysis(patterns: list[dict], daily_dir: Path) -> Path:
    """保存分析结果到 analysis.json"""
    output_path = daily_dir / "analysis.json"
    payload = {
        "stage": "analyze",
        "timestamp": datetime.now().isoformat(),
        "pattern_count": len(patterns),
        "patterns": patterns,
    }
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    logger.info("💾 分析结果已保存: %s", output_path)
    return output_path


def _format_analysis_for_feishu(
    patterns: list[dict],
    niche_name: str,
) -> tuple[str, list[list[dict]]]:
    """将分析结果格式化为飞书富文本"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    title = f"🧠 【{niche_name}】创意模式分析（{now}）"

    paragraphs = []
    paragraphs.append([
        {"tag": "text", "text": f"📊 共提取 {len(patterns)} 个创意模式\n"},
    ])

    for i, p in enumerate(patterns, 1):
        source = p.get("source", {})
        hook = p.get("hook", {})
        tags = p.get("tags", [])

        row = [
            {"tag": "text", "text": f"\n{'─' * 30}\n"},
            {"tag": "text", "text": f"🎯 #{i} "},
            {"tag": "a", "text": source.get("title", "未知"), "href": source.get("link", "")},
            {"tag": "text", "text": (
                f"\n   🪝 钩子: {hook.get('type', '未知')} — {hook.get('desc', '')}"
                f"\n   🎭 情感: {' → '.join(p.get('emotion_curve', []))}"
                f"\n   🎬 类型: {p.get('content_type', '未知')}"
                f"\n   🔥 爆款原因: {p.get('viral_reason', '未知')}"
                f"\n   🏷️ 标签: {', '.join(tags)}"
                f"\n   📊 评分: {p.get('engagement_score', 0)}\n"
            )},
        ]
        paragraphs.append(row)

    paragraphs.append([
        {"tag": "text", "text": f"\n✅ 分析完成 ⏰ {now}"},
    ])

    return title, paragraphs


async def run_analyze(
    skip_feishu: bool = False,
    scout_items: list[dict] | None = None,
) -> list[dict]:
    """
    执行完整的 Analyze 阶段。

    Args:
        skip_feishu: 是否跳过飞书推送
        scout_items: 可选的 Scout 数据（不传则从文件加载）

    Returns:
        提取到的创意模式卡片列表
    """
    cfg = get_config()
    niche = cfg.get("niche", {})
    niche_name = niche.get("name", "未命名赛道")
    analyze_cfg = cfg.get("analyze", {})
    max_items = analyze_cfg.get("max_items", 10)

    logger.info("=" * 60)
    logger.info("🧠 Analyze 阶段启动 — 赛道: %s", niche_name)
    logger.info("=" * 60)

    daily_dir = _get_daily_dir()

    # ── 1. 读取 Scout 产出 ──
    if scout_items is None:
        scout_items = _load_scout_results(daily_dir)

    if not scout_items:
        logger.warning("⚠️  没有可分析的内容")
        return []

    # ── 2. LLM 创意模式提取 ──
    try:
        patterns = await extract_patterns_batch(
            scout_items,
            date_str=datetime.now().strftime("%Y%m%d"),
            max_items=max_items,
        )
    except Exception as e:
        logger.error("❌ 创意模式提取失败: %s", e)
        return []

    if not patterns:
        logger.warning("⚠️  未提取到任何创意模式")
        return []

    # ── 3. 存入创意模式数据库 ──
    try:
        db = PatternDB()
        db.save_patterns(patterns, niche=niche_name)
        logger.info("📊 模式库累计: %d 条记录", db.count())
    except Exception as e:
        logger.error("❌ 模式入库失败: %s", e)

    # ── 4. 保存分析结果 ──
    _save_analysis(patterns, daily_dir)

    # ── 5. 飞书通知 ──
    if not skip_feishu:
        try:
            title, paragraphs = _format_analysis_for_feishu(patterns, niche_name)
            await send_feishu_rich_text(title, paragraphs)
            logger.info("📨 飞书推送完成")
        except Exception as e:
            logger.error("❌ 飞书推送失败: %s", e)

    # ── 汇总 ──
    logger.info("=" * 60)
    logger.info("✅ Analyze 阶段完成")
    logger.info("   提取模式: %d 个", len(patterns))
    logger.info("   数据目录: %s", daily_dir)
    logger.info("=" * 60)

    return patterns
