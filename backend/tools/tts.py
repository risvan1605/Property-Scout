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


# Set when ElevenLabs itself refuses the key. A 401 is sticky — it will not
# come right on the next request — so remembering it lets /api/health say the
# voice is unavailable instead of reporting "configured" for a key that has been
# revoked. A later success clears it, so rotating the key needs no restart.
_key_rejected = False
# Quota is a different thing entirely: the credential is good, the credits are
# spent, and it comes right on its own when the allowance resets.
_quota_exhausted = False


def is_configured() -> bool:
    return bool(ELEVENLABS_API_KEY)


def runtime_status() -> tuple[bool, str]:
    """Whether high-quality speech can actually be produced, and if not, why.

    Deliberately makes no API call. A key that is merely *present* tells you
    nothing — this reports what the service said the last time it was asked.
    """
    if not ELEVENLABS_API_KEY:
        return False, "ELEVENLABS_API_KEY is not set"
    if _key_rejected:
        return False, "ElevenLabs rejected the API key — it may have been revoked or rotated"
    if _quota_exhausted:
        return False, "ElevenLabs credits are spent — the key is valid; the allowance is not"
    return True, "ok"


def key_shape_warning() -> str:
    """A note when the key does not look like one, or is empty.

    Current ElevenLabs keys are `sk_` followed by ~48 characters. Older ones are
    bare hex, so this only ever warns — a truncated paste or a value from the
    wrong field is far more common than a legacy key, and both produce the same
    401 that reads as "the key was revoked".
    """
    key = ELEVENLABS_API_KEY
    if not key:
        return ""
    if key.startswith("sk_"):
        return "" if len(key) >= 40 else f"key starts with sk_ but is only {len(key)} characters — truncated?"
    if len(key) == 32 and all(c in "0123456789abcdef" for c in key.lower()):
        return ""
    return (
        f"key is {len(key)} characters and does not start with 'sk_' — check that "
        "ELEVENLABS_API_KEY holds the API key and not another value"
    )


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


def _upstream_reason(response) -> str:
    """The slug ElevenLabs uses for a refusal, e.g. "voice_not_found".

    Names a voice id or a model id at worst — never credentials — so it is safe
    to carry back to the caller, where it saves a trip through the host's logs.
    """
    try:
        detail = response.json().get("detail")
    except ValueError:
        return ""
    if isinstance(detail, dict):
        return str(detail.get("status") or detail.get("message") or "")[:120]
    if isinstance(detail, str):
        return detail[:120]
    return ""


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
        # ElevenLabs answers 401 for an exhausted quota as well as for a bad
        # credential, with only the body telling them apart. Reading the status
        # code alone reports a working key as revoked and sends whoever is
        # debugging off to rotate a key that was never the problem.
        reason = _upstream_reason(response)
        if reason == "quota_exceeded":
            global _quota_exhausted
            _quota_exhausted = True
            logger.warning(
                "ElevenLabs credits are spent — the key is fine. Speech falls back "
                "to the browser voice until the quota resets or the plan is upgraded."
            )
            raise TTSUnavailable("Speech quota exhausted for now")
        global _key_rejected
        _key_rejected = True
        logger.error(
            "ElevenLabs rejected the API key (%s) — replace ELEVENLABS_API_KEY. "
            "Speech falls back to the browser voice until it is valid.",
            reason or "no reason given",
        )
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
        globals()["_quota_exhausted"] = True
        logger.warning("ElevenLabs quota exhausted")
        raise TTSUnavailable("Speech quota exhausted for now")
    if response.status_code >= 400:
        # ElevenLabs puts a machine-readable slug in detail.status and a
        # sentence in detail.message. A bare status code says only that the
        # request was refused, not which part of it was wrong — and the part
        # that is wrong here is the voice or the model, which is a
        # configuration mistake someone has to act on.
        reason = _upstream_reason(response)
        logger.error(
            "ElevenLabs refused the request: %s %s (voice=%s, model=%s)",
            response.status_code, reason or response.text[:200], voice, ELEVENLABS_MODEL,
        )
        detail = f"Speech service error ({response.status_code}"
        raise TTSUnavailable(f"{detail}: {reason})" if reason else f"{detail})")

    audio = response.content
    if not audio:
        raise TTSUnavailable("Speech service returned no audio")

    # Proof the key works again, so a rotation takes effect without a restart.
    if _key_rejected or _quota_exhausted:
        globals()["_key_rejected"] = False
        globals()["_quota_exhausted"] = False
        logger.info("ElevenLabs is answering again")

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
