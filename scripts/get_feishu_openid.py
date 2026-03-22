#!/usr/bin/env python3
"""
快速获取飞书用户 open_id 的小工具。

方法一（推荐）：通过长链接，给「OpenClaw」机器人发任意消息自动显示
方法二：直接通过飞书网页端开发者工具查询

用法：
  python scripts/get_feishu_openid.py
"""

import sys, threading, time, logging
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
import os

load_dotenv(PROJECT_ROOT / ".env")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger()

FEISHU_APP_ID     = os.environ.get("FEISHU_APP_ID", "")
FEISHU_APP_SECRET = os.environ.get("FEISHU_APP_SECRET", "")


def main():
    try:
        import lark_oapi as lark
    except ImportError:
        print("❌ 请先安装: pip3 install lark-oapi")
        sys.exit(1)

    got = threading.Event()
    found_id = []

    def on_message(data):
        try:
            msg = data.event
            if not msg or not msg.sender:
                return
            sender_id = msg.sender.sender_id
            if not sender_id:
                return
            open_id = sender_id.open_id
            if open_id and open_id.startswith("ou_"):
                logger.info("=" * 50)
                logger.info("✅ 检测到您的 open_id：")
                logger.info("   %s", open_id)
                logger.info("=" * 50)
                logger.info("📋 复制上面的 open_id，然后:")
                logger.info("   1. 写入 .env: FEISHU_RECEIVE_OPEN_ID=%s", open_id)
                logger.info("   2. 或直接用参数: python scripts/test_feishu_lark_approval.py --open-id %s", open_id)
                found_id.append(open_id)
                got.set()
        except Exception as e:
            logger.warning("处理消息异常: %s", e)

    handler = (
        lark.EventDispatcherHandler.builder("", "")
        .register_p2_im_message_receive_v1(on_message)
        .build()
    )
    ws = lark.ws.Client(
        FEISHU_APP_ID, FEISHU_APP_SECRET,
        log_level=lark.LogLevel.WARNING,
        event_handler=handler,
    )

    print()
    print("=" * 55)
    print("  飞书 open_id 自动获取工具")
    print("=" * 55)
    print()
    print("  步骤：")
    print("  1. 打开飞书，搜索「OpenClaw」机器人")
    print("  2. 给它发送任意一条消息（如「你好」）")
    print("  3. 本工具将自动显示您的 open_id")
    print()
    print("  按 Ctrl+C 退出")
    print("=" * 55)
    print()

    t = threading.Thread(target=ws.start, daemon=True)
    t.start()

    try:
        got.wait(timeout=300)
    except KeyboardInterrupt:
        print("\n⛔ 用户中断")

    if not found_id:
        print("⏰ 未收到消息，请确认已给「OpenClaw」机器人发送消息")


if __name__ == "__main__":
    main()
