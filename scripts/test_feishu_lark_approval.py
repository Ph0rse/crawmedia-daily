#!/usr/bin/env python3
"""
飞书互动卡片审批测试脚本 — 长链接（WebSocket）模式

工作原理：
  1. 启动一个 WebSocket 长链接（全程只用一个连接，避免 asyncio 冲突）
  2. 若未配置接收方：监听用户给机器人发送的第一条消息，自动识别 open_id
  3. 识别后（或直接配置了 open_id）：发送带按钮的互动审批卡片
  4. 用户点击按钮后回调到同一连接，更新 approval.json 并刷新卡片
  5. 所有条目处理完毕后自动退出

用法：
  python scripts/test_feishu_lark_approval.py
  python scripts/test_feishu_lark_approval.py --open-id ou_xxxx
  python scripts/test_feishu_lark_approval.py --chat-id oc_xxxx
  python scripts/test_feishu_lark_approval.py --all   # 显示全部（含已审批）

前置条件：
  pip3 install lark-oapi
  飞书开发者后台：https://open.feishu.cn/app/cli_a934afacb7785bb3
    → 权限管理：im:message  im:message:send_as_bot
    → 事件订阅：订阅方式选「长连接」，添加 im.message.receive_v1 / card.action.trigger
    → 发布版本后生效
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

# ── 路径配置 ────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")

# ── 日志 ─────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("feishu-approval")

# ── 飞书应用凭证（优先读 .env，其次用硬编码默认值供测试）─
FEISHU_APP_ID     = os.getenv("FEISHU_APP_ID",     "cli_a934afacb7785bb3")
FEISHU_APP_SECRET = os.getenv("FEISHU_APP_SECRET",  "ezJfJZCuichCnfs1rdicFdXfNp5tUYWA")


# ════════════════════════════════════════════════════════
#  数据工具
# ════════════════════════════════════════════════════════

def get_daily_dir() -> Path:
    from modules.config import get_config
    cfg = get_config()
    data_dir = Path(cfg.get("output", {}).get("data_dir", "./data"))
    today = datetime.now().strftime("%Y-%m-%d")
    return data_dir / "daily" / today


def load_approval(daily_dir: Path) -> dict | None:
    path = daily_dir / "approval.json"
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_approval(approval: dict, daily_dir: Path) -> None:
    path = daily_dir / "approval.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(approval, f, ensure_ascii=False, indent=2)


def update_item_status(daily_dir: Path, index: int, status: str, operator_id: str) -> str:
    """更新指定条目审批状态，返回标题"""
    approval = load_approval(daily_dir)
    if not approval:
        return "unknown"
    for item in approval["items"]:
        if item["index"] == index:
            item["status"] = status
            item["review_note"] = f"feishu_{operator_id}_{datetime.now().strftime('%H:%M')}"
            title = item["title"]
            save_approval(approval, daily_dir)
            logger.info("✏️  [%d] %s → %s (by %s)", index, title, status, operator_id)
            return title
    return "unknown"


def get_pending_items(approval: dict, include_all: bool = False) -> list[dict]:
    items = approval.get("items", [])
    return items if include_all else [i for i in items if i["status"] == "pending"]


# ════════════════════════════════════════════════════════
#  飞书互动卡片构建
# ════════════════════════════════════════════════════════

STRATEGY_LABELS = {
    "combine": "🔀融合", "generalize": "🔄泛化",
    "transfer": "🚀迁移", "extend": "📐延展",
}
STATUS_ICONS = {
    "pending": "⏳", "approved": "✅", "rejected": "❌", "skipped": "⏭️",
}


def build_approval_card(items: list[dict], publish_time: str) -> str:
    """构建飞书互动卡片 JSON（兼容格式，schema 1.x）"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    elements: list[dict] = []

    elements.append({
        "tag": "div",
        "text": {
            "tag": "lark_md",
            "content": (
                f"**📋 CrawMedia 内容审批**\n"
                f"共 **{len(items)}** 条，发布时间 **{publish_time}**　⏰ {now}"
            ),
        },
    })
    elements.append({"tag": "hr"})

    for item in items:
        idx    = item["index"]
        title  = item["title"]
        strat  = STRATEGY_LABELS.get(item.get("strategy", ""), "💡原创")
        status = item.get("status", "pending")
        icon   = STATUS_ICONS.get(status, "❓")

        flags = "　".join(
            f for f, k in [("🎬视频", "has_video"), ("✍️文案", "has_copy"), ("🎨封面", "has_cover")]
            if item.get(k)
        )
        elements.append({
            "tag": "div",
            "text": {"tag": "lark_md", "content": (
                f"**[{idx}] {title}**\n"
                f"策略: {strat}　状态: {icon} {status}　{flags}"
            )},
        })

        if status == "pending":
            elements.append({
                "tag": "action",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "✅ 确认发布"},
                        "type": "primary",
                        "value": {"action": "approve", "index": str(idx)},
                    },
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "⏭️ 跳过"},
                        "type": "default",
                        "value": {"action": "skip", "index": str(idx)},
                    },
                ],
            })
        elements.append({"tag": "hr"})

    elements.append({
        "tag": "div",
        "text": {"tag": "lark_md", "content": "💡 点击按钮后系统自动更新审批状态"},
    })

    return json.dumps({
        "elements": elements,
        "header": {
            "title": {"content": "CrawMedia 内容审批", "tag": "plain_text"},
            "template": "blue",
        },
    }, ensure_ascii=False)


# ════════════════════════════════════════════════════════
#  飞书 API 封装
# ════════════════════════════════════════════════════════

def send_card_message(client, card_json: str, receive_id: str, receive_id_type: str) -> str | None:
    """发送互动卡片，返回 message_id"""
    import lark_oapi as lark
    from lark_oapi.api.im.v1 import CreateMessageRequest, CreateMessageRequestBody

    resp = client.im.v1.message.create(
        CreateMessageRequest.builder()
        .receive_id_type(receive_id_type)
        .request_body(
            CreateMessageRequestBody.builder()
            .receive_id(receive_id)
            .msg_type("interactive")
            .content(card_json)
            .build()
        )
        .build()
    )
    if not resp.success():
        logger.error("发送卡片失败: code=%s msg=%s", resp.code, resp.msg)
        return None
    msg_id = resp.data.message_id if resp.data else None
    logger.info("📨 卡片发送成功 message_id=%s", msg_id)
    return msg_id


def update_card(client, message_id: str, card_json: str) -> None:
    """更新已发出的卡片"""
    from lark_oapi.api.im.v1 import PatchMessageRequest, PatchMessageRequestBody
    resp = client.im.v1.message.patch(
        PatchMessageRequest.builder()
        .message_id(message_id)
        .request_body(PatchMessageRequestBody.builder().content(card_json).build())
        .build()
    )
    if not resp.success():
        logger.warning("更新卡片失败: code=%s msg=%s", resp.code, resp.msg)
    else:
        logger.info("🔄 卡片已刷新")


def reply_text(client, message_id: str, text: str) -> None:
    """回复文本消息"""
    from lark_oapi.api.im.v1 import ReplyMessageRequest, ReplyMessageRequestBody
    resp = client.im.v1.message.reply(
        ReplyMessageRequest.builder()
        .message_id(message_id)
        .request_body(
            ReplyMessageRequestBody.builder()
            .msg_type("text")
            .content(json.dumps({"text": text}, ensure_ascii=False))
            .build()
        )
        .build()
    )
    if not resp.success():
        logger.warning("回复消息失败: code=%s msg=%s", resp.code, resp.msg)


# ════════════════════════════════════════════════════════
#  主逻辑（单 ws.Client 架构）
# ════════════════════════════════════════════════════════

def run(args: argparse.Namespace) -> None:
    import lark_oapi as lark
    from lark_oapi.event.callback.model.p2_card_action_trigger import (
        P2CardActionTrigger, P2CardActionTriggerResponse, CallBackToast,
    )

    # ── 加载审批数据 ──────────────────────────────────────
    daily_dir = get_daily_dir()
    approval = load_approval(daily_dir)
    if not approval:
        logger.error("❌ 未找到 approval.json，请先运行: python scripts/run_single.py approve")
        sys.exit(1)

    all_items    = approval.get("items", [])
    pending      = get_pending_items(approval, include_all=args.all)
    publish_time = approval.get("publish_time", "18:00")

    if not pending:
        logger.warning("没有待审批内容（所有条目已有审批状态）")
        logger.info("当前: %s", {i["title"]: i["status"] for i in all_items})
        return

    logger.info("待审批 %d 条（总计 %d 条）", len(pending), len(all_items))

    # ── 接收方配置 ──────────────────────────────────────
    receive_id = (
        args.open_id or args.chat_id
        or os.getenv("FEISHU_RECEIVE_OPEN_ID", "")
        or os.getenv("FEISHU_CHAT_ID", "")
    ).strip()
    receive_id_type = "chat_id" if (args.chat_id or receive_id.startswith("oc_")) else "open_id"
    auto_detect = not receive_id  # 未配置时进入自动识别模式

    # ── Feishu Client（HTTP API 用）──────────────────────
    client = (
        lark.Client.builder()
        .app_id(FEISHU_APP_ID)
        .app_secret(FEISHU_APP_SECRET)
        .log_level(lark.LogLevel.WARNING)
        .build()
    )

    # ── 共享状态 ─────────────────────────────────────────
    pending_indices: set[int] = {i["index"] for i in pending}
    reviewed_count  = [0]
    message_id      = [None]    # 审批卡片的 message_id（发出后记录）
    user_identified = [False]   # 自动识别模式下是否已确定接收方
    done_event      = threading.Event()

    # ── 辅助：发送审批卡片 ─────────────────────────────
    def send_approval_card(to_id: str, to_type: str) -> None:
        """加载当前审批状态并发送卡片"""
        current = load_approval(daily_dir)
        if not current:
            return
        display = get_pending_items(current, include_all=args.all)
        card_json = build_approval_card(display if not args.all else current["items"], publish_time)
        mid = send_card_message(client, card_json, to_id, to_type)
        message_id[0] = mid

    # ── 事件处理器 1：用户发消息给机器人 ─────────────
    def on_message_receive(data) -> None:
        """自动识别模式：收到任意消息后提取 open_id，发送审批卡片"""
        if not auto_detect or user_identified[0]:
            return
        try:
            event = data.event
            if not event or not event.sender:
                return
            sender_id = event.sender.sender_id
            if not sender_id or not sender_id.open_id:
                return
            oid = sender_id.open_id
            if not oid.startswith("ou_"):
                return

            user_identified[0] = True
            logger.info("=" * 50)
            logger.info("✅ 识别到用户 open_id: %s", oid)
            logger.info("   建议写入 .env: FEISHU_RECEIVE_OPEN_ID=%s", oid)
            logger.info("=" * 50)

            # 回复确认消息
            try:
                mid = event.message.message_id if event.message else None
                if mid:
                    reply_text(client, mid, "✅ 已识别身份，审批卡片即将发送！")
            except Exception:
                pass

            # 发送审批卡片
            send_approval_card(oid, "open_id")

        except Exception as e:
            logger.warning("on_message_receive 异常: %s", e)

    # ── 事件处理器 2：用户点击卡片按钮 ──────────────
    def on_card_action(data: P2CardActionTrigger) -> P2CardActionTriggerResponse | None:
        """按钮点击回调：更新 approval.json 并刷新卡片"""
        try:
            event = data.event
            if not event or not event.action or not event.action.value:
                return None

            action  = event.action.value.get("action", "")   # approve / skip
            idx_str = event.action.value.get("index", "")
            op_id   = event.operator.open_id if event.operator else "unknown"
            ctx_mid = event.context.open_message_id if event.context else message_id[0]

            logger.info("🔔 按钮回调: action=%s index=%s by=%s", action, idx_str, op_id)

            if action not in ("approve", "skip"):
                return _toast(f"未知操作: {action}", "warning")

            idx = int(idx_str)
            if idx not in pending_indices:
                return _toast("该条目无需操作", "info")

            # 更新本地状态
            status = "approved" if action == "approve" else "skipped"
            title  = update_item_status(daily_dir, idx, status, op_id)
            pending_indices.discard(idx)
            reviewed_count[0] += 1

            # 刷新卡片
            if ctx_mid:
                refreshed = load_approval(daily_dir)
                if refreshed:
                    display = refreshed["items"] if args.all else get_pending_items(refreshed, include_all=True)
                    update_card(client, ctx_mid, build_approval_card(display, publish_time))

            # 全部审批完
            if not pending_indices:
                logger.info("🎉 全部 %d 条已审批完成！", reviewed_count[0])
                if ctx_mid:
                    reply_text(client, ctx_mid,
                               f"🎉 全部 {reviewed_count[0]} 条内容审批完成！\n"
                               f"运行发布: python scripts/run_single.py publish")
                done_event.set()

            label = "✅ 确认发布" if action == "approve" else "⏭️ 跳过"
            return _toast(f"{label}：{title}", "success")

        except Exception as e:
            logger.exception("on_card_action 异常: %s", e)
            return _toast(f"处理出错: {e}", "error")

    def _toast(content: str, t: str = "info") -> P2CardActionTriggerResponse:
        resp = P2CardActionTriggerResponse()
        toast = CallBackToast()
        toast.type = t
        toast.content = content
        resp.toast = toast
        return resp

    # ── 注册所有事件到同一 EventDispatcherHandler ──────
    # 关键：全程只创建一个 ws.Client，避免 asyncio 事件循环冲突
    handler_builder = lark.EventDispatcherHandler.builder("", "")
    if auto_detect:
        handler_builder = handler_builder.register_p2_im_message_receive_v1(on_message_receive)
    handler_builder = handler_builder.register_p2_card_action_trigger(on_card_action)
    event_handler = handler_builder.build()

    ws_client = lark.ws.Client(
        FEISHU_APP_ID, FEISHU_APP_SECRET,
        log_level=lark.LogLevel.WARNING,
        event_handler=event_handler,
    )

    # ── 启动 WebSocket（后台线程）──────────────────────
    ws_thread = threading.Thread(target=ws_client.start, daemon=True)
    ws_thread.start()
    time.sleep(2)  # 等待连接建立

    if auto_detect:
        logger.info("=" * 55)
        logger.info("🔍 自动识别模式 — 未配置接收方")
        logger.info("   ➡  在飞书中找到「OpenClaw测试版」机器人")
        logger.info("   ➡  给它发送任意一条消息（如「你好」）")
        logger.info("   ➡  系统将自动识别您并发送审批卡片")
        logger.info("   Ctrl+C 退出 | 超时 %d 分钟", args.timeout)
        logger.info("=" * 55)
    else:
        logger.info("接收方: %s (%s)", receive_id, receive_id_type)
        send_approval_card(receive_id, receive_id_type)
        logger.info("🔗 长链接监听中，等待按钮点击... (Ctrl+C 退出)")

    # ── 等待所有条目审批完成或超时 ────────────────────
    timeout_sec = args.timeout * 60
    try:
        done = done_event.wait(timeout=timeout_sec)
        if done:
            logger.info("✅ 全部审批完成，退出")
        else:
            logger.warning("⏰ 超时（%d 分钟），已处理 %d/%d 条",
                           args.timeout, reviewed_count[0], len(pending))
    except KeyboardInterrupt:
        logger.info("\n🛑 用户中断，已处理 %d/%d 条", reviewed_count[0], len(pending))

    # ── 最终汇总 ──────────────────────────────────────
    final = load_approval(daily_dir)
    if final:
        items = final["items"]
        ap  = sum(1 for i in items if i["status"] == "approved")
        sk  = sum(1 for i in items if i["status"] in ("skipped", "rejected"))
        pnd = sum(1 for i in items if i["status"] == "pending")
        logger.info("─" * 40)
        logger.info("审批结果: ✅已通过 %d  ⏭️已跳过 %d  ⏳待处理 %d", ap, sk, pnd)
        if ap > 0:
            logger.info("  → python scripts/run_single.py publish")


# ════════════════════════════════════════════════════════
#  入口
# ════════════════════════════════════════════════════════

def main() -> None:
    parser = argparse.ArgumentParser(description="飞书互动卡片审批（长链接模式）")
    parser.add_argument("--open-id",  type=str, default="", help="接收用户 open_id（ou_xxxx）")
    parser.add_argument("--chat-id",  type=str, default="", help="接收群组 chat_id（oc_xxxx）")
    parser.add_argument("--all",      action="store_true",   help="显示全部条目（含已审批）")
    parser.add_argument("--timeout",  type=int, default=30,  help="等待超时（分钟，默认 30）")
    args = parser.parse_args()
    run(args)


if __name__ == "__main__":
    main()
