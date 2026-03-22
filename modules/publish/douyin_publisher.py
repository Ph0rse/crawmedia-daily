"""
抖音发布模块 — 通过 vendor/distribute/distribute.ts 发布视频到抖音

工作原理：
1. 从 generated.json 提取视频文件路径和文案信息
2. 构建符合 Manifest 格式的 JSON
3. 调用 distribute.ts 通过 Chrome CDP 自动化上传到抖音创作者平台

前置条件：
- 需要 Chrome 浏览器
- 首次使用需手动登录抖音创作者平台（后续复用 profile）
- distribute.ts 已内置于 vendor/，无需外部依赖
"""
from __future__ import annotations

import json
import logging
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional

from ..config import get_config, PROJECT_ROOT

logger = logging.getLogger(__name__)

# 分发脚本路径（已内置到 vendor/，不再依赖外部 content-pipeline 目录）
DISTRIBUTE_SCRIPT = PROJECT_ROOT / "vendor" / "distribute" / "distribute.ts"
MANIFEST_DIR = Path("/tmp/crawmedia")


def _resolve_video_path(item: dict) -> str | None:
    """从 generated.json 条目中提取本地视频文件路径"""
    video = item.get("video")
    if not video:
        return None

    # draft 模式
    if "final" in video and video["final"]:
        return video["final"].get("local_path")
    if "draft" in video and video["draft"]:
        return video["draft"].get("local_path")

    # 标准模式
    return video.get("local_path")


def _get_douyin_copy(item: dict) -> dict | None:
    """提取抖音平台文案"""
    copy = item.get("copy", {})
    douyin = copy.get("douyin")
    if douyin:
        return douyin

    # 如果没有专门的抖音文案，用通用信息兜底
    title = item.get("title", "")
    tags = []
    if isinstance(copy, dict):
        for platform_copy in copy.values():
            if isinstance(platform_copy, dict) and "tags" in platform_copy:
                tags = platform_copy["tags"]
                break

    if title:
        return {
            "title": title[:20],
            "description": item.get("concept", title),
            "tags": tags or [f"#{t}" for t in item.get("tags", ["萌宠", "日常"])[:8]],
        }
    return None


def build_manifest(
    item: dict,
    idx: int,
    *,
    platforms: list[str] | None = None,
) -> dict | None:
    """
    为单条内容构建分发 Manifest。

    Manifest 结构参考 vendor/distribute/cdp-utils.ts 的 Manifest 接口：
    {
        outputs: {
            douyin: { video: string, copy: { title, description, tags } }
        }
    }
    """
    video_path = _resolve_video_path(item)
    if not video_path:
        logger.warning("[%d] %s: 无视频文件，跳过", idx, item.get("title", ""))
        return None

    # 验证视频文件存在
    if not Path(video_path).exists():
        logger.warning("[%d] 视频文件不存在: %s", idx, video_path)
        return None

    platforms = platforms or ["douyin"]

    manifest: dict = {
        "version": "1.0",
        "created": datetime.now().isoformat(),
        "source": "crawmedia-daily",
        "title": item.get("title", f"内容_{idx}"),
        "outputs": {},
        "meta": {
            "idea_id": item.get("idea_id", ""),
            "strategy": item.get("strategy", ""),
            "generated_at": datetime.now().strftime("%Y-%m-%d"),
        },
    }

    # 构建抖音输出
    if "douyin" in platforms:
        douyin_copy = _get_douyin_copy(item)
        if douyin_copy:
            manifest["outputs"]["douyin"] = {
                "video": str(Path(video_path).resolve()),
                "copy": {
                    "title": douyin_copy.get("title", "")[:20],
                    "description": douyin_copy.get("description", ""),
                    "tags": douyin_copy.get("tags", []),
                },
            }

    # 构建小红书输出（如果有图文素材）
    if "xiaohongshu" in platforms:
        xhs_copy = item.get("copy", {}).get("xiaohongshu")
        if xhs_copy:
            manifest["outputs"]["xiaohongshu"] = {
                "copy": {
                    "title": xhs_copy.get("title", ""),
                    "body": xhs_copy.get("body", xhs_copy.get("description", "")),
                    "tags": xhs_copy.get("tags", []),
                },
            }

    if not manifest["outputs"]:
        logger.warning("[%d] 无可用平台输出，跳过", idx)
        return None

    return manifest


def save_manifest(manifest: dict, idx: int) -> Path:
    """保存 manifest 到临时目录"""
    MANIFEST_DIR.mkdir(parents=True, exist_ok=True)
    path = MANIFEST_DIR / f"manifest_{idx}_{datetime.now().strftime('%H%M%S')}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    return path


def _parse_distribute_output(output: str) -> dict:
    """
    根据 distribute.ts 控制台输出判断真实发布结果。
    严格匹配 douyin.ts 的返回消息，避免误报「成功」。
    """
    o = output or ""

    # ── 真正成功（包含验证关键字）──────────────────────────────
    # 注意：已加入 verifyPublishSuccess，成功时消息为
    #   "Published to Douyin | URL跳转至:..." 或
    #   "Published to Douyin | 页面出现「发布成功」" 等
    if "Published to Douyin" in o:
        # 从输出中提取验证原因
        reason = "已点击发布并通过页面验证"
        for line in o.splitlines():
            if "Publish verified:" in line:
                reason = line.split("Publish verified:")[-1].strip()
                break
        return {"status": "success", "message": f"发布成功 | {reason}", "output": o[-1500:]}

    # ── 预览模式 ──────────────────────────────────────────────
    if "Content pre-filled in Douyin editor" in o or (
        "Content pre-filled" in o and "Douyin" in o
    ):
        return {
            "status": "preview_only",
            "message": "预览模式：仅预填，未点击发布",
            "output": o[-1500:],
        }

    # ── 点击了但验证失败（新增）────────────────────────────────
    if "Button clicked but publish not confirmed" in o:
        # 截取 verify.reason 部分
        msg = "已点击发布，但页面验证失败"
        for line in o.splitlines():
            if "Verify failed:" in line or "Button clicked but publish not confirmed" in line:
                msg = line.strip()
                break
        return {
            "status": "assisted",
            "message": msg,
            "output": o[-1500:],
        }

    # ── 未找到发布按钮 ────────────────────────────────────────
    if "publish button not found" in o or "Content filled but publish button" in o:
        return {
            "status": "assisted",
            "message": "已填写素材，但未找到发布按钮（视频转码超时或页面结构变化）",
            "output": o[-1500:],
        }

    # ── 需要登录 ─────────────────────────────────────────────
    if "Login required" in o or "log in to Douyin" in o.lower():
        return {"status": "assisted", "message": "需先登录抖音创作者平台", "output": o[-1500:]}

    # ── 兜底：有成功摘要但未识别到发布确认 ────────────────────
    if "platforms published successfully" in o and ("🔵 抖音" in o or "✅ 抖音" in o):
        # 从 distribute.ts 汇总行中提取实际状态
        for line in o.splitlines():
            if "🔵 抖音:" in line or "✅ 抖音:" in line:
                platform_msg = line.strip()
                break
        else:
            platform_msg = "未识别到具体状态"
        return {
            "status": "assisted",
            "message": f"脚本已结束，请人工核对: {platform_msg}",
            "output": o[-1500:],
        }

    return {"status": "assisted", "message": "未识别到发布结果，请手动核查", "output": o[-1500:]}


def _detect_ts_runner() -> list[str]:
    """
    自动检测 TypeScript 运行器：bun > tsx > npx bun（兜底）。
    返回命令前缀列表，如 ["bun"] 或 ["npx", "tsx"]。
    """
    import shutil

    # 优先直接安装的 bun（最快）
    if shutil.which("bun"):
        return ["bun"]

    # 其次 tsx（通过 npx 调用也很快）
    if shutil.which("tsx"):
        return ["tsx"]

    # npx tsx 作为备选
    if shutil.which("npx"):
        return ["npx", "tsx"]

    # 最后兜底
    return ["npx", "-y", "bun"]


def publish_to_douyin(
    manifest_path: Path,
    *,
    platforms: str = "douyin",
    preview: bool = False,
    timeout: int = 120,
) -> dict:
    """
    调用 vendor/distribute/distribute.ts 执行抖音发布。

    Args:
        manifest_path: manifest.json 文件路径
        platforms: 目标平台（逗号分隔）
        preview: True = 只填写不点发布
        timeout: 超时时间（秒）

    Returns:
        {"status": "success"/"error", "message": str, "output": str}
    """
    if not DISTRIBUTE_SCRIPT.exists():
        return {
            "status": "error",
            "message": f"distribute.ts 未找到: {DISTRIBUTE_SCRIPT}",
            "output": "",
        }

    runner = _detect_ts_runner()
    cmd = [
        *runner,
        str(DISTRIBUTE_SCRIPT),
        "--manifest", str(manifest_path),
        "--platforms", platforms,
    ]
    if preview:
        cmd.append("--preview")

    logger.info("执行分发: %s", " ".join(cmd))

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )

        output = (result.stdout or "") + (result.stderr or "")
        if result.returncode != 0:
            logger.error("分发失败 (exit=%d): %s", result.returncode, output[-500:])
            return {
                "status": "error",
                "message": f"distribute.ts 执行失败 (exit={result.returncode})",
                "output": output[-1000:],
            }

        logger.info("分发完成: %s", output[-200:])
        return _parse_distribute_output(output)

    except subprocess.TimeoutExpired:
        return {
            "status": "error",
            "message": f"发布超时（{timeout}s）",
            "output": "",
        }
    except FileNotFoundError:
        return {
            "status": "error",
            "message": "npx 或 bun 未安装，请先安装 Node.js 环境",
            "output": "",
        }
