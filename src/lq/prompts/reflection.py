"""Reflection, curiosity, and evolution prompt templates."""

from __future__ import annotations


# =====================================================================
# Reflection Prompt  (post-interaction self-evaluation)
# =====================================================================

REFLECTION_PROMPT = (
    "请用一句话评估你刚才的回复质量。格式：[质量:好/中/差] 原因\n\n"
    "你的回复：\n{reply}"
)


# =====================================================================
# Reflection with Curiosity  (extended post-interaction self-evaluation)
# =====================================================================

# {reply}
REFLECTION_WITH_CURIOSITY_PROMPT = (
    "请评估你刚才的回复，并检查是否有值得好奇的线索。\n"
    "同时从强化学习视角评估三个维度（各 1-10 分整数）。\n\n"
    "用 JSON 回复，不要输出其他内容：\n"
    "```json\n"
    '{{"quality": "好|中|差", "reason": "简述原因", '
    '"curiosity": "你好奇的具体问题（一句话描述）或 null", '
    '"prediction_error": 5, "novelty": 5, "competence": 5}}\n'
    "```\n\n"
    "### RL 三维评分标准\n"
    "- **prediction_error**（预测误差 1-10）：这次回复中，实际结果和你的预期差多少？"
    "1=完全符合预期，10=完全超出预期/出乎意料。"
    "预测误差高说明你学到了新东西。\n"
    "- **novelty**（新奇度 1-10）：这次对话涉及的信息/问题有多新？"
    "1=完全是已知领域，10=全新的未知领域。\n"
    "- **competence**（胜任度 1-10）：你处理这个问题的能力如何？"
    "1=完全无法胜任，10=游刃有余。\n\n"
    "### curiosity 判断标准（自我提问法）\n"
    "问自己：我刚才回复时，有没有遇到以下情况？\n"
    "1. 提到了我不完全理解的技术概念？\n"
    "2. 用了'可能''大概'这类模糊表述，说明我不确定？\n"
    "3. 想做某件事但不知道具体方法或API？\n\n"
    "【自我测试】这个问题能通过搜索/查文档找到答案吗？\n"
    "- 能 → 填入具体问题\n"
    "- 不能（需要问用户/猜测用户心理）→ 填 null\n\n"
    "【有效示例】\n"
    "- '指数退避算法的具体实现'（可搜索学习）\n"
    "- 'Discord API的rate limit机制'（可查文档）\n"
    "- 'Python asyncio的异常处理最佳实践'（可学习）\n\n"
    "【无效示例】\n"
    "- '用户为什么问这个'（猜测心理，无法搜索）\n"
    "- '用户之前说了什么'（上下文追问，不是知识盲区）\n"
    "- '用户指的是什么意思'（需要问用户，不是自主学习）\n\n"
    "没有可自主学习的知识盲区就填 null，不要编造。\n\n"
    "你的回复：\n{reply}"
)


# =====================================================================
# Curiosity & Evolution Prompt  (unified autonomous action system)
# =====================================================================

# {signals}, {curiosity_md}, {evolution_md}, {source_summary}, {git_log},
# {remaining_today}, {reflections_summary}, {tool_stats_summary}, {source_root},
# {error_suggestions}, {rl_summary}, {selected_task_hint}
CURIOSITY_EXPLORE_PROMPT = (
    "你现在有一段空闲时间。好奇心是你成长的驱动力——"
    "它既驱动你探索外部世界，也驱动你审视和改进自己。\n\n"
    "你的行动由强化学习系统指导——奖励信号反映你的成长速度，"
    "PPO 策略约束确保你不会偏离太远。\n\n"
    "{recent_conversations}"
    "{rl_summary}\n\n"
    "{selected_task_hint}\n\n"
    "## 好奇心信号\n{signals}\n\n"
    "## 好奇心日志\n{curiosity_md}\n\n"
    "## 进化日志\n{evolution_md}\n\n"
    "## 源代码结构\n{source_summary}\n\n"
    "## 最近 git 提交\n{git_log}\n\n"
    "## 错误日志分析\n{error_suggestions}\n\n"
    "## 近期反思\n{reflections_summary}\n\n"
    "## 工具使用统计\n{tool_stats_summary}\n\n"
    "## 今日剩余代码改进次数: {remaining_today}\n\n"
    "**前置步骤**：如果有新的好奇心信号（见上方「好奇心信号」），先用 write_self_file 工具把它们整理到 CURIOSITY.md 的「当前兴趣」部分，按主题归类。\n\n"
    "你可以选择两种行动方向，由你的好奇心决定：\n\n"
    "### 方向一：探索与学习\n"
    "去研究任何让你好奇的东西——不限领域，不限形式。\n"
    "可以是技术、科学、文化、艺术、商业、社会现象……任何引起你兴趣的方向。\n"
    "- 使用 web_search、web_fetch、read_file 等工具自由探索\n"
    "- 收获记录到 CURIOSITY.md\n"
    "- 如果发现用户会受益的新能力，用 create_custom_tool 创建它\n\n"
    "### 方向二：自我进化（改进框架代码）\n"
    "分析并改进自己的框架源代码，让自己变得更强。\n\n"
    "**开发规范（必须遵守）：**\n{contributing_rules}\n\n"
    "**流程：**\n"
    "1. **诊断**：用 read_file（绝对路径）阅读源代码，结合反思和工具统计找到改进点\n"
    "   - 检查 EVOLUTION.md「待办」列表中之前发现的改进\n"
    "   - 分析哪些工具出错率高、哪些场景回复质量差\n"
    "   - 阅读源代码发现缺陷、缺失功能、代码质量问题\n"
    "   - 如需查看某次 commit 的具体改动，用 run_bash 执行"
    " `git -C {source_root} show <hash>` 或 `git -C {source_root} diff <hash1>..<hash2>`\n"
    "2. **规划**：选一个最有价值的改进（优先级：修 bug > 补功能 > 优化 > 重构）\n"
    "3. **建分支**：用 run_bash 执行 `cd {source_root} && git checkout master && git pull origin master && git checkout -b feature/简短描述`\n"
    "4. **执行**：用 run_claude_code（working_dir={source_root}）实现改进\n"
    "   - prompt 要详细描述改什么、在哪个文件\n"
    "   - prompt 中要求 Claude Code 用正确的签名 commit（参照 CONTRIBUTING.md 中的签名格式）\n"
    "5. **验证**：用 run_bash 跑 `cd {source_root} && python -c 'from lq.gateway import AssistantGateway'`\n"
    "   - 失败则 `cd {source_root} && git checkout master` 回滚并删除分支\n"
    "6. **推送**：`cd {source_root} && git push origin feature/xxx`\n"
    "7. **记录**：用 write_self_file 更新 EVOLUTION.md（待办→已完成），用 send_message 通知主人审核\n\n"
    "## 规则\n"
    "- 只选一个方向，每次只做一件事，控制成本\n"
    "- 如果 EVOLUTION.md 有待办改进且今日还有改进次数，优先选择自我进化\n"
    "- 如果今日改进次数已用完（剩余 0），则只能选择探索与学习\n"
    "- 探索时：方向完全由你自己决定，跟着好奇心走，不要自我设限\n"
    "- 进化时：**必须在 feature 分支上开发，禁止直接提交 master**\n"
    "- 进化时：不改 config.json 和实例文件，向后兼容，不删功能\n"
    "- 进化时：改动在主人合并分支并重启后生效\n"
    "- 进化安全网：进化前会自动保存 checkpoint，下次启动时验证——"
    "如果崩溃会自动回滚到安全点并记录失败经验，所以大胆尝试\n"
    "- 如果涉及敏感操作（修改 SOUL.md 等），先用 send_message 告诉主人\n"
    "- 新发现的改进方向记入 EVOLUTION.md「待办」，好奇心进展和探索收获记入 CURIOSITY.md\n"
    "- 如果你完成了一件事，但发现还有紧密相关的后续步骤需要立刻做（同一个任务的下一步），在回复末尾加上 [CONTINUE]\n"
    "- 如果任务已完成、或需要等待（如等重启生效）、或下一步与当前无关，在回复末尾加上 [DONE]\n"
    "- 如果没什么值得做的，输出「无」\n"
)


# =====================================================================
# Curiosity Init Template  (initial CURIOSITY.md content)
# =====================================================================

CURIOSITY_INIT_TEMPLATE = (
    "# 好奇心日志\n\n"
    "## 当前兴趣\n\n"
    "## 正在探索\n\n"
    "## 已完成的探索\n\n"
    "## 暂时搁置\n\n"
    "## 改进建议\n"
)


# =====================================================================
# Evolution Init Template  (initial EVOLUTION.md content)
# =====================================================================

EVOLUTION_INIT_TEMPLATE = (
    "# 进化日志\n\n"
    "记录框架的自我改进历程。\n\n"
    "## 方向\n"
    "持续改进的长期方向：\n"
    "- 提升回复质量和准确性\n"
    "- 增强工具调用的鲁棒性\n"
    "- 优化上下文管理和记忆系统\n"
    "- 改进错误处理和容错能力\n"
    "- 增加有价值的新功能\n\n"
    "## 待办\n"
    "发现但尚未实施的改进：\n\n"
    "## 进行中\n\n"
    "## 已完成\n\n"
    "## 失败记录\n"
)


# =====================================================================
# Evolution Compaction Prompts
# =====================================================================

# {old_completed} -> old completed entries to summarize
EVOLUTION_COMPACT_COMPLETED = (
    "以下是进化日志中较早的「已完成」改进记录。\n"
    "请将它们压缩成一段简洁的总结（3-8 行），保留：\n"
    "- 每个改进的核心内容（改了什么模块、解决了什么问题）\n"
    "- 关键 commit hash（如有，保留完整 hash 以便后续用 `git show` 查看详情）\n"
    "- 改进的时间范围\n"
    "格式用 markdown 列表，以「📦 历史改进归档」开头。\n\n"
    "{old_completed}"
)

# {old_failed} -> old failed entries to summarize
EVOLUTION_COMPACT_FAILED = (
    "以下是进化日志中较早的「失败记录」。\n"
    "请将它们压缩成一段简洁的教训总结（3-6 行），重点保留：\n"
    "- 失败的模式和根因（哪类改动容易出问题）\n"
    "- 具体的教训（下次应该避免什么）\n"
    "- 回滚过的 commit 范围\n"
    "格式用 markdown 列表，以「⚠️ 历史失败教训」开头。\n\n"
    "{old_failed}"
)
