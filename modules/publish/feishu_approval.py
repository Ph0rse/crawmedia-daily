"""
飞书审批模块 — 方案 A: Webhook 富文本卡片 + 本地审批文件

功能：
1. 将 generated.json 中的每条内容组装为可预览的飞书卡片消息
   （包含视频链接 / 封面图 / 平台文案 / 操作指引）
2. 创建 approval.json 跟踪每条内容的审批状态
3. 支持三种审批方式：
   - CLI: python scripts/run_single.py approve --ids 0,1
   - 环境变量: APPROVED_IDS=0,1
   - 回复文件: data/daily/YYYY-MM-DD/approval_reply.json
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from ..config import get_config
from ..feishu import send_feishu_rich_text

logger = logging.getLogger(__name__)

# ────────────────────────────────────────────────────────
# 审批文件读写
# ────────────────────────────────────────────────────────

def _get_daily_dir() -> Path:
    cfg = get_config()
    data_dir = Path(cfg.get("output", {}).get("data_dir", "./data"))
    today = datetime.now().strftime("%Y-%m-%d")
    daily_dir = data_dir / "daily" / today
    daily_dir.mkdir(parents=True, exist_ok=True)
    return daily_dir


def load_generated(daily_dir: Path) -> list[dict]:
    """从 generated.json 加载生成结果"""
    path = daily_dir / "generated.json"
    if not path.exists():
        logger.warning("generated.json 不存在: %s", path)
        return []
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("items", [])


def create_approval_queue(items: list[dict], daily_dir: Path) -> dict:
    """
    根据生成结果创建审批队列文件 approval.json。
    每条内容初始状态为 pending。
    """
    cfg = get_config()
    publish_time = cfg.get("schedule", {}).get("publish_time", "18:00")

    approval = {
        "created_at": datetime.now().isoformat(),
        "publish_time": publish_time,
        "item_count": len(items),
        "items": [],
    }

    for idx, item in enumerate(items):
        has_video = bool(_resolve_video_path(item))
        has_copy = bool(item.get("copy"))
        has_cover = bool(item.get("cover"))

        approval["items"].append({
            "index": idx,
            "idea_id": item.get("idea_id", f"item_{idx}"),
            "title": item.get("title", "未命名"),
            "strategy": item.get("strategy", ""),
            "status": "pending",  # pending / approved / rejected / skipped
            "review_note": "",
            "has_video": has_video,
            "has_copy": has_copy,
            "has_cover": has_cover,
        })

    path = daily_dir / "approval.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(approval, f, ensure_ascii=False, indent=2)
    logger.info("审批队列已创建: %s (%d 条)", path, len(items))
    return approval


def load_approval(daily_dir: Path) -> dict | None:
    path = daily_dir / "approval.json"
    if not path.exists():
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_approval(approval: dict, daily_dir: Path) -> None:
    path = daily_dir / "approval.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(approval, f, ensure_ascii=False, indent=2)


def approve_items(daily_dir: Path, indices: list[int], status: str = "approved") -> int:
    """
    批量更新审批状态。
    indices: 要更新的条目索引列表
    status: approved / rejected / skipped
    返回实际更新的条数。
    """
    approval = load_approval(daily_dir)
    if not approval:
        logger.error("审批队列不存在")
        return 0

    updated = 0
    for item in approval["items"]:
        if item["index"] in indices:
            item["status"] = status
            item["review_note"] = f"manual_{datetime.now().strftime('%H:%M')}"
            updated += 1

    save_approval(approval, daily_dir)
    logger.info("审批更新: %d 条 → %s", updated, status)
    return updated


def sync_approval_from_env(daily_dir: Path) -> int:
    """从环境变量同步审批决策"""
    import os
    approved_raw = os.environ.get("APPROVED_IDS", "")
    rejected_raw = os.environ.get("REJECTED_IDS", "")

    if not approved_raw and not rejected_raw:
        return 0

    approval = load_approval(daily_dir)
    if not approval:
        return 0

    approved_ids = {int(x.strip()) for x in approved_raw.split(",") if x.strip().isdigit()}
    rejected_ids = {int(x.strip()) for x in rejected_raw.split(",") if x.strip().isdigit()}

    updated = 0
    for item in approval["items"]:
        idx = item["index"]
        if idx in approved_ids and item["status"] == "pending":
            item["status"] = "approved"
            item["review_note"] = "env_sync"
            updated += 1
        elif idx in rejected_ids and item["status"] == "pending":
            item["status"] = "rejected"
            item["review_note"] = "env_sync"
            updated += 1

    save_approval(approval, daily_dir)
    return updated


def sync_approval_from_reply_file(daily_dir: Path) -> int:
    """从 approval_reply.json 同步审批决策"""
    reply_path = daily_dir / "approval_reply.json"
    if not reply_path.exists():
        return 0

    with open(reply_path, "r", encoding="utf-8") as f:
        reply = json.load(f)

    approved_ids = set(reply.get("approved_ids", []))
    rejected_ids = set(reply.get("rejected_ids", []))

    approval = load_approval(daily_dir)
    if not approval:
        return 0

    updated = 0
    for item in approval["items"]:
        idx = item["index"]
        idea_id = item["idea_id"]
        # 支持按 index 或 idea_id 匹配
        if (idx in approved_ids or idea_id in approved_ids) and item["status"] == "pending":
            item["status"] = "approved"
            item["review_note"] = "reply_file"
            updated += 1
        elif (idx in rejected_ids or idea_id in rejected_ids) and item["status"] == "pending":
            item["status"] = "rejected"
            item["review_note"] = "reply_file"
            updated += 1

    save_approval(approval, daily_dir)
    return updated


def get_approved_items(daily_dir: Path) -> list[dict]:
    """获取所有已审批通过的条目"""
    approval = load_approval(daily_dir)
    if not approval:
        return []
    return [item for item in approval["items"] if item["status"] == "approved"]


# ────────────────────────────────────────────────────────
# 视频路径解析（兼容 default / draft 模式）
# ────────────────────────────────────────────────────────

def _resolve_video_path(item: dict) -> str | None:
    """从 generated.json 条目中提取视频文件路径"""
    video = item.get("video")
    if not video:
        return None

    # draft 模式: {final: {local_path: ...}, draft: {local_path: ...}}
    if "final" in video and video["final"]:
        return video["final"].get("local_path")
    if "draft" in video and video["draft"]:
        return video["draft"].get("local_path")

    # 标准模式: {local_path: ...}
    return video.get("local_path")


def _resolve_video_url(item: dict) -> str | None:
    """从 generated.json 条目中提取视频在线 URL"""
    video = item.get("video")
    if not video:
        return None

    if "final" in video and video["final"]:
        return video["final"].get("video_url")
    if "draft" in video and video["draft"]:
        return video["draft"].get("video_url")

    return video.get("video_url")


# ────────────────────────────────────────────────────────
# 飞书审批卡片推送
# ────────────────────────────────────────────────────────

def _format_approval_card(
    items: list[dict],
    niche_name: str,
    publish_time: str,
) -> tuple[str, list[list[dict]]]:
    """
    将待审批内容格式化为飞书富文本预览卡片。
    包含：序号 / 标题 / 策略 / 视频链接 / 文案预览 / 操作指引
    """
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    title = f"📋 【{niche_name}】内容审批 — 共 {len(items)} 条待确认（{now}）"

    paragraphs: list[list[dict]] = []

    # 头部说明
    paragraphs.append([
        {"tag": "text", "text": (
            f"🎯 以下内容已生成完毕，请审批确认：\n"
            f"⏰ 发布时间：{publish_time}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        )},
    ])

    strategy_labels = {
        "combine": "🔀 融合",
        "generalize": "🔄 泛化",
        "transfer": "🚀 迁移",
        "extend": "📐 延展",
    }

    for idx, item in enumerate(items):
        strategy = strategy_labels.get(item.get("strategy", ""), "💡 原创")
        video_url = _resolve_video_url(item)
        video_path = _resolve_video_path(item)
        cover = item.get("cover", {})
        copy = item.get("copy", {})
        douyin_copy = copy.get("douyin", {})

        # 标题行
        row: list[dict] = [
            {"tag": "text", "text": f"\n{'─' * 28}\n"},
            {"tag": "text", "text": f"📌 [{idx}] {item.get('title', '未命名')}\n"},
            {"tag": "text", "text": f"   策略: {strategy}\n"},
        ]

        # 视频信息
        if video_url:
            row.append({"tag": "text", "text": "   🎬 视频: "})
            row.append({"tag": "a", "text": "点击预览", "href": video_url})
            row.append({"tag": "text", "text": "\n"})
        elif video_path:
            row.append({"tag": "text", "text": f"   🎬 视频: {Path(video_path).name}\n"})
        else:
            row.append({"tag": "text", "text": "   🎬 视频: ❌ 未生成\n"})

        # 封面信息
        cover_path = cover.get("local_path") if isinstance(cover, dict) else None
        if cover_path:
            row.append({"tag": "text", "text": f"   🎨 封面: ✅ {Path(cover_path).name}\n"})
        else:
            row.append({"tag": "text", "text": "   🎨 封面: ❌ 未生成\n"})

        # 文案预览
        if douyin_copy:
            douyin_title = douyin_copy.get("title", "")[:40]
            douyin_tags = " ".join(douyin_copy.get("tags", [])[:5])
            row.append({"tag": "text", "text": f"   ✍️ 抖音文案: {douyin_title}\n"})
            if douyin_tags:
                row.append({"tag": "text", "text": f"   🏷️ 标签: {douyin_tags}\n"})
        else:
            row.append({"tag": "text", "text": "   ✍️ 文案: ❌ 未生成\n"})

        # 错误信息
        errors = item.get("errors", [])
        if errors:
            row.append({"tag": "text", "text": f"   ⚠️ 问题: {'; '.join(errors[:2])}\n"})

        paragraphs.append(row)

    # 底部操作指引
    paragraphs.append([
        {"tag": "text", "text": (
            f"\n{'━' * 30}\n"
            f"📝 审批方式（任选其一）：\n"
            f"  1️⃣ CLI: python scripts/run_single.py approve --ids 0,1,2\n"
            f"  2️⃣ 回复文件: 编辑 approval_reply.json\n"
            f"  3️⃣ 环境变量: APPROVED_IDS=0,1,2\n"
            f"\n⏰ 截止时间: {publish_time}，届时自动发布已确认内容"
        )},
    ])

    return title, paragraphs


async def send_approval_cards(
    items: list[dict],
    skip_feishu: bool = False,
) -> dict:
    """
    发送审批预览卡片到飞书，并创建审批队列。

    Args:
        items: generated.json 中的 items 列表
        skip_feishu: 跳过飞书推送（仅创建本地审批文件）

    Returns:
        approval 字典
    """
    cfg = get_config()
    niche_name = cfg.get("niche", {}).get("name", "未命名赛道")
    publish_time = cfg.get("schedule", {}).get("publish_time", "18:00")
    daily_dir = _get_daily_dir()

    # 创建审批队列
    approval = create_approval_queue(items, daily_dir)

    # 推送飞书
    if not skip_feishu:
        try:
            title, paragraphs = _format_approval_card(items, niche_name, publish_time)
            await send_feishu_rich_text(title, paragraphs)
            logger.info("📨 审批卡片已推送飞书")
        except Exception as e:
            logger.error("❌ 飞书审批推送失败: %s", e)

    return approval
