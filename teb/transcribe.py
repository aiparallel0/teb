from __future__ import annotations

import logging
from typing import Optional

import httpx

from teb import config

logger = logging.getLogger(__name__)

SUPPORTED_FORMATS = {"mp3", "mp4", "mpeg", "mpga", "m4a", "wav", "webm", "ogg"}
MAX_FILE_SIZE = 25 * 1024 * 1024  # 25 MB – Whisper API limit
_TRANSCRIBE_TIMEOUT = 60  # seconds; audio transcription can be slow


def transcribe_audio(audio_bytes: bytes, filename: str) -> str:
    """Transcribe audio bytes via the OpenAI Whisper API.

    Returns the transcribed text, or an empty string when no API key is
    configured or the call fails.
    """
    if not config.OPENAI_API_KEY:
        logger.warning("OPENAI_API_KEY not set – skipping transcription")
        return ""

    if len(audio_bytes) > MAX_FILE_SIZE:
        raise ValueError(
            f"Audio file too large ({len(audio_bytes)} bytes). "
            f"Maximum allowed is {MAX_FILE_SIZE} bytes (25 MB)."
        )

    ext = _extension(filename)
    if ext not in SUPPORTED_FORMATS:
        raise ValueError(
            f"Unsupported audio format '.{ext}'. "
            f"Supported: {', '.join(sorted(SUPPORTED_FORMATS))}"
        )

    url = f"{config.OPENAI_BASE_URL.rstrip('/')}/audio/transcriptions"
    headers = {"Authorization": f"Bearer {config.OPENAI_API_KEY}"}

    try:
        with httpx.Client(timeout=_TRANSCRIBE_TIMEOUT) as client:
            resp = client.post(
                url,
                headers=headers,
                files={"file": (filename, audio_bytes)},
                data={"model": "whisper-1", "response_format": "text"},
            )
        if resp.status_code != 200:
            logger.error(
                "Whisper API returned %d: %s", resp.status_code, resp.text[:300]
            )
            return ""
        return resp.text.strip()
    except httpx.HTTPError as exc:
        logger.error("Whisper API request failed: %s", exc)
        return ""


def _extension(filename: str) -> str:
    """Return the lowercase file extension without the leading dot."""
    dot = filename.rfind(".")
    if dot == -1:
        return ""
    return filename[dot + 1 :].lower()
