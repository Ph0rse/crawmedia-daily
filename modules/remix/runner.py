"""
Remix 阶段主入口
编排：读取 Analyze 产出 → 执行创意策略 → 生成 Seedance Prompt → 存储 → 飞书通知
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

from ..config import get_config
from ..feishu import send_feishu_rich_text
from .strategy import run_all_strategies
from .prompt_generator import generate_prompts_for_ideas

logger = logging.getLogger(__name__)


def _get_daily_dir() -> Path:
    cfg = get_config()
    data_dir = Path(cfg.get("output", {}).get("data_dir", "./data"))
    today = datetime.now().strftime("%Y-%m-%d")
    daily_dir = data_dir / "daily" / today
    daily_dir.mkdir(parents=True, exist_ok=True)
    return daily_dir


def _load_analysis_results(daily_dir: Path) -> list[dict]:
    """从当天目录加载 Analyze 阶段产出"""
    path = daily_dir / "analysis.json"
    if not path.exists():
        logger.warning("⚠️  未找到 analysis.json")
        return []
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    patterns = data.get("patterns", [])
    logger.info("📂 加载分析数据: %d 个创意模式", len(patterns))
    return patterns


def _save_remix_results(ideas_with_prompts: list[dict], daily_dir: Path) -> Path:
    """保存创意方案 + Prompt 到 remixed.json"""
    output_path = daily_dir / "remixed.json"
    payload = {
        "stage": "remix",
        "timestamp": datetime.now().isoformat(),
        "idea_count": len(ideas_with_prompts),
        "ideas": ideas_with_prompts,
    }
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    logger.info("💾 创意方案已保存: %s", output_path)
    return output_path


def _format_remix_for_feishu(
    ideas: list[dict],
    niche_name: str,
) -> tuple[str, list[list[dict]]]:
    """将创意方案格式化为飞书富文本"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    title = f"🎨 【{niche_name}】今日创意方案（{now}）"

    paragraphs = []
    paragraphs.append([
        {"tag": "text", "text": f"✨ 共生成 {len(ideas)} 个创意方案 + Seedance Prompt\n"},
    ])

    strategy_emoji = {
        "combine": "🔀", "generalize": "🔄",
        "transfer": "🚀", "extend": "📐",
    }

    for i, idea in enumerate(ideas, 1):
        strategy = idea.get("strategy", "unknown")
        emoji = strategy_emoji.get(strategy, "💡")
        prompt_result = idea.get("prompt_result", {})
        prompt_text = (prompt_result or {}).get("seedance_prompt", "⚠️ Prompt 生成失败")
        # 截断过长的 prompt
        if len(prompt_text) > 200:
            prompt_text = prompt_text[:200] + "..."

        row = [
            {"tag": "text", "text": f"\n{'─' * 30}\n"},
            {"tag": "text", "text": (
                f"{emoji} #{i} [{strategy}] {idea.get('title', '未命名')}\n"
                f"   💡 概述: {idea.get('concept', '')}\n"
                f"   🪝 钩子: {idea.get('hook', {}).get('desc', '')}\n"
                f"   🎭 情感: {' → '.join(idea.get('emotion_curve', []))}\n"
                f"   🏷️ 标签: {', '.join(idea.get('tags', []))}\n"
                f"   ⏱️ 时长: {idea.get('duration_seconds', '?')}s\n"
                f"   📝 Prompt 预览: {prompt_text}\n"
            )},
        ]
        paragraphs.append(row)

    paragraphs.append([
        {"tag": "text", "text": f"\n🎯 下一步：Generate 阶段将这些 Prompt 提交给 Seedance 2.0 生成视频 ⏰ {now}"},
    ])

    return title, paragraphs


async def run_remix(
    skip_feishu: bool = False,
    patterns: list[dict] | None = None,
    strategies: list[str] | None = None,
) -> list[dict]:
    """
    执行完整的 Remix 阶段。

    Args:
        skip_feishu: 是否跳过飞书推送
        patterns: 可选的创意模式列表（不传则从文件加载）
        strategies: 指定策略，None 则全部执行

    Returns:
        附带 Seedance Prompt 的创意方案列表
    """
    cfg = get_config()
    niche = cfg.get("niche", {})
    niche_name = niche.get("name", "未命名赛道")
    niche_keywords = niche.get("keywords", [])
    daily_count = cfg.get("output", {}).get("daily_count", 3)
    remix_cfg = cfg.get("remix", {})
    enabled_strategies = strategies or remix_cfg.get("strategies", None)

    logger.info("=" * 60)
    logger.info("🎨 Remix 阶段启动 — 赛道: %s", niche_name)
    logger.info("   目标: 生成 %d 条创意方案 + Seedance Prompt", daily_count)
    logger.info("=" * 60)

    daily_dir = _get_daily_dir()

    # ── 1. 读取 Analyze 产出 ──
    if patterns is None:
        patterns = _load_analysis_results(daily_dir)

    if not patterns:
        logger.warning("⚠️  没有可用的创意模式")
        return []

    # ── 2. 执行创意策略 ──
    try:
        ideas = await run_all_strategies(
            patterns,
            niche=niche_name,
            niche_keywords=niche_keywords,
            daily_count=daily_count,
            strategies=enabled_strategies,
        )
    except Exception as e:
        logger.error("❌ 创意策略执行失败: %s", e)
        return []

    if not ideas:
        logger.warning("⚠️  未生成任何创意方案")
        return []

    # ── 3. 生成 Seedance Prompt ──
    try:
        ideas_with_prompts = await generate_prompts_for_ideas(
            ideas, niche=niche_name, niche_keywords=niche_keywords
        )
    except Exception as e:
        logger.error("❌ Prompt 生成失败: %s", e)
        ideas_with_prompts = ideas

    # ── 4. 保存结果 ──
    _save_remix_results(ideas_with_prompts, daily_dir)

    # ── 5. 飞书通知 ──
    if not skip_feishu:
        try:
            title, paragraphs = _format_remix_for_feishu(ideas_with_prompts, niche_name)
            await send_feishu_rich_text(title, paragraphs)
            logger.info("📨 飞书推送完成")
        except Exception as e:
            logger.error("❌ 飞书推送失败: %s", e)

    # ── 汇总 ──
    logger.info("=" * 60)
    logger.info("✅ Remix 阶段完成")
    logger.info("   创意方案: %d 个", len(ideas_with_prompts))
    prompt_ok = sum(1 for i in ideas_with_prompts if i.get("prompt_result"))
    logger.info("   Prompt 成功: %d/%d", prompt_ok, len(ideas_with_prompts))
    logger.info("   数据目录: %s", daily_dir)
    logger.info("=" * 60)

    return ideas_with_prompts
