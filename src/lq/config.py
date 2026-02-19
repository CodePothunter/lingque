"""配置加载与管理"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from dataclasses import dataclass, field, asdict
from pathlib import Path

from dotenv import dotenv_values
from pypinyin import lazy_pinyin, Style

logger = logging.getLogger(__name__)


def slugify(name: str) -> str:
    """将任意名称转为安全的 ASCII 目录名。

    中文 → 拼音，非 ASCII → 丢弃，空格/符号 → 连字符，全部小写。
    示例: "奶油" → "naiyu", "Test Bot" → "test-bot", "灵雀v2" → "lingquev2"
    """
    # 中文转拼音（无声调），非中文字符原样保留
    parts = lazy_pinyin(name, style=Style.NORMAL)
    ascii_name = "".join(parts)
    # 去掉剩余非 ASCII
    ascii_name = unicodedata.normalize("NFKD", ascii_name)
    ascii_name = ascii_name.encode("ascii", "ignore").decode("ascii")
    # 替换空格和特殊字符为连字符
    ascii_name = re.sub(r"[^a-zA-Z0-9]+", "-", ascii_name)
    ascii_name = ascii_name.strip("-").lower()
    return ascii_name or "lq"


@dataclass
class APIConfig:
    base_url: str = "https://api.anthropic.com"
    api_key: str = ""
    proxy: str = ""  # HTTP/SOCKS 代理（如 socks5://127.0.0.1:1080）
    mcp_key: str = ""  # 智谱 MCP API Key（联网搜索等），默认复用 api_key


@dataclass
class FeishuConfig:
    app_id: str = ""
    app_secret: str = ""
    bot_open_id: str = ""  # 启动时自动获取
    owner_chat_id: str = ""  # 主人的 chat_id，用于晨报等主动消息


@dataclass
class GroupConfig:
    chat_id: str = ""
    note: str = ""  # 群描述/用途，用于 LLM 介入判断
    eval_threshold: int = 5


@dataclass
class LQConfig:
    name: str = "lingque"       # 显示名（可以是中文）
    slug: str = ""              # 目录名（纯 ASCII），为空时自动从 name 生成
    api: APIConfig = field(default_factory=APIConfig)
    feishu: FeishuConfig = field(default_factory=FeishuConfig)
    model: str = "glm-5"
    heartbeat_interval: int = 3600  # 秒
    active_hours: tuple[int, int] = (8, 23)  # 活跃时段
    groups: list[GroupConfig] = field(default_factory=list)
    cost_alert_daily: float = 5.0  # USD
    curiosity_budget: float = 1.0  # 每日好奇心探索预算 (USD)
    evolution_max_daily: int = 3   # 每日最大自进化次数
    evolution_budget: float = 2.0  # 每日自进化预算 (USD)
    owner_name: str = ""  # 主人的飞书名（init 时设置，用于运行时自动发现 owner_chat_id）
    chat_memory_budget: int = 2000  # per-chat 长期记忆 token 预算

    def __post_init__(self) -> None:
        if not self.slug:
            self.slug = slugify(self.name)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["active_hours"] = list(self.active_hours)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> LQConfig:
        cfg = cls.__new__(cls)
        cfg.name = d.get("name", "lingque")
        cfg.slug = d.get("slug", "")
        cfg.model = d.get("model", "glm-5")
        cfg.heartbeat_interval = d.get("heartbeat_interval", 3600)
        cfg.cost_alert_daily = d.get("cost_alert_daily", 5.0)
        cfg.curiosity_budget = d.get("curiosity_budget", 1.0)
        cfg.evolution_max_daily = d.get("evolution_max_daily", 3)
        cfg.evolution_budget = d.get("evolution_budget", 2.0)
        cfg.owner_name = d.get("owner_name", "")
        cfg.chat_memory_budget = d.get("chat_memory_budget", 2000)
        ah = d.get("active_hours", [8, 23])
        cfg.active_hours = (ah[0], ah[1])

        api = d.get("api", {})
        api_key = api.get("api_key", "")
        cfg.api = APIConfig(
            base_url=api.get("base_url", "https://api.anthropic.com"),
            api_key=api_key,
            proxy=api.get("proxy", ""),
            mcp_key=api.get("mcp_key", "") or api_key,
        )

        fs = d.get("feishu", {})
        cfg.feishu = FeishuConfig(
            app_id=fs.get("app_id", ""),
            app_secret=fs.get("app_secret", ""),
            bot_open_id=fs.get("bot_open_id", ""),
            owner_chat_id=fs.get("owner_chat_id", ""),
        )

        cfg.groups = [GroupConfig(**g) for g in d.get("groups", [])]

        # 兼容旧配置：没有 slug 字段时自动生成
        if not cfg.slug:
            cfg.slug = slugify(cfg.name)
        return cfg


def resolve_home(slug: str) -> Path:
    """用 slug（纯 ASCII）构建实例主目录"""
    return Path.home() / f".lq-{slug}"


def find_instance(identifier: str) -> tuple[Path, LQConfig] | None:
    """通过 name 或 slug 查找实例。支持 `@奶油` 和 `@naiyu` 两种写法。"""
    identifier = identifier.lstrip("@")
    for entry in Path.home().iterdir():
        if not entry.is_dir() or not entry.name.startswith(".lq-"):
            continue
        config_path = entry / "config.json"
        if not config_path.exists():
            continue
        try:
            with open(config_path) as f:
                cfg = LQConfig.from_dict(json.load(f))
        except (json.JSONDecodeError, KeyError):
            continue
        if cfg.name == identifier or cfg.slug == identifier:
            return entry, cfg
    return None


def load_config(home: Path) -> LQConfig:
    config_path = home / "config.json"
    if not config_path.exists():
        raise FileNotFoundError(f"配置文件不存在: {config_path}")
    with open(config_path) as f:
        return LQConfig.from_dict(json.load(f))


def save_config(home: Path, config: LQConfig) -> None:
    config_path = home / "config.json"
    with open(config_path, "w") as f:
        json.dump(config.to_dict(), f, ensure_ascii=False, indent=2)


def load_from_env(env_path: Path) -> LQConfig:
    """从 .env 文件读取凭证（开发模式）"""
    vals = dotenv_values(env_path)
    cfg = LQConfig()
    cfg.api.api_key = vals.get("ANTHROPIC_AUTH_TOKEN", "")
    cfg.api.base_url = vals.get("ANTHROPIC_BASE_URL", cfg.api.base_url)
    cfg.api.proxy = (
        vals.get("HTTPS_PROXY")
        or vals.get("HTTP_PROXY")
        or vals.get("ALL_PROXY")
        or vals.get("https_proxy")
        or vals.get("http_proxy")
        or vals.get("all_proxy")
        or ""
    )
    cfg.api.mcp_key = vals.get("ZHIPU_API_KEY", "") or cfg.api.api_key
    cfg.feishu.app_id = vals.get("FEISHU_APP_ID", "")
    cfg.feishu.app_secret = vals.get("FEISHU_APP_SECRET", "")
    return cfg
