"""
Chat Route
----------
The single conversational endpoint the voice UI talks to.
"""

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from core.conversation import session_store
from core.orchestrator import orchestrator

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/chat", tags=["chat"])


class ChatRequest(BaseModel):
    session_id: str | None = Field(default=None, description="Omit to start a new session")
    text: str = Field(min_length=1, description="Transcribed user speech, or typed text")


@router.post("")
async def chat(request: ChatRequest) -> dict:
    """Process one user turn and return the response with the updated shortlist."""
    session = session_store.get_or_create_session(request.session_id)

    try:
        result = await orchestrator.process(session, request.text.strip())
    except Exception as exc:
        logger.exception("Chat turn failed")
        raise HTTPException(status_code=500, detail=f"Chat processing failed: {exc}")

    session_store.update_session(session)
    return result


@router.get("/{session_id}")
def get_session(session_id: str) -> dict:
    """Return the current state of a session — used on page reload."""
    session = session_store.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found")
    return {
        "session_id": session.session_id,
        "state": session.stage.value,
        "preferences": vars(session.preferences),
        "shortlist": session.shortlist,
        "sources": session.sources,
        "booking": vars(session.booking) if session.booking else None,
        "history": [{"role": m.role, "text": m.text} for m in session.conversation_history],
    }
