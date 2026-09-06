"""
Database Seeding Script
-----------------------
Loads listings.json into SQLite and neighborhood text files into ChromaDB.

Usage:
    cd backend/
    source .venv/bin/activate
    python data/seed_db.py

This script:
1. Creates/recreates listings.db with 15 rental listings
2. Chunks neighborhood text files, embeds them with text-embedding-004,
   and stores them in ChromaDB for RAG retrieval
"""

import json
import os
import re
import sqlite3
import sys

# Add parent directory so we can import config later if needed
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

try:
    import chromadb
    from google import genai
    from dotenv import load_dotenv
except ImportError as e:
    print(f"Missing dependency: {e}")
    print("Run: pip install -r requirements.txt")
    sys.exit(1)


# ─── Configuration ────────────────────────────────────────────────────────────

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BACKEND_DIR = os.path.dirname(BASE_DIR)

LISTINGS_JSON = os.path.join(BASE_DIR, "listings.json")
NEIGHBORHOODS_DIR = os.path.join(BASE_DIR, "neighborhoods")
# Same overrides as config.py, so seeding writes where the app will read.
SQLITE_DB = os.getenv("SQLITE_DB_PATH") or os.path.join(BACKEND_DIR, "listings.db")
CHROMA_DIR = os.getenv("CHROMA_DB_PATH") or os.path.join(BACKEND_DIR, "chroma_db")

# Chunking parameters
CHUNK_SIZE = 250       # words per chunk (proxy for ~330 tokens)
CHUNK_OVERLAP = 50     # words carried into the next chunk
EMBEDDING_MODEL = "gemini-embedding-001"

# Filename stem → display name, so RAG metadata matches the neighborhood values
# in listings.db exactly (e.g. "HSR Layout", not "Hsr Layout").
NEIGHBORHOOD_DISPLAY_NAMES = {
    "hsr_layout": "HSR Layout",
    "koramangala": "Koramangala",
    "indiranagar": "Indiranagar",
}


# ─── SQLite Seeding ──────────────────────────────────────────────────────────

def seed_sqlite():
    """Load listings.json into SQLite database."""
    print("\n📦 Seeding SQLite database...")

    # Load listings
    with open(LISTINGS_JSON, "r") as f:
        listings = json.load(f)
    print(f"   Loaded {len(listings)} listings from listings.json")

    # Remove existing DB if present
    if os.path.exists(SQLITE_DB):
        os.remove(SQLITE_DB)
        print(f"   Removed existing {os.path.basename(SQLITE_DB)}")

    # Create database and table
    conn = sqlite3.connect(SQLITE_DB)
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE listings (
            id TEXT PRIMARY KEY,
            society_name TEXT NOT NULL,
            neighborhood TEXT NOT NULL,
            rent INTEGER NOT NULL,
            bedrooms INTEGER NOT NULL,
            sqft INTEGER,
            furnishing TEXT,
            amenities TEXT,
            latitude REAL NOT NULL,
            longitude REAL NOT NULL,
            availability TEXT,
            source_url TEXT,
            scraped_at TEXT
        )
    """)

    # Insert listings
    for listing in listings:
        cursor.execute("""
            INSERT INTO listings
            (id, society_name, neighborhood, rent, bedrooms, sqft, furnishing,
             amenities, latitude, longitude, availability, source_url, scraped_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            listing["id"],
            listing["society_name"],
            listing["neighborhood"],
            listing["rent"],
            listing["bedrooms"],
            listing.get("sqft"),
            listing.get("furnishing"),
            json.dumps(listing.get("amenities", [])),
            listing["latitude"],
            listing["longitude"],
            listing.get("availability", "available"),
            listing.get("source_url"),
            listing.get("scraped_at"),
        ))

    conn.commit()

    # Verify
    count = cursor.execute("SELECT COUNT(*) FROM listings").fetchone()[0]
    print(f"   ✅ Inserted {count} rows into listings.db")

    # Show summary by neighborhood
    rows = cursor.execute(
        "SELECT neighborhood, COUNT(*), MIN(rent), MAX(rent) "
        "FROM listings GROUP BY neighborhood"
    ).fetchall()
    for neighborhood, cnt, min_rent, max_rent in rows:
        print(f"      {neighborhood}: {cnt} listings (₹{min_rent:,} – ₹{max_rent:,})")

    conn.close()


# ─── Text Chunking ───────────────────────────────────────────────────────────

def strip_header_comments(text):
    """Drop the leading `# Sources:` comment block so it never lands in a chunk."""
    lines = text.split("\n")
    i = 0
    while i < len(lines) and (not lines[i].strip() or lines[i].startswith("# ")):
        # A "## " line is a real section heading, not a header comment.
        if lines[i].startswith("## "):
            break
        i += 1
    return "\n".join(lines[i:]).strip()


def split_sections(text):
    """Split a neighborhood file into (section_title, body) pairs on `## ` headings."""
    sections = []
    title = "General"
    body = []
    for line in text.split("\n"):
        if line.startswith("## "):
            if body:
                sections.append((title, "\n".join(body).strip()))
            title = line.lstrip("# ").strip()
            body = []
        else:
            body.append(line)
    if body:
        sections.append((title, "\n".join(body).strip()))
    return [(t, b) for t, b in sections if b]


def chunk_text(text, chunk_size=CHUNK_SIZE, overlap=CHUNK_OVERLAP):
    """
    Split a neighborhood file into retrievable chunks.

    Sections (`## ` headings) always start a new chunk, so every chunk can be
    cited by the section it came from. Within a section, paragraphs are packed
    up to `chunk_size` words (a proxy for tokens) with `overlap` words carried
    into the next chunk.

    Returns a list of {"text", "section_title"} dicts.
    """
    chunks = []

    for section_title, body in split_sections(strip_header_comments(text)):
        paragraphs = [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip()]

        current = []
        current_words = 0

        def flush():
            """Emit the pending chunk and return the overlap tail to carry over."""
            nonlocal current, current_words
            joined = "\n\n".join(current)
            chunks.append({"text": joined, "section_title": section_title})
            tail = " ".join(joined.split()[-overlap:]) if overlap else ""
            current = [tail] if tail else []
            current_words = len(tail.split())

        for para in paragraphs:
            para_words = len(para.split())

            # A single oversized paragraph is packed sentence by sentence.
            if para_words > chunk_size:
                for sentence in re.split(r"(?<=[.!?])\s+", para):
                    sentence_words = len(sentence.split())
                    if current_words + sentence_words > chunk_size and current:
                        flush()
                    current.append(sentence)
                    current_words += sentence_words
                continue

            if current_words + para_words > chunk_size and current:
                flush()
            current.append(para)
            current_words += para_words

        if current and " ".join(current).strip():
            chunks.append({"text": "\n\n".join(current), "section_title": section_title})

    return chunks


def extract_source_urls(text):
    """Extract source URLs from the header comments of a neighborhood file."""
    urls = []
    for line in text.split("\n"):
        if line.startswith("# -"):
            url_match = re.search(r"https?://[^\s]+", line)
            if url_match:
                urls.append(url_match.group())
        elif not line.startswith("#") and line.strip():
            break  # Stop after header comments
    return urls


# ─── ChromaDB Seeding ────────────────────────────────────────────────────────

def seed_chromadb():
    """Chunk neighborhood files, embed, and store in ChromaDB."""
    print("\n🧠 Seeding ChromaDB vector store...")

    # Load environment for API key
    load_dotenv(os.path.join(BACKEND_DIR, ".env"))
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key or api_key == "your_gemini_api_key_here":
        print("   ⚠️  GEMINI_API_KEY not set in .env — skipping ChromaDB seeding.")
        print("   Set your API key in backend/.env and re-run this script.")
        return

    genai_client = genai.Client(api_key=api_key)

    # Initialize ChromaDB
    if os.path.exists(CHROMA_DIR):
        import shutil
        shutil.rmtree(CHROMA_DIR)
        print(f"   Removed existing {os.path.basename(CHROMA_DIR)}/")

    chroma_client = chromadb.PersistentClient(path=CHROMA_DIR)
    collection = chroma_client.create_collection(
        name="neighborhoods",
        metadata={"hnsw:space": "cosine"}
    )

    # Process each neighborhood file
    neighborhood_files = [
        f for f in os.listdir(NEIGHBORHOODS_DIR)
        if f.endswith(".txt")
    ]

    total_chunks = 0
    for filename in sorted(neighborhood_files):
        filepath = os.path.join(NEIGHBORHOODS_DIR, filename)
        stem = os.path.splitext(filename)[0]
        neighborhood_name = NEIGHBORHOOD_DISPLAY_NAMES.get(
            stem, stem.replace("_", " ").title()
        )

        print(f"\n   Processing: {filename} ({neighborhood_name})")

        with open(filepath, "r") as f:
            text = f.read()

        source_urls = extract_source_urls(text)
        chunks = chunk_text(text)
        documents = [c["text"] for c in chunks]
        print(f"   → {len(chunks)} chunks created")

        # Embed all chunks
        embeddings = []
        for document in documents:
            result = genai_client.models.embed_content(
                model=EMBEDDING_MODEL,
                contents=document,
            )
            embeddings.append(result.embeddings[0].values)

        # Store in ChromaDB
        ids = [f"{os.path.splitext(filename)[0]}_chunk_{i}" for i in range(len(chunks))]
        metadatas = [
            {
                "source_file": filename,
                "source_urls": json.dumps(source_urls),
                "section_title": chunk["section_title"],
                "chunk_index": i,
                "neighborhood": neighborhood_name,
            }
            for i, chunk in enumerate(chunks)
        ]

        collection.add(
            ids=ids,
            documents=documents,
            embeddings=embeddings,
            metadatas=metadatas,
        )

        total_chunks += len(chunks)

    # Verify
    final_count = collection.count()
    print(f"\n   ✅ ChromaDB seeded: {final_count} chunks total")
    assert final_count == total_chunks, f"Expected {total_chunks}, got {final_count}"

    # Test a sample query
    print("\n   🔍 Test query: 'safety at night in Koramangala'")
    test_result = genai_client.models.embed_content(
        model=EMBEDDING_MODEL,
        contents="safety at night in Koramangala",
    )
    results = collection.query(
        query_embeddings=[test_result.embeddings[0].values],
        n_results=3,
    )
    for i, (doc, meta) in enumerate(zip(results["documents"][0], results["metadatas"][0])):
        print(f"      Result {i+1} [{meta['neighborhood']}] "
              f"({meta['section_title']}): {doc[:80]}...")


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("  Voice-First AI Property Scout — Database Seeder")
    print("=" * 60)

    seed_sqlite()
    seed_chromadb()

    print("\n" + "=" * 60)
    print("  ✅ Seeding complete!")
    print("=" * 60)


if __name__ == "__main__":
    main()
