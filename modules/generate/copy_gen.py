"""
平台文案生成器

根据创意方案，用 LLM 生成适配各平台风格的文案。
当前支持：抖音（标题+描述+标签）、小红书（标题+正文+标签）
"""
from __future__ import annotations

import logging

from ..llm import chat_completion_json

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
你是一位资深短视频文案专家，擅长为不同平台撰写爆款文案。
根据提供的创意方案，生成适配各平台风格的文案。

要求：
- 文案要有网感，贴合平台用户的阅读习惯
- 标题要有吸引力，能引起点击欲望
- 善用 emoji 增加可读性和互动感
- 标签要精准，覆盖目标人群高频搜索词
- 文案风格应与创意的情感曲线匹配

请严格按照 JSON 格式输出。"""

# 各平台文案规格说明
_PLATFORM_SPECS = {
    "douyin": (
        "抖音（douyin）：生成 title（≤30字，吸睛标题，适合信息流展示）、"
        "description（≤200字，引导互动，带话题感）、"
        "tags（5-8个热门标签，不带#号，覆盖赛道+话题+情感词）"
    ),
    "xiaohongshu": (
        "小红书（xiaohongshu）：生成 title（≤20字，种草感/好奇感标题）、"
        "body（≤500字，分段+emoji，口语化种草风格，像朋友分享）、"
        "tags（5-10个精准标签，不带#号，涵盖品类词+场景词+情感词）"
    ),
}


async def generate_copy(
    idea: dict,
    platforms: list[str] | None = None,
) -> dict:
    """
    根据创意方案生成多平台文案。

    Args:
        idea: 创意方案（包含 title, concept, hook, tags, emotion_curve 等）
        platforms: 目标平台列表，默认 ["douyin", "xiaohongshu"]

    Returns:
        {
            "douyin": {"title": "...", "description": "...", "tags": [...]},
            "xiaohongshu": {"title": "...", "body": "...", "tags": [...]}
        }
    """
    platforms = platforms or ["douyin", "xiaohongshu"]
    specs = [_PLATFORM_SPECS[p] for p in platforms if p in _PLATFORM_SPECS]

    if not specs:
        logger.warning("未找到匹配的平台规格: %s", platforms)
        return {}

    emotion = " → ".join(idea.get("emotion_curve", []))
    hook = idea.get("hook", {})
    tags = ", ".join(idea.get("tags", []))

    # 结构信息
    structure_lines = []
    for seg in idea.get("structure", []):
        if isinstance(seg, dict):
            structure_lines.append(f"  {seg.get('time', '')}: {seg.get('desc', '')}")
        else:
            structure_lines.append(f"  {seg}")
    structure_text = "\n".join(structure_lines) if structure_lines else "无"

    user_msg = f"""请为以下创意方案生成平台文案：

【创意标题】{idea.get('title', '')}
【创意概念】{idea.get('concept', '')}
【钩子类型】{hook.get('type', '')} — {hook.get('desc', '')}
【内容结构】
{structure_text}
【情感曲线】{emotion}
【视觉风格】{idea.get('visual_style', '')}
【BGM 氛围】{idea.get('bgm_mood', '')}
【标签】{tags}

请为以下平台生成文案：
{chr(10).join(f"- {s}" for s in specs)}

输出格式（JSON）：
{{
  "douyin": {{"title": "...", "description": "...", "tags": ["标签1", "标签2", ...]}},
  "xiaohongshu": {{"title": "...", "body": "...", "tags": ["标签1", "标签2", ...]}}
}}
只输出请求的平台，不要输出未请求的平台。"""

    logger.info("✍️  生成文案: %s（%s）", idea.get("title", ""), ", ".join(platforms))

    result = await chat_completion_json(
        system_prompt=_SYSTEM_PROMPT,
        user_message=user_msg,
        temperature=0.85,
        max_tokens=2048,
    )

    # 确保返回的是 dict，兼容 LLM 可能的嵌套格式
    if isinstance(result, dict) and len(result) == 1 and "copy" in result:
        result = result["copy"]

    logger.info("✅ 文案生成完成: %s", list(result.keys()))
    return result
