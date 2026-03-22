#!/usr/bin/env python3
"""
测试完整的 视频生成 → 后处理 流水线。
生成 12 秒视频，然后叠加字幕 + 淡入淡出 + 封面截帧。

用法:
    python scripts/test_post_process.py
"""
import asyncio
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from modules.config import get_config
from modules.generate.volcengine_video import generate_video
from modules.generate.video_post import post_process_video, concat_videos, get_video_info


def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        handlers=[logging.StreamHandler(sys.stdout)],
    )


def load_first_idea() -> dict:
    cfg = get_config()
    data_dir = Path(cfg.get("output", {}).get("data_dir", "./data"))
    today = datetime.now().strftime("%Y-%m-%d")
    remix_path = data_dir / "daily" / today / "remixed.json"
    with open(remix_path, "r", encoding="utf-8") as f:
        return json.load(f).get("ideas", [])[0]


def build_subtitles(idea: dict) -> list[dict]:
    """从创意结构自动生成字幕"""
    subtitles = []
    for seg in idea.get("structure", []):
        if not isinstance(seg, dict):
            continue
        time_str = seg.get("time", "")
        desc = seg.get("desc", "")
        if not time_str or not desc:
            continue
        try:
            clean = time_str.replace("s", "").replace("S", "")
            parts = clean.split("-")
            start = float(parts[0])
            end = float(parts[1]) if len(parts) > 1 else start + 3
            text = desc[:35] if len(desc) > 35 else desc
            subtitles.append({"start": start, "end": end, "text": text})
        except (ValueError, IndexError):
            continue
    return subtitles


async def main():
    setup_logging()
    logger = logging.getLogger("test")

    idea = load_first_idea()
    prompt_result = idea.get("prompt_result", {})
    prompt = prompt_result.get("seedance_prompt", "")

    logger.info("=" * 60)
    logger.info("🎬 测试: 长视频生成 + 后处理")
    logger.info("   创意: %s", idea.get("title"))
    logger.info("   时长: 12s (Seedance 最大)")
    logger.info("   分辨率: 480p (测试用)")
    logger.info("=" * 60)

    cfg = get_config()
    data_dir = Path(cfg.get("output", {}).get("data_dir", "./data"))
    today = datetime.now().strftime("%Y-%m-%d")
    test_dir = data_dir / "daily" / today / "test_postprocess"
    test_dir.mkdir(parents=True, exist_ok=True)

    # ── 1. 生成 12 秒视频 ──
    logger.info("\n📹 阶段 1: 生成 12 秒视频...")
    raw_path = test_dir / "raw_12s.mp4"

    video_result = await generate_video(
        prompt,
        test_dir,
        filename="raw_12s.mp4",
        duration=12,
        ratio="9:16",
        resolution="480p",
        generate_audio=True,
        poll_interval=10,
        poll_timeout=600,
    )

    if video_result.get("status") != "succeeded":
        logger.error("❌ 视频生成失败: %s", video_result)
        return

    raw_path = Path(video_result["local_path"])
    raw_info = await get_video_info(raw_path)
    logger.info("✅ 原始视频: %.1fs, %dx%d, %.0ffps",
                raw_info["duration"], raw_info["width"], raw_info["height"], raw_info["fps"])

    # ── 2. 后处理: 淡入淡出 + 字幕 + 封面截帧 ──
    logger.info("\n🔧 阶段 2: 后处理...")

    subtitles = build_subtitles(idea)
    logger.info("   字幕: %d 条", len(subtitles))
    for s in subtitles:
        logger.info("     [%.0f-%.0fs] %s", s["start"], s["end"], s["text"])

    final_path = test_dir / "final_12s.mp4"
    post_result = await post_process_video(
        raw_path,
        final_path,
        subtitles=subtitles,
        fade_in=0.5,
        fade_out=0.8,
        extract_cover=True,
        cover_timestamp=2.0,
    )

    final_info = post_result.get("info", {})
    logger.info("\n✅ 后处理完成:")
    logger.info("   处理步骤: %s", " → ".join(post_result.get("steps", [])))
    logger.info("   输出视频: %s", post_result.get("output_path"))
    logger.info("   封面图: %s", post_result.get("cover_path"))
    logger.info("   时长: %.1fs", final_info.get("duration", 0))

    # ── 3. 测试拼接: 原始 + 后处理版本 拼在一起 ──
    logger.info("\n🔗 阶段 3: 拼接测试 (原始 + 后处理版本)...")
    concat_path = test_dir / "concat_comparison.mp4"
    await concat_videos(
        [raw_path, final_path],
        concat_path,
        transition="none",
    )
    concat_info = await get_video_info(concat_path)
    logger.info("✅ 拼接完成: %.1fs, %s", concat_info["duration"], concat_path.name)

    # ── 汇总 ──
    logger.info("\n" + "=" * 60)
    logger.info("📊 测试结果汇总:")
    logger.info("   原始视频:   %s (%.1fs, %.1fMB)",
                raw_path.name, raw_info["duration"],
                raw_path.stat().st_size / 1024 / 1024)
    logger.info("   后处理视频: %s (%.1fs, %.1fMB)",
                final_path.name, final_info.get("duration", 0),
                final_path.stat().st_size / 1024 / 1024)
    logger.info("   拼接视频:   %s (%.1fs, %.1fMB)",
                concat_path.name, concat_info["duration"],
                concat_path.stat().st_size / 1024 / 1024)
    logger.info("   封面图:     %s", post_result.get("cover_path"))
    logger.info("=" * 60)

    # 打开最终视频
    import subprocess
    subprocess.run(["open", str(final_path)])
    logger.info("🎉 已打开后处理视频!")


if __name__ == "__main__":
    asyncio.run(main())
