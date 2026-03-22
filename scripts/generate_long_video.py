#!/usr/bin/env python3
"""
长视频分段生成脚本

用法：
    # 默认参数，生成 60 秒的萌宠长视频
    python scripts/generate_long_video.py

    # 指定主题和时长
    python scripts/generate_long_video.py --topic "一只小橘猫的一天" --duration 45

    # 指定每段时长和转场效果
    python scripts/generate_long_video.py --topic "流浪猫被收养的故事" --duration 60 --segment 10 --transition fade

    # 仅规划不生成（dry run，查看分段计划）
    python scripts/generate_long_video.py --topic "猫咪学游泳" --dry-run

    # 横屏 + 1080p 高清
    python scripts/generate_long_video.py --topic "宠物公园的一天" --ratio 16:9 --resolution 1080p

示例主题：
    - "一只小橘猫从流浪到被收养，在新家里逐渐信任主人的温馨故事"
    - "猫咪运动会：跳高、短跑、障碍赛，最后萌宠大合影"
    - "狗狗第一次看到雪，从惊恐到疯狂玩耍的有趣过程"
    - "一只柯基在海滩上追逐海浪，和螃蟹斗智斗勇"
"""
import argparse
import asyncio
import logging
import sys
from pathlib import Path

# 确保能从项目根目录导入 modules
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from modules.generate.long_video import generate_long_video, plan_story_segments


def setup_logging(level: str = "INFO") -> None:
    """配置日志格式"""
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="分段生成长视频并自动拼接",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例:\n"
            '  python scripts/generate_long_video.py --topic "一只小橘猫的一天"\n'
            '  python scripts/generate_long_video.py --topic "猫咪运动会" --duration 45 --transition fade\n'
            '  python scripts/generate_long_video.py --topic "流浪猫被收养" --dry-run\n'
        ),
    )
    parser.add_argument(
        "--topic", "-t",
        type=str,
        default="一只小橘猫从流浪到被收养，在新家里逐渐信任主人，最终和主人一起晒太阳的温馨故事",
        help="视频的主题或故事概要",
    )
    parser.add_argument(
        "--duration", "-d",
        type=int,
        default=40,
        help="目标总时长（秒），默认 40",
    )
    parser.add_argument(
        "--segment", "-s",
        type=int,
        default=8,
        help="每段时长（秒），2-12 之间，默认 8",
    )
    parser.add_argument(
        "--ratio",
        type=str,
        default="9:16",
        help="宽高比，默认 9:16（竖屏短视频）",
    )
    parser.add_argument(
        "--resolution",
        type=str,
        default="720p",
        choices=["480p", "720p", "1080p"],
        help="视频分辨率，默认 720p",
    )
    parser.add_argument(
        "--transition",
        type=str,
        default="fade",
        choices=["none", "fade", "dissolve"],
        help="转场效果，默认 fade（淡入淡出）",
    )
    parser.add_argument(
        "--transition-duration",
        type=float,
        default=0.5,
        help="转场时长（秒），默认 0.5",
    )
    parser.add_argument(
        "--no-audio",
        action="store_true",
        help="不生成音频",
    )
    parser.add_argument(
        "--concurrent",
        type=int,
        default=2,
        help="最大并发生成数，默认 2",
    )
    parser.add_argument(
        "--output", "-o",
        type=str,
        default=None,
        help="输出目录（默认 ./data/long_videos/<时间戳>）",
    )
    parser.add_argument(
        "--extra",
        type=str,
        default="",
        help="额外创作指导",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="仅生成分段计划，不实际生成视频（用于预览方案）",
    )
    parser.add_argument(
        "--mode",
        type=str,
        default="default",
        choices=["default", "draft", "flex"],
        help="Seedance 生成模式，默认 default",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="日志级别",
    )
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    setup_logging(args.log_level)
    logger = logging.getLogger("long_video")

    # 确定输出目录
    if args.output:
        save_dir = Path(args.output)
    else:
        from datetime import datetime
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        save_dir = Path("./data/long_videos") / timestamp
    save_dir.mkdir(parents=True, exist_ok=True)

    logger.info("🎬 长视频生成器启动")
    logger.info("   主题: %s", args.topic)
    logger.info("   目标: %d 秒（每段 %d 秒）", args.duration, args.segment)
    logger.info("   输出: %s", save_dir)

    if args.dry_run:
        # 仅规划模式：只生成分段计划
        logger.info("📋 Dry Run 模式：仅生成分段计划，不调用视频 API")
        import json
        plan = await plan_story_segments(
            args.topic,
            total_duration=args.duration,
            segment_duration=args.segment,
            extra_instructions=args.extra,
        )
        plan_path = save_dir / "long_video_plan.json"
        with open(plan_path, "w", encoding="utf-8") as f:
            json.dump(plan, f, ensure_ascii=False, indent=2)

        print("\n" + "=" * 60)
        print(f"📋 分段计划: {plan.get('title', '')}")
        print(f"   共 {len(plan.get('segments', []))} 段")
        print(f"   全局风格: {plan.get('style_guide', '')[:80]}...")
        print(f"   BGM 情绪: {plan.get('bgm_mood', '')}")
        print("-" * 60)
        for seg in plan.get("segments", []):
            print(f"   [{seg.get('segment_id')}] {seg.get('duration_seconds', '?')}s "
                  f"| {seg.get('emotion', '')} | {seg.get('scene_desc', '')[:50]}")
        print("=" * 60)
        print(f"\n💾 计划已保存: {plan_path}")
        print("   去掉 --dry-run 参数即可开始生成视频")
        return

    # 完整生成流程
    result = await generate_long_video(
        args.topic,
        save_dir,
        total_duration=args.duration,
        segment_duration=args.segment,
        extra_instructions=args.extra,
        video_ratio=args.ratio,
        video_resolution=args.resolution,
        generate_audio=not args.no_audio,
        transition=args.transition,
        transition_duration=args.transition_duration,
        max_concurrent=args.concurrent,
        poll_interval=10,
        poll_timeout=600,
        mode=args.mode,
    )

    # 打印最终结果
    print("\n" + "=" * 60)
    print(f"🎬 长视频生成完成: {result.get('title', '')}")
    print(f"   成功: {result['success_count']} 段")
    print(f"   失败: {result['fail_count']} 段")
    if result.get("final_video"):
        print(f"   成片: {result['final_video']}")
    print("=" * 60)

    for seg in result.get("segments", []):
        status = "✅" if seg.get("local_path") else "❌"
        err = f" ({seg['error']})" if seg.get("error") else ""
        print(f"   {status} [{seg.get('segment_id')}] {seg.get('scene_desc', '')[:50]}{err}")


if __name__ == "__main__":
    asyncio.run(main())
