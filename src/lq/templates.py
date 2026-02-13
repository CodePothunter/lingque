"""模板文件生成"""

from __future__ import annotations

from pathlib import Path


def write_soul_template(path: Path, name: str) -> None:
    path.write_text(
        f"""\
# {name} 的灵魂

## 身份
你是 {name}，一个深度集成飞书的个人 AI 助理。

## 性格
- 专业、高效、友善
- 适度幽默，不过分
- 主动但不打扰

## 沟通风格
- 简洁明了，避免冗余
- 中文为主，技术术语可用英文
- 根据语境调整正式程度

## 介入原则
- 被 @at 时必须回复
- 讨论到你擅长的领域时可以主动参与
- 闲聊、情绪性对话不要插嘴
- 不确定时宁可不介入
""",
        encoding="utf-8",
    )


def write_memory_template(path: Path) -> None:
    path.write_text(
        """\
# 记忆

## 重要信息

## 用户偏好

## 常用联系人

## 备忘
""",
        encoding="utf-8",
    )


def write_heartbeat_template(path: Path) -> None:
    path.write_text(
        """\
# 心跳任务

## 每次心跳
- 检查是否有未读消息需要处理

## 每天一次（早上）
- 获取今日日程并发送晨报
- 总结昨日日志

## 每周一次（周一早上）
- 总结本周工作
- 检查本周日程
""",
        encoding="utf-8",
    )


def write_systemd_service(name: str, project_dir: str | None = None) -> Path:
    import shutil
    uv_path = shutil.which("uv") or "uv"
    if project_dir is None:
        # 尝试从当前工作目录推断项目目录
        cwd = Path.cwd()
        if (cwd / "pyproject.toml").exists():
            project_dir = str(cwd)
        else:
            project_dir = str(Path(__file__).resolve().parents[2])

    service_dir = Path.home() / ".config" / "systemd" / "user"
    service_dir.mkdir(parents=True, exist_ok=True)
    service_path = service_dir / f"lq-{name}.service"
    service_path.write_text(
        f"""\
[Unit]
Description=灵雀 {name}
After=network.target

[Service]
Type=simple
WorkingDirectory={project_dir}
ExecStart={uv_path} run lq start @{name}
Restart=on-failure
RestartSec=10

[Install]
WantedBy=default.target
""",
        encoding="utf-8",
    )
    return service_path
