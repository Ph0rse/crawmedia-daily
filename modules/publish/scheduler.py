"""
发布调度器 — 基于 APScheduler 的定时审批检查与自动发布

两个定时任务：
1. approval_deadline 时间（如 17:30）：同步审批状态（从 env / reply_file）
2. publish_time 时间（如 18:00）：自动发布已审批通过的内容

也支持一次性立即执行（bypass 定时逻辑）。
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Optional

from ..config import get_config
from .feishu_approval import (
    _get_daily_dir,
    get_approved_items,
    load_approval,
    sync_approval_from_env,
    sync_approval_from_reply_file,
)
from .distributor import distribute_approved

logger = logging.getLogger(__name__)


def should_publish(now_str: str, schedule_str: str) -> bool:
    """检查当前时间是否已到达发布时间"""
    try:
        now_t = datetime.strptime(now_str, "%H:%M")
        sch_t = datetime.strptime(schedule_str, "%H:%M")
        return now_t >= sch_t
    except ValueError:
        return False


async def sync_approvals() -> int:
    """从所有来源同步审批状态"""
    daily_dir = _get_daily_dir()

    total = 0
    # 环境变量
    env_count = sync_approval_from_env(daily_dir)
    if env_count:
        logger.info("从环境变量同步 %d 条审批", env_count)
    total += env_count

    # 回复文件
    file_count = sync_approval_from_reply_file(daily_dir)
    if file_count:
        logger.info("从回复文件同步 %d 条审批", file_count)
    total += file_count

    return total


async def check_and_publish(
    *,
    now_override: str | None = None,
    preview: bool = False,
    skip_feishu: bool = False,
    force: bool = False,
) -> list[dict]:
    """
    检查审批状态，如果到了发布时间则执行发布。

    Args:
        now_override: 覆盖当前时间（HH:MM，测试用）
        preview: 预览模式
        skip_feishu: 跳过飞书通知
        force: 强制发布（忽略时间检查）

    Returns:
        发布结果列表（空列表 = 未到时间或无内容）
    """
    cfg = get_config()
    publish_time = cfg.get("schedule", {}).get("publish_time", "18:00")
    now_str = now_override or datetime.now().strftime("%H:%M")

    # 时间检查
    if not force and not should_publish(now_str, publish_time):
        logger.info("未到发布时间 (当前=%s, 计划=%s)", now_str, publish_time)
        return []

    # 同步审批
    synced = await sync_approvals()
    if synced:
        logger.info("本次同步 %d 条审批决策", synced)

    # 检查是否有审批通过的内容
    daily_dir = _get_daily_dir()
    approved = get_approved_items(daily_dir)
    if not approved:
        logger.info("没有已审批通过的内容，跳过发布")
        return []

    logger.info("发现 %d 条已审批内容，开始发布...", len(approved))

    # 执行发布
    results = await distribute_approved(
        preview=preview,
        skip_feishu=skip_feishu,
    )

    return results


def start_scheduler(skip_feishu: bool = False) -> None:
    """
    启动 APScheduler 定时调度器。
    在 approval_deadline 和 publish_time 分别执行检查任务。

    注意：此函数会阻塞当前线程。适合作为独立进程运行。
    """
    try:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        from apscheduler.triggers.cron import CronTrigger
    except ImportError:
        logger.error("APScheduler 未安装，请运行: pip install apscheduler")
        return

    cfg = get_config()
    schedule = cfg.get("schedule", {})
    tz = schedule.get("timezone", "Asia/Shanghai")

    # 解析时间
    deadline = schedule.get("approval_deadline", "17:30")
    publish_time = schedule.get("publish_time", "18:00")

    deadline_h, deadline_m = map(int, deadline.split(":"))
    publish_h, publish_m = map(int, publish_time.split(":"))

    scheduler = AsyncIOScheduler(timezone=tz)

    # 审批同步任务（截止时间触发）
    async def _sync_job():
        logger.info("⏰ 审批同步任务触发")
        count = await sync_approvals()
        daily_dir = _get_daily_dir()
        approved = get_approved_items(daily_dir)
        logger.info("同步完成: 新增 %d 条, 已审批 %d 条", count, len(approved))

    # 发布任务（发布时间触发）
    async def _publish_job():
        logger.info("⏰ 自动发布任务触发")
        results = await check_and_publish(
            force=True,
            skip_feishu=skip_feishu,
        )
        success = sum(1 for r in results if r.get("publish_status") == "success")
        logger.info("发布完成: %d/%d 成功", success, len(results))

    scheduler.add_job(
        _sync_job,
        CronTrigger(hour=deadline_h, minute=deadline_m, timezone=tz),
        id="approval_sync",
        name="审批状态同步",
    )

    scheduler.add_job(
        _publish_job,
        CronTrigger(hour=publish_h, minute=publish_m, timezone=tz),
        id="auto_publish",
        name="自动发布",
    )

    logger.info("📅 调度器启动:")
    logger.info("   审批同步: %s", deadline)
    logger.info("   自动发布: %s", publish_time)
    logger.info("   时区: %s", tz)

    scheduler.start()

    try:
        asyncio.get_event_loop().run_forever()
    except (KeyboardInterrupt, SystemExit):
        scheduler.shutdown()
        logger.info("调度器已停止")
