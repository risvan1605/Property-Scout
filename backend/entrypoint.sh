#!/usr/bin/env bash
# Container start-up: make sure the data exists, then serve.
set -euo pipefail

# The listings DB and the vector store are build artefacts of seed_db.py, not
# source. On a platform with an ephemeral filesystem they are rebuilt on each
# boot; with a mounted volume this is a no-op after the first start.
DB_PATH="${SQLITE_DB_PATH:-listings.db}"
CHROMA_PATH="${CHROMA_DB_PATH:-chroma_db}"

if [ ! -f "$DB_PATH" ] || [ ! -d "$CHROMA_PATH" ]; then
  echo "Seeding database and vector store…"
  python data/seed_db.py
fi

# Platforms assign the port at runtime.
exec uvicorn main:app --host 0.0.0.0 --port "${PORT:-8000}"
