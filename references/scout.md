# Scout 阶段 — 详细参考

## 调用链

```
scripts/run_single.py scout → modules/scout/runner.py :: run_scout()
  ├─→ modules/scout/douyin.py :: fetch_douyin_trends()
  │     subprocess: node vendor/douyin-hot-trend/scripts/douyin.js hot <limit> --json [--filter 关键词]
  │     HTTP GET → https://www.douyin.com/aweme/v1/hot/search/list/ (无需认证)
  │     返回 JSON → stdout → douyin.py 解析为 snake_case → 并发下载封面图
  ├─→ modules/scout/trend_ranker.py :: rank_items()  — 评分排序取 Top N
  ├─→ 保存 data/daily/YYYY-MM-DD/scout.json
  └─→ modules/feishu.py :: send_feishu_rich_text()  （可选）
```

## 命令

```bash
# 演示模式：Top 10，无赛道过滤
python3 scripts/demo_scout.py

# 正式采集：按 config.yaml 关键词过滤
python3 scripts/run_single.py scout --no-feishu

# 直接调用 douyin.js 调试
node vendor/douyin-hot-trend/scripts/douyin.js hot 10 --json
node vendor/douyin-hot-trend/scripts/douyin.js hot 50 --filter 猫咪,狗狗 --json
```

## 产出文件：scout.json

```json
{
  "stage": "scout",
  "timestamp": "2026-03-22T12:26:15",
  "active_time": "2026-03-22 12:24:27",
  "hot_count": 10,
  "trending_count": 5,
  "hot_items": [
    {
      "rank": 1,
      "title": "湖人胜魔术迎9连胜",
      "popularity": 12111324,
      "link": "https://www.douyin.com/hot/2439267/...",
      "sentence_id": "2439267",
      "group_id": "7618742647842608424",
      "platform": "douyin",
      "video_count": 2,
      "event_time": 1774144409,
      "sentence_tag": 5000,
      "cover": "https://p26-sign.douyinpic.com/...",
      "cover_local": "2026-03-22/covers/hot_2439267.jpg",
      "likes": 12111324,
      "collects": 0,
      "comments": 0,
      "score": 12111324.0
    }
  ],
  "trending_items": [ ]
}
```

## 字段含义

| 字段 | 含义 |
|------|------|
| `sentence_id` | 热搜话题唯一 ID，构建链接用 `/hot/{sentence_id}/{word}` |
| `popularity` | 热度值（热搜榜有值；上升热点为 0） |
| `video_count` | 该话题相关视频数 |
| `event_time` | 话题上热搜的 Unix 时间戳 |
| `sentence_tag` | 话题类型（5000=热搜, 4003=社会, 2003=娱乐, 12000=游戏, 7000=科技） |
| `cover_local` | 封面图本地缓存（CDN URL 几小时后过期，采集时立即下载） |
| `score` | 爆款评分（抖音用 popularity，其他平台用加权公式） |

## 评分算法

`modules/scout/trend_ranker.py` — 抖音直接用 `popularity` 排序。其他平台支持加权公式：

```
score = (likes * w_likes + collects * w_collects + comments * w_comments) / age_days
```

权重在 `config.yaml` 的 `scout.ranking.weights` 中配置。

## 注意事项

- 抖音热榜 API 公开无需认证，但有频率限制，勿高频调用
- 封面图 CDN URL 有签名时效（2-6 小时），必须采集时立即下载
- 链接格式 `/hot/{sentence_id}/{word}` 是热点详情页，不要用 `/search/`
