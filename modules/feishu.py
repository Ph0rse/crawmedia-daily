"""
飞书自定义机器人推送模块（Python 版）
复用 douyin-hot-trend/senders/feishu.js 的逻辑，用 Python 重写。

支持：
- 富文本（post）消息格式
- HmacSHA256 签名校验
- 通用消息推送（不限于热榜数据）
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import time
from datetime import datetime
from typing import Optional, Tuple, List, Dict

import httpx

from .config import get_config


def _generate_sign(timestamp: str, secret: str) -> str:
    """飞书 Webhook 签名算法：HmacSHA256(timestamp + '\\n' + secret) 对空字符串摘要"""
    string_to_sign = f"{timestamp}\n{secret}"
    h = hmac.new(string_to_sign.encode("utf-8"), b"", hashlib.sha256)
    return base64.b64encode(h.digest()).decode("utf-8")


def _get_feishu_config() -> tuple[str, str]:
    """从全局配置获取飞书 Webhook URL 和 Secret"""
    cfg = get_config()
    url = cfg.get("feishu", {}).get("webhook_url", "") or ""
    secret = cfg.get("feishu", {}).get("secret", "") or ""
    return url, secret


async def send_feishu_rich_text(
    title: str,
    paragraphs: list[list[dict]],
    webhook_url: str | None = None,
    secret: str | None = None,
):
    """
    发送飞书富文本消息。

    Args:
        title: 消息标题
        paragraphs: 富文本段落列表，每个段落是一个 element 列表
            例如: [[{"tag": "text", "text": "hello"}, {"tag": "a", "text": "link", "href": "..."}]]
        webhook_url: 飞书 Webhook 地址，不传则从配置读取
        secret: 签名密钥，不传则从配置读取
    """
    if webhook_url is None or secret is None:
        cfg_url, cfg_secret = _get_feishu_config()
        webhook_url = webhook_url or cfg_url
        secret = secret if secret is not None else cfg_secret

    if not webhook_url:
        raise ValueError("飞书 Webhook URL 未配置，请在 .env 中设置 FEISHU_WEBHOOK_URL")

    body = {
        "msg_type": "post",
        "content": {
            "post": {
                "zh_cn": {
                    "title": title,
                    "content": paragraphs,
                }
            }
        },
    }

    if secret:
        ts = str(int(time.time()))
        body["timestamp"] = ts
        body["sign"] = _generate_sign(ts, secret)

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(webhook_url, json=body)
        result = resp.json()

    if result.get("code") != 0:
        raise RuntimeError(f"飞书推送失败：{result.get('msg')}（code: {result.get('code')}）")

    return result


async def send_feishu_text(text: str, **kwargs):
    """快捷方法：发送纯文本消息（内部转为单段落富文本）"""
    paragraphs = [[{"tag": "text", "text": text}]]
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    await send_feishu_rich_text(title=f"📢 通知 {now}", paragraphs=paragraphs, **kwargs)


def format_scout_results_for_feishu(
    items: list[dict],
    niche_name: str,
    keywords: list[str],
) -> tuple[str, list[list[dict]]]:
    """
    将采集结果格式化为飞书富文本消息。

    Returns:
        (title, paragraphs) 可直接传入 send_feishu_rich_text
    """
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    title = f"🔍 【{niche_name}】赛道爆款 TOP {len(items)}（{now}）"

    paragraphs = []

    # 关键词提示
    paragraphs.append([
        {"tag": "text", "text": f"📌 关键词：{', '.join(keywords)}\n"},
    ])

    for item in items:
        rank = item.get("rank", "?")
        rank_emoji = ["🥇", "🥈", "🥉"][rank - 1] if isinstance(rank, int) and rank <= 3 else "🎯"
        platform_emoji = "📱"
        score = item.get("score", 0)

        row = [
            {"tag": "text", "text": f"{rank_emoji} {rank}. "},
            {"tag": "a", "text": item.get("title", "无标题"), "href": item.get("link", "")},
            {"tag": "text", "text": f"\n   🔥 热度: {item.get('popularity', 0):,}  📊 分数: {score:.1f}\n"},
        ]
        paragraphs.append(row)

    # 底部
    paragraphs.append([
        {"tag": "text", "text": f"\n📊 共采集 {len(items)} 条爆款内容  ⏰ {now}"},
    ])

    return title, paragraphs
