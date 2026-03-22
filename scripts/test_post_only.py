#!/usr/bin/env python3
"""
仅测试后处理流水线（复用已生成的 raw_12s.mp4）。
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
from modules.generate.video_post import post_process_video, concat_videos, get_video_info


def setup_logging():
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        handlers=[logging.StreamHandler(sys.stdout)],
    )


async def main():
    setup_logging()
    logger = logging.getLogger("test")

    cfg = get_config()
    data_dir = Path(cfg.get("output", {}).get("data_dir", "./data"))
    today = datetime.now().strftime("%Y-%m-%d")
    test_dir = data_dir / "daily" / today / "test_postprocess"

    raw_path = test_dir / "raw_12s.mp4"
    if not raw_path.exists():
        logger.error("❌ 原始视频不存在: %s", raw_path)
        return

    raw_info = await get_video_info(raw_path)
    logger.info("📹 原始视频: %.1fs, %dx%d", raw_info["duration"], raw_info["width"], raw_info["height"])

    # 从 remixed.json 取字幕结构
    remix_path = data_dir / "daily" / today / "remixed.json"
    with open(remix_path, "r", encoding="utf-8") as f:
        idea = json.load(f).get("ideas", [])[0]

    subtitles = []
    for seg in idea.get("structure", []):
        if not isinstance(seg, dict):
            continue
        time_str = seg.get("time", "").replace("s", "").replace("S", "")
        desc = seg.get("desc", "")
        if not time_str or not desc:
            continue
        try:
            parts = time_str.split("-")
            start = float(parts[0])
            end = float(parts[1]) if len(parts) > 1 else start + 3
            subtitles.append({"start": start, "end": end, "text": desc[:30]})
        except (ValueError, IndexError):
            continue

    logger.info("📝 字幕 %d 条:", len(subtitles))
    for s in subtitles:
        logger.info("   [%.0f-%.0fs] %s", s["start"], s["end"], s["text"])

    # ── 执行后处理 ──
    logger.info("\n🔧 开始后处理...")
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

    logger.info("\n✅ 后处理完成!")
    logger.info("   步骤: %s", " → ".join(post_result.get("steps", [])))
    logger.info("   输出: %s", post_result.get("output_path"))
    logger.info("   封面: %s", post_result.get("cover_path"))

    final_info = post_result.get("info", {})
    logger.info("   时长: %.1fs", final_info.get("duration", 0))
    logger.info("   大小: %.1fMB", Path(post_result["output_path"]).stat().st_size / 1024 / 1024)

    # ── 拼接测试 ──
    logger.info("\n🔗 拼接测试...")
    concat_path = test_dir / "concat_comparison.mp4"
    await concat_videos([raw_path, final_path], concat_path, transition="none")
    concat_info = await get_video_info(concat_path)
    logger.info("✅ 拼接: %.1fs, %.1fMB",
                concat_info["duration"], concat_path.stat().st_size / 1024 / 1024)

    # 打开结果
    import subprocess
    subprocess.run(["open", str(final_path)])
    logger.info("\n🎉 已打开后处理视频!")


if __name__ == "__main__":
    asyncio.run(main())
