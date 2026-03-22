# Analyze 阶段 — 详细参考

## 调用链

```
scripts/run_single.py analyze → modules/analyze/runner.py :: run_analyze()
  ├─→ 加载 data/daily/YYYY-MM-DD/scout.json（或 scout_demo.json），提取 hot_items
  ├─→ modules/analyze/creative_extractor.py :: extract_patterns_batch()
  │     拼接 N 条爆款信息为文本 → chat_completion_json()
  │     System Prompt: 批量创意模式提取（8 维度分析）
  │     temperature: 0.5（偏确定性分析）
  │     返回 {"patterns": [...]}
  ├─→ modules/analyze/pattern_db.py :: PatternDB.save_patterns()
  │     SQLite 写入 data/patterns/patterns.db（持续积累，不覆盖历史）
  ├─→ 保存 data/daily/YYYY-MM-DD/analysis.json
  └─→ modules/feishu.py（可选）
```

## 命令

```bash
python3 scripts/run_single.py analyze --no-feishu
```

## LLM System Prompt 核心

`creative_extractor.py` 中的 `BATCH_EXTRACT_SYSTEM_PROMPT` 指示 LLM 对每条内容提取 8 个维度：

1. **hook** — 钩子类型（反差/悬念/共鸣/争议/视觉冲击/热点借势）
2. **structure** — 时间段节奏拆解
3. **emotion_curve** — 观众情绪路径
4. **visual_style** — 拍摄/滤镜/字幕特点
5. **bgm_mood** — 配乐氛围
6. **tags** — 3-5 个可复用标签
7. **content_type** — 内容分类
8. **viral_reason** — 一句话爆款原因

LLM 输入示例：
```
请分析以下 5 条热门内容，为每条提取创意模式卡片。
日期：20260322

#1 [douyin] 标题：湖人胜魔术迎9连胜 | 热度：12,111,324 | 链接：... | 视频数：2 | 参考评分：100
#2 [douyin] 标题：2026无锡马拉松 | 热度：11,846,721 | ...
```

## 产出文件：analysis.json

```json
{
  "stage": "analyze",
  "timestamp": "2026-03-22T12:59:40",
  "pattern_count": 5,
  "patterns": [
    {
      "pattern_id": "体育赛事-20260322-1",
      "source": { "platform": "douyin", "title": "...", "link": "...", "popularity": 12111324 },
      "hook": { "type": "热点借势", "desc": "..." },
      "structure": ["开场介绍结果", "回顾精彩瞬间", "分析意义"],
      "emotion_curve": ["兴奋", "回味", "期待"],
      "visual_style": { "拍摄角度": "...", "滤镜": "...", "字幕风格": "..." },
      "bgm_mood": "激昂振奋",
      "content_type": "体育赛事资讯",
      "viral_reason": "...",
      "tags": ["湖人", "魔术", "9连胜", "体育赛事", "篮球"],
      "engagement_score": 100
    }
  ]
}
```

## SQLite 知识库：patterns.db

路径：`data/patterns/patterns.db`

```sql
CREATE TABLE patterns (
    pattern_id TEXT PRIMARY KEY,
    date TEXT, niche TEXT, platform TEXT, title TEXT, link TEXT,
    popularity INTEGER, hook_type TEXT, hook_desc TEXT,
    structure TEXT,        -- JSON array
    emotion_curve TEXT,    -- JSON array
    visual_style TEXT,     -- JSON object
    bgm_mood TEXT, content_type TEXT, viral_reason TEXT,
    tags TEXT,             -- JSON array
    engagement_score REAL,
    raw_json TEXT,
    created_at TEXT
);

CREATE TABLE pattern_tags (
    pattern_id TEXT, tag TEXT,
    PRIMARY KEY (pattern_id, tag)
);
```

查询 API（`PatternDB` 类）：

```python
from modules.analyze.pattern_db import PatternDB
db = PatternDB()
db.save_patterns(patterns, niche="萌宠")
db.query_by_tags(["猫咪", "搞笑"], limit=20)
db.query_by_niche("萌宠", limit=20)
db.query_by_content_type("体育赛事资讯")
db.query_top_patterns(limit=20, days=7)
db.get_all_tags()
db.count()
```

## 已知限制

- 火山引擎豆包 API 批量分析 10 条易超时断开，`config.yaml` 的 `analyze.max_items` 建议 ≤5
- LLM 超时设置 180 秒（`modules/llm.py`）
- 每日分析结果累积到 patterns.db，不要删除此文件
