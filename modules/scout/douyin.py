"""
抖音赛道爆款采集器
调用 douyin-hot-trend 的 douyin.js --json 模式，直接获取结构化数据。
支持热榜 (hotList) + 上升热点 (trendingList) 双通道采集。
"""
from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

# 热榜抓取脚本路径（已内置到 vendor/，不再依赖外部同级目录）
DOUYIN_HOT_TREND_DIR = Path(__file__).resolve().parent.parent.parent / "vendor" / "douyin-hot-trend"


def _call_douyin_script(limit: int = 50, keywords: list[str] | None = None) -> dict | None:
    """
    调用 douyin-hot-trend/scripts/douyin.js --json 获取结构化热榜数据。
    返回解析后的 dict，失败返回 None。
    """
    script_path = DOUYIN_HOT_TREND_DIR / "scripts" / "douyin.js"
    if not script_path.exists():
        raise FileNotFoundError(f"抖音热榜脚本不存在: {script_path}")

    cmd = ["node", str(script_path), "hot", str(limit), "--json"]
    if keywords:
        cmd.extend(["--filter", ",".join(keywords)])

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
            cwd=str(DOUYIN_HOT_TREND_DIR),
        )
        if result.returncode != 0:
            logger.error("抖音热榜脚本执行失败: %s", result.stderr)
            return None

        return json.loads(result.stdout)

    except json.JSONDecodeError as e:
        logger.error("抖音热榜 JSON 解析失败: %s", e)
        return None
    except subprocess.TimeoutExpired:
        logger.error("抖音热榜脚本执行超时")
        return None
    except FileNotFoundError:
        logger.error("未找到 node 命令，请确保 Node.js 已安装")
        return None


def _normalize_item(raw: dict) -> dict:
    """将 JS 端的 camelCase 字段转为 Python 端的 snake_case 标准结构"""
    return {
        "rank": raw.get("rank", 0),
        "original_rank": raw.get("originalRank"),
        "title": raw.get("title", ""),
        "popularity": raw.get("popularity", 0),
        "link": raw.get("link", ""),
        "sentence_id": raw.get("sentenceId", ""),
        "group_id": raw.get("groupId", ""),
        "label": raw.get("label"),
        "cover": raw.get("cover"),
        "cover_urls": raw.get("coverUrls", []),
        "video_count": raw.get("videoCount", 0),
        "event_time": raw.get("eventTime"),
        "sentence_tag": raw.get("sentenceTag"),
        "platform": "douyin",
        "likes": raw.get("popularity", 0),
        "collects": 0,
        "comments": 0,
    }


def _download_cover(url: str, save_dir: Path, filename: str) -> str | None:
    """
    下载封面图到本地。CDN 链接有签名时效，采集时立即缓存。
    返回本地相对路径，失败返回 None。
    """
    if not url:
        return None

    save_dir.mkdir(parents=True, exist_ok=True)
    ext = ".jpg"
    local_path = save_dir / f"{filename}{ext}"

    if local_path.exists():
        return str(local_path.relative_to(save_dir.parent.parent))

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            local_path.write_bytes(resp.read())
        return str(local_path.relative_to(save_dir.parent.parent))
    except Exception as e:
        logger.warning("封面图下载失败 [%s]: %s", filename, e)
        return None


async def fetch_douyin_trends(
    keywords: list[str],
    fetch_limit: int = 50,
    top_n: int = 20,
    save_covers: bool = True,
    daily_dir: Path | None = None,
) -> dict:
    """
    获取抖音热榜 + 上升热点。

    Args:
        keywords: 赛道关键词列表（空列表 = 不过滤）
        fetch_limit: 原始抓取条数
        top_n: 过滤后取前 N 条
        save_covers: 是否下载封面图到本地
        daily_dir: 当日数据目录（用于存储封面图）

    Returns:
        {
            "hot_list": [...],       # 热搜词列表
            "trending_list": [...],  # 上升热点列表
            "active_time": "...",    # 热榜最后更新时间
        }
    """
    logger.info("🔥 开始抓取抖音热榜（关键词: %s）...", ", ".join(keywords) or "全部")

    raw = _call_douyin_script(limit=fetch_limit, keywords=keywords)
    if not raw:
        logger.warning("抖音热榜抓取失败，返回空结果")
        return {"hot_list": [], "trending_list": [], "active_time": None}

    if raw.get("error"):
        logger.error("抖音热榜返回错误: %s", raw["error"])
        return {"hot_list": [], "trending_list": [], "active_time": None}

    # 标准化字段
    hot_list = [_normalize_item(item) for item in raw.get("hotList", [])][:top_n]
    trending_list = [_normalize_item(item) for item in raw.get("trendingList", [])][:top_n]

    logger.info("✅ 抖音热榜解析完成：热搜 %d 条，上升热点 %d 条", len(hot_list), len(trending_list))

    # 并发下载封面图（CDN 签名 URL 几小时后过期，采集时立即缓存）
    if save_covers and daily_dir:
        covers_dir = daily_dir / "covers"
        all_items = hot_list + trending_list
        download_tasks = []
        for item in all_items:
            if item.get("cover"):
                safe_id = item.get("sentence_id") or str(item.get("rank", 0))
                download_tasks.append((item, item["cover"], covers_dir, f"hot_{safe_id}"))

        if download_tasks:
            loop = asyncio.get_event_loop()
            with ThreadPoolExecutor(max_workers=8) as pool:
                futures = [
                    loop.run_in_executor(pool, _download_cover, url, cdir, fname)
                    for _, url, cdir, fname in download_tasks
                ]
                results = await asyncio.gather(*futures, return_exceptions=True)

            saved = 0
            for (item, _, _, _), result in zip(download_tasks, results):
                if isinstance(result, str):
                    item["cover_local"] = result
                    saved += 1
            logger.info("🖼️  封面图下载完成: %d/%d 张", saved, len(download_tasks))

    return {
        "hot_list": hot_list,
        "trending_list": trending_list,
        "active_time": raw.get("activeTime"),
    }
