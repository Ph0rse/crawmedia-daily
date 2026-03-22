"""
Generate 阶段主入口
编排：读取 Remix 产出 → 视频生成 → 后处理 → 封面图生成 → 文案生成 → 存储 → 飞书通知

数据流：
    remixed.json
        → [Seedance 视频生成]     (default / draft / flex 三种模式)
        → [后处理: 淡入淡出+字幕]  (ffmpeg)
        → [封面图: AI 生成 or 视频截帧]
        → [文案: LLM 多平台适配]
        → generated.json
"""
from __future__ import annotations

import asyncio
import json
import logging
import shutil
from datetime import datetime
from pathlib import Path

from ..config import get_config
from ..feishu import send_feishu_rich_text
from .volcengine_video import (
    generate_video,
    generate_video_with_draft,
    MODE_DEFAULT, MODE_DRAFT, MODE_FLEX,
)
from .volcengine_image import generate_cover_from_idea
from .copy_gen import generate_copy

logger = logging.getLogger(__name__)


def _get_daily_dir() -> Path:
    cfg = get_config()
    data_dir = Path(cfg.get("output", {}).get("data_dir", "./data"))
    today = datetime.now().strftime("%Y-%m-%d")
    daily_dir = data_dir / "daily" / today
    daily_dir.mkdir(parents=True, exist_ok=True)
    return daily_dir


def _load_remix_results(daily_dir: Path) -> list[dict]:
    """从当天目录加载 Remix 阶段产出"""
    path = daily_dir / "remixed.json"
    if not path.exists():
        logger.warning("⚠️  未找到 remixed.json，尝试查找最近的数据...")
        return []
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    ideas = data.get("ideas", [])
    logger.info("📂 加载创意方案: %d 个", len(ideas))
    return ideas


def _save_generate_results(results: list[dict], daily_dir: Path) -> Path:
    """保存 Generate 阶段产出到 generated.json"""
    output_path = daily_dir / "generated.json"
    payload = {
        "stage": "generate",
        "timestamp": datetime.now().isoformat(),
        "item_count": len(results),
        "items": results,
    }
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    logger.info("💾 生成结果已保存: %s", output_path)
    return output_path


def _build_subtitles_from_structure(idea: dict) -> list[dict]:
    """
    从创意方案的 structure 字段自动生成字幕列表。
    例如 [{"time": "0-3s", "desc": "展示场景"}] → [{"start": 0, "end": 3, "text": "展示场景"}]
    """
    subtitles = []
    for seg in idea.get("structure", []):
        if not isinstance(seg, dict):
            continue
        time_str = seg.get("time", "")
        desc = seg.get("desc", "")
        if not time_str or not desc:
            continue
        # 解析 "0-3s" / "3-6s" / "6-10s" 格式
        try:
            clean = time_str.replace("s", "").replace("S", "")
            parts = clean.split("-")
            start = float(parts[0])
            end = float(parts[1]) if len(parts) > 1 else start + 3
            # 字幕不宜过长，截断
            text = desc[:40] if len(desc) > 40 else desc
            subtitles.append({"start": start, "end": end, "text": text})
        except (ValueError, IndexError):
            continue
    return subtitles


async def _generate_single_idea(
    idea: dict,
    idx: int,
    daily_dir: Path,
    *,
    platforms: list[str],
    video_ratio: str = "9:16",
    video_resolution: str = "720p",
    generate_audio: bool = True,
    poll_interval: int = 10,
    poll_timeout: int = 600,
    mode: str = MODE_DEFAULT,
    post_cfg: dict | None = None,
) -> dict:
    """
    为单个创意方案生成全部素材（视频 + 后处理 + 封面 + 文案）。
    各生成步骤独立容错，单项失败不影响其他项。
    """
    idea_id = idea.get("idea_id", f"idea_{idx}")
    title = idea.get("title", "未命名")
    prompt_result = idea.get("prompt_result", {})
    seedance_prompt = prompt_result.get("seedance_prompt", "")
    duration = prompt_result.get("duration_seconds") or idea.get("duration_seconds", 5)
    post_cfg = post_cfg or {}

    logger.info("━" * 50)
    logger.info("🎯 [%d] 开始生成: %s (%s) [模式: %s]", idx, title, idea_id, mode)

    result = {
        "idea_id": idea_id,
        "title": title,
        "strategy": idea.get("strategy"),
        "video": None,
        "post_process": None,
        "cover": None,
        "copy": None,
        "errors": [],
    }

    # ── 1. 视频生成 ──────────────────────────────────────
    video_local_path = None
    if seedance_prompt:
        try:
            videos_dir = daily_dir / "videos"

            if mode == MODE_DRAFT:
                # 两阶段: 样片 → 高清成片
                video_result = await generate_video_with_draft(
                    seedance_prompt,
                    videos_dir,
                    filename=f"video_{idx}.mp4",
                    duration=min(12, max(2, duration)),
                    ratio=video_ratio,
                    final_resolution=video_resolution,
                    generate_audio=generate_audio,
                    poll_interval=poll_interval,
                    poll_timeout=poll_timeout,
                    auto_upgrade=True,
                )
                result["video"] = video_result
                # 最终视频路径优先取 final，否则取 draft
                final = video_result.get("final")
                draft = video_result.get("draft")
                video_local_path = (
                    (final or {}).get("local_path")
                    or (draft or {}).get("local_path")
                )
                task_id = (final or {}).get("task_id") or (draft or {}).get("task_id")
                logger.info("🎬 [%d] 两阶段视频生成完成: %s", idx, task_id)
            else:
                # 标准 / 离线模式
                video_result = await generate_video(
                    seedance_prompt,
                    videos_dir,
                    filename=f"video_{idx}.mp4",
                    duration=min(12, max(2, duration)),
                    ratio=video_ratio,
                    resolution=video_resolution,
                    generate_audio=generate_audio,
                    poll_interval=poll_interval,
                    poll_timeout=poll_timeout,
                    mode=mode,
                )
                result["video"] = video_result
                video_local_path = video_result.get("local_path")
                logger.info("🎬 [%d] 视频生成成功: %s", idx, video_result.get("task_id"))

        except Exception as e:
            err = f"视频生成失败: {e}"
            result["errors"].append(err)
            logger.error("❌ [%d] %s", idx, err)
    else:
        result["errors"].append("缺少 seedance_prompt，跳过视频生成")
        logger.warning("⚠️  [%d] 无 Prompt，跳过视频", idx)

    # ── 2. 视频后处理（需要 ffmpeg）────────────────────────
    if video_local_path and post_cfg.get("enabled", False):
        try:
            from .video_post import post_process_video

            raw_path = Path(video_local_path)
            final_path = raw_path.parent / f"final_{raw_path.name}"

            # 从创意结构自动生成字幕
            subtitles = None
            if post_cfg.get("auto_subtitle", False):
                subtitles = _build_subtitles_from_structure(idea)
                if subtitles:
                    logger.info("📝 [%d] 自动生成 %d 条字幕", idx, len(subtitles))

            post_result = await post_process_video(
                raw_path,
                final_path,
                subtitles=subtitles,
                fade_in=post_cfg.get("fade_in", 0.3),
                fade_out=post_cfg.get("fade_out", 0.3),
                extract_cover=post_cfg.get("extract_cover", True),
                cover_timestamp=post_cfg.get("cover_timestamp", 1.0),
            )
            result["post_process"] = post_result
            # 更新视频路径为后处理后的版本
            video_local_path = post_result.get("output_path", video_local_path)
            logger.info("🔧 [%d] 后处理完成: %s", idx, " → ".join(post_result.get("steps", [])))

        except EnvironmentError as e:
            logger.warning("⚠️  [%d] 跳过后处理（%s）", idx, e)
        except Exception as e:
            err = f"后处理失败: {e}"
            result["errors"].append(err)
            logger.error("❌ [%d] %s", idx, err)

    # ── 3. 封面图生成 ────────────────────────────────────
    # 如果后处理已截取了封面，就用截帧版本；否则 AI 生成
    post_cover = (result.get("post_process") or {}).get("cover_path")
    if post_cover:
        result["cover"] = {"local_path": post_cover, "source": "video_frame"}
        logger.info("🎨 [%d] 使用视频截帧封面", idx)
    else:
        try:
            covers_dir = daily_dir / "covers"
            cover_result = await generate_cover_from_idea(
                idea,
                covers_dir / f"cover_{idx}.jpg",
                ratio=video_ratio,
            )
            result["cover"] = cover_result
            logger.info("🎨 [%d] AI 封面图生成成功", idx)
        except Exception as e:
            err = f"封面图生成失败: {e}"
            result["errors"].append(err)
            logger.error("❌ [%d] %s", idx, err)

    # ── 4. 文案生成 ──────────────────────────────────────
    try:
        copy_result = await generate_copy(idea, platforms=platforms)
        result["copy"] = copy_result
        logger.info("✍️  [%d] 文案生成成功: %s", idx, list(copy_result.keys()))
    except Exception as e:
        err = f"文案生成失败: {e}"
        result["errors"].append(err)
        logger.error("❌ [%d] %s", idx, err)

    return result


def _format_generate_for_feishu(
    results: list[dict],
    niche_name: str,
) -> tuple[str, list[list[dict]]]:
    """将生成结果格式化为飞书富文本"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    title = f"🎬 【{niche_name}】内容生成完成（{now}）"

    paragraphs = []

    # 统计
    video_ok = sum(1 for r in results if r.get("video"))
    cover_ok = sum(1 for r in results if r.get("cover"))
    copy_ok = sum(1 for r in results if r.get("copy"))

    post_ok = sum(1 for r in results if r.get("post_process"))

    paragraphs.append([
        {"tag": "text", "text": (
            f"📊 生成统计：共 {len(results)} 个创意\n"
            f"   🎬 视频: {video_ok}/{len(results)}\n"
            f"   🔧 后处理: {post_ok}/{len(results)}\n"
            f"   🎨 封面: {cover_ok}/{len(results)}\n"
            f"   ✍️ 文案: {copy_ok}/{len(results)}\n"
        )},
    ])

    for r in results:
        strategy_emoji = {
            "combine": "🔀", "generalize": "🔄",
            "transfer": "🚀", "extend": "📐",
        }
        emoji = strategy_emoji.get(r.get("strategy", ""), "💡")

        # 视频信息（兼容 draft 模式的嵌套结构）
        video_info = "❌ 未生成"
        if r.get("video"):
            v = r["video"]
            # draft 模式返回 {draft: {...}, final: {...}}
            if "final" in v and v["final"]:
                vf = v["final"]
                video_info = f"✅ {vf.get('duration', '?')}s {vf.get('resolution', '?')} (样片→成片)"
                if vf.get("video_url"):
                    video_info += f"\n      📎 {vf['video_url'][:60]}..."
            elif "draft" in v and v["draft"]:
                vd = v["draft"]
                video_info = f"📋 {vd.get('duration', '?')}s 480p (仅样片)"
            elif v.get("status"):
                video_info = f"✅ {v.get('duration', '?')}s {v.get('resolution', '?')}"
                mode_tag = v.get("mode", "")
                if mode_tag == "flex":
                    video_info += " (离线)"
                if v.get("video_url"):
                    video_info += f"\n      📎 {v['video_url'][:60]}..."

        # 文案预览
        copy_preview = "❌ 未生成"
        if r.get("copy"):
            douyin_copy = r["copy"].get("douyin", {})
            if douyin_copy:
                copy_preview = f"✅ {douyin_copy.get('title', '')[:30]}"

        row = [
            {"tag": "text", "text": f"\n{'─' * 30}\n"},
            {"tag": "text", "text": (
                f"{emoji} {r.get('title', '未命名')}\n"
                f"   🎬 视频: {video_info}\n"
                f"   🎨 封面: {'✅' if r.get('cover') else '❌'}\n"
                f"   ✍️ 文案: {copy_preview}\n"
            )},
        ]

        if r.get("errors"):
            row.append({"tag": "text", "text": f"   ⚠️ 错误: {'; '.join(r['errors'])}\n"})

        paragraphs.append(row)

    paragraphs.append([
        {"tag": "text", "text": f"\n🎯 下一步：审批确认后将自动发布 ⏰ {now}"},
    ])

    return title, paragraphs


async def run_generate(
    skip_feishu: bool = False,
    ideas: list[dict] | None = None,
    max_concurrent: int = 2,
) -> list[dict]:
    """
    执行完整的 Generate 阶段。

    Args:
        skip_feishu: 是否跳过飞书推送
        ideas: 可选的创意方案列表（不传则从 remixed.json 加载）
        max_concurrent: 最大并发生成数（视频生成较慢，建议 1-2）

    Returns:
        生成结果列表，每项包含 video / cover / copy 信息
    """
    cfg = get_config()
    niche = cfg.get("niche", {})
    niche_name = niche.get("name", "未命名赛道")
    daily_count = cfg.get("output", {}).get("daily_count", 3)
    platforms = cfg.get("output", {}).get("platforms") or [cfg.get("output", {}).get("platform", "douyin")]
    if isinstance(platforms, str):
        platforms = [platforms]

    # 生成配置
    gen_cfg = cfg.get("generate", {})
    video_ratio = gen_cfg.get("video_ratio", "9:16")
    video_resolution = gen_cfg.get("video_resolution", "720p")
    generate_audio = gen_cfg.get("generate_audio", True)
    poll_interval = gen_cfg.get("poll_interval", 10)
    poll_timeout = gen_cfg.get("poll_timeout", 600)
    mode = gen_cfg.get("mode", MODE_DEFAULT)
    post_cfg = gen_cfg.get("post_process", {})

    mode_labels = {
        MODE_DEFAULT: "标准",
        MODE_DRAFT: "样片→成片",
        MODE_FLEX: "离线推理",
    }

    logger.info("=" * 60)
    logger.info("🎬 Generate 阶段启动 — 赛道: %s", niche_name)
    logger.info("   模式: %s（%s）", mode, mode_labels.get(mode, mode))
    logger.info("   视频: %s %s 音频=%s", video_ratio, video_resolution, generate_audio)
    logger.info("   后处理: %s", "开启" if post_cfg.get("enabled") else "关闭")
    logger.info("   平台: %s", ", ".join(platforms))
    logger.info("   目标: %d 条内容", daily_count)
    logger.info("=" * 60)

    daily_dir = _get_daily_dir()

    # ── 1. 读取 Remix 产出 ──
    if ideas is None:
        ideas = _load_remix_results(daily_dir)

    if not ideas:
        logger.warning("⚠️  没有可用的创意方案")
        return []

    # 取前 daily_count 条
    selected = ideas[:daily_count]
    logger.info("📋 选取 %d/%d 个创意方案进行生成", len(selected), len(ideas))

    # ── 2. 逐个生成（视频 API 有并发限制，按顺序执行更稳定） ──
    # 如果需要并发可用 semaphore 控制，但视频生成本身是异步轮询，瓶颈在 API 端
    semaphore = asyncio.Semaphore(max_concurrent)

    async def _gen_with_limit(idea: dict, idx: int) -> dict:
        async with semaphore:
            return await _generate_single_idea(
                idea, idx, daily_dir,
                platforms=platforms,
                video_ratio=video_ratio,
                video_resolution=video_resolution,
                generate_audio=generate_audio,
                poll_interval=poll_interval,
                poll_timeout=poll_timeout,
                mode=mode,
                post_cfg=post_cfg,
            )

    tasks = [_gen_with_limit(idea, i) for i, idea in enumerate(selected)]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    # 处理异常结果
    final_results = []
    for i, r in enumerate(results):
        if isinstance(r, Exception):
            logger.error("❌ [%d] 整体生成失败: %s", i, r)
            final_results.append({
                "idea_id": selected[i].get("idea_id", f"idea_{i}"),
                "title": selected[i].get("title", "未命名"),
                "strategy": selected[i].get("strategy"),
                "video": None,
                "cover": None,
                "copy": None,
                "errors": [str(r)],
            })
        else:
            final_results.append(r)

    # ── 3. 保存结果 ──
    _save_generate_results(final_results, daily_dir)

    # ── 4. 飞书通知 ──
    if not skip_feishu:
        try:
            title, paragraphs = _format_generate_for_feishu(final_results, niche_name)
            await send_feishu_rich_text(title, paragraphs)
            logger.info("📨 飞书推送完成")
        except Exception as e:
            logger.error("❌ 飞书推送失败: %s", e)

    # ── 汇总 ──
    video_ok = sum(1 for r in final_results if r.get("video"))
    post_ok = sum(1 for r in final_results if r.get("post_process"))
    cover_ok = sum(1 for r in final_results if r.get("cover"))
    copy_ok = sum(1 for r in final_results if r.get("copy"))
    error_count = sum(len(r.get("errors", [])) for r in final_results)

    logger.info("=" * 60)
    logger.info("✅ Generate 阶段完成 [模式: %s]", mode)
    logger.info("   🎬 视频: %d/%d 成功", video_ok, len(final_results))
    if post_cfg.get("enabled"):
        logger.info("   🔧 后处理: %d/%d 成功", post_ok, len(final_results))
    logger.info("   🎨 封面: %d/%d 成功", cover_ok, len(final_results))
    logger.info("   ✍️ 文案: %d/%d 成功", copy_ok, len(final_results))
    if error_count:
        logger.info("   ⚠️ 错误: %d 个", error_count)
    logger.info("   📁 数据目录: %s", daily_dir)
    logger.info("=" * 60)

    return final_results
