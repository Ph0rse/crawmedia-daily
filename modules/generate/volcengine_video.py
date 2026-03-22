"""
火山引擎视频生成客户端（方舟 Ark API）

通过 Seedance 模型生成短视频，支持：
- 文生视频（text-to-video）
- 图生视频-首帧（image-to-video, single frame）
- 图生视频-首尾帧（image-to-video, first+last frame）
- 有声视频（仅 doubao-seedance-1-5-pro 支持）
- 样片预览模式（draft → 确认 → 高清成片）
- 离线推理模式（service_tier=flex，低成本批量生成）

模型选择策略：
┌──────────────────────────────────────┬───────┬───────┬────────┬──────┬───────┬──────┐
│ 模型                                │ 文生  │ 首帧  │ 首尾帧 │ 音频 │ 样片  │ 离线 │
├──────────────────────────────────────┼───────┼───────┼────────┼──────┼───────┼──────┤
│ doubao-seedance-1-5-pro-251215      │  ✅   │  ✅   │  ✅    │  ✅  │  ✅   │  ✅  │ ← 推荐
│ doubao-seedance-1-0-pro-250528      │  ✅   │  ✅   │  ✅    │  ❌  │  ❌   │  ✅  │
│ doubao-seedance-1-0-pro-fast-251015 │  ✅   │  ✅   │  ❌    │  ❌  │  ❌   │  ✅  │ ← 快速
└──────────────────────────────────────┴───────┴───────┴────────┴──────┴───────┴──────┘

生成模式：
- default:  标准模式，提交后数分钟内出结果
- draft:    先生成 480p 样片快速预览，确认后再升级为高清成片（仅 1.5-pro）
- flex:     离线推理模式，成本更低但耗时可能更长（分钟~小时），适合批量非紧急任务
"""
from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

import httpx

from ..config import get_config

logger = logging.getLogger(__name__)

# ── 模型常量 ──────────────────────────────────────────────
MODEL_1_5_PRO = "doubao-seedance-1-5-pro-251215"
MODEL_1_0_PRO = "doubao-seedance-1-0-pro-250528"
MODEL_1_0_FAST = "doubao-seedance-1-0-pro-fast-251015"

_MODEL_ALIAS = {
    "seedance-2.0": MODEL_1_5_PRO,
    "seedance-1.5": MODEL_1_5_PRO,
    "seedance-1.0": MODEL_1_0_PRO,
    "seedance-1.0-fast": MODEL_1_0_FAST,
}

# 终态状态集合
_TERMINAL = {"succeeded", "failed", "expired", "cancelled"}

# 生成模式
MODE_DEFAULT = "default"
MODE_DRAFT = "draft"
MODE_FLEX = "flex"


# ── 配置读取 ──────────────────────────────────────────────

def _get_ark_config() -> dict[str, str]:
    """从环境变量获取 Ark API 密钥和基地址（复用 LLM 同一套凭证）"""
    return {
        "api_key": os.environ.get("LLM_API_KEY", ""),
        "base_url": os.environ.get(
            "LLM_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3"
        ).rstrip("/"),
    }


def _resolve_model(name: str | None = None) -> str:
    """将配置中的模型名解析为实际 API 模型 ID"""
    if name and name not in _MODEL_ALIAS:
        return name
    if not name:
        cfg = get_config()
        name = cfg.get("volcengine", {}).get("video_model", "seedance-2.0")
    return _MODEL_ALIAS.get(name, name)


# ── 核心 API ──────────────────────────────────────────────

async def create_video_task(
    prompt: str,
    *,
    images: list[str] | None = None,
    model: str | None = None,
    duration: int = 5,
    ratio: str = "9:16",
    resolution: str = "720p",
    generate_audio: bool = True,
    watermark: bool = False,
    seed: int = -1,
    camera_fixed: bool = False,
    draft: bool = False,
    draft_task_id: str | None = None,
    service_tier: str | None = None,
) -> str:
    """
    提交视频生成任务到方舟 API。

    Args:
        prompt: 视频描述文本（建议 ≤500 字）
        images: 图片 URL 列表。空=文生视频，1张=首帧图生视频，2张=首尾帧
        model: 模型名（None 则从 config 读取）
        duration: 时长 2–12 秒
        ratio: 宽高比，短视频推荐 9:16
        resolution: 480p / 720p / 1080p
        generate_audio: 是否生成同步音频（仅 1.5-pro 支持）
        watermark: 是否添加水印
        seed: 随机种子，-1 为随机
        camera_fixed: 是否固定镜头
        draft: 是否为样片模式（仅 1.5-pro，强制 480p，速度快成本低）
        draft_task_id: 样片任务 ID，传入后基于该样片生成高清成片
        service_tier: 服务层级，"flex" = 离线推理（成本更低，耗时更长）

    Returns:
        任务 ID（形如 cgt-xxxxxxxx）
    """
    ark = _get_ark_config()
    if not ark["api_key"]:
        raise ValueError("LLM_API_KEY 未设置，无法调用视频生成 API")

    model = _resolve_model(model)
    url = f"{ark['base_url']}/contents/generations/tasks"

    # 构建 content 数组（Ark 官方格式）
    content = [{"type": "text", "text": prompt}]
    if images:
        for img_url in images:
            content.append({"type": "image_url", "image_url": {"url": img_url}})

    body: dict = {
        "model": model,
        "content": content,
        "duration": max(2, min(12, duration)),
        "ratio": ratio,
        "resolution": resolution,
        "watermark": watermark,
        "seed": seed,
        "camera_fixed": camera_fixed,
    }

    # 有声视频仅 1.5-pro 支持
    if "1-5-pro" in model and generate_audio:
        body["generate_audio"] = True

    # 样片模式：快速生成 480p 预览
    if draft and "1-5-pro" in model:
        body["draft"] = True
        body["resolution"] = "480p"
        logger.info("📋 样片模式: 强制 480p 快速预览")

    # 基于样片升级为高清成片
    if draft_task_id:
        body["draft_task_id"] = draft_task_id
        logger.info("⬆️  基于样片 %s 升级为高清成片", draft_task_id)

    # 离线推理模式
    if service_tier:
        body["service_tier"] = service_tier
        logger.info("💰 离线推理模式: service_tier=%s", service_tier)

    headers = {
        "Authorization": f"Bearer {ark['api_key']}",
        "Content-Type": "application/json",
    }

    mode_label = "样片" if draft else ("离线" if service_tier == "flex" else "标准")
    logger.info(
        "📹 提交视频任务 [%s]: model=%s, duration=%ds, ratio=%s, resolution=%s",
        mode_label, model, body["duration"], ratio, body["resolution"],
    )
    logger.debug("Prompt (前100字): %s", prompt[:100])

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(url, json=body, headers=headers)
        resp.raise_for_status()
        data = resp.json()

    task_id = data.get("id") or data.get("task_id")
    if not task_id:
        raise RuntimeError(f"视频任务创建失败，API 返回: {data}")

    logger.info("✅ 视频任务已提交: %s", task_id)
    return task_id


async def query_video_task(task_id: str) -> dict:
    """
    查询视频生成任务状态。

    Returns:
        任务详情 dict，关键字段:
        - status: queued / running / succeeded / failed / expired
        - content.video_url: 生成的视频 CDN 地址（succeeded 时）
        - usage.completion_tokens: 消耗的 token 数
    """
    ark = _get_ark_config()
    url = f"{ark['base_url']}/contents/generations/tasks/{task_id}"
    headers = {
        "Authorization": f"Bearer {ark['api_key']}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(url, headers=headers)
        resp.raise_for_status()
        data = resp.json()

    # 兼容包裹格式 {"data": {...}} 和直接返回格式
    if "data" in data and isinstance(data["data"], dict) and "status" in data["data"]:
        return data["data"]
    return data


async def poll_video_task(
    task_id: str,
    *,
    interval: int = 10,
    timeout: int = 600,
) -> dict:
    """
    轮询任务直到终态或超时。

    Args:
        task_id: 任务 ID
        interval: 轮询间隔（秒），建议 ≥ 10
        timeout: 最大等待时间（秒），默认 10 分钟

    Returns:
        终态任务详情

    Raises:
        TimeoutError: 超时
        RuntimeError: 任务失败 / 过期 / 取消
    """
    logger.info("⏳ 开始轮询: %s（间隔%ds，超时%ds）", task_id, interval, timeout)
    elapsed = 0

    while elapsed < timeout:
        result = await query_video_task(task_id)
        status = result.get("status", "unknown").lower()

        if status == "succeeded":
            video_url = result.get("content", {}).get("video_url", "")
            logger.info("🎬 视频生成完成: %s", task_id)
            logger.debug("视频 URL: %s", video_url[:80] if video_url else "(空)")
            return result

        if status in _TERMINAL:
            reason = result.get("fail_reason") or result.get("error") or "未知原因"
            raise RuntimeError(f"视频生成失败 [{status}]: {reason} (task={task_id})")

        logger.info(
            "   [%ds/%ds] %s → %s",
            elapsed, timeout, task_id, status,
        )
        await asyncio.sleep(interval)
        elapsed += interval

    raise TimeoutError(f"视频生成超时（{timeout}s）: {task_id}")


async def download_video(video_url: str, save_path: Path) -> Path:
    """下载视频到本地文件"""
    save_path.parent.mkdir(parents=True, exist_ok=True)
    logger.info("⬇️  下载视频 → %s", save_path.name)

    async with httpx.AsyncClient(timeout=180, follow_redirects=True) as client:
        resp = await client.get(video_url)
        resp.raise_for_status()
        save_path.write_bytes(resp.content)

    size_mb = save_path.stat().st_size / (1024 * 1024)
    logger.info("✅ 视频已保存: %s (%.1fMB)", save_path.name, size_mb)
    return save_path


async def generate_video(
    prompt: str,
    save_dir: Path,
    *,
    filename: str = "video.mp4",
    images: list[str] | None = None,
    duration: int = 5,
    ratio: str = "9:16",
    resolution: str = "720p",
    generate_audio: bool = True,
    poll_interval: int = 10,
    poll_timeout: int = 600,
    mode: str = MODE_DEFAULT,
) -> dict:
    """
    一站式视频生成：提交 → 轮询 → 下载。

    Args:
        mode: 生成模式
            - "default": 标准模式
            - "draft":   样片预览模式（480p，速度快成本低，仅 1.5-pro）
            - "flex":    离线推理模式（成本更低，适合批量非紧急任务）

    Returns:
        {
            "task_id": "cgt-xxx",
            "status": "succeeded",
            "video_url": "https://...",
            "local_path": "data/daily/2026-03-22/videos/video_0.mp4",
            "duration": 5,
            "resolution": "720p",
            "model": "doubao-seedance-1-5-pro-251215",
            "seed": 12345,
            "mode": "default",
            "usage": {"completion_tokens": ...}
        }
    """
    task_id = await create_video_task(
        prompt,
        images=images,
        duration=duration,
        ratio=ratio,
        resolution=resolution,
        generate_audio=generate_audio,
        draft=(mode == MODE_DRAFT),
        service_tier="flex" if mode == MODE_FLEX else None,
    )

    # 离线推理模式超时更长
    actual_timeout = poll_timeout
    if mode == MODE_FLEX:
        actual_timeout = max(poll_timeout, 3600)

    result = await poll_video_task(
        task_id,
        interval=poll_interval,
        timeout=actual_timeout,
    )

    video_url = result.get("content", {}).get("video_url", "")
    local_path = None
    if video_url:
        local_path = await download_video(video_url, save_dir / filename)

    return {
        "task_id": task_id,
        "status": result.get("status"),
        "video_url": video_url,
        "local_path": str(local_path) if local_path else None,
        "duration": result.get("duration") or duration,
        "resolution": result.get("resolution") or resolution,
        "model": result.get("model") or _resolve_model(),
        "seed": result.get("seed"),
        "mode": mode,
        "usage": result.get("usage"),
    }


async def generate_video_with_draft(
    prompt: str,
    save_dir: Path,
    *,
    filename: str = "video.mp4",
    images: list[str] | None = None,
    duration: int = 5,
    ratio: str = "9:16",
    final_resolution: str = "720p",
    generate_audio: bool = True,
    poll_interval: int = 10,
    poll_timeout: int = 600,
    auto_upgrade: bool = True,
) -> dict:
    """
    两阶段视频生成：先出 480p 样片 → 自动升级为高清成片。

    优势：
    - 样片速度更快、成本更低，适合快速验证效果
    - 自动继承样片的内容和风格，高清成片成功率更高
    - 如果 auto_upgrade=False，仅生成样片供人工审核

    流程：
    ┌───────────┐     ┌──────────────┐     ┌───────────┐
    │ 1. 样片   │ ──→ │ 2. 自动/人工 │ ──→ │ 3. 高清   │
    │ 480p draft│     │    确认      │     │   成片    │
    └───────────┘     └──────────────┘     └───────────┘

    Returns:
        包含 draft_result 和 final_result（如已升级）的完整信息
    """
    logger.info("🎬 两阶段生成启动: 样片(480p) → 成片(%s)", final_resolution)

    # ── 阶段 1: 生成 480p 样片 ──
    draft_filename = filename.replace(".mp4", "_draft.mp4")
    draft_result = await generate_video(
        prompt,
        save_dir,
        filename=draft_filename,
        images=images,
        duration=duration,
        ratio=ratio,
        resolution="480p",
        generate_audio=generate_audio,
        poll_interval=poll_interval,
        poll_timeout=poll_timeout,
        mode=MODE_DRAFT,
    )

    output = {
        "draft": draft_result,
        "final": None,
        "mode": "draft_then_final" if auto_upgrade else "draft_only",
    }

    if draft_result.get("status") != "succeeded":
        logger.warning("⚠️  样片生成未成功，跳过高清升级")
        return output

    logger.info("✅ 样片生成完成: %s", draft_result.get("task_id"))

    if not auto_upgrade:
        logger.info("📋 auto_upgrade=False，样片已就绪等待人工确认")
        return output

    # ── 阶段 2: 基于样片升级为高清成片 ──
    logger.info("⬆️  开始升级为高清成片: %s", final_resolution)
    draft_task_id = draft_result["task_id"]

    final_task_id = await create_video_task(
        prompt,
        images=images,
        duration=duration,
        ratio=ratio,
        resolution=final_resolution,
        generate_audio=generate_audio,
        draft_task_id=draft_task_id,
    )

    final_api_result = await poll_video_task(
        final_task_id,
        interval=poll_interval,
        timeout=poll_timeout,
    )

    video_url = final_api_result.get("content", {}).get("video_url", "")
    local_path = None
    if video_url:
        local_path = await download_video(video_url, save_dir / filename)

    output["final"] = {
        "task_id": final_task_id,
        "draft_task_id": draft_task_id,
        "status": final_api_result.get("status"),
        "video_url": video_url,
        "local_path": str(local_path) if local_path else None,
        "duration": final_api_result.get("duration") or duration,
        "resolution": final_api_result.get("resolution") or final_resolution,
        "model": final_api_result.get("model") or _resolve_model(),
        "seed": final_api_result.get("seed"),
        "mode": "final_from_draft",
        "usage": final_api_result.get("usage"),
    }

    logger.info("🎬 高清成片完成: %s → %s", draft_task_id, final_task_id)
    return output
