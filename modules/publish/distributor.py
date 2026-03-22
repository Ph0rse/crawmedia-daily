"""
分发编排器 — 将审批通过的内容分发到目标平台

职责：
1. 读取 approval.json 获取已审批通过的条目索引
2. 从 generated.json 匹配对应内容
3. 为每条内容构建 Manifest 并逐一分发
4. 生成 publish_results.json 记录发布结果
5. 推送飞书发布结果通知
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from ..config import get_config
from ..feishu import send_feishu_rich_text
from .feishu_approval import (
    load_generated,
    load_approval,
    get_approved_items,
    _get_daily_dir,
)
from .douyin_publisher import build_manifest, save_manifest, publish_to_douyin

logger = logging.getLogger(__name__)


def _save_publish_results(results: list[dict], daily_dir: Path) -> Path:
    """保存发布结果到 publish_results.json"""
    path = daily_dir / "publish_results.json"
    payload = {
        "stage": "publish",
        "timestamp": datetime.now().isoformat(),
        "result_count": len(results),
        "results": results,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    logger.info("发布结果已保存: %s", path)
    return path


def _format_publish_results_for_feishu(
    results: list[dict],
    niche_name: str,
) -> tuple[str, list[list[dict]]]:
    """将发布结果格式化为飞书富文本"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    title = f"🚀 【{niche_name}】发布结果（{now}）"

    paragraphs: list[list[dict]] = []

    success_count = sum(1 for r in results if r.get("publish_status") == "success")
    error_count = sum(1 for r in results if r.get("publish_status") == "error")
    skip_count = sum(1 for r in results if r.get("publish_status") in ("skipped", "no_manifest"))
    assisted_count = sum(1 for r in results if r.get("publish_status") in ("assisted", "preview_only"))

    paragraphs.append([
        {"tag": "text", "text": (
            f"📊 发布统计：共 {len(results)} 条内容\n"
            f"   ✅ 已点发布: {success_count}\n"
            f"   🔵 需人工/预览: {assisted_count}\n"
            f"   ❌ 失败: {error_count}\n"
            f"   ⏭️ 跳过: {skip_count}\n"
        )},
    ])

    for r in results:
        status_icon = {
            "success": "✅",
            "error": "❌",
            "skipped": "⏭️",
            "assisted": "🔵",
            "preview_only": "👁",
        }.get(r.get("publish_status", ""), "❓")
        row = [
            {"tag": "text", "text": (
                f"\n{'─' * 28}\n"
                f"{status_icon} [{r.get('index', '?')}] {r.get('title', '未命名')}\n"
                f"   平台: {', '.join(r.get('platforms', []))}\n"
                f"   消息: {r.get('message', '')[:80]}\n"
            )},
        ]
        paragraphs.append(row)

    return title, paragraphs


async def distribute_approved(
    *,
    preview: bool = False,
    skip_feishu: bool = False,
    force_items: list[int] | None = None,
) -> list[dict]:
    """
    分发所有已审批通过的内容。

    Args:
        preview: True = 预览模式，只填写表单不点发布
        skip_feishu: 跳过飞书结果通知
        force_items: 强制发布指定索引的内容（忽略审批状态）

    Returns:
        发布结果列表
    """
    cfg = get_config()
    niche_name = cfg.get("niche", {}).get("name", "未命名赛道")
    target_platforms = cfg.get("output", {}).get("platforms", ["douyin"])
    if isinstance(target_platforms, str):
        target_platforms = [target_platforms]

    pub_cfg = cfg.get("publish", {}) or {}
    timeout_sec = int(pub_cfg.get("timeout_per_item", 180))
    # 非预览模式需轮询「发布」按钮 + 点击，适当加长超时
    effective_preview = bool(preview) or bool(pub_cfg.get("preview_mode", False))
    if not effective_preview:
        timeout_sec = max(timeout_sec, 240)

    daily_dir = _get_daily_dir()

    # 加载生成结果
    generated_items = load_generated(daily_dir)
    if not generated_items:
        logger.warning("没有可发布的生成内容")
        return []

    # 确定要发布的条目
    if force_items is not None:
        indices_to_publish = set(force_items)
    else:
        approved = get_approved_items(daily_dir)
        if not approved:
            logger.warning("没有已审批通过的内容")
            return []
        indices_to_publish = {item["index"] for item in approved}

    logger.info("准备发布 %d 条内容到 %s", len(indices_to_publish), target_platforms)

    results = []

    for idx in sorted(indices_to_publish):
        if idx >= len(generated_items):
            logger.warning("索引 %d 超出生成内容范围 (%d)", idx, len(generated_items))
            continue

        item = generated_items[idx]
        title = item.get("title", f"内容_{idx}")
        logger.info("━" * 40)
        logger.info("📦 [%d] 开始分发: %s", idx, title)

        # 构建 manifest
        manifest = build_manifest(item, idx, platforms=target_platforms)
        if not manifest:
            results.append({
                "index": idx,
                "title": title,
                "platforms": target_platforms,
                "publish_status": "no_manifest",
                "message": "无法构建 Manifest（缺少视频或文案）",
            })
            continue

        # 保存 manifest
        manifest_path = save_manifest(manifest, idx)
        logger.info("📋 Manifest: %s", manifest_path)

        # 逐平台执行发布
        platforms_str = ",".join(
            p if p != "xiaohongshu" else "xhs"
            for p in target_platforms
            if p in manifest.get("outputs", {}) or
               (p == "xiaohongshu" and "xiaohongshu" in manifest.get("outputs", {}))
        )

        if not platforms_str:
            results.append({
                "index": idx,
                "title": title,
                "platforms": target_platforms,
                "publish_status": "skipped",
                "message": "Manifest 中无匹配的平台输出",
            })
            continue

        pub_result = publish_to_douyin(
            manifest_path,
            platforms=platforms_str,
            preview=effective_preview,
            timeout=timeout_sec,
        )

        results.append({
            "index": idx,
            "title": title,
            "platforms": target_platforms,
            "publish_status": pub_result["status"],
            "message": pub_result["message"],
            "output_snippet": pub_result.get("output", "")[-300:],
            "manifest_path": str(manifest_path),
        })

        if pub_result["status"] == "success":
            logger.info("✅ [%d] 发布成功: %s", idx, title)
        else:
            logger.error("❌ [%d] 发布失败: %s", idx, pub_result["message"])

    # 保存发布结果
    _save_publish_results(results, daily_dir)

    # 飞书通知
    if not skip_feishu and results:
        try:
            feishu_title, paragraphs = _format_publish_results_for_feishu(results, niche_name)
            await send_feishu_rich_text(feishu_title, paragraphs)
            logger.info("📨 发布结果已推送飞书")
        except Exception as e:
            logger.error("❌ 飞书推送失败: %s", e)

    # 汇总
    success = sum(1 for r in results if r["publish_status"] == "success")
    logger.info("=" * 50)
    logger.info("🚀 发布完成: %d/%d 成功", success, len(results))
    logger.info("=" * 50)

    return results
