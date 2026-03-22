"""
创意模式数据库
使用 SQLite 存储历史创意模式卡片，支持按标签、赛道、评分查询。
随着每日分析的积累，这个库会成为创意决策的知识库。
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

from ..config import get_config

logger = logging.getLogger(__name__)

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS patterns (
    pattern_id   TEXT PRIMARY KEY,
    date         TEXT NOT NULL,
    niche        TEXT NOT NULL DEFAULT '',
    platform     TEXT NOT NULL DEFAULT '',
    title        TEXT NOT NULL DEFAULT '',
    link         TEXT DEFAULT '',
    popularity   INTEGER DEFAULT 0,
    hook_type    TEXT DEFAULT '',
    hook_desc    TEXT DEFAULT '',
    structure    TEXT DEFAULT '[]',
    emotion_curve TEXT DEFAULT '[]',
    visual_style TEXT DEFAULT '{}',
    bgm_mood     TEXT DEFAULT '',
    content_type TEXT DEFAULT '',
    viral_reason TEXT DEFAULT '',
    tags         TEXT DEFAULT '[]',
    engagement_score REAL DEFAULT 0,
    raw_json     TEXT DEFAULT '{}',
    created_at   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_patterns_date ON patterns(date);
CREATE INDEX IF NOT EXISTS idx_patterns_niche ON patterns(niche);
CREATE INDEX IF NOT EXISTS idx_patterns_content_type ON patterns(content_type);
CREATE INDEX IF NOT EXISTS idx_patterns_score ON patterns(engagement_score DESC);
"""

_TAG_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS pattern_tags (
    pattern_id TEXT NOT NULL,
    tag        TEXT NOT NULL,
    PRIMARY KEY (pattern_id, tag),
    FOREIGN KEY (pattern_id) REFERENCES patterns(pattern_id)
);

CREATE INDEX IF NOT EXISTS idx_tags_tag ON pattern_tags(tag);
"""


class PatternDB:
    """创意模式 SQLite 数据库"""

    def __init__(self, db_path: str | Path | None = None):
        if db_path is None:
            cfg = get_config()
            data_dir = Path(cfg.get("output", {}).get("data_dir", "./data"))
            db_path = data_dir / "patterns" / "patterns.db"

        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript(_CREATE_TABLE_SQL)
            conn.executescript(_TAG_TABLE_SQL)
        logger.debug("PatternDB 初始化完成: %s", self.db_path)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def save_pattern(self, pattern: dict, niche: str = "") -> str:
        """
        保存一个创意模式卡片到数据库。
        如果 pattern_id 已存在则更新。

        Returns:
            pattern_id
        """
        source = pattern.get("source", {})
        pid = pattern.get("pattern_id", "")
        hook = pattern.get("hook", {})
        now = datetime.now().isoformat()

        with self._connect() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO patterns
                   (pattern_id, date, niche, platform, title, link, popularity,
                    hook_type, hook_desc, structure, emotion_curve, visual_style,
                    bgm_mood, content_type, viral_reason, tags,
                    engagement_score, raw_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    pid,
                    datetime.now().strftime("%Y-%m-%d"),
                    niche,
                    source.get("platform", ""),
                    source.get("title", ""),
                    source.get("link", ""),
                    source.get("popularity", 0),
                    hook.get("type", ""),
                    hook.get("desc", ""),
                    json.dumps(pattern.get("structure", []), ensure_ascii=False),
                    json.dumps(pattern.get("emotion_curve", []), ensure_ascii=False),
                    json.dumps(pattern.get("visual_style", {}), ensure_ascii=False),
                    pattern.get("bgm_mood", ""),
                    pattern.get("content_type", ""),
                    pattern.get("viral_reason", ""),
                    json.dumps(pattern.get("tags", []), ensure_ascii=False),
                    pattern.get("engagement_score", 0),
                    json.dumps(pattern, ensure_ascii=False),
                    now,
                ),
            )

            # 更新标签索引
            conn.execute("DELETE FROM pattern_tags WHERE pattern_id = ?", (pid,))
            for tag in pattern.get("tags", []):
                conn.execute(
                    "INSERT OR IGNORE INTO pattern_tags (pattern_id, tag) VALUES (?, ?)",
                    (pid, tag),
                )

        logger.debug("已保存模式: %s", pid)
        return pid

    def save_patterns(self, patterns: list[dict], niche: str = "") -> int:
        """批量保存模式卡片，返回保存数量"""
        count = 0
        for p in patterns:
            try:
                self.save_pattern(p, niche=niche)
                count += 1
            except Exception as e:
                logger.warning("保存模式失败 [%s]: %s", p.get("pattern_id"), e)
        logger.info("💾 已保存 %d/%d 个创意模式到数据库", count, len(patterns))
        return count

    def query_by_tags(self, tags: list[str], limit: int = 20) -> list[dict]:
        """按标签查询模式（OR 逻辑，匹配任一标签即返回）"""
        if not tags:
            return []
        placeholders = ",".join("?" * len(tags))
        sql = f"""
            SELECT DISTINCT p.raw_json FROM patterns p
            JOIN pattern_tags t ON p.pattern_id = t.pattern_id
            WHERE t.tag IN ({placeholders})
            ORDER BY p.engagement_score DESC
            LIMIT ?
        """
        with self._connect() as conn:
            rows = conn.execute(sql, [*tags, limit]).fetchall()
        return [json.loads(r["raw_json"]) for r in rows]

    def query_by_niche(self, niche: str, limit: int = 20) -> list[dict]:
        """按赛道查询最近的模式"""
        sql = """
            SELECT raw_json FROM patterns
            WHERE niche = ?
            ORDER BY date DESC, engagement_score DESC
            LIMIT ?
        """
        with self._connect() as conn:
            rows = conn.execute(sql, (niche, limit)).fetchall()
        return [json.loads(r["raw_json"]) for r in rows]

    def query_by_content_type(self, content_type: str, limit: int = 20) -> list[dict]:
        """按内容类型查询"""
        sql = """
            SELECT raw_json FROM patterns
            WHERE content_type = ?
            ORDER BY engagement_score DESC
            LIMIT ?
        """
        with self._connect() as conn:
            rows = conn.execute(sql, (content_type, limit)).fetchall()
        return [json.loads(r["raw_json"]) for r in rows]

    def query_top_patterns(self, limit: int = 20, days: int = 7) -> list[dict]:
        """查询最近 N 天内评分最高的模式"""
        sql = """
            SELECT raw_json FROM patterns
            WHERE date >= date('now', ?)
            ORDER BY engagement_score DESC
            LIMIT ?
        """
        with self._connect() as conn:
            rows = conn.execute(sql, (f"-{days} days", limit)).fetchall()
        return [json.loads(r["raw_json"]) for r in rows]

    def get_all_tags(self) -> list[tuple[str, int]]:
        """获取所有标签及其出现次数"""
        sql = "SELECT tag, COUNT(*) as cnt FROM pattern_tags GROUP BY tag ORDER BY cnt DESC"
        with self._connect() as conn:
            rows = conn.execute(sql).fetchall()
        return [(r["tag"], r["cnt"]) for r in rows]

    def count(self) -> int:
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) as c FROM patterns").fetchone()
        return row["c"]
