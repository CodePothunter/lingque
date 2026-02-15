# 将自定义工具升级为内置工具 - 分析报告

## 背景
捏捏在工作区创建了 knowledge_card 工具，现在想让它成为灵雀框架的内置工具，让所有实例都能使用。

## 内置工具注册机制

框架内置工具注册分散在 router.py 的三个位置：

1. `TOOLS` 列表（77-462行）- 工具定义
2. `_build_all_tools()`（1080-1085行）- 合并工具列表
3. `_execute_tool()`（1281-1500行）- 执行分派

## 两种升级路径

### 路径 A：直接硬编码（当前框架做法）
**修改文件**：
- src/lq/router.py：添加定义 + 执行分支
- src/lq/prompts.py：添加描述常量

**优点**：最小改动，约30-40行代码
**缺点**：router.py继续膨胀

### 路径 B：框架级内置插件目录（推荐长期方案）
**新增/修改**：
- src/lq/builtin_tools/ - 新目录
- src/lq/builtin_tools/__init__.py - 扫描加载
- src/lq/builtin_tools/knowledge_card.py - 工具文件
- src/lq/router.py - 合并和分派逻辑

**优点**：可扩展，工具独立可测试
**缺点**：改动更大，引入新机制

## 建议
- 短期：路径A，快速验证
- 长期：考虑路径B，建立可扩展的内置工具体系

## knowledge_card 工具说明
将知识点整理成结构化卡片，保存到 knowledge/ 目录。支持：
- title, category, summary, key_points（必填）
- examples, related_topics, source（可选）

---
*捏捏 - 2026-02-15*
