"""
长视频分段生成与拼接模块

将一个故事/主题通过 LLM 拆分为多个连贯的视频片段，
逐段调用 Seedance API 生成，最后用 ffmpeg 自动拼接为一条完整长视频。

流程：
┌────────────┐    ┌───────────────┐    ┌──────────────┐    ┌────────────┐
│ 1. 故事拆分 │ →  │ 2. 逐段 Prompt │ →  │ 3. 并发生成   │ →  │ 4. 拼接成片 │
│  (LLM)     │    │   (LLM)       │    │  (Seedance)  │    │  (ffmpeg)  │
└────────────┘    └───────────────┘    └──────────────┘    └────────────┘

限制：
- Seedance 单段最长 12 秒，推荐 5-10 秒
- 总视频长度 = 段数 × 单段时长，建议 30-120 秒
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path

from ..llm import chat_completion_json
from .volcengine_video import generate_video, MODE_DEFAULT
from .video_post import concat_videos

logger = logging.getLogger(__name__)

# ── LLM 故事分段系统提示词 ──────────────────────────────

STORY_SPLIT_SYSTEM = """\
你是一位专业的视频导演和编剧，擅长将一个故事或主题拆解为多个连贯的短视频片段。

## 任务
将用户提供的故事/主题拆分为多个视频片段（每段 {segment_duration} 秒），确保：
1. 片段之间有清晰的叙事递进（起承转合）
2. 视觉风格全程一致（同一套角色造型、色调、场景风格）
3. 每段的结尾与下一段的开头在画面上自然衔接
4. 每段独立也有可看性，但串联后构成完整故事

## 输出要求
以 JSON 格式输出：
{{
  "title": "整体视频标题",
  "total_segments": 段数,
  "style_guide": "全局视觉风格描述（所有片段共用，确保一致性）",
  "bgm_mood": "全局 BGM 情绪",
  "segments": [
    {{
      "segment_id": 1,
      "duration_seconds": {segment_duration},
      "scene_desc": "本段场景与动作的详细描述（2-3 句话）",
      "camera_move": "运镜方式（推/拉/跟/环绕/固定等）",
      "emotion": "本段的情感基调",
      "transition_hint": "与下一段的衔接提示（最后一段可为空）"
    }}
  ]
}}"""

# ── LLM 片段 Prompt 生成系统提示词 ──────────────────────

SEGMENT_PROMPT_SYSTEM = """\
你是 Seedance 2.0 的专业提示词工程师。
你的任务是为一个长视频中的「单个片段」生成 Seedance 提示词。

## 关键约束
1. 必须严格遵守全局视觉风格，确保所有片段拼接后风格统一
2. 开头画面必须与上一段的结尾自然衔接（如果有前一段信息）
3. 提示词需包含：主体、场景、动作、运镜、音效、风格
4. 不要在提示词中包含文字/字幕内容，Seedance 不支持文字渲染

## Seedance 2.0 提示词结构
[主体/角色] + [场景/环境] + [动作/运动] + [运镜] + [音频/音效] + [风格/氛围]

## 输出
以 JSON 格式返回：
{{
  "seedance_prompt": "可直接提交给 Seedance 的完整提示词",
  "duration_seconds": 时长
}}"""


async def plan_story_segments(
    topic: str,
    *,
    total_duration: int = 60,
    segment_duration: int = 8,
    extra_instructions: str = "",
) -> dict:
    """
    使用 LLM 将一个主题/故事拆分为多个连贯的视频片段。

    Args:
        topic: 视频的主题或故事概要
        total_duration: 目标总时长（秒），默认 60 秒
        segment_duration: 每段时长（秒），2-12 之间，默认 8 秒
        extra_instructions: 额外的创作指导（可选）

    Returns:
        包含 segments 列表的拆分计划
    """
    segment_duration = max(2, min(12, segment_duration))
    num_segments = max(2, total_duration // segment_duration)

    system = STORY_SPLIT_SYSTEM.format(segment_duration=segment_duration)

    user_msg = (
        f"请将以下主题拆分为 {num_segments} 个视频片段，"
        f"每段约 {segment_duration} 秒，总时长约 {total_duration} 秒：\n\n"
        f"【主题/故事】\n{topic}\n"
    )
    if extra_instructions:
        user_msg += f"\n【额外要求】\n{extra_instructions}\n"

    logger.info("📋 开始故事分段规划: 目标 %d 段 × %ds = %ds", num_segments, segment_duration, total_duration)

    result = await chat_completion_json(system, user_msg, temperature=0.8, max_tokens=4096)

    actual_segments = len(result.get("segments", []))
    logger.info("✅ 故事分段完成: %s（%d 段）", result.get("title", ""), actual_segments)
    return result


async def generate_segment_prompt(
    segment: dict,
    style_guide: str,
    bgm_mood: str,
    *,
    prev_segment: dict | None = None,
) -> dict:
    """
    为单个视频片段生成 Seedance 提示词，确保与全局风格一致。

    Args:
        segment: 片段描述（来自 plan_story_segments）
        style_guide: 全局视觉风格描述
        bgm_mood: 全局 BGM 情绪
        prev_segment: 前一个片段的信息（用于衔接）
    """
    prev_hint = ""
    if prev_segment:
        prev_hint = (
            f"\n【上一段信息】\n"
            f"场景: {prev_segment.get('scene_desc', '')}\n"
            f"衔接提示: {prev_segment.get('transition_hint', '')}\n"
        )

    user_msg = (
        f"【全局视觉风格】\n{style_guide}\n"
        f"【全局 BGM 情绪】\n{bgm_mood}\n"
        f"{prev_hint}"
        f"\n【当前片段】\n"
        f"编号: {segment.get('segment_id', '?')}\n"
        f"时长: {segment.get('duration_seconds', 8)} 秒\n"
        f"场景: {segment.get('scene_desc', '')}\n"
        f"运镜: {segment.get('camera_move', '')}\n"
        f"情感: {segment.get('emotion', '')}\n"
        f"衔接: {segment.get('transition_hint', '')}\n"
    )

    result = await chat_completion_json(
        SEGMENT_PROMPT_SYSTEM, user_msg, temperature=0.7, max_tokens=1024,
    )
    return result


async def generate_long_video(
    topic: str,
    save_dir: Path,
    *,
    total_duration: int = 60,
    segment_duration: int = 8,
    extra_instructions: str = "",
    video_ratio: str = "9:16",
    video_resolution: str = "720p",
    generate_audio: bool = True,
    transition: str = "fade",
    transition_duration: float = 0.5,
    max_concurrent: int = 2,
    poll_interval: int = 10,
    poll_timeout: int = 600,
    mode: str = MODE_DEFAULT,
) -> dict:
    """
    一站式长视频生成：故事拆分 → 逐段 Prompt → 并发生成 → 拼接成片。

    Args:
        topic: 视频主题或故事概要
        save_dir: 视频保存目录
        total_duration: 目标总时长（秒）
        segment_duration: 每段时长（秒），建议 5-10
        extra_instructions: 额外创作指导
        video_ratio: 宽高比
        video_resolution: 分辨率
        generate_audio: 是否生成音频
        transition: 转场效果 "none" / "fade" / "dissolve"
        transition_duration: 转场时长（秒）
        max_concurrent: 最大并发生成数
        poll_interval: 轮询间隔
        poll_timeout: 单段超时
        mode: 生成模式

    Returns:
        {
            "title": "...",
            "plan": {...},
            "segments": [{segment_id, prompt, video_result, local_path}, ...],
            "final_video": "最终拼接视频路径",
            "total_duration_actual": 实际总时长,
            "success_count": 成功段数,
            "fail_count": 失败段数
        }
    """
    save_dir = Path(save_dir)
    save_dir.mkdir(parents=True, exist_ok=True)
    segments_dir = save_dir / "segments"
    segments_dir.mkdir(exist_ok=True)

    logger.info("=" * 60)
    logger.info("🎬 长视频生成启动")
    logger.info("   主题: %s", topic[:60])
    logger.info("   目标: %ds（%d段 × %ds）", total_duration, total_duration // segment_duration, segment_duration)
    logger.info("   规格: %s %s 音频=%s", video_ratio, video_resolution, generate_audio)
    logger.info("   转场: %s (%.1fs)", transition, transition_duration)
    logger.info("=" * 60)

    # ── 阶段 1: LLM 故事分段 ──
    logger.info("📋 [阶段 1/4] 故事分段规划...")
    plan = await plan_story_segments(
        topic,
        total_duration=total_duration,
        segment_duration=segment_duration,
        extra_instructions=extra_instructions,
    )

    segments = plan.get("segments", [])
    style_guide = plan.get("style_guide", "")
    bgm_mood = plan.get("bgm_mood", "")

    plan_path = save_dir / "long_video_plan.json"
    with open(plan_path, "w", encoding="utf-8") as f:
        json.dump(plan, f, ensure_ascii=False, indent=2)
    logger.info("💾 分段计划已保存: %s", plan_path)

    # ── 阶段 2: 逐段生成 Seedance Prompt ──
    logger.info("✍️  [阶段 2/4] 逐段生成 Seedance 提示词（%d 段）...", len(segments))
    segment_prompts = []
    for i, seg in enumerate(segments):
        prev = segments[i - 1] if i > 0 else None
        logger.info("   生成 Prompt [%d/%d]: %s", i + 1, len(segments), seg.get("scene_desc", "")[:40])
        prompt_result = await generate_segment_prompt(seg, style_guide, bgm_mood, prev_segment=prev)
        segment_prompts.append({
            **seg,
            "prompt_result": prompt_result,
        })

    # ── 阶段 3: 并发视频生成 ──
    logger.info("🎥 [阶段 3/4] 并发视频生成（最大并发 %d）...", max_concurrent)
    semaphore = asyncio.Semaphore(max_concurrent)

    async def _gen_one(idx: int, seg_data: dict) -> dict:
        async with semaphore:
            seg_id = seg_data.get("segment_id", idx + 1)
            prompt = seg_data.get("prompt_result", {}).get("seedance_prompt", "")
            dur = seg_data.get("duration_seconds", segment_duration)

            if not prompt:
                logger.warning("⚠️  片段 %d 缺少 Prompt，跳过", seg_id)
                return {**seg_data, "video_result": None, "local_path": None, "error": "缺少 prompt"}

            logger.info("📹 片段 %d/%d 开始生成...", seg_id, len(segment_prompts))
            try:
                video_result = await generate_video(
                    prompt,
                    segments_dir,
                    filename=f"seg_{seg_id:02d}.mp4",
                    duration=max(2, min(12, dur)),
                    ratio=video_ratio,
                    resolution=video_resolution,
                    generate_audio=generate_audio,
                    poll_interval=poll_interval,
                    poll_timeout=poll_timeout,
                    mode=mode,
                )
                local_path = video_result.get("local_path")
                logger.info("✅ 片段 %d 生成成功: %s", seg_id, local_path)
                return {**seg_data, "video_result": video_result, "local_path": local_path, "error": None}

            except Exception as e:
                logger.error("❌ 片段 %d 生成失败: %s", seg_id, e)
                return {**seg_data, "video_result": None, "local_path": None, "error": str(e)}

    tasks = [_gen_one(i, sp) for i, sp in enumerate(segment_prompts)]
    results = await asyncio.gather(*tasks)

    # 统计成功/失败
    success_paths = []
    success_count = 0
    fail_count = 0
    for r in results:
        if r.get("local_path"):
            success_paths.append(Path(r["local_path"]))
            success_count += 1
        else:
            fail_count += 1

    logger.info("📊 视频生成完成: %d 成功, %d 失败", success_count, fail_count)

    # ── 阶段 4: 拼接成片 ──
    final_path = None
    if len(success_paths) >= 2:
        logger.info("🔗 [阶段 4/4] 拼接 %d 段视频（转场: %s）...", len(success_paths), transition)
        final_path = save_dir / "long_video_final.mp4"
        try:
            await concat_videos(
                success_paths,
                final_path,
                transition=transition,
                transition_duration=transition_duration,
            )
            logger.info("✅ 长视频拼接完成: %s", final_path)
        except Exception as e:
            logger.error("❌ 视频拼接失败: %s", e)
            final_path = None
    elif len(success_paths) == 1:
        import shutil
        final_path = save_dir / "long_video_final.mp4"
        shutil.copy2(success_paths[0], final_path)
        logger.info("📋 仅 1 段成功，直接使用: %s", final_path)
    else:
        logger.error("❌ 没有成功生成的视频片段，无法拼接")

    # ── 保存完整结果 ──
    output = {
        "title": plan.get("title", ""),
        "topic": topic,
        "plan": plan,
        "segments": [
            {
                "segment_id": r.get("segment_id"),
                "scene_desc": r.get("scene_desc"),
                "prompt": r.get("prompt_result", {}).get("seedance_prompt", ""),
                "local_path": r.get("local_path"),
                "error": r.get("error"),
            }
            for r in results
        ],
        "final_video": str(final_path) if final_path else None,
        "success_count": success_count,
        "fail_count": fail_count,
        "transition": transition,
        "timestamp": datetime.now().isoformat(),
    }

    result_path = save_dir / "long_video_result.json"
    with open(result_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    logger.info("=" * 60)
    logger.info("🎬 长视频生成完成!")
    logger.info("   标题: %s", output["title"])
    logger.info("   片段: %d 成功 / %d 失败", success_count, fail_count)
    if final_path:
        size_mb = final_path.stat().st_size / (1024 * 1024) if final_path.exists() else 0
        logger.info("   成片: %s (%.1fMB)", final_path, size_mb)
    logger.info("   结果: %s", result_path)
    logger.info("=" * 60)

    return output
