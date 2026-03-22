"""
创意模式提取器
从爆款内容中提取可复用的创意模式卡片（Creative Pattern Card）。
使用 LLM 分析内容的钩子类型、结构、情感曲线、视觉风格等维度。
"""
from __future__ import annotations

import logging
from datetime import datetime

from ..llm import chat_completion_json

logger = logging.getLogger(__name__)

# ── 创意模式提取的系统提示词 ──
EXTRACT_SYSTEM_PROMPT = """你是一位资深的短视频内容策略分析师，擅长拆解爆款内容的创意逻辑。

你的任务是分析一条热门内容，提取出其中**可复用的创意模式**（Creative Pattern），
输出一张结构化的"创意模式卡片"。

分析维度包括：
1. **钩子类型 (hook)**：开头如何吸引注意力（反差对比/悬念/共鸣/争议/视觉冲击/热点借势/情感共鸣）
2. **内容结构 (structure)**：按时间段拆解内容节奏（如 "铺垫(3s) → 触发(2s) → 反应(5s) → 反转结尾(2s)"）
3. **情感曲线 (emotion_curve)**：观众的情绪变化路径（如 "好奇→紧张→释放→感动"）
4. **视觉风格 (visual_style)**：拍摄角度、滤镜、字幕等视觉元素特点
5. **BGM/音效情绪 (bgm_mood)**：配乐氛围（轻快/紧张/温馨/燃...）
6. **可复用标签 (tags)**：归纳为 3-5 个标签，方便后续检索
7. **内容类型推断 (content_type)**：新闻热点/搞笑/情感/知识科普/生活记录/挑战/故事...
8. **爆款原因分析 (viral_reason)**：一句话总结为什么这条内容能火

请根据标题、热度数据和平台上下文进行合理推断。如果无法确定某个维度，给出最可能的判断并标注"推断"。

输出格式（JSON）：
{
  "pattern_id": "自动生成的ID，格式: 类型缩写-日期-序号，如 news-20260322-001",
  "source": {
    "platform": "平台名",
    "title": "原标题",
    "link": "链接",
    "popularity": 热度数字
  },
  "hook": {
    "type": "钩子类型",
    "desc": "具体描述"
  },
  "structure": ["时段1描述", "时段2描述", ...],
  "emotion_curve": ["情感节点1", "情感节点2", ...],
  "visual_style": {
    "拍摄角度": "描述",
    "滤镜": "描述",
    "字幕风格": "描述"
  },
  "bgm_mood": "配乐情绪描述",
  "content_type": "内容类型",
  "viral_reason": "爆款原因",
  "tags": ["标签1", "标签2", ...],
  "engagement_score": 热度归一化评分(0-100)
}"""

# ── 批量分析的系统提示词 ──
BATCH_EXTRACT_SYSTEM_PROMPT = """你是一位资深的短视频内容策略分析师，擅长批量拆解爆款内容的创意逻辑。

你的任务是分析一批热门内容，为每条内容提取**可复用的创意模式**（Creative Pattern）。

对于每条内容，提取以下维度：
1. **钩子类型 (hook)**：开头如何抓注意力（反差/悬念/共鸣/争议/视觉冲击/热点借势）
2. **内容结构 (structure)**：时间段节奏拆解
3. **情感曲线 (emotion_curve)**：观众情绪路径
4. **视觉风格 (visual_style)**：拍摄/滤镜/字幕特点
5. **BGM情绪 (bgm_mood)**：配乐氛围
6. **标签 (tags)**：3-5个复用标签
7. **内容类型 (content_type)**：分类
8. **爆款原因 (viral_reason)**：一句话总结

输出格式（JSON）：
{
  "patterns": [
    {
      "pattern_id": "类型-日期-序号",
      "source": {"platform": "...", "title": "...", "link": "...", "popularity": 0},
      "hook": {"type": "...", "desc": "..."},
      "structure": ["..."],
      "emotion_curve": ["..."],
      "visual_style": {"拍摄角度": "...", "滤镜": "...", "字幕风格": "..."},
      "bgm_mood": "...",
      "content_type": "...",
      "viral_reason": "...",
      "tags": ["..."],
      "engagement_score": 0
    }
  ]
}"""


def _normalize_score(popularity: int, max_popularity: int) -> float:
    """将热度值归一化到 0-100 区间"""
    if max_popularity <= 0:
        return 0.0
    return round(min(popularity / max_popularity * 100, 100), 1)


async def extract_single_pattern(item: dict, date_str: str, index: int) -> dict:
    """
    对单条爆款内容提取创意模式。

    Args:
        item: scout 阶段的单条内容数据
        date_str: 日期字符串，如 "20260322"
        index: 序号（用于生成 pattern_id）
    """
    user_message = (
        f"请分析以下热门内容并提取创意模式卡片：\n\n"
        f"标题：{item.get('title', '未知')}\n"
        f"平台：{item.get('platform', '未知')}\n"
        f"热度：{item.get('popularity', 0):,}\n"
        f"链接：{item.get('link', '')}\n"
        f"排名：第{item.get('rank', '?')}名\n"
        f"视频数：{item.get('video_count', 0)}\n"
        f"爆款分数：{item.get('score', 0):.1f}\n"
        f"\n参考日期：{date_str}，序号：{index:03d}"
    )

    result = await chat_completion_json(
        EXTRACT_SYSTEM_PROMPT,
        user_message,
        temperature=0.5,
        max_tokens=2048,
    )
    return result


async def extract_patterns_batch(
    items: list[dict],
    date_str: str | None = None,
    max_items: int = 10,
) -> list[dict]:
    """
    批量提取创意模式卡片。
    将所有条目一次性发给 LLM 分析，比逐条调用更高效。

    Args:
        items: scout 阶段的爆款内容列表
        date_str: 日期字符串，默认取当天
        max_items: 最多分析的条目数

    Returns:
        创意模式卡片列表
    """
    if not date_str:
        date_str = datetime.now().strftime("%Y%m%d")

    items_to_analyze = items[:max_items]
    max_pop = max((it.get("popularity", 0) for it in items_to_analyze), default=1)

    items_text = []
    for i, item in enumerate(items_to_analyze, 1):
        score = _normalize_score(item.get("popularity", 0), max_pop)
        items_text.append(
            f"#{i} [{item.get('platform', 'douyin')}] "
            f"标题：{item.get('title', '未知')} | "
            f"热度：{item.get('popularity', 0):,} | "
            f"链接：{item.get('link', '')} | "
            f"视频数：{item.get('video_count', 0)} | "
            f"参考评分：{score}"
        )

    user_message = (
        f"请分析以下 {len(items_to_analyze)} 条热门内容，"
        f"为每条提取创意模式卡片。\n"
        f"日期：{date_str}\n\n"
        + "\n".join(items_text)
    )

    logger.info("📝 正在分析 %d 条内容的创意模式...", len(items_to_analyze))

    result = await chat_completion_json(
        BATCH_EXTRACT_SYSTEM_PROMPT,
        user_message,
        temperature=0.5,
        max_tokens=4096,
    )

    patterns = result.get("patterns", [])
    logger.info("✅ 提取到 %d 个创意模式", len(patterns))
    return patterns
