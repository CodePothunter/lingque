# 这是要追加到 EvolutionEngine 类末尾的方法
# 使用 cat >> 追加到 evolution.py

    # ── 错误日志分析 ──

    def scan_error_patterns(self, log_path: Path | None = None) -> list[dict]:
        """扫描日志文件中的错误模式，发现改进机会。

        Args:
            log_path: 日志文件路径，默认使用 workspace/logs/gateway.log

        Returns:
            错误模式列表，每项含 {"pattern": 描述, "count": 次数, "sample": 示例}
        """
        if log_path is None:
            log_path = self.workspace / "logs" / "gateway.log"

        if not log_path.exists():
            logger.debug("日志文件不存在: %s", log_path)
            return []

        try:
            # 读取最后 500 行
            content = log_path.read_text(encoding="utf-8", errors="ignore")
            lines = content.splitlines()[-500:]
        except Exception as e:
            logger.warning("读取日志失败: %s", e)
            return []

        # 收集 ERROR 和 WARNING
        import re as _re
        error_pattern = _re.compile(r'\[(ERROR|WARNING)\]\s+(.+?)(?::\s|$)')
        patterns: dict[str, dict] = {}

        for line in lines:
            match = error_pattern.search(line)
            if match:
                level = match.group(1)
                msg = match.group(2).strip()
                # 提取关键部分（去掉变量数据）
                key = _re.sub(r'\d{4}-\d{2}-\d{2}.*', '', msg)
                key = _re.sub(r"'[^']{20,}'", "'...'", key)
                key = f"[{level}] {key[:80]}"

                if key not in patterns:
                    patterns[key] = {"pattern": key, "count": 0, "sample": line[:200]}
                patterns[key]["count"] += 1

        result = sorted(patterns.values(), key=lambda x: x["count"], reverse=True)
        logger.info("扫描到 %d 种错误/警告模式", len(result))
        return result

    def suggest_improvements(self) -> list[str]:
        """根据错误日志自动生成改进建议。

        Returns:
            改进建议字符串列表
        """
        patterns = self.scan_error_patterns()
        suggestions = []

        for p in patterns[:5]:
            pattern = p["pattern"]
            count = p["count"]

            if "心跳任务执行失败" in pattern:
                suggestions.append(f"心跳任务失败 {count} 次 → 检查心跳回调的错误处理和重试机制")
            elif "健康检查失败" in pattern:
                suggestions.append(f"健康检查失败 {count} 次 → 增强启动时的自诊断能力")
            elif "工具执行失败" in pattern or "Tool execution failed" in pattern:
                suggestions.append(f"工具执行失败 {count} 次 → 改进工具的错误处理和用户反馈")
            elif "配置一致性警告" in pattern:
                suggestions.append(f"配置警告 {count} 次 → 改进配置校验或启动提示")
            elif "HTTPStatusError" in pattern or "HTTP" in pattern:
                suggestions.append(f"HTTP 错误 {count} 次 → 增强 API 调用的容错和重试")
            elif "WARNING" in pattern and count >= 3:
                suggestions.append(f"警告 [{pattern}] 出现 {count} 次 → 调查是否需要处理")

        logger.info("生成 %d 条改进建议", len(suggestions))
        return suggestions
