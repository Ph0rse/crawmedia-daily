"""
统一 LLM 客户端
通过 OpenAI 兼容接口调用大语言模型，支持 GPT / Claude / DeepSeek / 豆包等。
配置从 .env 读取：LLM_API_KEY, LLM_BASE_URL, LLM_MODEL
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

import httpx

from .config import get_config

logger = logging.getLogger(__name__)


def _get_llm_config() -> dict[str, str]:
    """从环境变量读取 LLM 配置"""
    return {
        "api_key": os.environ.get("LLM_API_KEY", ""),
        "base_url": os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/"),
        "model": os.environ.get("LLM_MODEL", "gpt-4o"),
    }


async def chat_completion(
    system_prompt: str,
    user_message: str,
    *,
    temperature: float = 0.7,
    max_tokens: int = 4096,
    response_format: dict | None = None,
) -> str:
    """
    调用 OpenAI 兼容的 Chat Completions API，返回纯文本回复。

    Args:
        system_prompt: 系统提示词
        user_message: 用户消息
        temperature: 创造性参数（0=确定性，1=更随机）
        max_tokens: 最大生成长度
        response_format: 可选，如 {"type": "json_object"} 要求 JSON 输出
    """
    cfg = _get_llm_config()
    if not cfg["api_key"]:
        raise ValueError("LLM_API_KEY 未设置，请在 .env 中配置")

    url = f"{cfg['base_url']}/chat/completions"
    headers = {
        "Authorization": f"Bearer {cfg['api_key']}",
        "Content-Type": "application/json",
    }

    body: dict[str, Any] = {
        "model": cfg["model"],
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if response_format:
        body["response_format"] = response_format

    logger.debug("LLM 请求: model=%s, tokens=%d", cfg["model"], max_tokens)

    async with httpx.AsyncClient(timeout=180) as client:
        resp = await client.post(url, json=body, headers=headers)
        resp.raise_for_status()
        data = resp.json()

    content = data["choices"][0]["message"]["content"]
    logger.debug("LLM 回复: %d 字符", len(content))
    return content


async def chat_completion_json(
    system_prompt: str,
    user_message: str,
    **kwargs,
) -> dict | list:
    """
    调用 LLM 并解析 JSON 回复。
    自动在 system_prompt 末尾追加 JSON 输出要求。
    """
    system_prompt_with_json = (
        system_prompt + "\n\n请严格以 JSON 格式回复，不要包含 markdown 代码块标记。"
    )
    raw = await chat_completion(
        system_prompt_with_json,
        user_message,
        response_format={"type": "json_object"},
        **kwargs,
    )

    # 兼容：有些模型仍会包裹 ```json ... ```
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

    return json.loads(text)
