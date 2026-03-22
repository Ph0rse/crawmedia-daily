"""
Publish 阶段主入口
编排：加载生成结果 → 飞书审批推送 → 审批同步 → 分发执行 → 结果通知

支持三种运行模式：
1. approve  — 发送审批卡片（Generate 完成后立即调用）
2. publish  — 执行发布（到达 publish_time 后调用）
3. full     — 完整流程：发送审批 → 等待 → 发布
4. daemon   — 启动调度器常驻进程

数据流：
    generated.json → [审批卡片推送] → approval.json
                                         ↓
    [CLI/env/reply 审批] → approval.json (updated)
                                         ↓
    [构建 manifest.json] → [distribute.ts] → publish_results.json
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path

from ..config import get_config
from .feishu_approval import (
    _get_daily_dir,
    load_generated,
    send_approval_cards,
    approve_items,
    sync_approval_from_env,
    sync_approval_from_reply_file,
    get_approved_items,
    load_approval,
)
from .distributor import distribute_approved
from .scheduler import check_and_publish, sync_approvals

logger = logging.getLogger(__name__)


async def run_publish(
    *,
    mode: str = "approve",
    skip_feishu: bool = False,
    preview: bool = False,
    approve_ids: list[int] | None = None,
    now_override: str | None = None,
) -> list[dict]:
    """
    Publish 阶段统一入口。

    Args:
        mode: 运行模式
            - "approve": 发送审批卡片到飞书
            - "publish": 检查审批状态并发布
            - "full": 先发送审批，然后直接发布（测试用）
            - "status": 查看当前审批状态
        skip_feishu: 跳过飞书推送
        preview: 预览模式（发布时不点发布按钮）
        approve_ids: 直接审批通过指定索引
        now_override: 覆盖当前时间（测试用）

    Returns:
        - approve 模式: 返回审批队列 items
        - publish 模式: 返回发布结果列表
        - full 模式: 返回发布结果列表
        - status 模式: 返回审批状态列表
    """
    cfg = get_config()
    niche_name = cfg.get("niche", {}).get("name", "未命名赛道")
    daily_dir = _get_daily_dir()

    logger.info("=" * 60)
    logger.info("🚀 Publish 阶段启动 — 赛道: %s, 模式: %s", niche_name, mode)
    logger.info("=" * 60)

    # ── 直接审批指定条目 ──
    if approve_ids is not None:
        count = approve_items(daily_dir, approve_ids, "approved")
        logger.info("✅ 审批通过 %d 条内容 (indices: %s)", count, approve_ids)

        # 如果同时指定了 publish 模式，继续发布
        if mode != "publish":
            approval = load_approval(daily_dir)
            return approval.get("items", []) if approval else []

    # ── approve 模式：发送审批卡片 ──
    if mode in ("approve", "full"):
        items = load_generated(daily_dir)
        if not items:
            logger.warning("⚠️  没有可审批的生成内容")
            return []

        approval = await send_approval_cards(items, skip_feishu=skip_feishu)
        logger.info("📋 审批队列已就绪，共 %d 条待确认", len(approval.get("items", [])))

        if mode == "approve":
            return approval.get("items", [])

    # ── full 模式：直接全部审批通过并发布（测试用）──
    if mode == "full":
        approval = load_approval(daily_dir)
        if approval:
            all_indices = [item["index"] for item in approval.get("items", [])]
            approve_items(daily_dir, all_indices, "approved")
            logger.info("⚡ Full 模式: 自动审批全部 %d 条内容", len(all_indices))

    # ── publish 模式：同步审批 + 执行发布 ──
    if mode in ("publish", "full"):
        # 先同步审批
        await sync_approvals()

        results = await distribute_approved(
            preview=preview,
            skip_feishu=skip_feishu,
        )
        return results

    # ── status 模式：查看审批状态 ──
    if mode == "status":
        approval = load_approval(daily_dir)
        if not approval:
            logger.info("📋 暂无审批记录")
            return []

        items = approval.get("items", [])
        pending = sum(1 for i in items if i["status"] == "pending")
        approved = sum(1 for i in items if i["status"] == "approved")
        rejected = sum(1 for i in items if i["status"] == "rejected")

        logger.info("📋 审批状态: 待审批 %d / 已通过 %d / 已拒绝 %d", pending, approved, rejected)
        for item in items:
            status_icon = {
                "pending": "⏳", "approved": "✅",
                "rejected": "❌", "skipped": "⏭️",
            }.get(item["status"], "❓")
            logger.info(
                "   %s [%d] %s — %s",
                status_icon, item["index"], item["title"], item["status"]
            )
        return items

    logger.warning("⚠️  未知模式: %s", mode)
    return []
