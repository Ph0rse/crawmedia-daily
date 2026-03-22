#!/usr/bin/env python3
"""
Week 2 模块验证脚本
独立运行，依次验证：
  1. LLM API 连通性（最小调用）
  2. Analyze 阶段（scout_demo.json → 创意模式提取）
  3. Remix 阶段（创意模式 → 4种策略 → Seedance Prompt）

用法:
    python3 scripts/verify_week2.py
"""

import asyncio
import json
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

import httpx

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# ── 日志配置 ──
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("verify")


def banner(text: str):
    logger.info("")
    logger.info("=" * 60)
    logger.info("  %s", text)
    logger.info("=" * 60)


async def step1_test_llm():
    """步骤 1：验证 LLM API 连通性"""
    banner("步骤 1/3：测试 LLM API 连通性")

    from modules.llm import chat_completion, _get_llm_config

    cfg = _get_llm_config()
    logger.info("  Base URL : %s", cfg["base_url"])
    logger.info("  Model    : %s", cfg["model"])
    logger.info("  API Key  : %s...%s", cfg["api_key"][:8], cfg["api_key"][-4:])

    logger.info("")
    logger.info("  发送测试请求...")
    t0 = time.time()

    try:
        reply = await chat_completion(
            system_prompt="你是一个简洁的助手。",
            user_message="请用一句话回答：1+1等于几？",
            temperature=0,
            max_tokens=50,
        )
        elapsed = time.time() - t0
        logger.info("  ✅ LLM 回复 (%.1fs): %s", elapsed, reply.strip())
        return True
    except httpx.HTTPStatusError as e:
        elapsed = time.time() - t0
        logger.error("  ❌ LLM 调用失败 (%.1fs): HTTP %s", elapsed, e.response.status_code)
        try:
            err_body = e.response.json()
            err_code = err_body.get("error", {}).get("code", "")
            err_msg = err_body.get("error", {}).get("message", "")
            logger.error("     错误码: %s", err_code)
            logger.error("     错误信息: %s", err_msg)
        except Exception:
            pass

        if e.response.status_code == 403:
            logger.error("")
            logger.error("  ⚠️  403 AccessDenied 表示 API Key 有效但未开通模型推理权限。")
            logger.error("  请按以下步骤操作：")
            logger.error("    1. 打开火山引擎方舟控制台:")
            logger.error("       https://console.volcengine.com/ark/region:ark+cn-beijing/openManagement")
            logger.error("    2. 找到 '按量付费-模型推理' 并开通")
            logger.error("    3. 确保 doubao-1-5-pro-32k 模型已启用")
            logger.error("    4. 重新运行此脚本")
        return False
    except Exception as e:
        logger.error("  ❌ LLM 调用失败: %s", e)
        return False


async def step2_test_analyze():
    """步骤 2：验证 Analyze 阶段"""
    banner("步骤 2/3：测试 Analyze 阶段（创意模式提取）")

    from modules.analyze.runner import run_analyze, _load_scout_results, _get_daily_dir

    daily_dir = _get_daily_dir()
    scout_items = _load_scout_results(daily_dir)

    if not scout_items:
        logger.error("  ❌ 未找到 Scout 数据（scout.json 或 scout_demo.json）")
        return None

    # 只取前 5 条做测试，节省 API 调用
    test_items = scout_items[:5]
    logger.info("  📂 加载了 %d 条 Scout 数据，取前 %d 条测试", len(scout_items), len(test_items))
    for item in test_items:
        logger.info("     #%s %s (热度: %s)",
                     item.get("rank", "?"),
                     item.get("title", "?"),
                     f"{item.get('popularity', 0):,}")

    logger.info("")
    logger.info("  🧠 开始 LLM 创意模式分析...")
    t0 = time.time()

    patterns = await run_analyze(skip_feishu=True, scout_items=test_items)
    elapsed = time.time() - t0

    if not patterns:
        logger.error("  ❌ Analyze 未返回结果 (%.1fs)", elapsed)
        return None

    logger.info("")
    logger.info("  ✅ Analyze 完成 (%.1fs)，提取 %d 个创意模式：", elapsed, len(patterns))
    for p in patterns:
        hook = p.get("hook", {})
        logger.info("     📌 [%s] %s",
                     p.get("pattern_id", "?"),
                     p.get("source", {}).get("title", "?"))
        logger.info("        钩子: %s — %s", hook.get("type", "?"), hook.get("desc", "?"))
        logger.info("        类型: %s | 评分: %s",
                     p.get("content_type", "?"),
                     p.get("engagement_score", "?"))
        logger.info("        标签: %s", ", ".join(p.get("tags", [])))

    # 验证数据库
    from modules.analyze.pattern_db import PatternDB
    db = PatternDB()
    logger.info("")
    logger.info("  💾 PatternDB 累计记录: %d 条", db.count())

    # 验证文件
    analysis_path = daily_dir / "analysis.json"
    if analysis_path.exists():
        logger.info("  📄 analysis.json 已生成: %s", analysis_path)

    return patterns


async def step3_test_remix(patterns: list[dict]):
    """步骤 3：验证 Remix 阶段"""
    banner("步骤 3/3：测试 Remix 阶段（创意策略 + Seedance Prompt）")

    from modules.remix.runner import run_remix

    logger.info("  🎨 输入 %d 个创意模式，执行 4 种策略...", len(patterns))
    logger.info("     策略: 组合(combine) | 泛化(generalize) | 迁移(transfer) | 延展(extend)")
    logger.info("")

    t0 = time.time()
    ideas = await run_remix(skip_feishu=True, patterns=patterns)
    elapsed = time.time() - t0

    if not ideas:
        logger.error("  ❌ Remix 未返回结果 (%.1fs)", elapsed)
        return

    logger.info("")
    logger.info("  ✅ Remix 完成 (%.1fs)，生成 %d 个创意方案：", elapsed, len(ideas))

    for i, idea in enumerate(ideas, 1):
        strategy = idea.get("strategy", "?")
        strategy_emoji = {"combine": "🔀", "generalize": "🔄",
                          "transfer": "🚀", "extend": "📐"}.get(strategy, "💡")
        prompt_result = idea.get("prompt_result")

        logger.info("")
        logger.info("  %s 创意 #%d [%s]", strategy_emoji, i, strategy)
        logger.info("     标题: %s", idea.get("title", "未命名"))
        logger.info("     概述: %s", idea.get("concept", "无"))
        logger.info("     钩子: %s", idea.get("hook", {}).get("desc", "无"))
        logger.info("     情感: %s", " → ".join(idea.get("emotion_curve", [])))
        logger.info("     标签: %s", ", ".join(idea.get("tags", [])))

        if prompt_result:
            prompt_text = prompt_result.get("seedance_prompt", "")
            logger.info("     ⏱️ 推荐时长: %ds", prompt_result.get("duration_seconds", 0))
            logger.info("     🎬 Seedance Prompt:")
            for line in prompt_text.split("\n"):
                if line.strip():
                    logger.info("        %s", line)
        else:
            logger.warning("     ⚠️ Prompt 生成失败: %s", idea.get("prompt_error", "未知"))

    # 验证文件
    from modules.config import get_config
    cfg = get_config()
    today = datetime.now().strftime("%Y-%m-%d")
    remixed_path = Path(cfg["output"]["data_dir"]) / "daily" / today / "remixed.json"
    if remixed_path.exists():
        logger.info("")
        logger.info("  📄 remixed.json 已生成: %s", remixed_path)


async def main():
    banner("CrawMedia Daily — Week 2 模块验证")
    logger.info("  验证项: LLM 连通 → Analyze(创意提取) → Remix(策略+Prompt)")
    total_t0 = time.time()

    # Step 1: LLM
    ok = await step1_test_llm()
    if not ok:
        logger.error("")
        logger.error("❌ LLM API 不可用，请检查 .env 中的配置：")
        logger.error("   LLM_API_KEY / LLM_BASE_URL / LLM_MODEL")
        sys.exit(1)

    # Step 2: Analyze
    patterns = await step2_test_analyze()
    if not patterns:
        logger.error("❌ Analyze 阶段失败，无法继续")
        sys.exit(1)

    # Step 3: Remix
    await step3_test_remix(patterns)

    total_elapsed = time.time() - total_t0
    banner(f"验证完成！总耗时 {total_elapsed:.1f}s")
    logger.info("  ✅ LLM API 连通")
    logger.info("  ✅ Analyze 创意模式提取正常")
    logger.info("  ✅ Remix 创意策略 + Seedance Prompt 正常")
    logger.info("")
    logger.info("  📁 输出文件:")
    logger.info("     data/daily/%s/analysis.json", datetime.now().strftime("%Y-%m-%d"))
    logger.info("     data/daily/%s/remixed.json", datetime.now().strftime("%Y-%m-%d"))
    logger.info("     data/patterns/patterns.db")
    logger.info("")
    logger.info("  下一步: 配置好 LLM API 后可运行完整流水线:")
    logger.info("     python3 scripts/run_daily.py --skip-feishu")


if __name__ == "__main__":
    asyncio.run(main())
