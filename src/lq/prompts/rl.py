"""Reinforcement learning evaluation prompt templates."""

from __future__ import annotations


# =====================================================================
# Reinforcement Learning Prompts
# =====================================================================

# 自主行动奖励评估
# {action_description}, {action_result}, {source}
RL_REWARD_EVAL_PROMPT = (
    "你刚刚完成了一次自主行动，请从强化学习的角度评估它。\n\n"
    "行动类型: {source}\n"
    "行动描述: {action_description}\n"
    "行动结果: {action_result}\n\n"
    "请评估以下三个维度（各 1-10 整数），并用 JSON 回复：\n"
    "```json\n"
    '{{"prediction_error": 5, "novelty": 5, "competence": 5, "reasoning": "简述评分理由"}}\n'
    "```\n\n"
    "- **prediction_error**（预测误差）：结果和你预期的差多少？1=完全预料之中，10=完全出乎意料\n"
    "- **novelty**（新奇度）：这次行动涉及的知识/领域有多新？1=旧知识，10=全新领域\n"
    "- **competence**（胜任度）：你完成这个任务的质量如何？1=完全失败，10=完美完成\n\n"
    "只输出 JSON，不要其他文字。"
)

# 任务选择评分（融合策略偏好）
# {task_list}, {policy_summary}, {baseline_reward}
RL_THOMPSON_EVAL_PROMPT = (
    "你面前有以下候选任务，请为每个任务评估其探索价值。\n\n"
    "候选任务:\n{task_list}\n\n"
    "当前RL策略状态: {policy_summary}\n"
    "奖励基线: {baseline_reward}\n\n"
    "请为每个任务打分（1-10），用 JSON 回复：\n"
    "```json\n"
    '{{"scores": [7, 5, 8, ...]}}\n'
    "```\n\n"
    "评分标准:\n"
    "- 与你的好奇心和成长方向的匹配度\n"
    "- 预期能带来的学习收益\n"
    "- 完成难度与你当前能力的匹配\n"
    "- 对用户和自身的长期价值\n"
    "- 与当前策略偏好的契合度\n\n"
    "只输出 JSON，不要其他文字。"
)

# PPO 策略守卫（变更评估）
# {change_description}, {target_file}, {policy_summary}, {baseline_reward}, {recent_trend}
RL_POLICY_EVAL_PROMPT = (
    "一个自主行动想要修改你的核心文件，请评估这个变更的幅度。\n\n"
    "目标文件: {target_file}\n"
    "变更内容: {change_description}\n"
    "当前策略状态: {policy_summary}\n"
    "奖励基线: {baseline_reward}\n"
    "近期趋势: {recent_trend}\n\n"
    "请判断这个变更的类型，用 JSON 回复：\n"
    "```json\n"
    '{{"type": "微调|中调|大改", "reason": "判断理由"}}\n'
    "```\n\n"
    "- **微调**: 小幅调整，如修正措辞、补充细节、调整格式\n"
    "- **中调**: 中等幅度，如新增章节、修改行为准则、调整优先级\n"
    "- **大改**: 大幅改动，如重写人设、改变核心价值观、删除大量内容\n\n"
    "PPO 原则: 策略变化不宜过大（clip ε=0.2），大改应被拒绝要求重新思考。\n\n"
    "只输出 JSON，不要其他文字。"
)
