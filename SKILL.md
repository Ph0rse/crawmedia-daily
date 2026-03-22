---
name: crawmedia-daily
description: >
  每日自媒体短视频自动生产 Pipeline。完整执行 5 阶段流水线：
  采集抖音热榜爆款 → LLM 分析创意模式 → 4 种策略组合/泛化/迁移/延展创意并生成 Seedance Prompt →
  火山引擎生成视频和封面 → 飞书审批后自动发布到抖音。
  当用户要求采集热榜、分析爆款、生成短视频内容、发布抖音、或执行每日内容生产时激活此技能。
metadata:
  author: max
  version: "0.6.0"
compatibility: Python 3.10+, Node.js, ffmpeg, bun (发布阶段)
---

# CrawMedia Daily — 每日自媒体内容自动生产

## Pipeline 概览

```
① Scout（采集爆款）→ ② Analyze（LLM 分析创意）→ ③ Remix（创意组合 + Prompt）
    → ④ Generate（视频/封面/文案生成）→ ⑤ Publish（审批 + 发布）
```

每个阶段独立可运行，产出 JSON 文件供下一阶段消费。

## 项目结构

```
crawmedia-daily/
├── SKILL.md                     # 本文件（Agent 操作手册）
├── config.yaml                  # 主配置（赛道/策略/时间表/模型）
├── .env                         # 环境变量（API 密钥）
├── requirements.txt             # Python 依赖
├── references/                  # 各阶段深度参考文档
│   ├── scout.md                 # Scout 调用链 + JSON schema
│   ├── analyze.md               # Analyze + PatternDB 详解
│   ├── remix.md                 # 4 种策略 + Prompt 模板详解
│   ├── generate.md              # 视频/图片/文案/后处理详解
│   ├── publish.md               # 飞书审批 + 抖音发布详解
│   └── config.md                # config.yaml + .env 完整说明
├── scripts/                     # 入口脚本
│   ├── run_daily.py             # 每日完整 Pipeline
│   ├── run_single.py            # 单阶段入口
│   └── demo_scout.py            # Scout 演示（无需配置）
├── modules/                     # 业务模块
│   ├── config.py                # 配置加载（YAML + .env 替换）
│   ├── llm.py                   # 统一 LLM 客户端（OpenAI 兼容）
│   ├── feishu.py                # 飞书 Webhook 推送
│   ├── scout/                   # ① 采集 + 评分
│   ├── analyze/                 # ② LLM 创意模式提取 + SQLite 知识库
│   ├── remix/                   # ③ 创意策略 + Seedance Prompt 生成
│   ├── generate/                # ④ 视频/封面/文案/后处理
│   └── publish/                 # ⑤ 审批 + 分发
├── vendor/                      # 内置依赖
│   ├── douyin-hot-trend/        # 抖音热榜 JS 脚本
│   └── distribute/              # 多平台发布 TS 脚本
└── data/                        # 产出数据（自动创建）
    ├── patterns/patterns.db     # SQLite 创意知识库（持续积累）
    └── daily/YYYY-MM-DD/        # 每日产出归档
```

---

## 前置环境检查

> **⚠️ 注意：以下命令仅供人工参考，AI Agent 不应自动执行。仅当用户明确要求「检查环境」时才逐项运行。**

执行任何阶段前，先验证环境就绪（人工逐项运行）：

1. **Python 依赖** — `pip3 install -r requirements.txt`
2. **Node.js** — `node --version`（Scout 阶段需要）
3. **LLM 连通性** — 运行 `python3 scripts/run_single.py` 观察是否报错（Analyze + Remix 需要）
4. **ffmpeg** — `ffmpeg -version`（Generate 后处理需要）
5. **bun** — `bun --version`（Publish 阶段需要）

如果任一步失败，修复后再继续。

---

## 完整 Pipeline 执行工作流

> **⚠️ 重要：以下所有命令仅在用户明确要求执行对应阶段时才运行。AI Agent 在学习/索引本项目时不应自动执行任何命令。**

### 一键执行

命令：`python3 scripts/run_daily.py`（按 `config.yaml` 时间表自动执行全部阶段）

### 分步手动执行

逐阶段执行，每步验证后再进入下一步。

#### Step 1: Scout — 采集爆款

- 演示命令：`python3 scripts/demo_scout.py`
- 正式采集：`python3 scripts/run_single.py scout --no-feishu`
- 验证：检查 `data/daily/YYYY-MM-DD/scout_demo.json` 是否存在，`hot_items` 不为空

> 详细参考 → [references/scout.md](references/scout.md)

#### Step 2: Analyze — LLM 分析创意模式

- 命令：`python3 scripts/run_single.py analyze --no-feishu`
- 验证：检查 `analysis.json` 中 `pattern_count > 0`
- 如果失败：LLM 超时通常是批量条数太多，在 `config.yaml` 中将 `analyze.max_items` 降到 3-5

> 详细参考 → [references/analyze.md](references/analyze.md)

#### Step 3: Remix — 创意策略 + Prompt 生成

- 命令：`python3 scripts/run_single.py remix --no-feishu`
- 验证：检查 `remixed.json` 中 `idea_count > 0` 且每条都有 `prompt_result`

> 详细参考 → [references/remix.md](references/remix.md)

#### Step 4: Generate — 视频 / 封面 / 文案生成

- Dry-run 测试：`python3 scripts/test_generate.py --dry-run`
- 完整生成（含视频 API 调用，需数分钟）：`python3 scripts/run_single.py generate --no-feishu`
- 验证：检查 `generated.json` 中各项有 `video` 和 `copy`
- 如果视频生成失败：检查 `LLM_API_KEY` 是否有方舟「按量付费-模型推理」权限

> 详细参考 → [references/generate.md](references/generate.md)

#### Step 5: Publish — 审批 + 发布

- 创建审批队列：`python3 scripts/run_single.py approve --no-feishu`
- CLI 审批通过指定条目：`python3 scripts/run_single.py approve --ids 0,1`
- 查看审批状态：`python3 scripts/run_single.py status`
- 预览发布（不真实发布）：`python3 scripts/run_single.py publish --preview --no-feishu`
- 正式发布：`python3 scripts/run_single.py publish --no-feishu`
- 验证：检查 `approval.json` 中有 approved 条目，`publish_results.json` 中有 success 记录

> 详细参考 → [references/publish.md](references/publish.md)

---

## 数据流与产出文件

```
config.yaml（赛道关键词）
    ↓ ① Scout
抖音热榜 API → douyin.js → douyin.py → trend_ranker.py
    ↓ 产出: scout.json / scout_demo.json（爆款列表 + 封面图）
    ↓ ② Analyze
scout.json → creative_extractor.py → LLM → pattern_db.py
    ↓ 产出: analysis.json（创意模式卡片）+ patterns.db（知识库）
    ↓ ③ Remix
analysis.json → strategy.py（4种策略 × LLM）→ prompt_generator.py（× LLM）
    ↓ 产出: remixed.json（创意方案 + Seedance 2.0 Prompt）
    ↓ ④ Generate
remixed.json → Seedance API（视频）+ Seedream API（封面）+ LLM（文案）+ ffmpeg（后处理）
    ↓ 产出: generated.json + videos/*.mp4 + covers/*.jpg
    ↓ ⑤ Publish
generated.json → 审批(飞书/CLI) → approval.json → distribute.ts → Chrome CDP → 抖音
    ↓ 产出: publish_results.json
```

| 阶段 | 产出文件 | 下游消费者 |
|------|---------|-----------|
| ① Scout | `scout.json` / `scout_demo.json` | ② Analyze |
| ② Analyze | `analysis.json` + `patterns.db` | ③ Remix |
| ③ Remix | `remixed.json` | ④ Generate |
| ④ Generate | `generated.json` + videos/ + covers/ | ⑤ Publish |
| ⑤ Publish | `approval.json` + `publish_results.json` | — |

---

## LLM 调用统计（单次完整运行）

| 阶段 | 调用次数 | 用途 | 耗时参考 |
|------|---------|------|---------|
| ② Analyze | 1 次 | 批量分析 N 条爆款 | ~45s |
| ③ Remix 策略 | 4 次 | 每种策略各 1 次 | ~60s |
| ③ Remix Prompt | 4 次 | 每个创意生成 Prompt | ~45s |
| ④ Generate 文案 | 3 次 | 每条创意双平台文案 | ~30s |
| **合计** | **~12 次** | | **~180s** |

---

## 关键设计决策

**subprocess 调用 Node.js**：抖音 API 逻辑已在 `vendor/douyin-hot-trend` 实现（Node.js），通过 `--json` 输出结构化数据供 Python 解析，避免重复开发。

**Analyze 批量模式**：将多条内容一次性发给 LLM（1 次 API 调用），比逐条调用更高效，且 LLM 可跨条目比较产生更好的归一化评分。批量条数 ≤5 以避免火山引擎超时。

**4 种独立策略**：每种策略有不同的 System Prompt 和 temperature（0.8-0.9），分别针对不同创意思维模式。串行执行避免 API 限流。

**Seedance Prompt 模板**：不同赛道和内容类型的 Prompt 风格差异大（温馨 vs 搞笑），模板提供风格参考让 LLM 生成更贴合预期的 Prompt。

---

## 排错指南

| 症状 | 原因 | 解决 |
|------|------|------|
| Analyze 报 "Server disconnected" | 批量条数过多导致 LLM 超时 | `config.yaml` 中 `analyze.max_items` 改为 3-5 |
| 视频生成 403 | 方舟 API 未开通推理权限 | 到方舟控制台开通「按量付费-模型推理」 |
| 封面图下载 SSL 超时 | CDN 网络波动 | 已有容错，个别失败可忽略 |
| Publish 报 bun 不存在 | 未安装 bun | `brew install bun` |
| 飞书卡片无回调 | 未配置长连接 | 先启动本地脚本再去后台保存配置 |
| patterns.db 被删除 | — | 将丢失历史创意知识库，重新运行 Analyze 可重建 |

---

## 配置修改

修改赛道、策略、模型等参数，编辑 `config.yaml` 和 `.env`。

> 完整配置参考 → [references/config.md](references/config.md)
