"""
Text-to-Speech (ElevenLabs)
---------------------------
Turns the scout's reply into speech. The browser's built-in `speechSynthesis`
is the fallback, so a missing key or an exhausted quota degrades to a robotic
voice rather than to silence.

The free tier is small (~10k characters/month), so identical text is served
from an in-process cache and long replies are refused rather than truncated
mid-sentence.
"""

import hashlib
import logging
from collections import OrderedDict

import httpx

from config import (
    ELEVENLABS_API_KEY,
    ELEVENLABS_MODEL,
    ELEVENLABS_VOICE_ID,
    TTS_CACHE_ENTRIES,
    TTS_MAX_CHARS,
)

logger = logging.getLogger(__name__)

API_BASE = "https://api.elevenlabs.io/v1"
OUTPUT_FORMAT = "mp3_44100_128"
REQUEST_TIMEOUT = 30.0

_cache: "OrderedDict[str, bytes]" = OrderedDict()


class TTSUnavailable(RuntimeError):
    """Speech could not be synthesized; the caller should fall back."""


def is_configured() -> bool:
    return bool(ELEVENLABS_API_KEY)


def _cache_key(text: str, voice_id: str) -> str:
    return hashlib.sha256(f"{voice_id}:{ELEVENLABS_MODEL}:{text}".encode()).hexdigest()


def _cache_get(key: str) -> bytes | None:
    if key in _cache:
        _cache.move_to_end(key)
        return _cache[key]
    return None


def _cache_put(key: str, audio: bytes) -> None:
    _cache[key] = audio
    _cache.move_to_end(key)
    while len(_cache) > TTS_CACHE_ENTRIES:
        _cache.popitem(last=False)


async def synthesize(text: str, voice_id: str | None = None) -> tuple[bytes, bool]:
    """
    Render `text` as MP3 audio.

    Returns (audio_bytes, from_cache). Raises TTSUnavailable when the key is
    missing, the text is unusable, or ElevenLabs refuses the request.
    """
    if not is_configured():
        raise TTSUnavailable("ELEVENLABS_API_KEY is not set")

    spoken = (text or "").strip()
    if not spoken:
        raise TTSUnavailable("Nothing to speak")
    if len(spoken) > TTS_MAX_CHARS:
        raise TTSUnavailable(
            f"Reply is {len(spoken)} characters, over the {TTS_MAX_CHARS} limit"
        )

    voice = voice_id or ELEVENLABS_VOICE_ID
    key = _cache_key(spoken, voice)
    cached = _cache_get(key)
    if cached is not None:
        return cached, True

    url = f"{API_BASE}/text-to-speech/{voice}"
    payload = {
        "text": spoken,
        "model_id": ELEVENLABS_MODEL,
        "voice_settings": {"stability": 0.4, "similarity_boost": 0.75, "speed": 1.0},
    }

    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            response = await client.post(
                url,
                params={"output_format": OUTPUT_FORMAT},
                headers={"xi-api-key": ELEVENLABS_API_KEY, "Content-Type": "application/json"},
                json=payload,
            )
    except httpx.HTTPError as exc:
        logger.warning("ElevenLabs unreachable: %s", exc)
        raise TTSUnavailable(f"Speech service unreachable: {exc}")

    if response.status_code == 401:
        logger.error("ElevenLabs rejected the API key")
        raise TTSUnavailable("Speech service rejected the API key")
    if response.status_code == 402:
        # Free accounts may only use premade voices over the API.
        logger.error(
            "ElevenLabs refused voice %s: the plan does not allow it. Set "
            "ELEVENLABS_VOICE_ID to a premade voice (e.g. EXAVITQu4vr4xnSDxMaL).",
            voice,
        )
        raise TTSUnavailable("Speech voice is not available on this plan")
    if response.status_code == 429:
        logger.warning("ElevenLabs quota exhausted")
        raise TTSUnavailable("Speech quota exhausted for now")
    if response.status_code >= 400:
        logger.warning("ElevenLabs error %s: %s", response.status_code, response.text[:200])
        raise TTSUnavailable(f"Speech service error ({response.status_code})")

    audio = response.content
    if not audio:
        raise TTSUnavailable("Speech service returned no audio")

    _cache_put(key, audio)
    return audio, False


async def list_voices() -> list[dict]:
    """Voices available on the account — used to pick ELEVENLABS_VOICE_ID."""
    if not is_configured():
        raise TTSUnavailable("ELEVENLABS_API_KEY is not set")
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        response = await client.get(
            f"{API_BASE}/voices", headers={"xi-api-key": ELEVENLABS_API_KEY}
        )
    response.raise_for_status()
    return [
        {
            "voice_id": v.get("voice_id"),
            "name": v.get("name"),
            "labels": v.get("labels", {}),
        }
        for v in response.json().get("voices", [])
    ]
