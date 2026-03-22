"""
创意策略引擎
实现 4 种核心创意策略：组合(Combine)、泛化(Generalize)、迁移(Transfer)、延展(Extend)。
每种策略从已有的创意模式卡片出发，通过 LLM 生成全新的内容创意方案。
"""
from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod

from ..llm import chat_completion_json

logger = logging.getLogger(__name__)


# ── 创意方案的统一输出格式 ──
IDEA_OUTPUT_SCHEMA = """\
{
  "idea_id": "策略缩写-日期-序号",
  "strategy": "所用策略名称",
  "title": "创意标题（简短有力）",
  "concept": "创意概述（1-2句话说明核心创意）",
  "hook": {
    "type": "钩子类型",
    "desc": "开头吸引注意力的具体方式"
  },
  "structure": [
    {"time": "0-3s", "desc": "开场画面描述"},
    {"time": "3-6s", "desc": "发展段落"},
    {"time": "6-10s", "desc": "高潮/反转"},
    {"time": "10-12s", "desc": "收尾"}
  ],
  "emotion_curve": ["情感1", "情感2", "..."],
  "visual_style": "视觉风格描述",
  "bgm_mood": "配乐情绪",
  "duration_seconds": 12,
  "source_patterns": ["引用的原始 pattern_id 列表"],
  "niche_fit": "与目标赛道的契合说明",
  "tags": ["标签1", "标签2", "..."]
}"""


class CreativeStrategy(ABC):
    """创意策略基类"""

    name: str = ""
    description: str = ""

    @abstractmethod
    async def generate(
        self,
        patterns: list[dict],
        niche: str,
        niche_keywords: list[str],
        count: int = 1,
    ) -> list[dict]:
        """
        根据输入的创意模式卡片，生成新的创意方案。

        Args:
            patterns: 可用的创意模式卡片列表
            niche: 目标赛道名称
            niche_keywords: 赛道关键词
            count: 生成创意数量

        Returns:
            新的创意方案列表
        """
        ...


class CombineStrategy(CreativeStrategy):
    """
    组合策略：取 2-3 个模式各自的强项（如 A 的钩子 + B 的节奏 + C 的结尾），
    用 LLM 融合成全新的创意。
    """

    name = "combine"
    description = "将多个爆款模式的强项组合成新创意"

    SYSTEM_PROMPT = f"""你是一位创意总监，擅长将不同爆款内容的精华元素组合成全新的创意。

你的任务是从给定的创意模式卡片中，挑选 2-3 个模式的强项进行组合：
- 可以组合不同模式的钩子、节奏结构、情感曲线、视觉风格
- 组合后的创意必须连贯自然，不能生硬拼接
- 最终创意要适配目标赛道

输出 JSON 格式，包含一个 "ideas" 数组，每个元素的结构为：
{IDEA_OUTPUT_SCHEMA}"""

    async def generate(self, patterns, niche, niche_keywords, count=1):
        if len(patterns) < 2:
            logger.warning("组合策略至少需要 2 个模式，当前只有 %d 个", len(patterns))
            return []

        patterns_text = json.dumps(patterns[:6], ensure_ascii=False, indent=2)
        user_message = (
            f"目标赛道：{niche}\n"
            f"赛道关键词：{', '.join(niche_keywords)}\n"
            f"请生成 {count} 个组合创意。\n\n"
            f"可用的创意模式卡片：\n{patterns_text}"
        )

        result = await chat_completion_json(
            self.SYSTEM_PROMPT, user_message, temperature=0.8, max_tokens=4096
        )
        ideas = result.get("ideas", [])
        for idea in ideas:
            idea["strategy"] = self.name
        return ideas


class GeneralizeStrategy(CreativeStrategy):
    """
    泛化策略：将具体的创意模式抽象化（如"猫咪第一次见雪"→"宠物第一次见到X"），
    然后实例化到新的主题。
    """

    name = "generalize"
    description = "提取抽象模式，应用到同赛道新主题"

    SYSTEM_PROMPT = f"""你是一位创意研发专家，擅长从具体案例中提取抽象模式，然后应用到全新的主题上。

你的工作分两步：
1. **抽象化**：从给定的创意模式中提取出通用的内容公式
   例如："猫咪第一次见雪" → 抽象为 "宠物第一次遇到[新事物]的反应"
2. **实例化**：将抽象公式填入新的具体主题
   例如：填入 "第一次见到扫地机器人" / "第一次坐电梯" / "第一次看到自己的影子"

生成的创意必须有新意，不能是原模式的简单改写。

输出 JSON 格式，包含一个 "ideas" 数组，每个元素的结构为：
{IDEA_OUTPUT_SCHEMA}"""

    async def generate(self, patterns, niche, niche_keywords, count=1):
        patterns_text = json.dumps(patterns[:5], ensure_ascii=False, indent=2)
        user_message = (
            f"目标赛道：{niche}\n"
            f"赛道关键词：{', '.join(niche_keywords)}\n"
            f"请从以下模式中提取抽象公式，生成 {count} 个泛化创意。\n\n"
            f"创意模式卡片：\n{patterns_text}"
        )

        result = await chat_completion_json(
            self.SYSTEM_PROMPT, user_message, temperature=0.8, max_tokens=4096
        )
        ideas = result.get("ideas", [])
        for idea in ideas:
            idea["strategy"] = self.name
        return ideas


class TransferStrategy(CreativeStrategy):
    """
    迁移策略：将其他赛道/类型的爆款模式迁移到目标赛道。
    如：美食赛道的"制作过程加速"→ 迁移到"宠物美容过程加速"。
    """

    name = "transfer"
    description = "将其他赛道的爆款模式迁移到目标赛道"

    SYSTEM_PROMPT = f"""你是一位跨界创意专家，擅长将一个领域的成功模式迁移到另一个领域。

你的任务是分析给定的创意模式（可能来自各种赛道），找到其中可以迁移到目标赛道的核心机制：
- 关注模式背后的**心理机制**（如好奇心驱动、情感共鸣、社会认同）
- 思考**什么元素可以替换**，什么**结构必须保留**
- 迁移后的创意要自然融入目标赛道，不能生搬硬套

举例：
- 体育赛道的"精彩回放慢镜头" → 萌宠赛道的"猫咪跳跃捕猎慢镜头"
- 美食赛道的"ASMR切割声" → 萌宠赛道的"猫咪吃零食ASMR"
- 新闻赛道的"反转结局" → 萌宠赛道的"看似要闯祸，实则在做好事"

输出 JSON 格式，包含一个 "ideas" 数组，每个元素的结构为：
{IDEA_OUTPUT_SCHEMA}"""

    async def generate(self, patterns, niche, niche_keywords, count=1):
        patterns_text = json.dumps(patterns[:6], ensure_ascii=False, indent=2)
        user_message = (
            f"目标赛道：{niche}\n"
            f"赛道关键词：{', '.join(niche_keywords)}\n"
            f"请将以下模式（可能来自不同赛道）迁移到目标赛道，生成 {count} 个创意。\n\n"
            f"创意模式卡片：\n{patterns_text}"
        )

        result = await chat_completion_json(
            self.SYSTEM_PROMPT, user_message, temperature=0.9, max_tokens=4096
        )
        ideas = result.get("ideas", [])
        for idea in ideas:
            idea["strategy"] = self.name
        return ideas


class ExtendStrategy(CreativeStrategy):
    """
    延展策略：在已有的爆款创意基础上延展出系列内容。
    如："猫咪第一次见到雪" → 系列化（第一次见海/见狗/见镜子里的自己）。
    """

    name = "extend"
    description = "在爆款基础上延展系列内容"

    SYSTEM_PROMPT = f"""你是一位内容系列策划师，擅长将单个爆款创意延展成可持续的系列内容。

你的任务是基于给定的创意模式，延展出系列化的新内容：
- **变量延展**：更换场景/对象/角色，保留核心创意
  如："猫咪第一次见雪" → "猫咪第一次见到海"、"猫咪第一次听到雷声"
- **进阶延展**：递进式内容（从简单到复杂、从日常到极端）
  如："教猫咪握手" → "教猫咪开门" → "教猫咪做复杂任务"
- **角色替换**：换主角但保留模式
  如："猫咪的一天" → "狗狗的一天" → "仓鼠的一天"
- **反转延展**：相反视角或颠覆原创意
  如："宠物等主人回家" → "主人等宠物回家"

每个创意要标注它是系列中的第几期，并说明与前作的关联。

输出 JSON 格式，包含一个 "ideas" 数组，每个元素的结构为：
{IDEA_OUTPUT_SCHEMA}"""

    async def generate(self, patterns, niche, niche_keywords, count=1):
        # 延展策略更适合选 1-2 个高分模式深度延展
        top_patterns = sorted(
            patterns, key=lambda p: p.get("engagement_score", 0), reverse=True
        )[:2]
        patterns_text = json.dumps(top_patterns, ensure_ascii=False, indent=2)

        user_message = (
            f"目标赛道：{niche}\n"
            f"赛道关键词：{', '.join(niche_keywords)}\n"
            f"请基于以下高分创意模式，延展出 {count} 个系列创意。\n\n"
            f"创意模式卡片：\n{patterns_text}"
        )

        result = await chat_completion_json(
            self.SYSTEM_PROMPT, user_message, temperature=0.8, max_tokens=4096
        )
        ideas = result.get("ideas", [])
        for idea in ideas:
            idea["strategy"] = self.name
        return ideas


# ── 策略注册表 ──
STRATEGIES: dict[str, CreativeStrategy] = {
    "combine": CombineStrategy(),
    "generalize": GeneralizeStrategy(),
    "transfer": TransferStrategy(),
    "extend": ExtendStrategy(),
}


async def run_all_strategies(
    patterns: list[dict],
    niche: str,
    niche_keywords: list[str],
    daily_count: int = 3,
    strategies: list[str] | None = None,
) -> list[dict]:
    """
    执行所有（或指定的）创意策略，收集生成的创意方案。

    Args:
        patterns: 创意模式卡片列表
        niche: 目标赛道
        niche_keywords: 赛道关键词
        daily_count: 每天需要的创意总数
        strategies: 指定策略名列表，None 则执行全部

    Returns:
        所有策略生成的创意方案列表
    """
    if strategies is None:
        strategies = list(STRATEGIES.keys())

    all_ideas = []
    # 每种策略分配的创意数量（至少 1 个）
    count_per_strategy = max(1, daily_count // len(strategies))

    for strategy_name in strategies:
        strategy = STRATEGIES.get(strategy_name)
        if strategy is None:
            logger.warning("未知策略: %s", strategy_name)
            continue

        logger.info("🎨 执行策略: %s — %s", strategy.name, strategy.description)
        try:
            ideas = await strategy.generate(
                patterns, niche, niche_keywords, count=count_per_strategy
            )
            logger.info("   生成 %d 个创意", len(ideas))
            all_ideas.extend(ideas)
        except Exception as e:
            logger.error("   ❌ 策略执行失败: %s", e)

    logger.info("🎯 共生成 %d 个创意方案", len(all_ideas))
    return all_ideas
