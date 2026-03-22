#!/usr/bin/env python3
"""
飞书应用配置检查工具

检查：
  1. 凭证是否有效（tenant_access_token）
  2. 机器人基础信息
  3. 长链接是否能连上
  4. 等待实际消息事件（验证事件订阅是否生效）

用法：
  python scripts/check_feishu_config.py
"""

import sys, threading, time, json
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
import os

load_dotenv(PROJECT_ROOT / ".env")

APP_ID     = os.environ.get("FEISHU_APP_ID", "")
APP_SECRET = os.environ.get("FEISHU_APP_SECRET", "")

REQUIRED_EVENTS = ["im.message.receive_v1", "card.action.trigger"]

SEP = "─" * 55


def check(label: str, ok: bool, detail: str = "") -> bool:
    icon = "✅" if ok else "❌"
    print(f"  {icon}  {label}")
    if detail:
        for line in detail.splitlines():
            print(f"       {line}")
    return ok


def step1_token():
    print(f"\n{SEP}")
    print("  Step 1 · 获取 tenant_access_token")
    print(SEP)
    try:
        payload = json.dumps({"app_id": APP_ID, "app_secret": APP_SECRET}).encode()
        req = urllib.request.Request(
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            data=payload, headers={"Content-Type": "application/json"}, method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())
        if data.get("code") == 0:
            token = data["tenant_access_token"]
            check("凭证有效，token 获取成功", True, f"App ID: {APP_ID}")
            return token
        else:
            check("凭证无效", False, f"错误: {data}")
            return None
    except Exception as e:
        check("网络请求失败", False, str(e))
        return None


def step2_bot_info(token: str):
    print(f"\n{SEP}")
    print("  Step 2 · 机器人基础信息")
    print(SEP)
    try:
        req = urllib.request.Request(
            "https://open.feishu.cn/open-apis/bot/v3/info",
            headers={"Authorization": f"Bearer {token}"}, method="GET",
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())
        bot = data.get("bot", {})
        name   = bot.get("app_name", "未知")
        status = bot.get("activate_status", -1)
        open_id = bot.get("open_id", "未知")
        status_label = {0: "未启用", 1: "未激活", 2: "已激活", 3: "已停用"}.get(status, f"未知({status})")
        ok = status == 2
        check(f"机器人: {name}  状态: {status_label}", ok,
              f"Bot open_id: {open_id}\n" + ("" if ok else "→ 请在开发者后台发布/激活应用"))
    except Exception as e:
        check("获取机器人信息失败", False, str(e))


def step3_and_4_websocket_and_event():
    """
    Step 3 + 4 合并为单个 ws.Client（避免 asyncio event loop 冲突）：
    先验证连通性，再等待消息事件确认事件订阅配置正确。
    """
    print(f"\n{SEP}")
    print("  Step 3 · 长链接连通性 + Step 4 · 等待消息事件")
    print(SEP)

    try:
        import lark_oapi as lark
    except ImportError:
        check("lark-oapi 未安装", False, "pip3 install lark-oapi")
        return False

    connected  = threading.Event()
    got_event  = threading.Event()
    result     = {}

    # 捕获 Lark 日志中的 "connected" 信息
    import logging
    lark_logger = logging.getLogger("Lark")

    class ConnectCapture(logging.Handler):
        def emit(self, record):
            if "connected to" in record.getMessage():
                connected.set()

    lark_logger.addHandler(ConnectCapture())

    def on_message(data):
        try:
            event = data.event
            sender_id = event.sender.sender_id if (event and event.sender) else None
            open_id = sender_id.open_id if sender_id else "unknown"
            result["open_id"] = open_id
            got_event.set()
        except Exception as e:
            result["error"] = str(e)
            got_event.set()

    # 单个 ws.Client 处理连通性验证 + 消息事件
    handler = (
        lark.EventDispatcherHandler.builder("", "")
        .register_p2_im_message_receive_v1(on_message)
        .build()
    )
    ws = lark.ws.Client(APP_ID, APP_SECRET, log_level=lark.LogLevel.INFO, event_handler=handler)
    t = threading.Thread(target=ws.start, daemon=True)
    t.start()

    # 等待连接
    ok = connected.wait(timeout=8)
    check("WebSocket 长链接连通", ok,
          "" if ok else "→ 请检查网络或防火墙")
    if not ok:
        return False

    # 等待消息事件
    print(f"\n  ⏳ 请在飞书中给「OpenClaw测试版」机器人发送任意消息...")
    print("  （等待 60 秒，收到消息后自动显示结果）\n")

    received = got_event.wait(timeout=60)

    if received and result.get("open_id"):
        open_id = result["open_id"]
        check("收到消息事件 ✓  事件订阅配置正确", True, f"发送人 open_id: {open_id}")
        print()
        print("=" * 55)
        print(f"  🎉  您的 open_id 是：")
        print(f"      {open_id}")
        print()
        print(f"  写入 .env：")
        print(f"      FEISHU_RECEIVE_OPEN_ID={open_id}")
        print()
        print(f"  或直接运行审批脚本：")
        print(f"      python scripts/test_feishu_lark_approval.py --open-id {open_id}")
        print("=" * 55)
    elif received and result.get("error"):
        check("收到事件但解析失败", False, result["error"])
    else:
        check("60 秒内未收到消息事件", False,
              "可能原因：\n"
              "① 未在飞书开发者后台添加「im.message.receive_v1」事件订阅\n"
              "② 事件订阅方式未选「长连接」\n"
              "③ 应用权限「im:message」未开启\n"
              "④ 改完配置后未重新发布版本\n\n"
              f"配置地址: https://open.feishu.cn/app/{APP_ID}")
    return True


def main():
    print()
    print("=" * 55)
    print("  飞书应用配置检查工具")
    print("=" * 55)

    token = step1_token()
    if not token:
        print("\n❌ 凭证无效，请检查 App ID 和 App Secret")
        return

    step2_bot_info(token)
    step3_and_4_websocket_and_event()

    print(f"\n{SEP}")
    print("  检查完成")
    print(SEP)


if __name__ == "__main__":
    main()
