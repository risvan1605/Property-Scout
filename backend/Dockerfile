# Voice-First AI Property Scout — backend
#
# Two things make this image bigger than a plain Python service:
#   * WeasyPrint renders the shortlist PDF and needs pango/cairo/gdk-pixbuf.
#   * The OpenStreetMap MCP server is a Node package launched over stdio,
#     so Node has to be on PATH inside the container.
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    NODE_MAJOR=22

# WeasyPrint's native dependencies + Node.js for the MCP server.
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl ca-certificates \
        libpango-1.0-0 libpangoft2-1.0-0 libcairo2 libgdk-pixbuf-2.0-0 \
        libffi8 shared-mime-info fonts-dejavu-core \
    && curl -fsSL https://deb.nodesource.com/setup_${NODE_MAJOR}.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Warm the MCP server into the npx cache so the first "what's nearby?" of the
# deployment isn't paying for a package download.
RUN npx -y @cyanheads/openstreetmap-mcp-server --help > /dev/null 2>&1 || true

COPY . .

RUN chmod +x entrypoint.sh
EXPOSE 8000
CMD ["./entrypoint.sh"]
