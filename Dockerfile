# Voice-First AI Property Scout — one image, both halves.
#
# The Vite bundle is built here and copied into the Python image, so a single
# Railway service serves the UI and the API on one origin. That is what keeps
# CORS out of production entirely: the browser never makes a cross-origin call.
#
# Build context is the REPOSITORY ROOT, not backend/ — the build needs both
# directories. Railway's Root Directory setting must stay empty.

# ─── Stage 1: compile the frontend ───────────────────────────────────────────
FROM node:22-slim AS frontend

WORKDIR /build
# Copy manifests first so `npm ci` is cached until the dependencies change.
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
# No VITE_API_URL: a production build makes api.js use an empty base, so every
# request resolves against whatever origin served the page.
RUN npm run build

# ─── Stage 2: the Python service ─────────────────────────────────────────────
# Two things make this image bigger than a plain Python service:
#   * WeasyPrint renders the shortlist PDF and needs pango/cairo/gdk-pixbuf.
#   * The OpenStreetMap MCP server is a Node package launched over stdio,
#     so Node has to be on PATH inside the container.
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    NODE_MAJOR=22

RUN apt-get update && apt-get install -y --no-install-recommends \
        curl ca-certificates \
        libpango-1.0-0 libpangoft2-1.0-0 libcairo2 libgdk-pixbuf-2.0-0 \
        libffi8 shared-mime-info fonts-dejavu-core \
    && curl -fsSL https://deb.nodesource.com/setup_${NODE_MAJOR}.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Warm the MCP server into the npx cache so the first "what's nearby?" of the
# deployment isn't paying for a package download.
RUN npx -y @cyanheads/openstreetmap-mcp-server --help > /dev/null 2>&1 || true

COPY backend/ .
# main.py looks for the compiled UI here and mounts it when present.
COPY --from=frontend /build/dist ./static

RUN chmod +x entrypoint.sh
EXPOSE 8000
CMD ["./entrypoint.sh"]
