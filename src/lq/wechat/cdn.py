"""微信 CDN 媒体文件加解密与上传下载。

微信 iLink 协议中，所有媒体文件通过 AES-128-ECB 加密存储在 CDN 上。
- 收图：CDN 下载加密文件 → AES-128-ECB 解密 → 原图
- 发图：原图 → AES-128-ECB 加密 → CDN 上传 → 构造 ImageItem 发送
"""

from __future__ import annotations

import base64
import hashlib
import logging
import math
import os
import uuid
from urllib.parse import quote

import httpx

logger = logging.getLogger(__name__)

DEFAULT_CDN_BASE = "https://novac2c.cdn.weixin.qq.com/c2c"

# ── AES-128-ECB 加解密 ──────────────────────────────────────────────


def _aes_ecb_encrypt(plaintext: bytes, key: bytes) -> bytes:
    """AES-128-ECB 加密（PKCS7 padding）。"""
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.primitives.padding import PKCS7

    padder = PKCS7(128).padder()
    padded = padder.update(plaintext) + padder.finalize()
    cipher = Cipher(algorithms.AES(key), modes.ECB())
    encryptor = cipher.encryptor()
    return encryptor.update(padded) + encryptor.finalize()


def _aes_ecb_decrypt(ciphertext: bytes, key: bytes) -> bytes:
    """AES-128-ECB 解密（PKCS7 unpadding）。"""
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.primitives.padding import PKCS7

    cipher = Cipher(algorithms.AES(key), modes.ECB())
    decryptor = cipher.decryptor()
    padded = decryptor.update(ciphertext) + decryptor.finalize()
    unpadder = PKCS7(128).unpadder()
    return unpadder.update(padded) + unpadder.finalize()


def _aes_ecb_padded_size(plaintext_size: int) -> int:
    """计算 AES-128-ECB PKCS7 加密后的大小。"""
    return math.ceil((plaintext_size + 1) / 16) * 16


def _parse_aes_key(aes_key_b64: str) -> bytes:
    """解析 CDNMedia.aes_key 为 16 字节原始 key。

    iLink 协议中有两种编码：
    - base64(raw 16 bytes) — 图片
    - base64(hex string 32 chars) — 文件/语音/视频
    """
    decoded = base64.b64decode(aes_key_b64)
    if len(decoded) == 16:
        return decoded
    if len(decoded) == 32:
        try:
            hex_str = decoded.decode("ascii")
            if all(c in "0123456789abcdefABCDEF" for c in hex_str):
                return bytes.fromhex(hex_str)
        except (UnicodeDecodeError, ValueError):
            pass
    raise ValueError(
        f"aes_key must decode to 16 raw bytes or 32-char hex string, got {len(decoded)} bytes"
    )


# ── CDN URL 构造 ─────────────────────────────────────────────────────


def _cdn_download_url(encrypt_query_param: str, cdn_base: str = DEFAULT_CDN_BASE) -> str:
    return f"{cdn_base}/download?encrypted_query_param={quote(encrypt_query_param)}"


def _cdn_upload_url(
    upload_param: str, filekey: str, cdn_base: str = DEFAULT_CDN_BASE,
) -> str:
    return f"{cdn_base}/upload?encrypted_query_param={quote(upload_param)}&filekey={quote(filekey)}"


# ── 下载 + 解密 ──────────────────────────────────────────────────────


async def download_and_decrypt(
    encrypt_query_param: str,
    aes_key_b64: str,
    cdn_base: str = DEFAULT_CDN_BASE,
) -> bytes:
    """从 CDN 下载加密媒体并解密，返回原始字节。"""
    key = _parse_aes_key(aes_key_b64)
    url = _cdn_download_url(encrypt_query_param, cdn_base)
    logger.debug("CDN 下载: %s", url[:80])
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(url)
        resp.raise_for_status()
    decrypted = _aes_ecb_decrypt(resp.content, key)
    logger.debug("CDN 解密完成: %d → %d bytes", len(resp.content), len(decrypted))
    return decrypted


# ── 加密 + 上传 ──────────────────────────────────────────────────────


async def upload_image(
    file_data: bytes,
    to_user_id: str,
    bot_token: str,
    base_url: str,
    cdn_base: str = DEFAULT_CDN_BASE,
) -> dict:
    """加密图片并上传到微信 CDN。

    Returns:
        dict with keys: encrypt_query_param, aes_key_b64, filekey, ciphertext_size
    """
    # 1. 生成随机 AES key
    aes_key = os.urandom(16)
    aes_key_hex = aes_key.hex()

    # 2. 计算文件信息
    raw_size = len(file_data)
    raw_md5 = hashlib.md5(file_data).hexdigest()
    cipher_size = _aes_ecb_padded_size(raw_size)
    filekey = os.urandom(16).hex()

    # 3. 获取预签名上传 URL
    upload_param = await _get_upload_url(
        bot_token=bot_token,
        base_url=base_url,
        filekey=filekey,
        media_type=1,  # IMAGE
        to_user_id=to_user_id,
        raw_size=raw_size,
        raw_md5=raw_md5,
        cipher_size=cipher_size,
        aes_key_hex=aes_key_hex,
    )

    # 4. AES 加密 + PUT 到 CDN
    ciphertext = _aes_ecb_encrypt(file_data, aes_key)
    cdn_url = _cdn_upload_url(upload_param, filekey, cdn_base)
    logger.debug("CDN 上传: %s (%d bytes)", cdn_url[:80], len(ciphertext))

    download_param = ""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            cdn_url,
            content=ciphertext,
            headers={"Content-Type": "application/octet-stream"},
        )
        resp.raise_for_status()
        download_param = resp.headers.get("x-encrypted-param", "")

    if not download_param:
        raise RuntimeError("CDN 上传成功但未返回 x-encrypted-param")

    logger.info("CDN 上传完成: filekey=%s size=%d", filekey, raw_size)
    return {
        "encrypt_query_param": download_param,
        "aes_key_b64": base64.b64encode(aes_key_hex.encode()).decode(),
        "filekey": filekey,
        "ciphertext_size": cipher_size,
    }


async def _get_upload_url(
    *,
    bot_token: str,
    base_url: str,
    filekey: str,
    media_type: int,
    to_user_id: str,
    raw_size: int,
    raw_md5: str,
    cipher_size: int,
    aes_key_hex: str,
) -> str:
    """调用 /ilink/bot/getuploadurl 获取 CDN 预签名上传参数。"""
    import random
    uin = base64.b64encode(str(random.randint(0, 0xFFFFFFFF)).encode()).decode()
    headers = {
        "Content-Type": "application/json",
        "AuthorizationType": "ilink_bot_token",
        "Authorization": f"Bearer {bot_token}",
        "X-WECHAT-UIN": uin,
    }
    payload = {
        "filekey": filekey,
        "media_type": media_type,
        "to_user_id": to_user_id,
        "rawsize": raw_size,
        "rawfilemd5": raw_md5,
        "filesize": cipher_size,
        "no_need_thumb": True,
        "aeskey": aes_key_hex,
        "base_info": {},
    }
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            f"{base_url}/ilink/bot/getuploadurl",
            json=payload,
            headers=headers,
        )
        resp.raise_for_status()
        data = resp.json()

    upload_param = data.get("upload_param", "")
    if not upload_param:
        raise RuntimeError(f"getuploadurl 未返回 upload_param: {data}")
    return upload_param
