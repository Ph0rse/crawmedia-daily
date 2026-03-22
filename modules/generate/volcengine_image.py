"""
火山引擎图片生成客户端（方舟 Ark API）

使用 Seedream 模型生成封面图 / 配图。API 为同步调用（非异步任务）。

模型选择：
- doubao-seedream-5-0-260128  ← 最新，效果最好，推荐
- doubao-seedream-4-5-251128  ← 增强版
- doubao-seedream-4-0-250828  ← 基础版
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

import httpx

from ..config import get_config

logger = logging.getLogger(__name__)

# ── 模型常量 ──────────────────────────────────────────────
MODEL_SEEDREAM_5 = "doubao-seedream-5-0-260128"
MODEL_SEEDREAM_4_5 = "doubao-seedream-4-5-251128"
MODEL_SEEDREAM_4 = "doubao-seedream-4-0-250828"

_MODEL_ALIAS = {
    "general_v2.0": MODEL_SEEDREAM_5,
    "seedream-5.0": MODEL_SEEDREAM_5,
    "seedream-4.5": MODEL_SEEDREAM_4_5,
    "seedream-4.0": MODEL_SEEDREAM_4,
}

# 短视频封面推荐尺寸（9:16 竖屏）
COVER_SIZES = {
    "9:16": "1440x2560",
    "16:9": "2560x1440",
    "1:1": "2048x2048",
    "4:3": "2304x1728",
    "3:4": "1728x2304",
}


def _get_ark_config() -> dict[str, str]:
    return {
        "api_key": os.environ.get("LLM_API_KEY", ""),
        "base_url": os.environ.get(
            "LLM_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3"
        ).rstrip("/"),
    }


def _resolve_model(name: str | None = None) -> str:
    if name and name not in _MODEL_ALIAS:
        return name
    if not name:
        cfg = get_config()
        name = cfg.get("volcengine", {}).get("image_model", "general_v2.0")
    return _MODEL_ALIAS.get(name, name)


async def generate_image(
    prompt: str,
    save_path: Path,
    *,
    model: str | None = None,
    size: str = "2K",
    ratio: str = "9:16",
    watermark: bool = False,
) -> dict:
    """
    生成封面图并下载到本地。

    Args:
        prompt: 图片描述文本（≤300 汉字）
        save_path: 本地保存路径（如 covers/cover_0.jpg）
        model: 模型名，默认从 config 读取
        size: 尺寸，"2K" / "4K" / 具体像素如 "1440x2560"
        ratio: 宽高比，用于选择推荐尺寸
        watermark: 是否添加水印

    Returns:
        {
            "image_url": "https://...",
            "local_path": "data/daily/.../covers/cover_0.jpg",
            "size": "1440x2560",
            "model": "doubao-seedream-5-0-260128",
            "usage": {...}
        }
    """
    ark = _get_ark_config()
    if not ark["api_key"]:
        raise ValueError("LLM_API_KEY 未设置，无法调用图片生成 API")

    model = _resolve_model(model)
    url = f"{ark['base_url']}/images/generations"

    # 如果传了具体尺寸字符串就用它，否则用 2K
    if size in ("2K", "4K"):
        actual_size = size
    elif ratio in COVER_SIZES:
        actual_size = COVER_SIZES[ratio]
    else:
        actual_size = size

    body = {
        "model": model,
        "prompt": prompt,
        "size": actual_size,
        "response_format": "url",
        "watermark": watermark,
    }

    headers = {
        "Authorization": f"Bearer {ark['api_key']}",
        "Content-Type": "application/json",
    }

    logger.info("🎨 生成封面图: model=%s, size=%s", model, actual_size)
    logger.debug("Prompt (前80字): %s", prompt[:80])

    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(url, json=body, headers=headers)
        resp.raise_for_status()
        data = resp.json()

    # 响应格式: {"data": [{"url": "...", "size": "..."}], "usage": {...}}
    images = data.get("data", [])
    if not images:
        raise RuntimeError(f"图片生成无结果，API 返回: {data}")

    image_url = images[0].get("url", "")
    image_size = images[0].get("size", actual_size)

    # 下载到本地
    local_path = None
    if image_url:
        save_path.parent.mkdir(parents=True, exist_ok=True)
        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
            img_resp = await client.get(image_url)
            img_resp.raise_for_status()
            save_path.write_bytes(img_resp.content)
        local_path = str(save_path)
        size_kb = save_path.stat().st_size / 1024
        logger.info("✅ 封面图已保存: %s (%.0fKB)", save_path.name, size_kb)

    return {
        "image_url": image_url,
        "local_path": local_path,
        "size": image_size,
        "model": model,
        "usage": data.get("usage"),
    }


async def generate_cover_from_idea(
    idea: dict,
    save_path: Path,
    *,
    ratio: str = "9:16",
) -> dict:
    """
    根据创意方案自动生成封面图。
    从创意的 visual_style、title、concept 等字段拼接封面 prompt。
    """
    title = idea.get("title", "")
    visual = idea.get("visual_style", "")
    concept = idea.get("concept", "")
    tags = ", ".join(idea.get("tags", [])[:5])

    prompt = (
        f"短视频封面设计。主题：{title}。"
        f"概念：{concept}。"
        f"视觉风格：{visual}。"
        f"关键词：{tags}。"
        f"要求：高清，吸引眼球，适合短视频平台封面，画面主体突出，色彩鲜明。"
    )

    return await generate_image(prompt, save_path, ratio=ratio)
