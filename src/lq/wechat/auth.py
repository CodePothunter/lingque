"""WeChat QR code login and credential management via iLink protocol."""

from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

ILINK_BASE = "https://ilinkai.weixin.qq.com/ilink/bot"
CREDENTIALS_FILE = "wechat_credentials.json"
QR_FILE = "wechat_qr.txt"


@dataclass
class WechatCredentials:
    bot_token: str
    bot_id: str       # ilink_bot_id
    base_url: str
    user_id: str      # ilink_user_id (the logged-in user)


async def fetch_qr_code() -> tuple[str, str]:
    """Fetch QR code for login. Returns (qr_code, qr_url)."""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(f"{ILINK_BASE}/get_bot_qrcode", params={"bot_type": "3"})
        resp.raise_for_status()
        data = resp.json()
    qr_code: str = data["qrcode"]
    qr_url: str = data["qrcode_img_content"]
    logger.info("Fetched QR code for WeChat login")
    return qr_code, qr_url


async def poll_qr_status(
    qr_code: str,
    on_status: Callable[[str], None] | None = None,
) -> WechatCredentials:
    """Poll QR code status until confirmed or expired.

    ``on_status`` callback is called on each status change.
    Raises :class:`RuntimeError` if the QR code expires before confirmation.
    """
    last_status = ""
    deadline = time.monotonic() + 300  # 5 minute overall timeout
    async with httpx.AsyncClient(timeout=60) as client:
        while True:
            if time.monotonic() > deadline:
                raise RuntimeError("QR 登录超时（5 分钟内未完成扫码确认）")
            try:
                resp = await client.get(
                    f"{ILINK_BASE}/get_qrcode_status",
                    params={"qrcode": qr_code},
                    timeout=60,
                )
                resp.raise_for_status()
                data = resp.json()
            except httpx.TimeoutException:
                logger.debug("QR status poll timed out, retrying")
                continue
            except httpx.HTTPStatusError:
                logger.exception("HTTP error polling QR status")
                raise

            status: str = data.get("status", "")
            if status != last_status:
                last_status = status
                logger.info("QR code status: %s", status)
                if on_status is not None:
                    on_status(status)

            if status == "confirmed":
                return WechatCredentials(
                    bot_token=data["bot_token"],
                    bot_id=data["ilink_bot_id"],
                    base_url=data["baseurl"],
                    user_id=data["ilink_user_id"],
                )

            if status == "expired":
                raise RuntimeError("QR code expired before confirmation")


def save_credentials(home: Path, creds: WechatCredentials) -> None:
    """Save credentials to *home*/wechat_credentials.json."""
    path = home / CREDENTIALS_FILE
    path.write_text(json.dumps(asdict(creds), ensure_ascii=False, indent=2), encoding="utf-8")
    os.chmod(path, 0o600)
    logger.info("Saved WeChat credentials to %s", path)


def load_credentials(home: Path) -> WechatCredentials | None:
    """Load credentials from *home*/wechat_credentials.json.

    Returns ``None`` if the file does not exist or is malformed.
    """
    path = home / CREDENTIALS_FILE
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return WechatCredentials(
            bot_token=data["bot_token"],
            bot_id=data["bot_id"],
            base_url=data["base_url"],
            user_id=data["user_id"],
        )
    except (json.JSONDecodeError, KeyError):
        logger.exception("Failed to load WeChat credentials from %s", path)
        return None


def _present_qr(home: Path, qr_url: str) -> None:
    """让用户看到 QR 链接——无论前台还是后台运行。

    1. 写入 ``wechat_qr.txt``（daemon 模式下可 ``cat`` 查看）
    2. ``logger.warning`` 写入日志（``lq logs`` 可见）
    3. ``print`` 输出到 stdout（前台运行时直接可见）
    4. 尝试渲染终端 ASCII QR 码（可选依赖）
    """
    # 持久化到文件，daemon 模式可查
    qr_path = home / QR_FILE
    qr_path.write_text(
        f"微信登录链接\n{qr_url}\n\n在手机上打开此链接，用微信确认登录。\n此文件登录成功后自动删除。\n",
        encoding="utf-8",
    )

    # 日志（lq logs 可见）
    logger.warning("微信需要登录，链接: %s", qr_url)
    logger.warning("也可查看文件: %s", qr_path)

    # stdout（前台直接可见）
    print(f"\n在手机上打开以下链接登录微信:\n{qr_url}\n")
    print(f"（后台运行时可查看: {qr_path}）\n")


def _cleanup_qr(home: Path) -> None:
    """登录成功后清理 QR 文件。"""
    qr_path = home / QR_FILE
    qr_path.unlink(missing_ok=True)


async def ensure_credentials(home: Path) -> WechatCredentials:
    """Load existing credentials or trigger QR login flow.

    QR 链接会同时输出到 stdout、日志和文件，确保前台和 daemon 模式都能看到。
    """
    creds = load_credentials(home)
    if creds is not None:
        logger.info("Loaded existing WeChat credentials for user %s", creds.user_id)
        return creds

    logger.info("No existing WeChat credentials found, starting QR login")
    qr_code, qr_url = await fetch_qr_code()
    _present_qr(home, qr_url)

    def _on_status(status: str) -> None:
        status_labels = {
            "wait": "等待扫码...",
            "scaned": "已扫码，请在手机上确认",
            "confirmed": "登录成功!",
            "expired": "二维码已过期",
        }
        label = status_labels.get(status, f"状态: {status}")
        logger.info("微信登录状态: %s", label)
        print(label)

    creds = await poll_qr_status(qr_code, on_status=_on_status)
    save_credentials(home, creds)
    _cleanup_qr(home)
    return creds
