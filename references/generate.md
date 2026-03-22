# Generate 阶段 — 详细参考

## 调用链

```
scripts/run_single.py generate → modules/generate/runner.py :: run_generate()
  ├─→ 读取 data/daily/YYYY-MM-DD/remixed.json，取前 daily_count 条
  ├─→ 逐条生成（semaphore 控制并发）:
  │     ├─→ 1. volcengine_video.py :: generate_video()
  │     │       提交任务 → 轮询状态 → 下载视频
  │     ├─→ 2. video_post.py :: post_process_video()（可选）
  │     │       ffmpeg 后处理：淡入淡出 + 字幕叠加 + 封面截帧
  │     ├─→ 3. volcengine_image.py :: generate_cover_from_idea()
  │     │       AI 生成封面图（如后处理已截帧则跳过）
  │     └─→ 4. copy_gen.py :: generate_copy()
  │           LLM 生成抖音 + 小红书双平台文案
  ├─→ 保存 data/daily/YYYY-MM-DD/generated.json
  └─→ modules/feishu.py（可选）
```

## 命令

```bash
# 仅测试文案（不调视频 API）
python3 scripts/test_generate.py --dry-run

# 标准生成
python3 scripts/test_generate.py --mode default

# 样片预览模式
python3 scripts/test_generate.py --mode draft

# 完整阶段运行
python3 scripts/run_single.py generate --no-feishu
```

## 视频生成（火山引擎 Seedance）

模块：`modules/generate/volcengine_video.py`

### 模型选择

| 模型 | 文生 | 首帧 | 首尾帧 | 音频 | 样片 | 离线 |
|------|------|------|--------|------|------|------|
| doubao-seedance-1-5-pro-251215 | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ← 推荐
| doubao-seedance-1-0-pro-250528 | ✅ | ✅ | ✅ | ❌ | ❌ | ✅ |
| doubao-seedance-1-0-pro-fast-251015 | ✅ | ✅ | ❌ | ❌ | ❌ | ✅ | ← 快速

### 生成模式

- **default** — 标准模式，提交后数分钟内出结果
- **draft** — 先 480p 样片快速预览，确认后升级高清成片（仅 1.5-pro）
- **flex** — 离线推理，成本更低，耗时可能更长，适合批量非紧急任务

### API 端点

```
POST {LLM_BASE_URL}/contents/generations/tasks     # 提交任务
GET  {LLM_BASE_URL}/contents/generations/tasks/{id} # 查询状态
```

认证复用 LLM_API_KEY（Bearer Token）。

### 核心参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `duration` | 5 | 时长 2-12 秒 |
| `ratio` | 9:16 | 宽高比（短视频竖屏） |
| `resolution` | 720p | 480p/720p/1080p |
| `generate_audio` | true | 同步音效（仅 1.5-pro） |
| `poll_interval` | 10 | 轮询间隔秒 |
| `poll_timeout` | 600 | 最大等待秒（flex 模式自动延长到 3600） |

## 封面图生成（Seedream）

模块：`modules/generate/volcengine_image.py`

同步调用 `POST {LLM_BASE_URL}/images/generations`，从创意的 title + concept + visual_style 自动拼接 prompt。

推荐模型：`doubao-seedream-5-0-260128`

## 文案生成

模块：`modules/generate/copy_gen.py`

调用 LLM 生成双平台文案：
- **抖音**：title (≤30字) + description (≤200字) + tags (5-8个)
- **小红书**：title (≤20字) + body (≤500字，种草风格) + tags (5-10个)

## 视频后处理

模块：`modules/generate/video_post.py`（依赖 ffmpeg）

功能：
- **淡入淡出** — `add_fade()`: 首尾转场效果
- **字幕叠加** — `add_subtitles()`: ASS 格式硬字幕，从创意 structure 自动生成
- **封面截帧** — `extract_frame()`: 指定时间点截取
- **多段拼接** — `concat_videos()`: 支持 xfade 转场

组合入口：`post_process_video()` 依次执行全部后处理步骤。

## 产出文件：generated.json

```json
{
  "stage": "generate",
  "timestamp": "2026-03-22T14:30:00",
  "item_count": 3,
  "items": [
    {
      "idea_id": "萌宠-20241120-1",
      "title": "萌宠界的超级赛事",
      "strategy": "combine",
      "video": {
        "task_id": "cgt-xxx",
        "status": "succeeded",
        "video_url": "https://...",
        "local_path": "data/daily/.../videos/video_0.mp4",
        "duration": 5,
        "resolution": "720p",
        "mode": "default"
      },
      "post_process": { "output_path": "...", "cover_path": "...", "steps": ["fade", "subtitles", "cover"] },
      "cover": { "local_path": "...", "source": "video_frame" },
      "copy": {
        "douyin": { "title": "...", "description": "...", "tags": [...] },
        "xiaohongshu": { "title": "...", "body": "...", "tags": [...] }
      },
      "errors": []
    }
  ]
}
```
