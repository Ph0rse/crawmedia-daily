# Remix 阶段 — 详细参考

## 调用链

```
scripts/run_single.py remix → modules/remix/runner.py :: run_remix()
  ├─→ 加载 data/daily/YYYY-MM-DD/analysis.json
  ├─→ modules/remix/strategy.py :: run_all_strategies()
  │     遍历 4 种策略，每种各生成 N 个创意（N = daily_count / 4）
  │     ├─→ CombineStrategy.generate()    → LLM → 组合创意
  │     ├─→ GeneralizeStrategy.generate() → LLM → 泛化创意
  │     ├─→ TransferStrategy.generate()   → LLM → 迁移创意
  │     └─→ ExtendStrategy.generate()     → LLM → 延展创意
  ├─→ modules/remix/prompt_generator.py :: generate_prompts_for_ideas()
  │     逐条创意 → 自动匹配模板 → LLM 生成 Seedance 2.0 Prompt
  ├─→ 保存 data/daily/YYYY-MM-DD/remixed.json
  └─→ modules/feishu.py（可选）
```

## 命令

```bash
python3 scripts/run_single.py remix --no-feishu
```

## 4 种创意策略引擎

位置：`modules/remix/strategy.py`

| 策略 | 类 | 核心指令 | temperature |
|------|-----|---------|-------------|
| **组合** | `CombineStrategy` | 从 2-3 个模式中挑选各自强项融合成新创意 | 0.8 |
| **泛化** | `GeneralizeStrategy` | 从案例提取抽象公式，再填入新主题 | 0.8 |
| **迁移** | `TransferStrategy` | 分析模式背后的心理机制，跨赛道迁移 | 0.9 |
| **延展** | `ExtendStrategy` | 基于高分模式延展系列内容（变量替换/反转） | 0.8 |

策略注册表：
```python
from modules.remix.strategy import STRATEGIES
# STRATEGIES = {"combine": CombineStrategy(), "generalize": ..., "transfer": ..., "extend": ...}
```

每种策略 LLM 交互模式相同：
```
System: {策略专属指令} + {创意方案 JSON schema}
User:   "目标赛道：萌宠\n赛道关键词：猫咪, 狗狗...\n请生成 1 个XX创意。\n\n可用模式卡片：{patterns JSON}"
返回:   {"ideas": [{...创意方案...}]}
```

## Seedance Prompt 模板

位置：`modules/remix/templates/*.yaml`

| 文件 | 适用场景 |
|------|---------|
| `pet_cute.yaml` | 萌宠温馨治愈类 |
| `pet_funny.yaml` | 萌宠搞笑反转类 |
| `generic.yaml` | 通用短视频 |

模板包含：`visual_style`, `bgm_mood`, `camera_moves`, `duration_range`, `example_prompt`, `structure_template` 等字段。`prompt_generator.py` 根据创意标签自动匹配最佳模板。

## 产出文件：remixed.json

```json
{
  "stage": "remix",
  "timestamp": "2026-03-22T13:01:23",
  "idea_count": 4,
  "ideas": [
    {
      "idea_id": "萌宠-20241120-1",
      "strategy": "combine",
      "title": "萌宠界的"超级赛事"",
      "concept": "以热点赛事的形式展现萌宠的可爱瞬间...",
      "hook": { "type": "热点借势", "desc": "..." },
      "structure": [
        {"time": "0-3s", "desc": "展示萌宠们聚集在'赛场'"},
        {"time": "3-6s", "desc": "猫咪抓逗玩具、狗狗追逐飞盘"},
        {"time": "6-10s", "desc": "小奶猫做出超萌动作"},
        {"time": "10-12s", "desc": "所有萌宠看向镜头，画面定格"}
      ],
      "emotion_curve": ["兴奋", "喜爱", "满足"],
      "visual_style": "全景与特写结合，明亮清新滤镜",
      "bgm_mood": "活力欢快",
      "duration_seconds": 12,
      "source_patterns": ["体育赛事-20260322-1", "体育活动-20260322-2"],
      "niche_fit": "将体育赛事模式迁移到萌宠赛道...",
      "tags": ["猫咪", "狗狗", "萌宠", "萌宠赛事"],
      "prompt_result": {
        "seedance_prompt": "猫咪、狗狗等萌宠们聚集在'赛场'...",
        "duration_seconds": 12,
        "style_tags": ["猫咪", "萌宠"],
        "required_assets": [],
        "notes": "生成视频时注意按照分时段描述精确展现画面..."
      }
    }
  ]
}
```

关键字段：
- `strategy` — 使用的策略名
- `source_patterns` — 引用的原始模式 ID（可溯源）
- `prompt_result.seedance_prompt` — 可直接提交给 Seedance 2.0 的视频 Prompt
- `prompt_result.duration_seconds` — 推荐视频时长（4-15s）

## LLM 调用统计

| 环节 | 调用次数 | 耗时参考 |
|------|---------|---------|
| 策略生成 | 4 次（每种策略 1 次） | ~60s |
| Prompt 生成 | 4 次（每个创意 1 次） | ~45s |
| **合计** | **8 次** | **~105s** |
