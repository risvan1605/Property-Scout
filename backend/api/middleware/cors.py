"""
CORS Middleware
---------------
Allows the Vite frontend (local dev + deployed) to call the API.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config import ALLOWED_ORIGIN_REGEX, ALLOWED_ORIGINS


def add_cors(app: FastAPI) -> None:
    """Attach CORS middleware allowing the configured frontend origin."""
    origins = {
        *ALLOWED_ORIGINS,
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    }

    app.add_middleware(
        CORSMiddleware,
        allow_origins=sorted(origins),
        allow_origin_regex=ALLOWED_ORIGIN_REGEX or None,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
