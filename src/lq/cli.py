"""灵雀 CLI — lq 命令行工具"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
import sys
from pathlib import Path

import click

from lq.config import (
    LQConfig,
    find_instance,
    load_config,
    load_from_env,
    resolve_home,
    save_config,
    slugify,
)
from lq.templates import (
    write_contributing_template,
    write_heartbeat_template,
    write_memory_template,
    write_soul_template,
    write_systemd_service,
)


def _resolve(instance: str) -> tuple[Path, str, LQConfig | None]:
    """解析实例标识 → (home, display_name, config_or_None)

    支持 `@奶油` 和 `@naiyu` 两种写法。
    """
    result = find_instance(instance)
    if result:
        home, cfg = result
        return home, cfg.name, cfg

    # 没有匹配 → 降级：当作 slug 直接拼路径
    identifier = instance.lstrip("@")
    slug = slugify(identifier) if not identifier.isascii() else identifier
    home = resolve_home(slug)
    return home, identifier, None


@click.group()
@click.version_option(package_name="lingque")
def cli() -> None:
    """灵雀 — 深度集成飞书的个人 AI 助理框架"""


@cli.command()
@click.option("--name", prompt="助理名称", help="助理实例名称（支持中文）")
@click.option("--from-env", type=click.Path(exists=True), help="从 .env 文件读取凭证")
@click.option("--owner", default="", help="主人的飞书名（安全相关：用于审批确认。留空则首个私聊用户自动成为主人）")
def init(name: str, from_env: str | None, owner: str) -> None:
    """初始化一个新的灵雀实例"""
    slug = slugify(name)
    home = resolve_home(slug)

    if home.exists():
        if not click.confirm(f"目录 {home} 已存在，是否覆盖?"):
            raise SystemExit(1)

    # 收集凭证
    if from_env:
        config = load_from_env(Path(from_env))
        config.name = name
        config.slug = slug
    else:
        config = LQConfig(name=name, slug=slug)
        config.feishu.app_id = click.prompt("飞书 App ID")
        config.feishu.app_secret = click.prompt("飞书 App Secret", hide_input=True)
        config.api.api_key = click.prompt("Anthropic API Key", hide_input=True)
        config.api.base_url = click.prompt(
            "API Base URL",
            default=config.api.base_url,
        )

    # 创建目录结构
    home.mkdir(parents=True, exist_ok=True)
    for sub in ("memory", "sessions", "sessions/archive", "groups", "logs", "chat_memories"):
        (home / sub).mkdir(parents=True, exist_ok=True)

    # 主人名配置
    if owner:
        config.owner_name = owner

    # 写入配置
    save_config(home, config)

    # 生成模板
    write_soul_template(home / "SOUL.md", name)
    write_memory_template(home / "MEMORY.md")
    write_heartbeat_template(home / "HEARTBEAT.md")

    # 生成开发规范
    contributing_path = home / "CONTRIBUTING.md"
    if not contributing_path.exists():
        write_contributing_template(contributing_path, name, slug)

    # 生成好奇心日志和进化日志
    from lq.prompts import CURIOSITY_INIT_TEMPLATE
    curiosity_path = home / "CURIOSITY.md"
    if not curiosity_path.exists():
        curiosity_path.write_text(CURIOSITY_INIT_TEMPLATE, encoding="utf-8")

    # 生成进化日志
    from lq.prompts import EVOLUTION_INIT_TEMPLATE
    evolution_path = home / "EVOLUTION.md"
    if not evolution_path.exists():
        evolution_path.write_text(EVOLUTION_INIT_TEMPLATE, encoding="utf-8")

    # 生成进度追踪
    from lq.templates import write_progress_template
    write_progress_template(home / "PROGRESS.md")

    # 生成 systemd service
    service_path = write_systemd_service(slug)

    click.echo(f"✓ 实例 @{name} (slug: {slug}) 初始化完成")
    click.echo(f"  配置目录: {home}")
    click.echo(f"  Systemd:  {service_path}")
    if owner:
        click.echo(f"  主人:      {owner}")
    else:
        click.echo("  主人:      未设置（首个私聊用户将自动成为主人）")
    click.echo()
    click.echo("⚠ 安全提示: 主人身份决定了谁能审批敏感操作。")
    click.echo("  建议使用 --owner 指定主人名，或在启动后尽快私聊 bot 以绑定身份。")
    click.echo()
    click.echo("后续操作:")
    click.echo(f"  编辑人格:   $EDITOR {home}/SOUL.md")
    click.echo(f"  启动:       uv run lq start @{name}")
    click.echo(f"  Systemd:    systemctl --user enable --now lq-{slug}")


def _parse_adapters(adapter_str: str) -> list[str]:
    """解析逗号分隔的适配器列表并校验。"""
    from lq.gateway import KNOWN_ADAPTERS
    types = [t.strip() for t in adapter_str.split(",") if t.strip()]
    unknown = set(types) - KNOWN_ADAPTERS
    if unknown:
        raise click.BadParameter(
            f"未知适配器: {', '.join(unknown)}（可选: {', '.join(sorted(KNOWN_ADAPTERS))}）"
        )
    if not types:
        raise click.BadParameter("至少需要一个适配器")
    return types


@cli.command()
@click.argument("instance")
@click.option("--adapter", "adapter_str", default="local",
              help="聊天平台适配器，逗号分隔多选（feishu=飞书, discord=Discord, telegram=Telegram, local=纯本地）")
@click.option("--show-thinking", is_flag=True, default=False,
              help="输出工具调用记录和思考过程（默认关闭）")
def start(instance: str, adapter_str: str, show_thinking: bool) -> None:
    """启动灵雀实例（@name 或 @slug）

    \b
    示例:
      lq start @name                    # 默认本地（无需平台凭证）
      lq start @name --adapter local    # 纯本地（无需飞书凭证）
      lq start @name --adapter discord  # Discord
      lq start @name --adapter telegram # Telegram
      lq start @name --adapter feishu,local  # 同时连接飞书 + 本地
      lq start @name --adapter discord,local # 同时连接 Discord + 本地
    """
    adapter_types = _parse_adapters(adapter_str)
    home, display, cfg = _resolve(instance)

    if not home.exists():
        click.echo(f"错误: 实例 @{display} 不存在，请先运行 uv run lq init", err=True)
        raise SystemExit(1)

    pid = _read_pid(home)
    if pid and _is_alive(pid):
        click.echo(f"@{display} 已在运行 (PID {pid})", err=True)
        raise SystemExit(1)

    config = cfg or load_config(home)

    if show_thinking:
        config.show_thinking = True

    click.echo(f"启动 @{display} (adapter={'+'.join(adapter_types)}) ...")

    if "local" in adapter_types and len(adapter_types) == 1:
        click.echo("💡 纯本地模式：通过终端 stdin 或 inbox.txt 交互，无远程连接")
    if len(adapter_types) > 1:
        click.echo(f"💡 多平台模式：同时连接 {', '.join(adapter_types)}，消息自动路由到来源平台")

    from lq.gateway import AssistantGateway
    gw = AssistantGateway(config, home, adapter_types=adapter_types)
    asyncio.run(gw.run())


@cli.command()
@click.argument("instance")
def stop(instance: str) -> None:
    """停止灵雀实例"""
    home, display, _ = _resolve(instance)
    pid = _read_pid(home)

    if not pid or not _is_alive(pid):
        click.echo(f"@{display} 未在运行")
        return

    os.kill(pid, signal.SIGTERM)
    click.echo(f"@{display} 正在停止 (PID {pid}) ...", nl=False)

    # 等待进程退出，超时后 SIGKILL
    import time
    for _ in range(10):  # 最多等 10 秒
        time.sleep(1)
        if not _is_alive(pid):
            click.echo(" 已停止")
            return
        click.echo(".", nl=False)

    # 宽限期结束，强制终止
    click.echo()
    os.kill(pid, signal.SIGKILL)
    click.echo(f"@{display} 强制终止 (SIGKILL)")


@cli.command()
@click.argument("instance")
@click.option("--adapter", "adapter_str", default="local",
              help="聊天平台适配器，逗号分隔多选（feishu=飞书, discord=Discord, telegram=Telegram, local=纯本地）")
def restart(instance: str, adapter_str: str) -> None:
    """重启灵雀实例"""
    adapter_types = _parse_adapters(adapter_str)
    home, display, _ = _resolve(instance)
    pid = _read_pid(home)

    if pid and _is_alive(pid):
        os.kill(pid, signal.SIGTERM)
        click.echo(f"@{display} 正在停止 (PID {pid}) ...", nl=False)
        import time
        for _ in range(10):
            time.sleep(1)
            if not _is_alive(pid):
                break
            click.echo(".", nl=False)
        click.echo()
        if _is_alive(pid):
            os.kill(pid, signal.SIGKILL)
            time.sleep(0.5)
            click.echo(f"@{display} 强制终止 (SIGKILL)")

    config = load_config(home)
    click.echo(f"启动 @{display} (adapter={'+'.join(adapter_types)}) ...")
    from lq.gateway import AssistantGateway
    gw = AssistantGateway(config, home, adapter_types=adapter_types)
    asyncio.run(gw.run())


@cli.command("list")
def list_instances() -> None:
    """列出所有灵雀实例"""
    found = False
    for entry in Path.home().iterdir():
        if not entry.is_dir() or not entry.name.startswith(".lq-"):
            continue
        config_path = entry / "config.json"
        if not config_path.exists():
            continue
        try:
            with open(config_path) as f:
                d = json.load(f)
            if "feishu" not in d or "api" not in d:
                continue
        except (json.JSONDecodeError, KeyError):
            continue

        name = d.get("name", "?")
        slug = d.get("slug", entry.name.removeprefix(".lq-"))
        pid = _read_pid(entry)
        status = "🟢 running" if pid and _is_alive(pid) else "⚫ stopped"
        label = f"@{name}" if name != slug else f"@{slug}"
        if name != slug:
            label += f"  ({slug})"
        click.echo(f"  {label:30s} {status}")
        if pid and _is_alive(pid):
            click.echo(f"    PID: {pid}")
        found = True

    if not found:
        click.echo("  暂无实例。运行 uv run lq init 创建。")


@cli.command()
@click.argument("instance")
def status(instance: str) -> None:
    """显示实例运行状态"""
    home, display, cfg = _resolve(instance)

    if not home.exists():
        click.echo(f"实例 @{display} 不存在", err=True)
        raise SystemExit(1)

    config = cfg or load_config(home)
    pid = _read_pid(home)
    alive = pid and _is_alive(pid)

    click.echo(f"实例: @{config.name}  (slug: {config.slug})")
    click.echo(f"目录: {home}")
    click.echo(f"状态: {'🟢 运行中' if alive else '⚫ 已停止'}")
    if alive:
        click.echo(f"PID:  {pid}")
        try:
            with open(f"/proc/{pid}/status") as f:
                for line in f:
                    if line.startswith("VmRSS:"):
                        click.echo(f"内存: {line.split(':')[1].strip()}")
                        break
        except (FileNotFoundError, PermissionError):
            pass

    stats_file = home / "stats.jsonl"
    if stats_file.exists():
        try:
            from lq.stats import StatsTracker
            tracker = StatsTracker(home)

            daily = tracker.get_daily_summary()
            click.echo()
            click.echo("--- 今日用量 ---")
            click.echo(f"  调用次数:   {daily['total_calls']}")
            click.echo(f"  输入 Token: {daily['total_input_tokens']:,}")
            click.echo(f"  输出 Token: {daily['total_output_tokens']:,}")
            click.echo(f"  费用估算:   ${daily['total_cost']:.4f}")
            if daily.get("by_type"):
                parts = [f"{k}={v}" for k, v in daily["by_type"].items()]
                click.echo(f"  调用类型:   {', '.join(parts)}")

            monthly = tracker.get_monthly_summary()
            click.echo()
            click.echo(f"--- {monthly['year']}-{monthly['month']:02d} 月度用量 ---")
            click.echo(f"  调用次数:   {monthly['total_calls']}")
            click.echo(f"  输入 Token: {monthly['total_input_tokens']:,}")
            click.echo(f"  输出 Token: {monthly['total_output_tokens']:,}")
            click.echo(f"  费用估算:   ${monthly['total_cost']:.4f}")
        except Exception:
            pass


@cli.command()
@click.argument("instance")
@click.option("--since", default=None, help="显示最近多长时间的日志（如 1h, 30m）")
def logs(instance: str, since: str | None) -> None:
    """查看实例日志"""
    home, display, _ = _resolve(instance)
    log_file = home / "logs" / "gateway.log"

    if not log_file.exists():
        click.echo("暂无日志")
        return

    if since:
        import re
        from datetime import datetime, timedelta
        match = re.match(r"(\d+)([hm])", since)
        if match:
            val, unit = int(match.group(1)), match.group(2)
            delta = timedelta(hours=val) if unit == "h" else timedelta(minutes=val)
            cutoff = datetime.now() - delta
            with open(log_file) as f:
                for line in f:
                    try:
                        ts = datetime.fromisoformat(line[:19])
                        if ts >= cutoff:
                            click.echo(line, nl=False)
                    except ValueError:
                        click.echo(line, nl=False)
        else:
            click.echo(f"无效时间格式: {since}，使用如 1h, 30m", err=True)
    else:
        subprocess.run(["tail", "-f", str(log_file)])


def _run_local_chat(instance: str, message: str) -> None:
    """共用逻辑：本地对话（chat / say 共享）"""
    home, display, cfg = _resolve(instance)

    if not home.exists():
        click.echo(f"错误: 实例 @{display} 不存在，请先运行 uv run lq init", err=True)
        raise SystemExit(1)

    config = cfg or load_config(home)

    from lq.conversation import run_conversation
    asyncio.run(run_conversation(home, config, single_message=message))


@cli.command()
@click.argument("instance")
@click.argument("message", required=False, default="")
def chat(instance: str, message: str) -> None:
    """和灵雀聊天（本地终端，不依赖飞书）

    \b
    交互模式:  lq chat @name
    单条模式:  lq chat @name "你好"
    """
    _run_local_chat(instance, message)


@cli.command()
@click.argument("instance")
@click.argument("message", required=False, default="")
def say(instance: str, message: str) -> None:
    """chat 的别名 — 和灵雀对话

    \b
    交互模式:  lq say @name
    单条模式:  lq say @name "你好"
    """
    _run_local_chat(instance, message)


@cli.command()
@click.argument("instance")
@click.argument("target", type=click.Choice(["soul", "memory", "heartbeat", "config"]))
def edit(instance: str, target: str) -> None:
    """编辑实例文件"""
    home, display, _ = _resolve(instance)
    file_map = {
        "soul": home / "SOUL.md",
        "memory": home / "MEMORY.md",
        "heartbeat": home / "HEARTBEAT.md",
        "config": home / "config.json",
    }
    editor = os.environ.get("EDITOR", "vi")
    subprocess.run([editor, str(file_map[target])])


@cli.command()
@click.argument("instance")
def upgrade(instance: str) -> None:
    """升级灵雀框架"""
    home, display, cfg = _resolve(instance)
    config = cfg or load_config(home)

    config_path = home / "config.json"
    backup_path = home / "config.json.bak"
    if config_path.exists():
        import shutil
        shutil.copy2(config_path, backup_path)
        click.echo(f"配置已备份到 {backup_path}")

    click.echo("升级 lingque ...")
    subprocess.run(["uv", "sync", "--upgrade"])

    write_systemd_service(config.slug)
    click.echo(f"@{display} 升级完成")


def _read_pid(home: Path) -> int | None:
    pid_path = home / "gateway.pid"
    if pid_path.exists():
        try:
            return int(pid_path.read_text().strip())
        except (ValueError, OSError):
            return None
    return None


def _is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False
