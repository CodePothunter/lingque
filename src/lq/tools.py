"""自定义工具插件系统 — 运行时创建、校验、管理 LLM 工具"""

from __future__ import annotations

import ast
import importlib.util
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

# 禁止在自定义工具中 import 的模块
BLOCKED_IMPORTS = frozenset({
    "os", "subprocess", "shutil", "sys", "socket", "ctypes",
    "signal", "multiprocessing", "threading",
})


@dataclass
class CustomTool:
    name: str
    description: str
    input_schema: dict
    module_path: Path
    execute_fn: Callable  # async
    enabled: bool = True


class ToolRegistry:
    """管理工作区 tools/ 目录下的自定义工具插件。"""

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace
        self.tools_dir = workspace / "tools"
        self.tools_dir.mkdir(parents=True, exist_ok=True)
        self._registry_path = self.tools_dir / "__registry__.json"
        self._tools: dict[str, CustomTool] = {}
        self._disabled: set[str] = set()
        self._load_registry_state()

    # ── 公共 API ──

    def load_all(self) -> None:
        """扫描 tools/ 目录，加载所有 .py 工具文件。"""
        for py_file in sorted(self.tools_dir.glob("*.py")):
            if py_file.name.startswith("_"):
                continue
            try:
                tool = self._load_tool_file(py_file)
                tool.enabled = tool.name not in self._disabled
                self._tools[tool.name] = tool
                logger.info("已加载自定义工具: %s (%s)", tool.name, "启用" if tool.enabled else "禁用")
            except Exception:
                logger.exception("加载工具失败: %s", py_file.name)

    def get_definitions(self) -> list[dict]:
        """返回所有已启用工具的定义（供 LLM 使用）。"""
        defs = []
        for tool in self._tools.values():
            if tool.enabled:
                defs.append({
                    "name": tool.name,
                    "description": tool.description,
                    "input_schema": tool.input_schema,
                })
        return defs

    async def execute(self, name: str, input_data: dict, context: dict) -> dict:
        """执行指定的自定义工具。"""
        tool = self._tools.get(name)
        if not tool:
            return {"success": False, "error": f"未知自定义工具: {name}"}
        if not tool.enabled:
            return {"success": False, "error": f"工具已禁用: {name}"}
        try:
            result = await tool.execute_fn(input_data, context)
            if not isinstance(result, dict):
                result = {"success": True, "result": str(result)}
            return result
        except Exception as e:
            logger.exception("自定义工具执行失败: %s", name)
            return {"success": False, "error": str(e)}

    def validate_code(self, code: str) -> list[str]:
        """AST 静态校验工具代码，返回错误列表（空列表 = 通过）。"""
        errors: list[str] = []

        # 1. 语法检查
        try:
            tree = ast.parse(code)
        except SyntaxError as e:
            errors.append(f"语法错误 (行 {e.lineno}): {e.msg}")
            return errors

        # 2. 检查 TOOL_DEFINITION 是否存在
        has_tool_def = False
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == "TOOL_DEFINITION":
                        has_tool_def = True
        if not has_tool_def:
            errors.append("缺少 TOOL_DEFINITION 变量")

        # 3. 检查 execute 函数是否存在
        has_execute = False
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name == "execute":
                    has_execute = True
        if not has_execute:
            errors.append("缺少 execute() 函数")

        # 4. 安全检查：遍历 AST 查找危险 import
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    mod_name = alias.name.split(".")[0]
                    if mod_name in BLOCKED_IMPORTS:
                        errors.append(f"禁止 import: {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    mod_name = node.module.split(".")[0]
                    if mod_name in BLOCKED_IMPORTS:
                        errors.append(f"禁止 from {node.module} import ...")

        return errors

    def create_tool(self, name: str, code: str) -> dict:
        """校验代码 → 写入文件 → 加载 → 注册。"""
        # 清理名称
        safe_name = self._sanitize_name(name)
        if not safe_name:
            return {"success": False, "error": "工具名称无效（仅支持字母、数字、下划线）"}

        # 校验代码
        errors = self.validate_code(code)
        if errors:
            return {"success": False, "errors": errors}

        # 写入文件
        file_path = self.tools_dir / f"{safe_name}.py"
        file_path.write_text(code, encoding="utf-8")

        # 尝试加载
        try:
            tool = self._load_tool_file(file_path)
            # 确保 TOOL_DEFINITION 中的 name 与文件名一致
            if tool.name != safe_name:
                logger.warning(
                    "工具定义中的 name '%s' 与文件名 '%s' 不一致，已使用文件名",
                    tool.name, safe_name,
                )
                tool.name = safe_name
            tool.enabled = True
            self._tools[safe_name] = tool
            self._disabled.discard(safe_name)
            self._save_registry_state()
            logger.info("已创建自定义工具: %s", safe_name)
            return {"success": True, "name": safe_name, "message": f"工具 {safe_name} 已创建并加载"}
        except Exception as e:
            # 加载失败，清理文件
            logger.exception("工具加载失败，清理文件: %s", file_path)
            file_path.unlink(missing_ok=True)
            return {"success": False, "error": f"工具加载失败: {e}"}

    def delete_tool(self, name: str) -> dict:
        """删除工具文件并从注册表中移除。"""
        safe_name = self._sanitize_name(name)
        if safe_name not in self._tools:
            return {"success": False, "error": f"工具不存在: {name}"}

        tool = self._tools.pop(safe_name)
        self._disabled.discard(safe_name)
        if tool.module_path.exists():
            tool.module_path.unlink()
        self._save_registry_state()
        logger.info("已删除自定义工具: %s", safe_name)
        return {"success": True, "message": f"工具 {safe_name} 已删除"}

    def toggle_tool(self, name: str, enabled: bool) -> dict:
        """启用或禁用工具。"""
        safe_name = self._sanitize_name(name)
        tool = self._tools.get(safe_name)
        if not tool:
            return {"success": False, "error": f"工具不存在: {name}"}

        tool.enabled = enabled
        if enabled:
            self._disabled.discard(safe_name)
        else:
            self._disabled.add(safe_name)
        self._save_registry_state()
        state = "启用" if enabled else "禁用"
        logger.info("工具 %s 已%s", safe_name, state)
        return {"success": True, "message": f"工具 {safe_name} 已{state}"}

    def list_tools(self) -> list[dict]:
        """返回所有已注册工具的概况。"""
        result = []
        for name, tool in sorted(self._tools.items()):
            result.append({
                "name": name,
                "description": tool.description,
                "enabled": tool.enabled,
                "file": str(tool.module_path),
            })
        return result

    def has_tool(self, name: str) -> bool:
        """检查工具是否已注册（无论是否启用）。"""
        return name in self._tools

    # ── 内部方法 ──

    def _sanitize_name(self, name: str) -> str:
        """清理工具名称：仅保留字母、数字、下划线，转小写。"""
        cleaned = re.sub(r"[^a-zA-Z0-9_]", "", name).lower()
        # 不能以数字开头
        if cleaned and cleaned[0].isdigit():
            cleaned = "_" + cleaned
        return cleaned

    def _load_tool_file(self, path: Path) -> CustomTool:
        """通过 importlib 加载单个工具文件。"""
        module_name = f"lq_custom_tool_{path.stem}"
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            raise ImportError(f"无法加载模块: {path}")

        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        # 读取 TOOL_DEFINITION
        tool_def = getattr(module, "TOOL_DEFINITION", None)
        if not isinstance(tool_def, dict):
            raise ValueError(f"TOOL_DEFINITION 不是字典: {path}")

        # 读取 execute 函数
        execute_fn = getattr(module, "execute", None)
        if execute_fn is None or not callable(execute_fn):
            raise ValueError(f"缺少可调用的 execute 函数: {path}")

        return CustomTool(
            name=tool_def.get("name", path.stem),
            description=tool_def.get("description", ""),
            input_schema=tool_def.get("input_schema", None) or tool_def.get("parameters", {"type": "object", "properties": {}}),
            module_path=path,
            execute_fn=execute_fn,
        )

    def _load_registry_state(self) -> None:
        """从 __registry__.json 加载禁用列表。"""
        if self._registry_path.exists():
            try:
                data = json.loads(self._registry_path.read_text(encoding="utf-8"))
                self._disabled = set(data.get("disabled", []))
            except (json.JSONDecodeError, Exception):
                logger.warning("注册表文件损坏，使用默认状态")
                self._disabled = set()

    def _save_registry_state(self) -> None:
        """保存禁用列表到 __registry__.json。"""
        data = {"disabled": sorted(self._disabled)}
        self._registry_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
