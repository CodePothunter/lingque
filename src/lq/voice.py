"""语音识别 (STT) 与语音合成 (TTS) 服务 — 兼容 OpenAI Audio API"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import tempfile
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from lq.config import VoiceConfig

logger = logging.getLogger(__name__)

_RETRYABLE_STATUS = {429, 500, 502, 503, 529}
_MAX_RETRIES = 3
_BASE_DELAY = 1.0  # 秒
_TIMEOUT = 60.0    # 秒

# Whisper 支持的格式
_WHISPER_SUPPORTED = {"audio/ogg", "audio/mpeg", "audio/mp3", "audio/wav",
                      "audio/x-wav", "audio/mp4", "audio/m4a", "audio/flac",
                      "audio/webm", "audio/mpga"}

# 需要转换为 ogg 的格式
_NEEDS_CONVERSION = {"audio/silk", "audio/amr"}

# Silk 文件魔数
_SILK_MAGIC = b"#!SILK_V3"

# MIME → 文件扩展名映射（用于 STT 上传时的文件名推断）
_MIME_EXT: dict[str, str] = {
    "audio/ogg": ".ogg",
    "audio/mpeg": ".mp3",
    "audio/mp3": ".mp3",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/mp4": ".m4a",
    "audio/m4a": ".m4a",
    "audio/flac": ".flac",
    "audio/webm": ".webm",
    "audio/amr": ".amr",
    "audio/silk": ".silk",
}


def _detect_silk(data: bytes) -> bool:
    """检测音频数据是否为 Silk 格式（微信语音）。"""
    # 微信 silk 有时带 1 字节前缀 \x02
    return data[:9] == _SILK_MAGIC or data[1:10] == _SILK_MAGIC


def _convert_silk_to_ogg(silk_data: bytes, sample_rate: int = 24000) -> bytes:
    """Silk → PCM (via pilk) → OGG/Opus (via ffmpeg)。

    依赖：pip install pilk + 系统安装 ffmpeg。
    """
    try:
        import pilk
    except ImportError:
        raise RuntimeError(
            "微信语音需要 pilk 库来解码 Silk 格式，请运行: "
            "uv pip install -e '.[wechat-voice]'"
        )

    silk_fd, silk_path = tempfile.mkstemp(suffix=".silk")
    pcm_path = silk_path.replace(".silk", ".pcm")
    ogg_path = silk_path.replace(".silk", ".ogg")
    try:
        # 写入 silk 文件
        with os.fdopen(silk_fd, "wb") as f:
            f.write(silk_data)

        # silk → pcm
        pilk.decode(silk_path, pcm_path, sample_rate=sample_rate)

        # pcm → ogg/opus via ffmpeg
        result = subprocess.run(
            [
                "ffmpeg", "-y",
                "-f", "s16le",        # 原始 PCM 格式
                "-ar", str(sample_rate),
                "-ac", "1",           # 单声道
                "-i", pcm_path,
                "-c:a", "libopus",    # Opus 编码
                "-b:a", "64k",
                ogg_path,
            ],
            capture_output=True,
            timeout=30,
        )
        if result.returncode != 0:
            stderr = result.stderr.decode(errors="replace")[:200]
            raise RuntimeError(f"ffmpeg 转换失败 (rc={result.returncode}): {stderr}")

        with open(ogg_path, "rb") as f:
            return f.read()
    finally:
        for p in (silk_path, pcm_path, ogg_path):
            try:
                os.unlink(p)
            except OSError:
                pass


def _convert_amr_to_ogg(amr_data: bytes) -> bytes:
    """AMR → OGG/Opus (via ffmpeg)。"""
    amr_fd, amr_path = tempfile.mkstemp(suffix=".amr")
    ogg_path = amr_path.replace(".amr", ".ogg")
    try:
        with os.fdopen(amr_fd, "wb") as f:
            f.write(amr_data)

        result = subprocess.run(
            [
                "ffmpeg", "-y",
                "-i", amr_path,
                "-c:a", "libopus",
                "-b:a", "64k",
                ogg_path,
            ],
            capture_output=True,
            timeout=30,
        )
        if result.returncode != 0:
            stderr = result.stderr.decode(errors="replace")[:200]
            raise RuntimeError(f"ffmpeg 转换失败 (rc={result.returncode}): {stderr}")

        with open(ogg_path, "rb") as f:
            return f.read()
    finally:
        for p in (amr_path, ogg_path):
            try:
                os.unlink(p)
            except OSError:
                pass


def _ensure_whisper_compatible(
    audio_data: bytes, mime_type: str,
) -> tuple[bytes, str]:
    """确保音频格式兼容 Whisper API，不兼容时自动转换为 OGG/Opus。

    返回 (audio_bytes, mime_type)。
    """
    # Silk 检测优先于 MIME（微信 CDN 返回的 content-type 可能不准）
    if _detect_silk(audio_data):
        logger.info("检测到 Silk 格式，转换为 OGG/Opus")
        return _convert_silk_to_ogg(audio_data), "audio/ogg"

    if mime_type in _NEEDS_CONVERSION:
        if mime_type == "audio/amr":
            logger.info("检测到 AMR 格式，转换为 OGG/Opus")
            return _convert_amr_to_ogg(audio_data), "audio/ogg"
        # 其他需要转换的格式：尝试按 silk 处理
        if _detect_silk(audio_data):
            return _convert_silk_to_ogg(audio_data), "audio/ogg"

    if mime_type not in _WHISPER_SUPPORTED:
        # 未知格式，尝试检测 silk 魔数
        if _detect_silk(audio_data):
            logger.info("MIME 未知但检测到 Silk 魔数，转换为 OGG/Opus")
            return _convert_silk_to_ogg(audio_data), "audio/ogg"
        logger.warning("未知音频格式 %s，直接传递给 STT（可能失败）", mime_type)

    return audio_data, mime_type


class VoiceService:
    """OpenAI 兼容的语音服务客户端（STT + TTS）。"""

    def __init__(self, config: VoiceConfig) -> None:
        self._stt_base_url = config.stt_base_url.rstrip("/")
        self._stt_api_key = config.stt_api_key
        self._stt_model = config.stt_model
        self._tts_base_url = config.tts_base_url.rstrip("/")
        self._tts_api_key = config.tts_api_key
        self._tts_model = config.tts_model
        self._tts_voice = config.tts_voice
        self._tts_reply = config.tts_reply

    @property
    def stt_enabled(self) -> bool:
        return bool(self._stt_base_url and self._stt_api_key)

    @property
    def tts_enabled(self) -> bool:
        return bool(self._tts_base_url and self._tts_api_key)

    @property
    def tts_reply(self) -> bool:
        return self._tts_reply and self.tts_enabled

    async def transcribe(
        self,
        audio_data: bytes,
        mime_type: str = "audio/ogg",
        filename: str | None = None,
    ) -> str:
        """语音转文字。

        POST {stt_base_url}/audio/transcriptions (multipart/form-data)
        返回识别出的文本，失败时抛出异常。
        """
        if not self.stt_enabled:
            raise RuntimeError("STT 未配置")

        # 自动转换不兼容的格式（如微信 Silk → OGG/Opus）
        audio_data, mime_type = _ensure_whisper_compatible(audio_data, mime_type)

        ext = _MIME_EXT.get(mime_type, ".ogg")
        upload_filename = filename or f"audio{ext}"

        url = f"{self._stt_base_url}/audio/transcriptions"
        headers = {"Authorization": f"Bearer {self._stt_api_key}"}

        last_exc: Exception | None = None
        for attempt in range(_MAX_RETRIES + 1):
            try:
                async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                    resp = await client.post(
                        url,
                        headers=headers,
                        files={"file": (upload_filename, audio_data, mime_type)},
                        data={"model": self._stt_model},
                    )
                if resp.status_code in _RETRYABLE_STATUS and attempt < _MAX_RETRIES:
                    delay = _BASE_DELAY * (2 ** attempt)
                    logger.warning(
                        "STT API %d，%.1fs 后重试 (%d/%d)",
                        resp.status_code, delay, attempt + 1, _MAX_RETRIES,
                    )
                    await asyncio.sleep(delay)
                    continue
                resp.raise_for_status()
                data = resp.json()
                return data.get("text", "")
            except httpx.HTTPStatusError:
                raise
            except Exception as e:
                last_exc = e
                if attempt < _MAX_RETRIES:
                    delay = _BASE_DELAY * (2 ** attempt)
                    logger.warning(
                        "STT 请求异常，%.1fs 后重试 (%d/%d): %s",
                        delay, attempt + 1, _MAX_RETRIES, e,
                    )
                    await asyncio.sleep(delay)
        raise last_exc or RuntimeError("STT 请求失败")

    async def synthesize(self, text: str) -> tuple[bytes, str]:
        """文字转语音。

        POST {tts_base_url}/audio/speech (JSON body)
        返回 (音频字节, MIME 类型)，失败时抛出异常。
        """
        if not self.tts_enabled:
            raise RuntimeError("TTS 未配置")

        url = f"{self._tts_base_url}/audio/speech"
        headers = {
            "Authorization": f"Bearer {self._tts_api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self._tts_model,
            "voice": self._tts_voice,
            "input": text,
        }

        last_exc: Exception | None = None
        for attempt in range(_MAX_RETRIES + 1):
            try:
                async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                    resp = await client.post(url, headers=headers, json=payload)
                if resp.status_code in _RETRYABLE_STATUS and attempt < _MAX_RETRIES:
                    delay = _BASE_DELAY * (2 ** attempt)
                    logger.warning(
                        "TTS API %d，%.1fs 后重试 (%d/%d)",
                        resp.status_code, delay, attempt + 1, _MAX_RETRIES,
                    )
                    await asyncio.sleep(delay)
                    continue
                resp.raise_for_status()
                content_type = resp.headers.get("content-type", "audio/mpeg")
                # 取主类型（去掉参数如 charset）
                mime = content_type.split(";")[0].strip()
                return resp.content, mime
            except httpx.HTTPStatusError:
                raise
            except Exception as e:
                last_exc = e
                if attempt < _MAX_RETRIES:
                    delay = _BASE_DELAY * (2 ** attempt)
                    logger.warning(
                        "TTS 请求异常，%.1fs 后重试 (%d/%d): %s",
                        delay, attempt + 1, _MAX_RETRIES, e,
                    )
                    await asyncio.sleep(delay)
        raise last_exc or RuntimeError("TTS 请求失败")
