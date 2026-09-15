"""
Conversation State Manager
--------------------------
Manages session state, user preferences, and conversation flow.
"""

import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class ConversationStage(str, Enum):
    """Stages of the property search conversation."""
    GREETING = "greeting"
    COLLECTING = "collecting"
    CONFIRMING = "confirming"
    SHORTLIST_READY = "shortlist_ready"
    BOOKING = "booking"
    VISIT_BOOKED = "visit_booked"


@dataclass
class UserPreferences:
    """User's stated property search preferences."""
    budget_max: Optional[int] = None
    bedrooms: Optional[int] = None
    neighborhood: Optional[str] = None
    must_have_amenities: list[str] = field(default_factory=list)
    commute_point: Optional[str] = None
    nice_to_haves: list[str] = field(default_factory=list)

    def is_sufficient(self) -> bool:
        """Check if we have enough info to generate a shortlist."""
        return self.budget_max is not None and self.bedrooms is not None


@dataclass
class Message:
    """A single message in the conversation history."""
    role: str  # "user" or "assistant"
    text: str


@dataclass
class BookingSlot:
    """Details of a booked site visit."""
    listing_id: str
    date: str
    time_slot: str
    user_email: str
    confirmation_code: Optional[str] = None
    calendar_link: Optional[str] = None


@dataclass
class ConversationState:
    """Complete state of a user's conversation session."""
    session_id: str
    stage: ConversationStage = ConversationStage.GREETING
    preferences: UserPreferences = field(default_factory=UserPreferences)
    shortlist: list[dict] = field(default_factory=list)
    conversation_history: list[Message] = field(default_factory=list)
    clarification_count: int = 0
    booking: Optional[BookingSlot] = None
    sources: list[dict] = field(default_factory=list)
    # Listings the user narrowed away, each with the reason it was dropped.
    dropped: list[dict] = field(default_factory=list)

    def add_user_message(self, text: str):
        """Add a user message to conversation history."""
        self.conversation_history.append(Message(role="user", text=text))

    def add_assistant_message(self, text: str):
        """Add an assistant message to conversation history."""
        self.conversation_history.append(Message(role="assistant", text=text))

    def get_history_for_llm(self) -> list[dict]:
        """Format conversation history for Gemini, which calls the assistant 'model'."""
        return [
            {"role": "model" if msg.role == "assistant" else "user",
             "parts": [{"text": msg.text}]}
            for msg in self.conversation_history
        ]


class SessionStore:
    """In-memory session store for conversation states."""

    def __init__(self):
        self._sessions: dict[str, ConversationState] = {}

    def create_session(self) -> ConversationState:
        """Create a new conversation session."""
        session_id = str(uuid.uuid4())
        session = ConversationState(session_id=session_id)
        self._sessions[session_id] = session
        return session

    def get_session(self, session_id: str) -> Optional[ConversationState]:
        """Retrieve an existing session by ID."""
        return self._sessions.get(session_id)

    def get_or_create_session(self, session_id: Optional[str] = None) -> ConversationState:
        """Get an existing session or create a new one."""
        if session_id and session_id in self._sessions:
            return self._sessions[session_id]
        return self.create_session()

    def update_session(self, session: ConversationState):
        """Update a session in the store."""
        self._sessions[session.session_id] = session

    def delete_session(self, session_id: str):
        """Remove a session from the store."""
        self._sessions.pop(session_id, None)


# Shared, process-wide session store. Sessions are in-memory only, so they are
# lost on restart — acceptable for a prototype.
session_store = SessionStore()
