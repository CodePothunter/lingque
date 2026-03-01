"""自然语言强化学习引擎 — Natural Language RL Framework

真正的强化学习实现，使用自然语言状态和开放动作空间。

核心设计：
1. 状态 S: 自然语言 + 语义指纹（保持可读性，添加可比较性）
2. 动作 A: 工具调用 + 动作类别（开放集合，动态扩展）
3. 策略 π_θ: 类别偏好权重（可学习参数）
4. 价值 V(s): TD学习的状态价值表
5. 优化器: 真正的PPO（重要性采样 + clip）

RL循环：
- 观察状态 s → 采样类别 c ~ π_θ(c) → LLM生成具体工具
- 执行动作 a → 获得奖励 r → 观察新状态 s'
- 存储 (s, a, r, s') → 定期PPO更新策略
"""

from __future__ import annotations

import json
import logging
import math
import random
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from hashlib import sha256
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from lq.executor.api import DirectAPIExecutor

logger = logging.getLogger(__name__)

CST = timezone(timedelta(hours=8))

# ── RL 超参数 ──

# 奖励权重
REWARD_ALPHA = 0.4         # 预测误差权重
REWARD_BETA = 0.4          # 新奇度权重
REWARD_GAMMA = 0.2         # 胜任度权重

# 价值学习
VALUE_ALPHA = 0.1          # TD学习步长
VALUE_DISCOUNT = 0.99      # 折扣因子 γ

# PPO
PPO_CLIP_EPSILON = 0.2     # Clipping参数
PPO_LEARNING_RATE = 0.01   # 策略学习率
PPO_ENTROPY_COEF = 0.01    # 熵正则化系数
PPO_BATCH_SIZE = 32        # 每次更新的样本数

# 探索
EXPLORATION_EPSILON = 0.1  # ε-greedy探索率
POLICY_TEMPERATURE = 1.0   # Softmax温度

# 经验回放
MAX_TRANSITIONS = 1000     # 最大存储transition数量

# 中文停用词（用于状态关键词提取）
STOP_WORDS_CN = {
    "的", "了", "是", "在", "有", "和", "就", "不", "人", "都", "一", "一个",
    "上", "也", "很", "到", "说", "要", "去", "你", "会", "着", "没有", "看",
    "好", "自己", "这", "那", "这个", "那个", "什么", "怎么", "如何", "为什么",
}

# 英文停用词
STOP_WORDS_EN = {
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "should",
    "could", "may", "might", "must", "shall", "can", "of", "to", "in", "for",
    "on", "at", "from", "by", "with", "about", "as", "into", "through",
}


# ── 动作类别定义 ──

class ActionCategory(Enum):
    """动作类别（策略空间）

    策略参数 θ 是这些类别的偏好权重。
    具体工具由LLM根据选定的类别方向生成，而不是θ直接选择。
    这保持了动作空间的开放性。
    """
    EXPLORE_WEB = "explore_web"       # 联网探索（搜索、抓取）
    EXPLORE_CODE = "explore_code"     # 代码探索（读源码、分析）
    EXPLORE_LOCAL = "explore_local"   # 本地探索（读文件、执行代码）

    REFLECT = "reflect"               # 反思总结
    EVOLVE = "evolve"                 # 自我进化（改代码、改配置）
    MEMORY = "memory"                 # 记忆操作（读写MEMORY.md等）

    INTERACT = "interact"             # 用户交互（发消息、卡片）

    IDLE = "idle"                     # 空闲/等待
    TERMINATE = "terminate"           # 终止当前任务

    @classmethod
    def all(cls) -> list[ActionCategory]:
        return list(cls)

    @classmethod
    def active(cls) -> list[ActionCategory]:
        """返回活跃的类别（排除IDLE和TERMINATE）"""
        return [c for c in cls.all() if c not in (cls.IDLE, cls.TERMINATE)]


# 工具到类别的映射
TOOL_TO_CATEGORY: dict[str, ActionCategory] = {
    "web_search": ActionCategory.EXPLORE_WEB,
    "web_fetch": ActionCategory.EXPLORE_WEB,
    "read_file": ActionCategory.EXPLORE_LOCAL,
    "run_python": ActionCategory.EXPLORE_LOCAL,
    "write_memory": ActionCategory.MEMORY,
    "read_self_file": ActionCategory.EXPLORE_LOCAL,
    "write_self_file": ActionCategory.MEMORY,
    "create_custom_tool": ActionCategory.EVOLVE,
    "send_message": ActionCategory.INTERACT,
    "send_card": ActionCategory.INTERACT,
}


# ── 状态表示 ──

@dataclass
class State:
    """自然语言状态

    保持原文，添加可比较的指纹用于状态分析。
    """
    raw_context: str          # 当前对话上下文
    raw_memory: str           # MEMORY.md摘要
    raw_curiosity: str        # CURIOSITY.md摘要

    # 语义指纹
    fingerprint: str = field(init=False)
    keywords: set[str] = field(default_factory=set)

    def __post_init__(self):
        # 提取语义指纹
        combined = f"{self.raw_context[:200]} {self.raw_curiosity[:200]}"
        self.fingerprint = sha256(combined.encode()).hexdigest()[:16]
        self.keywords = self._extract_keywords(combined)

    def _extract_keywords(self, text: str) -> set[str]:
        """从文本提取关键词（过滤停用词）"""
        # 提取中文词汇（2字以上）和英文单词（3字母以上）
        words = re.findall(r'[\u4e00-\u9fff]{2,}|[a-zA-Z]{3,}', text)

        # 过滤停用词
        filtered = {
            w for w in words
            if w.lower() not in STOP_WORDS_EN and w not in STOP_WORDS_CN
        }
        return filtered

    def similarity_to(self, other: State) -> float:
        """状态相似度（基于关键词重叠）"""
        if not self.keywords or not other.keywords:
            return 0.0
        intersection = self.keywords & other.keywords
        union = self.keywords | other.keywords
        return len(intersection) / len(union) if union else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "fingerprint": self.fingerprint,
            "context": self.raw_context[:100],
            "curiosity": self.raw_curiosity[:100],
            "keywords": list(self.keywords)[:10],
        }


@dataclass
class Action:
    """动作 - 开放工具空间的表示"""
    tool_name: str                    # 具体工具名
    parameters: dict[str, Any]        # 工具参数
    category: ActionCategory          # 动作类别（用于策略）
    reasoning: str = ""               # LLM的推理

    @property
    def signature(self) -> str:
        """动作签名（用于去重和分析）"""
        params = "&".join(f"{k}={str(v)[:20]}" for k, v in self.parameters.items())
        return f"{self.tool_name}:{params}" if params else self.tool_name

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool": self.tool_name,
            "category": self.category.value,
            "params": {k: str(v)[:50] for k, v in self.parameters.items()},
        }

    @classmethod
    def from_tool_call(
        cls,
        tool_name: str,
        parameters: dict,
        reasoning: str = "",
    ) -> Action:
        """从工具调用提取动作"""
        category = TOOL_TO_CATEGORY.get(tool_name, ActionCategory.EXPLORE_LOCAL)

        # 特殊处理：写SOUL.md归为进化
        if tool_name == "write_self_file" and parameters.get("filename") == "SOUL.md":
            category = ActionCategory.EVOLVE

        return cls(
            tool_name=tool_name,
            parameters=parameters,
            category=category,
            reasoning=reasoning
        )


# ── 策略参数 ──

@dataclass
class PolicyTheta:
    """策略参数 θ

    θ = {category: bias_weight}

    策略定义：π_θ(category) = softmax(bias_category / temperature)
    """
    version: int = 0
    biases: dict[ActionCategory, float] = field(default_factory=dict)
    exploration_epsilon: float = EXPLORATION_EPSILON
    temperature: float = POLICY_TEMPERATURE

    def __post_init__(self):
        # 初始化所有类别为0
        for cat in ActionCategory:
            if cat not in self.biases:
                self.biases[cat] = 0.0

    def get_category_distribution(
        self,
        available: list[ActionCategory] | None = None,
    ) -> dict[ActionCategory, float]:
        """计算类别概率分布 π_θ(category)"""
        if available is None:
            available = ActionCategory.active()

        if not available:
            return {}

        # 获取bias值
        biases = [self.biases.get(cat, 0.0) for cat in available]

        # Softmax
        exp_biases = [math.exp(b / self.temperature) for b in biases]
        total = sum(exp_biases)

        if total == 0:
            return {cat: 1.0/len(available) for cat in available}

        return {
            cat: exp_biases[i] / total
            for i, cat in enumerate(available)
        }

    def sample_category(
        self,
        available: list[ActionCategory] | None = None,
    ) -> ActionCategory:
        """根据策略采样一个类别"""
        avail = available or ActionCategory.active()
        if not avail:
            return ActionCategory.IDLE

        # ε-贪婪探索
        if random.random() < self.exploration_epsilon:
            return random.choice(avail)

        dist = self.get_category_distribution(avail)
        categories = list(dist.keys())
        probs = list(dist.values())
        return random.choices(categories, weights=probs)[0]

    def get_probability(
        self,
        category: ActionCategory,
        available: list[ActionCategory] | None = None,
    ) -> float:
        """获取某个类别的概率 π_θ(category)"""
        dist = self.get_category_distribution(available)
        return dist.get(category, 0.0)

    def entropy(self) -> float:
        """计算策略熵（衡量探索程度）"""
        dist = self.get_category_distribution()
        return -sum(p * math.log(p + 1e-8) for p in dist.values() if p > 0)

    def copy(self) -> PolicyTheta:
        """复制策略（用于PPO保存旧策略）"""
        return PolicyTheta(
            version=self.version,
            biases=self.biases.copy(),
            exploration_epsilon=self.exploration_epsilon,
            temperature=self.temperature,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "biases": {cat.value: w for cat, w in self.biases.items()},
            "epsilon": self.exploration_epsilon,
            "temperature": self.temperature,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PolicyTheta:
        biases = {}
        for cat_str, weight in data.get("biases", {}).items():
            try:
                cat = ActionCategory(cat_str)
                biases[cat] = weight
            except ValueError:
                pass
        return cls(
            version=data.get("version", 0),
            biases=biases,
            exploration_epsilon=data.get("epsilon", EXPLORATION_EPSILON),
            temperature=data.get("temperature", POLICY_TEMPERATURE),
        )


# ── 状态转移 ──

@dataclass
class Transition:
    """状态转移 (s, a, r, s', done)

    用于PPO训练的经验样本。
    """
    state_fingerprint: str       # s的指纹
    category: ActionCategory     # 采样的类别
    reward: float                # 获得的奖励
    done: bool                   # 是否终止

    # PPO所需
    prob_old: float = 0.0        # π_θ_old(a|s)
    prob_new: float = 0.0        # π_θ(a|s)
    advantage: float = 0.0       # A(s,a)

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state_fingerprint,
            "category": self.category.value,
            "reward": self.reward,
            "done": self.done,
            "prob_old": self.prob_old,
            "advantage": self.advantage,
        }


# ── 价值函数 ──

@dataclass
class ValueTable:
    """状态价值函数 V(s)

    使用状态聚类来估计价值，避免高维自然语言的复杂性。
    V(s) ← V(s) + α[r - V(s)]
    """
    cluster_values: dict[str, float] = field(default_factory=dict)
    baseline: float = 0.5
    visit_counts: dict[str, int] = field(default_factory=dict)

    def get_value(self, state_fingerprint: str) -> float:
        """获取V(s)，未见过则返回基线"""
        return self.cluster_values.get(state_fingerprint, self.baseline)

    def update(
        self,
        state_fingerprint: str,
        reward: float,
        alpha: float = VALUE_ALPHA,
    ) -> float:
        """TD更新: V(s) ← V(s) + α[r - V(s)]"""
        current_value = self.get_value(state_fingerprint)
        td_error = reward - current_value
        new_value = current_value + alpha * td_error

        self.cluster_values[state_fingerprint] = new_value
        self.visit_counts[state_fingerprint] = self.visit_counts.get(state_fingerprint, 0) + 1

        # 更新全局基线（EMA）
        self.baseline = 0.99 * self.baseline + 0.01 * new_value

        return new_value

    def to_dict(self) -> dict[str, Any]:
        # 只返回最近访问的状态
        recent = sorted(
            self.cluster_values.items(),
            key=lambda x: self.visit_counts.get(x[0], 0),
            reverse=True
        )[:50]
        return {
            "baseline": self.baseline,
            "cluster_values": dict(recent),
            "total_states": len(self.cluster_values),
        }


@dataclass
class AdvantageEstimator:
    """优势函数估计

    A(s,a) 使用 GAE(λ) 或简单 TD error。
    """
    reward_history: list[float] = field(default_factory=list)
    max_history: int = 100

    def compute_advantage(
        self,
        reward: float,
        value_baseline: float,
        use_gae: bool = True,
        gamma: float = VALUE_DISCOUNT,
        lambda_: float = 0.95,
    ) -> float:
        """计算优势 A(s,a)"""
        if use_gae and len(self.reward_history) > 1:
            # GAE简化版：只看最近的窗口
            window = min(10, len(self.reward_history))
            recent_rewards = self.reward_history[-window:] + [reward]
            gae = 0.0
            for r in reversed(recent_rewards):
                delta = r - value_baseline
                gae = delta + gamma * lambda_ * gae
            return gae / len(recent_rewards)
        else:
            # 简单TD error
            return reward - value_baseline

    def add_reward(self, reward: float) -> None:
        self.reward_history.append(reward)
        if len(self.reward_history) > self.max_history:
            self.reward_history.pop(0)


# ── PPO优化器 ──

class PPOOptimizer:
    """PPO优化器

    实现PPO的clipped surrogate objective：

    L^CLIP(θ) = E[min(r_t(θ)A_t, clip(r_t(θ), 1-ε, 1+ε)A_t)]

    其中 r_t(θ) = π_θ(a|s) / π_θ_old(a|s)

    梯度方向说明：
    - advantage > 0：行动优于平均 → 增加该类别的bias（增加选择概率）
    - advantage < 0：行动差于平均 → 减少该类别的bias（减少选择概率）
    """

    # bias绝对值上限，防止策略发散
    MAX_BIAS_ABS = 5.0

    def __init__(
        self,
        clip_epsilon: float = PPO_CLIP_EPSILON,
        learning_rate: float = PPO_LEARNING_RATE,
        entropy_coef: float = PPO_ENTROPY_COEF,
    ):
        self.clip_epsilon = clip_epsilon
        self.lr = learning_rate
        self.entropy_coef = entropy_coef
        self.theta_old: PolicyTheta | None = None
        self.update_count: int = 0

    def update(
        self,
        theta: PolicyTheta,
        transitions: list[Transition],
        value_baseline: float,
    ) -> dict[str, Any]:
        """PPO策略更新

        Args:
            theta: 当前策略参数
            transitions: 经验样本
            value_baseline: 价值基线

        Returns:
            更新统计信息
        """
        if not transitions:
            return {"updated": False, "reason": "No transitions"}

        # 保存旧策略
        self.theta_old = theta.copy()

        # 按类别聚合梯度
        category_gradients: dict[ActionCategory, float] = {
            cat: 0.0 for cat in ActionCategory
        }

        for trans in transitions:
            cat = trans.category
            advantage = trans.advantage

            # 当前策略概率
            prob_new = theta.get_probability(cat)
            prob_old = trans.prob_old if trans.prob_old > 0 else prob_new

            # 重要性采样比率
            ratio = prob_new / (prob_old + 1e-8) if prob_old > 1e-8 else 1.0

            # PPO clip
            clipped_ratio = max(
                1 - self.clip_epsilon,
                min(1 + self.clip_epsilon, ratio)
            )

            # PPO objective（要最大化）
            # advantage > 0 时，ratio * advantage 为正，鼓励该动作
            # advantage < 0 时，min会选clip后的，限制惩罚幅度
            policy_objective = min(ratio * advantage, clipped_ratio * advantage)

            # 累积梯度（符号正确：正advantage增加bias，负advantage减少bias）
            category_gradients[cat] += self.lr * policy_objective

        # 计算当前熵（用于探索鼓励）
        current_entropy = theta.entropy()

        # 应用更新
        updated_categories = []
        for cat, grad in category_gradients.items():
            if abs(grad) > 1e-6:
                old_bias = theta.biases.get(cat, 0.0)
                new_bias = old_bias + grad

                # 熵正则化：向均匀分布方向微调（探索鼓励）
                # 对低概率类别给予更多支持
                dist = theta.get_category_distribution()
                uniform_prob = 1.0 / len(dist) if dist else 0.0
                current_prob = dist.get(cat, 0.0)
                if current_prob < uniform_prob:
                    new_bias += self.entropy_coef * current_entropy

                # 防止bias过大导致概率饱和
                new_bias = max(-self.MAX_BIAS_ABS, min(self.MAX_BIAS_ABS, new_bias))

                theta.biases[cat] = new_bias
                updated_categories.append(cat)

        theta.version += 1
        self.update_count += 1

        logger.info(
            "PPO更新 #%d: 更新了 %d 个类别, baseline=%.3f, entropy=%.3f",
            self.update_count, len(updated_categories), value_baseline, current_entropy
        )

        return {
            "updated": True,
            "categories_updated": len(updated_categories),
            "categories": [c.value for c in updated_categories],
            "version": theta.version,
            "entropy": current_entropy,
        }


# ── 奖励函数 ──

@dataclass
class RewardSignal:
    """单次奖励信号"""
    timestamp: float
    context: str
    source: str                    # "reflection" | "exploration" | "evolution"
    prediction_error: int          # 1-10
    novelty: int                   # 1-10
    competence: int                # 1-10
    reward: float
    reasoning: str = ""

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "context": self.context,
            "source": self.source,
            "prediction_error": self.prediction_error,
            "novelty": self.novelty,
            "competence": self.competence,
            "reward": self.reward,
            "reasoning": self.reasoning,
        }


def calculate_reward(
    prediction_error: int,
    novelty: int,
    competence: int,
) -> float:
    """纯数学公式计算奖励值

    R = (α * PE + β * NV + γ * CP) / 10

    Returns:
        float in [0, 1]
    """
    pe = max(1, min(10, prediction_error))
    nv = max(1, min(10, novelty))
    cp = max(1, min(10, competence))
    return (REWARD_ALPHA * pe + REWARD_BETA * nv + REWARD_GAMMA * cp) / 10


# ─── RL引擎 ───

class ReinforcementLearner:
    """真正的强化学习引擎

    架构：
    1. State: 自然语言 + 指纹
    2. Action: 工具调用 + 类别
    3. Policy θ: 类别偏好权重
    4. Value V(s): 状态价值表
    5. Optimizer: PPO
    """

    def __init__(
        self,
        workspace: Path,
        executor: DirectAPIExecutor | None = None,
    ) -> None:
        self.workspace = workspace
        self.executor = executor
        self._state_path = workspace / "rl-state.json"
        self._log_dir = workspace / "logs"

        # RL组件
        self.policy: PolicyTheta = PolicyTheta()
        self.value_table: ValueTable = ValueTable()
        self.optimizer: PPOOptimizer = PPOOptimizer()
        self.advantage_estimator: AdvantageEstimator = AdvantageEstimator()

        # 经验回放
        self.transitions: list[Transition] = []
        self.reward_history: list[RewardSignal] = []

        # 当前状态
        self._current_state: State | None = None

        self._load_state()

    # ── 状态管理 ──

    def create_state(
        self,
        context: str = "",
        memory: str = "",
        curiosity: str = "",
    ) -> State:
        """创建当前状态"""
        state = State(
            raw_context=context,
            raw_memory=memory,
            raw_curiosity=curiosity,
        )
        self._current_state = state
        return state

    def get_state(self) -> State | None:
        return self._current_state

    # ── 动作采样 ──

    def sample_action_category(
        self,
        available: list[ActionCategory] | None = None,
    ) -> tuple[ActionCategory, dict[ActionCategory, float]]:
        """根据策略采样动作类别

        Returns:
            (category, distribution)
        """
        category = self.policy.sample_category(available)
        dist = self.policy.get_category_distribution(available)
        return category, dist

    # ── 记录转移 ──

    def record_transition(
        self,
        state: State,
        action: Action,
        reward: float,
        done: bool = False,
    ) -> Transition:
        """记录一次转移"""
        # 计算advantage
        advantage = self.advantage_estimator.compute_advantage(
            reward,
            self.value_table.baseline,
        )

        # 保存当前策略的概率
        prob = self.policy.get_probability(action.category)

        trans = Transition(
            state_fingerprint=state.fingerprint,
            category=action.category,
            reward=reward,
            done=done,
            advantage=advantage,
            prob_old=prob,
            prob_new=prob,
        )

        self.transitions.append(trans)
        if len(self.transitions) > MAX_TRANSITIONS:
            self.transitions.pop(0)

        # 更新价值函数
        self.value_table.update(state.fingerprint, reward)

        # 更新advantage估计器
        self.advantage_estimator.add_reward(reward)

        return trans

    # ── 策略更新 ──

    def update_policy(self, batch_size: int = PPO_BATCH_SIZE) -> dict[str, Any]:
        """PPO策略更新"""
        if len(self.transitions) < batch_size:
            return {"updated": False, "reason": "Not enough transitions"}

        # 采样最近的batch
        batch = self.transitions[-batch_size:]

        result = self.optimizer.update(
            self.policy,
            batch,
            value_baseline=self.value_table.baseline,
        )

        if result.get("updated"):
            # 更新后，重新计算transitions的概率
            self._update_transition_probs()
            self.save_state()

        return result

    def _update_transition_probs(self) -> None:
        """更新transitions中存储的概率"""
        for trans in self.transitions:
            trans.prob_new = self.policy.get_probability(trans.category)

    # ── 策略守卫 ──

    def should_allow_action(
        self,
        action: Action,
        state: State,
    ) -> tuple[bool, str]:
        """PPO策略守卫：判断动作是否符合当前策略约束"""
        # 获取当前策略分布
        dist = self.policy.get_category_distribution()
        prob = dist.get(action.category, 0.0)

        # 如果概率太低（<5%），说明偏离策略
        if prob < 0.05:
            # 探索模式允许
            if self.policy.exploration_epsilon > 0.2:
                return True, "低概率动作，但探索模式允许"

            # 计算策略比率（近似KL）
            uniform_prob = 1.0 / len(dist) if dist else 1.0
            ratio = prob / uniform_prob if uniform_prob > 0 else 1.0
            if ratio < 0.5:
                return False, f"动作偏离策略太远 (p={prob:.3f}, ratio={ratio:.3f})"

        return True, "OK"

    # ── 奖励计算 ──

    def record_reward(self, signal: RewardSignal) -> None:
        """记录奖励信号"""
        self.reward_history.append(signal)
        if len(self.reward_history) > 100:
            self.reward_history = self.reward_history[-100:]

        # 写入日志
        self._append_reward_log(signal)

        logger.info(
            "RL 奖励: R=%.3f (PE=%d, NV=%d, CP=%d) [%s] %s",
            signal.reward, signal.prediction_error, signal.novelty,
            signal.competence, signal.source, signal.context[:50],
        )

    def record_reward_from_reflection(
        self,
        prediction_error: int,
        novelty: int,
        competence: int,
        reply_summary: str,
    ) -> RewardSignal:
        """从反思结果中提取并记录奖励信号"""
        reward = calculate_reward(prediction_error, novelty, competence)
        signal = RewardSignal(
            timestamp=time.time(),
            context=reply_summary[:200],
            source="reflection",
            prediction_error=prediction_error,
            novelty=novelty,
            competence=competence,
            reward=reward,
        )
        self.record_reward(signal)
        return signal

    async def compute_reward(
        self,
        action_description: str,
        action_result: str,
        source: str = "exploration",
    ) -> RewardSignal | None:
        """对自主行动结果计算奖励信号"""
        if not self.executor:
            return None

        from lq.prompts import RL_REWARD_EVAL_PROMPT

        prompt = RL_REWARD_EVAL_PROMPT.format(
            action_description=action_description[:300],
            action_result=action_result[:500],
            source=source,
        )

        try:
            result = await self.executor.reply_with_history(
                "", [{"role": "user", "content": prompt}], max_tokens=200,
            )
            from json_repair import repair_json
            data = repair_json(result.strip(), return_objects=True)
            if not isinstance(data, dict):
                return None

            pe = max(1, min(10, int(data.get("prediction_error", 5))))
            nv = max(1, min(10, int(data.get("novelty", 5))))
            cp = max(1, min(10, int(data.get("competence", 5))))
            reasoning = data.get("reasoning", "")

            reward = calculate_reward(pe, nv, cp)
            signal = RewardSignal(
                timestamp=time.time(),
                context=action_description[:200],
                source=source,
                prediction_error=pe,
                novelty=nv,
                competence=cp,
                reward=reward,
                reasoning=reasoning,
            )
            self.record_reward(signal)

            # 更新策略
            if self._current_state:
                action = Action.from_tool_call("", {}, reasoning)
                trans = self.record_transition(
                    self._current_state, action, reward
                )
                # 尝试策略更新
                self.update_policy()

            return signal

        except Exception:
            logger.debug("奖励计算失败", exc_info=True)
            return None

    # ── 任务选择（Thompson Sampling） ──

    async def select_task(
        self,
        tasks: list[str],
    ) -> tuple[str | None, dict[str, float]]:
        """选择任务

        融合历史价值估计 + LLM实时评分 + 策略偏好。
        """
        if not tasks:
            return None, {}

        if len(tasks) == 1:
            return tasks[0], {tasks[0]: 10.0}

        scores: dict[str, float] = {}

        if self.executor:
            from lq.prompts import RL_THOMPSON_EVAL_PROMPT

            task_list = "\n".join(f"{i+1}. {t}" for i, t in enumerate(tasks[:10]))
            prompt = RL_THOMPSON_EVAL_PROMPT.format(
                task_list=task_list,
                policy_summary=self._get_policy_hint(),
                baseline_reward=f"{self.value_table.baseline:.3f}",
            )

            try:
                result = await self.executor.reply_with_history(
                    "", [{"role": "user", "content": prompt}], max_tokens=300,
                )
                from json_repair import repair_json
                data = repair_json(result.strip(), return_objects=True)

                if isinstance(data, dict) and "scores" in data:
                    score_list = data["scores"]
                    if isinstance(score_list, list):
                        for i, s in enumerate(score_list):
                            if i < len(tasks):
                                scores[tasks[i]] = float(s) if isinstance(s, (int, float)) else 5.0
                elif isinstance(data, list):
                    for i, s in enumerate(data):
                        if i < len(tasks):
                            val = s.get("score", 5) if isinstance(s, dict) else s
                            scores[tasks[i]] = float(val) if isinstance(val, (int, float)) else 5.0
            except Exception:
                logger.debug("任务评分失败，使用默认值", exc_info=True)

        # 未评分的任务用默认分
        for t in tasks:
            if t not in scores:
                scores[t] = 5.0

        # 融合历史价值
        for t in tasks:
            tid = t[:40]
            if tid in self.value_table.cluster_values:
                cached = self.value_table.cluster_values[tid] * 10
                scores[t] = 0.7 * scores[t] + 0.3 * cached

        # 加入随机噪声（Thompson Sampling的简化）
        noisy_scores = {
            t: s + random.uniform(-1.5, 1.5)
            for t, s in scores.items()
        }

        best = max(noisy_scores, key=noisy_scores.get)  # type: ignore
        logger.info(
            "RL 推荐: %s (score=%.1f, noisy=%.1f) / %d 候选",
            best[:50], scores[best], noisy_scores[best], len(tasks),
        )
        return best, scores

    def _get_policy_hint(self) -> str:
        """获取策略提示（用于注入prompt）"""
        dist = self.policy.get_category_distribution()
        top = sorted(dist.items(), key=lambda x: -x[1])[:3]
        top_str = ", ".join(f"{c.value}={p:.2f}" for c, p in top)
        return f"策略偏好: {top_str}, 探索率={self.policy.exploration_epsilon:.2f}"

    # ── 策略评估（变更守卫） ──

    async def evaluate_policy_change(
        self,
        change_description: str,
        target_file: str,
    ) -> tuple[bool, str, str]:
        """评估对SOUL.md/HEARTBEAT.md的变更"""
        if not self.executor:
            return True, "微调", "无 executor，跳过策略评估"

        from lq.prompts import RL_POLICY_EVAL_PROMPT

        prompt = RL_POLICY_EVAL_PROMPT.format(
            change_description=change_description,
            target_file=target_file,
            policy_summary=self._get_policy_hint(),
            baseline_reward=f"{self.value_table.baseline:.3f}",
            recent_trend=self._describe_trend(),
        )

        try:
            result = await self.executor.reply_with_history(
                "", [{"role": "user", "content": prompt}], max_tokens=200,
            )
            from json_repair import repair_json
            data = repair_json(result.strip(), return_objects=True)
            if not isinstance(data, dict):
                return True, "微调", "评估解析失败，默认放行"

            change_type = data.get("type", "微调")
            reason = data.get("reason", "")

            if change_type == "大改":
                logger.warning(
                    "策略守卫拒绝大改: %s → %s (%s)",
                    target_file, change_description[:60], reason,
                )
                return False, change_type, reason

            if change_type == "中调":
                logger.info("策略守卫允许中调: %s (%s)", target_file, reason)

            return True, change_type, reason

        except Exception:
            logger.debug("策略评估失败，默认放行", exc_info=True)
            return True, "微调", "评估异常，默认放行"

    # ── RL摘要 ──

    def get_rl_summary(self) -> str:
        """生成RL状态摘要"""
        lines = ["## 强化学习状态"]

        # 策略状态
        p = self.policy
        lines.append(f"- 策略版本: v{p.version}")
        lines.append(f"- 探索率: {p.exploration_epsilon:.3f}")
        lines.append(f"- 温度: {p.temperature:.2f}")
        lines.append(f"- 策略熵: {p.entropy():.3f}")

        # 策略分布
        dist = p.get_category_distribution()
        lines.append("- 策略分布:")
        for cat, prob in sorted(dist.items(), key=lambda x: -x[1]):
            if prob > 0.01:
                lines.append(f"  - {cat.value}: {prob:.3f}")

        # 价值函数
        lines.append(f"- 价值基线: {self.value_table.baseline:.3f}")
        lines.append(f"- 已探索状态: {len(self.value_table.cluster_values)}")

        # 近期奖励
        if self.reward_history:
            recent = self.reward_history[-10:]
            avg = sum(r.reward for r in recent) / len(recent)
            lines.append(f"- 近期平均奖励: {avg:.3f} ({len(recent)} 条)")
            lines.append(f"- 趋势: {self._describe_trend()}")

        # PPO更新统计
        lines.append(f"- PPO更新次数: {self.optimizer.update_count}")
        lines.append(f"- 存储transition: {len(self.transitions)} 条")

        return "\n".join(lines)

    def _describe_trend(self) -> str:
        """描述近期奖励趋势"""
        if len(self.reward_history) < 3:
            return "数据不足"
        recent = [r.reward for r in self.reward_history[-5:]]
        older = [r.reward for r in self.reward_history[-10:-5]] if len(self.reward_history) > 5 else []
        if not older:
            return "积累中"
        recent_avg = sum(recent) / len(recent)
        older_avg = sum(older) / len(older)
        diff = recent_avg - older_avg
        if diff > 0.05:
            return f"上升 (+{diff:.3f})"
        elif diff < -0.05:
            return f"下降 ({diff:.3f})"
        else:
            return "稳定"

    # ── 持久化 ──

    def save_state(self) -> None:
        """持久化RL状态"""
        data = {
            "policy": self.policy.to_dict(),
            "value_table": self.value_table.to_dict(),
            "transitions_count": len(self.transitions),
            "update_count": self.optimizer.update_count,
        }
        try:
            self._state_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            logger.warning("RL状态保存失败")

    def _load_state(self) -> None:
        """加载RL状态"""
        if not self._state_path.exists():
            return

        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
            self.policy = PolicyTheta.from_dict(data.get("policy", {}))

            vt_data = data.get("value_table", {})
            self.value_table = ValueTable(
                cluster_values=vt_data.get("cluster_values", {}),
                baseline=vt_data.get("baseline", 0.5),
            )

            logger.info(
                "RL引擎加载完成: 策略v%d, 状态数=%d",
                self.policy.version,
                len(self.value_table.cluster_values),
            )
        except Exception:
            logger.warning("RL状态加载失败，使用初始值")

    def _append_reward_log(self, signal: RewardSignal) -> None:
        """追加奖励信号到当日审计日志"""
        self._log_dir.mkdir(parents=True, exist_ok=True)
        today = datetime.now(CST).strftime("%Y-%m-%d")
        log_path = self._log_dir / f"rl-rewards-{today}.jsonl"
        try:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(signal.to_dict(), ensure_ascii=False) + "\n")
        except Exception:
            logger.debug("RL奖励日志写入失败", exc_info=True)

    # ── 属性 ──

    @property
    def reward_count(self) -> int:
        return len(self.reward_history)
