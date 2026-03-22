"""
Seedance 2.0 Prompt 生成器
将创意方案自动转换为 Seedance 2.0 格式的视频 Prompt。
支持分时段描述、运镜语言、音效设计等。
参考 seedance2-skill 的 Prompt 结构规范。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import yaml

from ..llm import chat_completion_json

logger = logging.getLogger(__name__)

# ── 模板目录 ──
TEMPLATES_DIR = Path(__file__).parent / "templates"

# ── Seedance Prompt 生成的系统提示词 ──
PROMPT_GEN_SYSTEM = """你是 Seedance 2.0 的专业提示词工程师。你的任务是将一份创意方案转化为可直接提交给 Seedance 2.0 的视频提示词。

## Seedance 2.0 提示词规范

### 基本结构
一条高质量的提示词遵循以下结构：
[主体/人物设定] + [场景/环境] + [动作/运动描述] + [运镜语言] + [分时段描述] + [转场/特效] + [音频/音效设计] + [风格/氛围]

### 分时段提示词（推荐）
精确控制画面内容，按时间段描述：
0–3秒：[开场画面描述、运镜、动作]
3–6秒：[中段发展]
6–10秒：[高潮或关键动作]
10–15秒：[收尾、定格画面]

### 常用运镜
- 推镜头/慢推：镜头向主体靠近
- 拉镜头/后拉：镜头远离主体
- 左摇/右摇：镜头水平旋转
- 跟随镜头/跟拍：镜头跟随主体移动
- 环绕镜头：镜头围绕主体旋转
- 一镜到底：全程无剪辑
- 低角度仰拍：低机位向上拍
- 俯拍/鸟瞰：从高处向下拍

### 风格修饰词
- 画面：电影级质感、浅景深、高饱和、暖色调...
- 氛围：温暖治愈、紧张悬疑、喜剧风格、轻松日常...
- 音频：BGM情绪 + 环境音效 + 特殊音效

### 注意事项
- 不支持写实真人脸部素材
- 生成时长：4-15秒
- 如有参考图/视频，使用 @图片N / @视频N 引用
- 每个 @ 引用必须标注用途
- 时长与内容复杂度要匹配

## 输出要求

输出 JSON 格式：
{
  "seedance_prompt": "完整的 Seedance 2.0 提示词（纯文本，可直接提交）",
  "duration_seconds": 生成时长建议(4-15),
  "style_tags": ["风格标签1", "风格标签2"],
  "required_assets": ["需要准备的素材描述（如首帧图片等）"],
  "notes": "补充说明或使用建议"
}"""


def _load_template(template_name: str) -> dict | None:
    """从 templates/ 目录加载 YAML 模板"""
    path = TEMPLATES_DIR / f"{template_name}.yaml"
    if not path.exists():
        return None
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _find_best_template(idea: dict, niche: str) -> dict | None:
    """根据创意方案和赛道，自动选择最匹配的模板"""
    tags = set(idea.get("tags", []))

    # 按赛道查找专属模板
    niche_templates = {
        "萌宠": ["pet_cute", "pet_funny"],
    }
    candidate_names = niche_templates.get(niche, []) + ["generic"]

    # 简单匹配：有搞笑标签优先用 funny 模板
    funny_keywords = {"搞笑", "幽默", "沙雕", "反转", "喜剧"}
    if tags & funny_keywords:
        candidate_names = [n for n in candidate_names if "funny" in n] + candidate_names

    for name in candidate_names:
        tpl = _load_template(name)
        if tpl is not None:
            logger.debug("使用模板: %s", name)
            return tpl

    return None


async def generate_seedance_prompt(
    idea: dict,
    niche: str = "",
    niche_keywords: list[str] | None = None,
) -> dict:
    """
    将单个创意方案转化为 Seedance 2.0 视频 Prompt。

    Args:
        idea: 创意方案（来自 strategy.py 的输出）
        niche: 赛道名
        niche_keywords: 赛道关键词

    Returns:
        包含 seedance_prompt, duration_seconds, style_tags 等的字典
    """
    template = _find_best_template(idea, niche)
    template_hint = ""
    if template:
        template_hint = (
            f"\n\n参考模板风格：\n"
            f"名称：{template.get('name', '')}\n"
            f"描述：{template.get('description', '')}\n"
            f"视觉风格：{template.get('visual_style', '')}\n"
            f"BGM情绪：{template.get('bgm_mood', '')}\n"
            f"运镜偏好：{', '.join(template.get('camera_moves', []))}\n"
            f"示例：{template.get('example_prompt', '')}"
        )

    idea_text = json.dumps(idea, ensure_ascii=False, indent=2)
    user_message = (
        f"赛道：{niche}\n"
        f"赛道关键词：{', '.join(niche_keywords or [])}\n\n"
        f"请将以下创意方案转化为 Seedance 2.0 视频提示词：\n{idea_text}"
        f"{template_hint}"
    )

    result = await chat_completion_json(
        PROMPT_GEN_SYSTEM,
        user_message,
        temperature=0.7,
        max_tokens=2048,
    )
    return result


async def generate_prompts_for_ideas(
    ideas: list[dict],
    niche: str = "",
    niche_keywords: list[str] | None = None,
) -> list[dict]:
    """
    为一组创意方案批量生成 Seedance Prompt。

    Returns:
        每个创意方案附带 prompt_result 字段的列表
    """
    results = []
    for i, idea in enumerate(ideas, 1):
        logger.info("🎬 生成 Prompt [%d/%d]: %s", i, len(ideas), idea.get("title", "未命名"))
        try:
            prompt_result = await generate_seedance_prompt(idea, niche, niche_keywords)
            idea_with_prompt = {**idea, "prompt_result": prompt_result}
            results.append(idea_with_prompt)
            logger.info("   ✅ Prompt 生成完成 (%ds)", prompt_result.get("duration_seconds", 0))
        except Exception as e:
            logger.error("   ❌ Prompt 生成失败: %s", e)
            results.append({**idea, "prompt_result": None, "prompt_error": str(e)})

    return results
