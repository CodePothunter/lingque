"""自然语言强化学习引擎 — Natural Language RL Framework

基于 LLM 评估 + 数学公式的混合强化学习系统。
核心原则：用 LLM 评估一切，用公式计算可计算的部分。

人类做强化学习不会掏计算器算 KL 散度，但会算「这次比上次好多少」。
灵雀也一样——LLM 评估 + 简单数学。

模块职责：
- 奖励函数：三维 LLM 评估（预测误差、新奇度、胜任度）+ 加权公式
- 价值函数：Bellman 方程 + LLM 估值
- PPO 策略更新：clipped surrogate objective + 自然语言约束
- Thompson Sampling：任务选择的探索-利用平衡
- 持久化：奖励历史、价值估计、策略版本
"""

from __future__ import annotations

import json
import logging
import random
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from lq.executor.api import DirectAPIExecutor

logger = logging.getLogger(__name__)

CST = timezone(timedelta(hours=8))

# ── RL 超参数 ──

REWARD_ALPHA = 0.4         # 预测误差权重
REWARD_BETA = 0.4          # 新奇度权重
REWARD_GAMMA = 0.2         # 胜任度权重
VALUE_DISCOUNT = 0.7       # Bellman 折扣因子 γ
THOMPSON_NOISE_RANGE = 1.5 # Thompson Sampling 噪声振幅
MAX_REWARD_HISTORY = 100   # 保留的奖励历史条数
PPO_CLIP_EPSILON = 0.2     # PPO clipping 参数 ε
PPO_LEARNING_RATE = 0.1    # 策略更新步长 α


# ── 数据结构 ──

@dataclass
class RewardSignal:
    """单次奖励信号"""
    timestamp: float
    context: str            # 被评估的内容摘要
    source: str             # "reflection" | "exploration" | "evolution"
    prediction_error: int   # 1-10，预测误差
    novelty: int            # 1-10，新奇度
    competence: int         # 1-10，胜任度
    reward: float           # R = (α*PE + β*NV + γ*CP) / 10
    reasoning: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> RewardSignal:
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class TaskValueEstimate:
    """任务的价值估计（V(s)）"""
    task_id: str
    description: str
    immediate_value: float = 0.0  # 即时价值 [0, 1]
    future_potential: float = 0.0  # 未来潜力 [0, 1]
    value: float = 0.0            # V(s) = IV + γ*FP
    last_updated: float = 0.0
    attempt_count: int = 0
    cumulative_reward: float = 0.0
    avg_reward: float = 0.0       # 滚动平均奖励

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> TaskValueEstimate:
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class PolicyState:
    """PPO 策略状态

    自然语言版 PPO：
    - 策略 π(a|s) 由 LLM 输出的行动偏好表示
    - advantage 通过当前奖励与基线的差值估计
    - clipped ratio 通过自然语言约束级别实现
    """
    version: int = 1
    baseline_reward: float = 0.5  # 奖励基线（滚动平均）
    update_count: int = 0         # 策略更新次数
    constraint_level: str = "正常"  # 当前约束级别：宽松/正常/谨慎
    last_kl_estimate: float = 0.0  # 近似 KL 散度
    soul_hash: str = ""            # SOUL.md 哈希，检测漂移

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> PolicyState:
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# ── 核心引擎 ──

class ReinforcementLearner:
    """自然语言强化学习器

    集成到灵雀心跳-好奇心-进化循环中，通过 RL 信号驱动智能体成长。

    生命周期（由 AssistantGateway 管理）：
    1. 启动时实例化，注入 executor
    2. 每次私聊反思后调用 record_reward_from_reflection()
    3. 每次心跳中调用 select_task_thompson() 选择任务
    4. 任务执行后调用 compute_reward() 计算奖励
    5. SOUL/HEARTBEAT 变更时调用 evaluate_policy_change() 做 PPO 守卫
    """

    def __init__(self, workspace: Path, executor: DirectAPIExecutor | None = None) -> None:
        self.workspace = workspace
        self.executor = executor
        self._state_path = workspace / "rl-state.json"
        self._log_dir = workspace / "logs"

        # RL 状态
        self._reward_history: list[RewardSignal] = []
        self._task_values: dict[str, TaskValueEstimate] = {}
        self._policy = PolicyState()
        self._load_state()

    # ── 状态持久化 ──

    def _load_state(self) -> None:
        if not self._state_path.exists():
            return
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
            self._reward_history = [
                RewardSignal.from_dict(r) for r in data.get("rewards", [])
            ][-MAX_REWARD_HISTORY:]
            self._task_values = {
                k: TaskValueEstimate.from_dict(v)
                for k, v in data.get("task_values", {}).items()
            }
            if "policy" in data:
                self._policy = PolicyState.from_dict(data["policy"])
        except Exception:
            logger.warning("RL 状态文件损坏，重置")

    def save_state(self) -> None:
        """持久化 RL 状态到磁盘"""
        data = {
            "rewards": [r.to_dict() for r in self._reward_history[-MAX_REWARD_HISTORY:]],
            "task_values": {k: v.to_dict() for k, v in self._task_values.items()},
            "policy": self._policy.to_dict(),
        }
        try:
            self._state_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            logger.warning("RL 状态保存失败")

    def _append_reward_log(self, signal: RewardSignal) -> None:
        """追加奖励信号到当日审计日志"""
        self._log_dir.mkdir(parents=True, exist_ok=True)
        today = datetime.now(CST).strftime("%Y-%m-%d")
        log_path = self._log_dir / f"rl-rewards-{today}.jsonl"
        try:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(signal.to_dict(), ensure_ascii=False) + "\n")
        except Exception:
            logger.debug("RL 奖励日志写入失败", exc_info=True)

    # ── 奖励函数 ──
    # R = (α * prediction_error + β * novelty + γ * competence) / 10

    @staticmethod
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

    def record_reward(self, signal: RewardSignal) -> None:
        """记录一个奖励信号并更新内部状态"""
        self._reward_history.append(signal)
        if len(self._reward_history) > MAX_REWARD_HISTORY:
            self._reward_history = self._reward_history[-MAX_REWARD_HISTORY:]
        self._append_reward_log(signal)
        self._update_baseline(signal.reward)
        self.save_state()
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
        """从反思结果中提取并记录奖励信号（零额外 LLM 调用）"""
        reward = self.calculate_reward(prediction_error, novelty, competence)
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

    # ── PPO 策略更新 ──
    # 自然语言版 Proximal Policy Optimization

    def _update_baseline(self, new_reward: float) -> None:
        """指数移动平均更新奖励基线（PPO 的 value baseline）"""
        alpha = 0.1  # EMA 衰减系数
        self._policy.baseline_reward = (
            (1 - alpha) * self._policy.baseline_reward + alpha * new_reward
        )

    def compute_advantage(self, reward: float) -> float:
        """计算优势函数 A(s,a) = R - V_baseline

        PPO 的核心：通过 advantage 判断当前行动是否优于平均水平。
        """
        return reward - self._policy.baseline_reward

    def ppo_clip_ratio(self, advantage: float) -> float:
        """PPO clipped surrogate objective

        L^CLIP = min(r * A, clip(r, 1-ε, 1+ε) * A)

        自然语言简化版：
        - advantage > 0 → 行动好于基线，ratio 鼓励但不超过 1+ε
        - advantage < 0 → 行动差于基线，ratio 抑制但不低于 1-ε
        - |advantage| 大 → 变更幅度大，需要 clip 约束

        Returns:
            clipped ratio ∈ [1-ε, 1+ε]
        """
        # 将 advantage 归一化到 [-1, 1] 范围
        normalized_adv = max(-1.0, min(1.0, advantage * 2))
        # 计算未裁剪的 ratio（模拟策略更新方向）
        raw_ratio = 1.0 + PPO_LEARNING_RATE * normalized_adv
        # PPO clip
        return max(1 - PPO_CLIP_EPSILON, min(1 + PPO_CLIP_EPSILON, raw_ratio))

    def ppo_update_constraint(self, reward: float) -> str:
        """基于 PPO 的策略约束更新

        根据奖励信号和优势函数调整约束级别，
        相当于 PPO 中更新策略参数 θ 的自然语言版本。

        Returns:
            更新后的约束级别
        """
        advantage = self.compute_advantage(reward)
        ratio = self.ppo_clip_ratio(advantage)

        # 近似 KL 散度：连续正/负 advantage 的累积
        recent_rewards = [r.reward for r in self._reward_history[-10:]]
        if len(recent_rewards) >= 3:
            recent_adv = [self.compute_advantage(r) for r in recent_rewards]
            # KL ≈ 连续偏移的一致性（全正或全负说明策略在漂移）
            pos_count = sum(1 for a in recent_adv if a > 0)
            neg_count = sum(1 for a in recent_adv if a < 0)
            drift_ratio = max(pos_count, neg_count) / len(recent_adv)
            self._policy.last_kl_estimate = drift_ratio
        else:
            drift_ratio = 0.5

        # 根据 PPO 信号调整约束级别
        old_level = self._policy.constraint_level
        if drift_ratio > 0.8 and advantage < 0:
            # 持续负 advantage + 高漂移 → 收紧策略
            self._policy.constraint_level = "谨慎"
        elif drift_ratio > 0.8 and advantage > 0:
            # 持续正 advantage + 高漂移 → 轻微放松但保持警惕
            self._policy.constraint_level = "正常"
        elif abs(advantage) < 0.1:
            # advantage 接近 0 → 稳态
            self._policy.constraint_level = "正常"
        elif advantage > 0.3:
            # 大幅正 advantage → 可以放松探索
            self._policy.constraint_level = "宽松"

        if old_level != self._policy.constraint_level:
            self._policy.update_count += 1
            logger.info(
                "PPO 策略更新 #%d: %s → %s (adv=%.3f, ratio=%.3f, KL≈%.2f)",
                self._policy.update_count, old_level,
                self._policy.constraint_level, advantage, ratio, drift_ratio,
            )

        self.save_state()
        return self._policy.constraint_level

    async def evaluate_policy_change(
        self,
        change_description: str,
        target_file: str,
    ) -> tuple[bool, str, str]:
        """PPO 策略守卫：评估对 SOUL.md/HEARTBEAT.md 的变更

        用自然语言「微调/中调/大改」代替 PPO 的 clip(ε=0.2)：
        - 微调（ε 内）→ 允许
        - 中调（ε 边界）→ 允许但记录
        - 大改（超出 ε）→ 拒绝，要求重新思考

        Returns:
            (allowed, change_type, reason)
        """
        if not self.executor:
            # 无 executor 时宽松放行
            return True, "微调", "无 executor，跳过策略评估"

        from lq.prompts import RL_POLICY_EVAL_PROMPT

        prompt = RL_POLICY_EVAL_PROMPT.format(
            change_description=change_description,
            target_file=target_file,
            constraint_level=self._policy.constraint_level,
            baseline_reward=f"{self._policy.baseline_reward:.3f}",
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
                    "PPO 策略守卫拒绝大改: %s → %s (%s)",
                    target_file, change_description[:60], reason,
                )
                return False, change_type, reason

            if change_type == "中调":
                logger.info("PPO 策略守卫允许中调: %s (%s)", target_file, reason)

            return True, change_type, reason

        except Exception:
            logger.debug("策略评估失败，默认放行", exc_info=True)
            return True, "微调", "评估异常，默认放行"

    # ── 价值函数 ──
    # V(s) = immediate_value/10 + γ * future_potential/10

    async def estimate_value(self, task_description: str) -> TaskValueEstimate:
        """估计任务的价值函数 V(s)

        V(s) = immediate_value/10 + γ * future_potential/10

        使用 LLM 评估即时价值和未来潜力，Bellman 公式计算总价值。
        """
        task_id = task_description[:40]

        if not self.executor:
            return TaskValueEstimate(task_id=task_id, description=task_description)

        from lq.prompts import RL_VALUE_EVAL_PROMPT

        prompt = RL_VALUE_EVAL_PROMPT.format(
            task_description=task_description,
            baseline_reward=f"{self._policy.baseline_reward:.3f}",
        )

        try:
            result = await self.executor.reply_with_history(
                "", [{"role": "user", "content": prompt}], max_tokens=200,
            )
            from json_repair import repair_json
            data = repair_json(result.strip(), return_objects=True)
            if not isinstance(data, dict):
                return TaskValueEstimate(task_id=task_id, description=task_description)

            iv = max(1, min(10, int(data.get("immediate_value", 5)))) / 10
            fp = max(1, min(10, int(data.get("future_potential", 5)))) / 10
            value = iv + VALUE_DISCOUNT * fp

            estimate = TaskValueEstimate(
                task_id=task_id,
                description=task_description,
                immediate_value=iv,
                future_potential=fp,
                value=value,
                last_updated=time.time(),
            )

            # 更新缓存
            self._task_values[task_id] = estimate
            self.save_state()

            logger.info(
                "RL 价值估计: V(s)=%.3f (IV=%.1f, FP=%.1f) %s",
                value, iv, fp, task_description[:50],
            )
            return estimate

        except Exception:
            logger.debug("价值估计失败", exc_info=True)
            return TaskValueEstimate(task_id=task_id, description=task_description)

    # ── Thompson Sampling 任务选择 ──

    async def select_task_thompson(
        self,
        tasks: list[str],
    ) -> tuple[str | None, dict[str, float]]:
        """Thompson Sampling 选择最优任务

        for task in tasks:
            scores[task] = LLM评估("值得探索吗？1-10") + noise
        best = max(scores)

        融合历史价值估计 + LLM 实时评分 + 随机噪声。

        Returns:
            (selected_task, {task: score})
        """
        if not tasks:
            return None, {}

        if len(tasks) == 1:
            return tasks[0], {tasks[0]: 10.0}

        scores: dict[str, float] = {}

        if self.executor:
            # LLM 批量评分
            from lq.prompts import RL_THOMPSON_EVAL_PROMPT

            task_list = "\n".join(f"{i+1}. {t}" for i, t in enumerate(tasks[:10]))
            prompt = RL_THOMPSON_EVAL_PROMPT.format(
                task_list=task_list,
                constraint_level=self._policy.constraint_level,
                baseline_reward=f"{self._policy.baseline_reward:.3f}",
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
                logger.debug("Thompson 评分失败，使用均匀分布", exc_info=True)

        # 未评分的任务用默认分
        for t in tasks:
            if t not in scores:
                scores[t] = 5.0

        # 融合历史价值
        for t in tasks:
            tid = t[:40]
            if tid in self._task_values:
                cached = self._task_values[tid]
                # 混合 LLM 实时评分和历史价值（7:3 权重）
                scores[t] = 0.7 * scores[t] + 0.3 * (cached.value * 10)

        # Thompson Sampling: 加入随机噪声
        noisy_scores = {
            t: s + random.uniform(-THOMPSON_NOISE_RANGE, THOMPSON_NOISE_RANGE)
            for t, s in scores.items()
        }

        best = max(noisy_scores, key=noisy_scores.get)  # type: ignore[arg-type]
        logger.info(
            "RL Thompson 推荐: %s (score=%.1f, noisy=%.1f) / %d 候选",
            best[:50], scores[best], noisy_scores[best], len(tasks),
        )
        return best, scores

    # ── 自主行动后的奖励计算（需要 LLM） ──

    async def compute_reward(
        self,
        action_description: str,
        action_result: str,
        source: str = "exploration",
    ) -> RewardSignal | None:
        """对自主行动结果计算奖励信号

        LLM 评估三维 → 公式计算 R → 记录 + PPO 更新
        """
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

            reward = self.calculate_reward(pe, nv, cp)
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

            # PPO 策略更新
            self.ppo_update_constraint(reward)

            # 更新任务累积奖励
            task_id = action_description[:40]
            if task_id in self._task_values:
                tv = self._task_values[task_id]
                tv.attempt_count += 1
                tv.cumulative_reward += reward
                tv.avg_reward = tv.cumulative_reward / tv.attempt_count
                self.save_state()

            return signal

        except Exception:
            logger.debug("奖励计算失败", exc_info=True)
            return None

    # ── 状态表示 ──

    def build_state_representation(
        self,
        memory_summary: str = "",
        curiosity_summary: str = "",
        context: str = "",
    ) -> str:
        """构建自然语言状态 s

        s = "当前对话上下文 + MEMORY.md 摘要 + CURIOSITY.md 当前任务"
        """
        parts = []
        if context:
            parts.append(f"当前上下文: {context[:200]}")
        if memory_summary:
            parts.append(f"长期记忆摘要: {memory_summary[:200]}")
        if curiosity_summary:
            parts.append(f"当前好奇心: {curiosity_summary[:200]}")
        return " | ".join(parts) if parts else "初始状态"

    # ── RL 摘要（注入 prompt） ──

    def get_rl_summary(self) -> str:
        """生成 RL 状态摘要，供注入到自主行动 prompt 中"""
        lines = ["## 强化学习状态"]

        # 策略状态
        p = self._policy
        lines.append(f"- 策略版本: v{p.version} (更新 {p.update_count} 次)")
        lines.append(f"- 约束级别: {p.constraint_level}")
        lines.append(f"- 奖励基线: {p.baseline_reward:.3f}")
        lines.append(f"- 近似 KL 散度: {p.last_kl_estimate:.2f}")

        # 近期奖励趋势
        if self._reward_history:
            recent = self._reward_history[-10:]
            avg = sum(r.reward for r in recent) / len(recent)
            lines.append(f"- 近期平均奖励: {avg:.3f} ({len(recent)} 条)")
            lines.append(f"- 趋势: {self._describe_trend()}")

            # 各来源分布
            sources: dict[str, list[float]] = {}
            for r in self._reward_history[-30:]:
                sources.setdefault(r.source, []).append(r.reward)
            for src, rewards in sources.items():
                lines.append(
                    f"  - {src}: 平均 {sum(rewards)/len(rewards):.3f} ({len(rewards)} 次)"
                )

        # 高价值任务
        if self._task_values:
            sorted_tasks = sorted(
                self._task_values.values(),
                key=lambda t: t.value,
                reverse=True,
            )[:5]
            lines.append("- 高价值任务:")
            for t in sorted_tasks:
                lines.append(f"  - V={t.value:.2f} | {t.description[:40]}")

        # PPO 更新指示
        advantage_hint = ""
        if self._reward_history:
            last_r = self._reward_history[-1].reward
            adv = self.compute_advantage(last_r)
            if adv > 0.2:
                advantage_hint = "最近行动优于基线，可继续当前方向"
            elif adv < -0.2:
                advantage_hint = "最近行动低于基线，建议调整方向"
            else:
                advantage_hint = "行动质量接近基线水平"
        if advantage_hint:
            lines.append(f"- PPO 信号: {advantage_hint}")

        return "\n".join(lines)

    def _describe_trend(self) -> str:
        """描述近期奖励趋势"""
        if len(self._reward_history) < 3:
            return "数据不足"
        recent = [r.reward for r in self._reward_history[-5:]]
        older = [r.reward for r in self._reward_history[-10:-5]] if len(self._reward_history) > 5 else []
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

    @property
    def reward_count(self) -> int:
        return len(self._reward_history)

    @property
    def policy(self) -> PolicyState:
        return self._policy
