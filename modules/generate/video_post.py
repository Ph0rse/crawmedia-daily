"""
视频后处理模块

基于 ffmpeg 实现，无需安装额外 Python 包（moviepy 底层也是调 ffmpeg）。
macOS 通过 brew install ffmpeg 安装，Linux 通过 apt install ffmpeg 安装。

功能：
- 字幕叠加（硬字幕 / ASS 样式）
- 多段视频拼接（concat）
- 转场效果（淡入淡出）
- 封面图提取（从视频截取）
"""
from __future__ import annotations

import asyncio
import json
import logging
import shutil
import subprocess
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)


def _check_ffmpeg() -> str:
    """检查 ffmpeg 是否可用，返回路径"""
    path = shutil.which("ffmpeg")
    if not path:
        raise EnvironmentError(
            "ffmpeg 未安装。请执行:\n"
            "  macOS:  brew install ffmpeg\n"
            "  Linux:  sudo apt install ffmpeg\n"
            "  Windows: choco install ffmpeg"
        )
    return path


def _check_ffprobe() -> str:
    path = shutil.which("ffprobe")
    if not path:
        raise EnvironmentError("ffprobe 未找到，请确保 ffmpeg 完整安装")
    return path


def _run_ffmpeg(args: list[str], desc: str = "") -> subprocess.CompletedProcess:
    """执行 ffmpeg 命令并检查返回码"""
    ffmpeg = _check_ffmpeg()
    cmd = [ffmpeg] + args
    logger.debug("ffmpeg cmd: %s", " ".join(cmd))

    result = subprocess.run(
        cmd, capture_output=True, text=True, timeout=300,
    )
    if result.returncode != 0:
        logger.error("ffmpeg 失败 [%s]: %s", desc, result.stderr[-500:])
        raise RuntimeError(f"ffmpeg 执行失败 ({desc}): {result.stderr[-200:]}")
    return result


def _escape_ffmpeg_path(path: str) -> str:
    """转义 ffmpeg 滤镜中的文件路径（: ' \\ [ ] 等需要转义）"""
    return path.replace("\\", "/").replace(":", "\\:").replace("'", "\\'")


def _abs(p: Path) -> str:
    """统一转换为绝对路径字符串"""
    return str(p.resolve())


# ── 视频信息 ──────────────────────────────────────────────

async def get_video_info(video_path: Path) -> dict:
    """
    获取视频元信息（时长、分辨率、帧率等）。

    Returns:
        {"duration": 5.0, "width": 720, "height": 1280, "fps": 24.0, "codec": "h264"}
    """
    ffprobe = _check_ffprobe()
    cmd = [
        ffprobe, "-v", "quiet",
        "-print_format", "json",
        "-show_format", "-show_streams",
        str(video_path),
    ]

    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None,
        lambda: subprocess.run(cmd, capture_output=True, text=True, timeout=30),
    )

    if result.returncode != 0:
        raise RuntimeError(f"ffprobe 失败: {result.stderr[:200]}")

    data = json.loads(result.stdout)
    video_stream = next(
        (s for s in data.get("streams", []) if s.get("codec_type") == "video"),
        {},
    )

    fps_parts = video_stream.get("r_frame_rate", "24/1").split("/")
    fps = float(fps_parts[0]) / float(fps_parts[1]) if len(fps_parts) == 2 else 24.0

    return {
        "duration": float(data.get("format", {}).get("duration", 0)),
        "width": int(video_stream.get("width", 0)),
        "height": int(video_stream.get("height", 0)),
        "fps": round(fps, 2),
        "codec": video_stream.get("codec_name", "unknown"),
    }


# ── 字幕叠加 ──────────────────────────────────────────────

async def add_subtitles(
    video_path: Path,
    output_path: Path,
    subtitles: list[dict],
    *,
    font_size: int = 48,
    font_color: str = "white",
    outline_color: str = "black",
    outline_width: int = 3,
    position: str = "bottom",
    margin_bottom: int = 60,
) -> Path:
    """
    在视频上叠加硬字幕。

    Args:
        video_path: 输入视频路径
        output_path: 输出视频路径
        subtitles: 字幕列表
            [{"start": 0.0, "end": 3.0, "text": "第一句字幕"}, ...]
        font_size: 字体大小
        font_color: 字体颜色
        outline_color: 描边颜色
        outline_width: 描边宽度
        position: 位置 "top" / "center" / "bottom"
        margin_bottom: 底部边距（仅 bottom 时生效）

    Returns:
        输出视频路径
    """
    if not subtitles:
        logger.info("无字幕内容，直接复制视频")
        shutil.copy2(video_path, output_path)
        return output_path

    # 生成 ASS 字幕文件
    ass_content = _generate_ass(
        subtitles,
        font_size=font_size,
        font_color=font_color,
        outline_color=outline_color,
        outline_width=outline_width,
        position=position,
        margin_bottom=margin_bottom,
    )

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".ass", delete=False, encoding="utf-8"
    ) as f:
        f.write(ass_content)
        ass_path = f.name

    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        # ffmpeg 滤镜语法中需要转义 : 和 \ 等特殊字符
        escaped_ass = _escape_ffmpeg_path(ass_path)
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, lambda: _run_ffmpeg(
            [
                "-i", _abs(video_path),
                "-vf", f"ass={escaped_ass}",
                "-c:a", "copy",
                "-y", _abs(output_path),
            ],
            desc="字幕叠加",
        ))
    finally:
        Path(ass_path).unlink(missing_ok=True)

    logger.info("✅ 字幕已叠加: %s (%d 条)", output_path.name, len(subtitles))
    return output_path


def _generate_ass(
    subtitles: list[dict],
    *,
    font_size: int = 48,
    font_color: str = "white",
    outline_color: str = "black",
    outline_width: int = 3,
    position: str = "bottom",
    margin_bottom: int = 60,
) -> str:
    """生成 ASS 格式字幕文件内容"""
    # ASS 颜色格式为 &HBBGGRR（BGR 倒序，带前缀 &H）
    color_map = {
        "white": "&H00FFFFFF",
        "black": "&H00000000",
        "yellow": "&H0000FFFF",
        "red": "&H000000FF",
    }
    primary = color_map.get(font_color, "&H00FFFFFF")
    outline = color_map.get(outline_color, "&H00000000")

    # 对齐位置: 1=左下 2=中下 3=右下 5=中左 6=中中 8=中上
    alignment = {"bottom": 2, "center": 5, "top": 8}.get(position, 2)

    header = f"""[Script Info]
Title: CrawMedia Subtitles
ScriptType: v4.00+
PlayResX: 720
PlayResY: 1280

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, Bold, Italic, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Noto Sans CJK SC,{font_size},{primary},{outline},1,0,1,{outline_width},0,{alignment},20,20,{margin_bottom},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    lines = []
    for sub in subtitles:
        start = _seconds_to_ass_time(sub.get("start", 0))
        end = _seconds_to_ass_time(sub.get("end", 0))
        text = sub.get("text", "").replace("\n", "\\N")
        lines.append(f"Dialogue: 0,{start},{end},Default,,0,0,0,,{text}")

    return header + "\n".join(lines) + "\n"


def _seconds_to_ass_time(seconds: float) -> str:
    """将秒数转为 ASS 时间格式 H:MM:SS.CC"""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    cs = int((seconds % 1) * 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


# ── 多段拼接 ──────────────────────────────────────────────

async def concat_videos(
    video_paths: list[Path],
    output_path: Path,
    *,
    transition: str = "none",
    transition_duration: float = 0.5,
) -> Path:
    """
    将多段视频拼接为一个完整视频。

    Args:
        video_paths: 输入视频路径列表（按顺序拼接）
        output_path: 输出文件路径
        transition: 转场类型 "none" / "fade" / "dissolve"
        transition_duration: 转场时长（秒）

    Returns:
        输出视频路径
    """
    if not video_paths:
        raise ValueError("视频列表为空")

    if len(video_paths) == 1:
        shutil.copy2(video_paths[0], output_path)
        return output_path

    output_path.parent.mkdir(parents=True, exist_ok=True)
    loop = asyncio.get_event_loop()

    if transition == "none":
        await _concat_simple(video_paths, output_path, loop)
    else:
        await _concat_with_transition(
            video_paths, output_path, loop,
            transition=transition,
            duration=transition_duration,
        )

    total_size = output_path.stat().st_size / (1024 * 1024)
    logger.info(
        "✅ 拼接完成: %d 段 → %s (%.1fMB)",
        len(video_paths), output_path.name, total_size,
    )
    return output_path


async def _concat_simple(
    video_paths: list[Path],
    output_path: Path,
    loop: asyncio.AbstractEventLoop,
) -> None:
    """无转场的简单拼接（concat demuxer，速度最快）"""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", delete=False
    ) as f:
        for vp in video_paths:
            f.write(f"file '{vp.resolve()}'\n")
        list_path = f.name

    try:
        await loop.run_in_executor(None, lambda: _run_ffmpeg(
            [
                "-f", "concat", "-safe", "0",
                "-i", list_path,
                "-c", "copy",
                "-y", _abs(output_path),
            ],
            desc="简单拼接",
        ))
    finally:
        Path(list_path).unlink(missing_ok=True)


async def _concat_with_transition(
    video_paths: list[Path],
    output_path: Path,
    loop: asyncio.AbstractEventLoop,
    *,
    transition: str = "fade",
    duration: float = 0.5,
) -> None:
    """带转场效果的拼接（使用 xfade 滤镜）"""
    # 获取每段视频的时长
    durations = []
    for vp in video_paths:
        info = await get_video_info(vp)
        durations.append(info["duration"])

    # 构建 ffmpeg filter_complex
    # xfade 转场需要逐段串联
    inputs = []
    for vp in video_paths:
        inputs.extend(["-i", _abs(vp)])

    # xfade 要求 offset = 前面所有视频时长之和 - 转场时长 * 已有转场数
    filter_parts = []
    offset = 0.0
    prev_label = "[0:v]"

    for i in range(1, len(video_paths)):
        offset = sum(durations[:i]) - duration * i
        offset = max(0, offset)
        out_label = f"[v{i}]" if i < len(video_paths) - 1 else "[vout]"

        xfade_type = "fade" if transition == "fade" else "dissolve"
        filter_parts.append(
            f"{prev_label}[{i}:v]xfade=transition={xfade_type}"
            f":duration={duration}:offset={offset}{out_label}"
        )
        prev_label = out_label

    # 音频也做交叉淡入淡出
    audio_parts = []
    prev_audio = "[0:a]"
    for i in range(1, len(video_paths)):
        offset = sum(durations[:i]) - duration * i
        offset = max(0, offset)
        out_label = f"[a{i}]" if i < len(video_paths) - 1 else "[aout]"
        audio_parts.append(
            f"{prev_audio}[{i}:a]acrossfade=d={duration}{out_label}"
        )
        prev_audio = out_label

    filter_complex = ";".join(filter_parts + audio_parts)

    await loop.run_in_executor(None, lambda: _run_ffmpeg(
        inputs + [
            "-filter_complex", filter_complex,
            "-map", "[vout]", "-map", "[aout]",
            "-y", _abs(output_path),
        ],
        desc=f"{transition}转场拼接",
    ))


# ── 淡入淡出 ──────────────────────────────────────────────

async def add_fade(
    video_path: Path,
    output_path: Path,
    *,
    fade_in: float = 0.5,
    fade_out: float = 0.5,
) -> Path:
    """
    给视频添加淡入淡出效果。

    Args:
        video_path: 输入视频
        output_path: 输出视频
        fade_in: 淡入时长（秒），0 表示不加
        fade_out: 淡出时长（秒），0 表示不加
    """
    info = await get_video_info(video_path)
    duration = info["duration"]

    filters = []
    if fade_in > 0:
        filters.append(f"fade=t=in:st=0:d={fade_in}")
    if fade_out > 0:
        fade_start = max(0, duration - fade_out)
        filters.append(f"fade=t=out:st={fade_start}:d={fade_out}")

    if not filters:
        shutil.copy2(video_path, output_path)
        return output_path

    output_path.parent.mkdir(parents=True, exist_ok=True)
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, lambda: _run_ffmpeg(
        [
            "-i", _abs(video_path),
            "-vf", ",".join(filters),
            "-c:a", "copy",
            "-y", _abs(output_path),
        ],
        desc="淡入淡出",
    ))

    logger.info("✅ 淡入淡出已添加: in=%.1fs out=%.1fs", fade_in, fade_out)
    return output_path


# ── 封面截图 ──────────────────────────────────────────────

async def extract_frame(
    video_path: Path,
    output_path: Path,
    *,
    timestamp: float = 1.0,
) -> Path:
    """
    从视频中截取一帧作为封面图。

    Args:
        video_path: 输入视频
        output_path: 输出图片路径（支持 .jpg / .png）
        timestamp: 截取时间点（秒）
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, lambda: _run_ffmpeg(
        [
            "-ss", str(timestamp),
            "-i", _abs(video_path),
            "-frames:v", "1",
            "-q:v", "2",
            "-y", _abs(output_path),
        ],
        desc="封面截图",
    ))

    logger.info("✅ 封面截图: %s (t=%.1fs)", output_path.name, timestamp)
    return output_path


# ── 组合后处理流水线 ──────────────────────────────────────

async def post_process_video(
    video_path: Path,
    output_path: Path,
    *,
    subtitles: list[dict] | None = None,
    fade_in: float = 0.3,
    fade_out: float = 0.3,
    extract_cover: bool = True,
    cover_timestamp: float = 1.0,
) -> dict:
    """
    对单个视频执行完整后处理流水线。

    Args:
        video_path: 原始视频路径
        output_path: 最终输出路径
        subtitles: 字幕列表（可选）
        fade_in: 淡入时长
        fade_out: 淡出时长
        extract_cover: 是否从视频截取封面
        cover_timestamp: 封面截取时间点

    Returns:
        {
            "output_path": "...",
            "cover_path": "..." or None,
            "info": {...},
            "steps": ["fade", "subtitles", ...]
        }
    """
    steps = []
    current = video_path
    temp_files = []

    try:
        # 淡入淡出
        if fade_in > 0 or fade_out > 0:
            faded = output_path.parent / f"_tmp_faded_{output_path.name}"
            temp_files.append(faded)
            await add_fade(current, faded, fade_in=fade_in, fade_out=fade_out)
            current = faded
            steps.append("fade")

        # 字幕叠加
        if subtitles:
            subtitled = output_path.parent / f"_tmp_sub_{output_path.name}"
            temp_files.append(subtitled)
            await add_subtitles(current, subtitled, subtitles)
            current = subtitled
            steps.append("subtitles")

        # 输出最终文件
        if current != output_path:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(current, output_path)

        # 提取封面
        cover_path = None
        if extract_cover:
            cover_path = output_path.with_suffix(".jpg")
            await extract_frame(video_path, cover_path, timestamp=cover_timestamp)
            steps.append("cover")

        info = await get_video_info(output_path)

    finally:
        for tmp in temp_files:
            tmp.unlink(missing_ok=True)

    logger.info("✅ 后处理完成: %s → %s (%s)", video_path.name, output_path.name, " → ".join(steps))

    return {
        "output_path": str(output_path),
        "cover_path": str(cover_path) if cover_path else None,
        "info": info,
        "steps": steps,
    }
