# 配置参考

## config.yaml 完整字段

```yaml
niche:
  name: "萌宠"                    # 赛道名称（影响 Remix 策略方向）
  keywords: ["猫咪", "狗狗", ...]  # 关键词过滤（Scout 标题匹配）
  platform: "douyin"

scout:
  douyin:
    fetch_limit: 50               # 热榜抓取条数
    top_n: 20                     # 排序后取 Top N
  ranking:
    weights:                      # 评分权重（非抖音平台使用）
      likes: 1
      collects: 2
      comments: 3

analyze:
  max_items: 5                    # 最多分析条数（≤5 更稳定，10 条易超时）

remix:
  strategies:                     # 启用策略（不配则全部执行）
    - "combine"
    - "generalize"
    - "transfer"
    - "extend"

volcengine:
  video_model: "doubao-seedance-1-5-pro-251215"   # Seedance 视频模型
  image_model: "doubao-seedream-5-0-260128"        # Seedream 封面图模型

generate:
  video_ratio: "9:16"
  video_resolution: "720p"        # 720p 性价比最优
  generate_audio: true            # 同步音效（仅 1.5-pro 支持）
  poll_interval: 10               # 轮询间隔（秒）
  poll_timeout: 600               # 最大等待（秒）
  max_concurrent: 2
  mode: "default"                 # default / draft / flex
  post_process:
    enabled: true
    fade_in: 0.3
    fade_out: 0.3
    extract_cover: true
    cover_timestamp: 1.0
    auto_subtitle: true

output:
  daily_count: 3                  # 每天目标创意数
  platforms: ["douyin", "xiaohongshu"]
  data_dir: "./data"

feishu:
  webhook_url: "${FEISHU_WEBHOOK_URL}"
  secret: "${FEISHU_SECRET}"

publish:
  timeout_per_item: 300
  preview_mode: false
  manifest_dir: "/tmp/crawmedia"

schedule:
  scout_time: "06:00"
  analyze_time: "06:30"
  remix_time: "07:00"
  generate_time: "08:00"
  approval_deadline: "17:30"
  publish_time: "18:00"
  timezone: "Asia/Shanghai"

logging:
  level: "INFO"
  file: "./data/crawmedia.log"
```

## .env 环境变量

```bash
# ── 飞书自定义机器人（进度推送）──
FEISHU_WEBHOOK_URL=https://open.feishu.cn/open-apis/bot/v2/hook/xxx
FEISHU_SECRET=xxx

# ── 飞书开放平台应用（互动卡片审批）──
FEISHU_APP_ID=cli_a934afacb7785bb3
FEISHU_APP_SECRET=your_app_secret
FEISHU_RECEIVE_OPEN_ID=ou_xxxx       # 个人 open_id
# FEISHU_CHAT_ID=oc_xxxx              # 群组 chat_id（群审批用）

# ── LLM API（Analyze + Remix 阶段必需）──
LLM_API_KEY=your_api_key
LLM_BASE_URL=https://ark.cn-beijing.volces.com/api/v3
LLM_MODEL=doubao-1-5-pro-32k-250115

# ── 火山引擎（Generate 阶段）──
VOLCENGINE_ACCESS_KEY=your_access_key
VOLCENGINE_SECRET_KEY=your_secret_key
```

## LLM 客户端

模块：`modules/llm.py`

```python
from modules.llm import chat_completion, chat_completion_json

# 文本回复
reply = await chat_completion(system_prompt="...", user_message="...", temperature=0.7)

# JSON 结构化回复（自动 response_format + json.loads）
data = await chat_completion_json(system_prompt="...", user_message="...", temperature=0.5)
```

配置由 .env 读取：`LLM_API_KEY` / `LLM_BASE_URL` / `LLM_MODEL`。

当前使用火山引擎方舟 ARK（豆包 doubao-1.5-pro）。切换其他服务只需改 3 个变量：
- OpenAI: `LLM_BASE_URL=https://api.openai.com/v1`, `LLM_MODEL=gpt-4o`
- DeepSeek: `LLM_BASE_URL=https://api.deepseek.com/v1`, `LLM_MODEL=deepseek-chat`

**注意**：需在[方舟控制台](https://console.volcengine.com/ark/region:ark+cn-beijing/openManagement)开通「按量付费-模型推理」权限。
