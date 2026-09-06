"""
Speech Route
------------
Renders the scout's reply as audio. A 503 here is expected and survivable —
the browser falls back to its own voice.
"""

import logging

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, Field

from tools.tts import TTSUnavailable, is_configured, synthesize

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/tts", tags=["speech"])


class SpeechRequest(BaseModel):
    text: str = Field(min_length=1, description="The reply to speak")
    voice_id: str | None = None


@router.post("")
async def speak(request: SpeechRequest) -> Response:
    """Return MP3 audio for the given text."""
    try:
        audio, cached = await synthesize(request.text, request.voice_id)
    except TTSUnavailable as exc:
        # Not an error worth alarming the user about; the client falls back.
        raise HTTPException(status_code=503, detail=str(exc))

    return Response(
        content=audio,
        media_type="audio/mpeg",
        headers={"Cache-Control": "no-store", "X-TTS-Cached": "1" if cached else "0"},
    )


@router.get("/status")
def status() -> dict:
    """Whether high-quality speech is available, so the UI can skip trying."""
    return {"configured": is_configured()}
