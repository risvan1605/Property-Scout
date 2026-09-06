"""
Neighborhood RAG Retriever
--------------------------
Semantic search over the neighborhood guide chunks stored in ChromaDB by
`data/seed_db.py`. Returns raw chunks with citations — it deliberately does
NOT generate an answer; the orchestrator grounds its own response on these.
"""

import json
import logging
import os
from typing import Optional

from config import CHROMA_DB_PATH, EMBEDDING_MODEL, GEMINI_API_KEY

logger = logging.getLogger(__name__)

COLLECTION_NAME = "neighborhoods"
DEFAULT_N_RESULTS = 5

# Lazily created singletons — building either is expensive.
_chroma_collection = None
_genai_client = None


class RAGUnavailable(RuntimeError):
    """Raised when the vector store or embedding client cannot be reached."""


def _get_collection():
    """Open (once) the persistent ChromaDB collection of neighborhood chunks."""
    global _chroma_collection
    if _chroma_collection is not None:
        return _chroma_collection

    if not os.path.exists(CHROMA_DB_PATH):
        raise RAGUnavailable(
            f"ChromaDB store not found at {CHROMA_DB_PATH}. Run `python data/seed_db.py`."
        )

    import chromadb

    client = chromadb.PersistentClient(path=CHROMA_DB_PATH)
    try:
        _chroma_collection = client.get_collection(name=COLLECTION_NAME)
    except Exception as exc:  # collection missing / corrupt store
        raise RAGUnavailable(f"ChromaDB collection '{COLLECTION_NAME}' unavailable: {exc}")
    return _chroma_collection


def _get_genai_client():
    """Open (once) the Gemini client used for query embeddings."""
    global _genai_client
    if _genai_client is not None:
        return _genai_client

    if not GEMINI_API_KEY or GEMINI_API_KEY == "your_gemini_api_key_here":
        raise RAGUnavailable("GEMINI_API_KEY is not set in backend/.env")

    from google import genai

    _genai_client = genai.Client(api_key=GEMINI_API_KEY)
    return _genai_client


def _embed_query(text: str) -> list[float]:
    """Embed a query with the same model used at ingestion time."""
    client = _get_genai_client()
    result = client.models.embed_content(model=EMBEDDING_MODEL, contents=text)
    return list(result.embeddings[0].values)


def list_known_neighborhoods() -> list[str]:
    """Return the neighborhoods that actually have guide data indexed."""
    try:
        collection = _get_collection()
    except RAGUnavailable:
        return []
    metadatas = collection.get(include=["metadatas"]).get("metadatas") or []
    return sorted({m.get("neighborhood") for m in metadatas if m.get("neighborhood")})


def _normalize(name: str) -> str:
    return name.strip().lower().replace("_", " ")


def _resolve_neighborhood(neighborhood: Optional[str]) -> Optional[str]:
    """Map a spoken neighborhood name to the exact indexed value, if known."""
    if not neighborhood:
        return None
    target = _normalize(neighborhood)
    for known in list_known_neighborhoods():
        if _normalize(known) == target:
            return known
    return None


def _chunk_from_result(doc: str, meta: dict, distance: float) -> dict:
    """Shape one ChromaDB hit into a citable chunk."""
    source_urls = meta.get("source_urls") or "[]"
    if isinstance(source_urls, str):
        try:
            source_urls = json.loads(source_urls)
        except json.JSONDecodeError:
            source_urls = [source_urls]

    return {
        "text": doc,
        "neighborhood": meta.get("neighborhood"),
        "section_title": meta.get("section_title", "General"),
        "source_url": source_urls[0] if source_urls else None,
        "source_urls": source_urls,
        "source_file": meta.get("source_file"),
        "chunk_index": meta.get("chunk_index"),
        # Collection uses cosine space, so similarity = 1 - distance.
        "similarity_score": round(1.0 - float(distance), 4),
    }


def retrieve_neighborhood_info(
    neighborhood: Optional[str],
    query: str,
    n_results: int = DEFAULT_N_RESULTS,
    min_similarity: float = 0.0,
) -> dict:
    """
    Retrieve the neighborhood guide chunks most relevant to `query`.

    Args:
        neighborhood: Neighborhood to restrict the search to. If it isn't in
            the index, `has_data` comes back False so the assistant can say
            "I don't have data on that area" instead of guessing.
        query: The user's question, e.g. "safety at night".
        n_results: Number of chunks to return.
        min_similarity: Drop chunks scoring below this. Weak matches are worse
            than none — they invite the model to answer from near-noise.

    Returns:
        {answer, chunks[], neighborhood, has_data, known_neighborhoods,
         filtered_out, error}. `answer` is always None — grounding is the
        orchestrator's job.
    """
    result = {
        "answer": None,
        "chunks": [],
        "neighborhood": neighborhood,
        "has_data": False,
        "known_neighborhoods": [],
        "filtered_out": 0,
        "error": None,
    }

    try:
        collection = _get_collection()
    except RAGUnavailable as exc:
        result["error"] = str(exc)
        logger.warning("RAG unavailable: %s", exc)
        return result

    known = list_known_neighborhoods()
    result["known_neighborhoods"] = known

    resolved = _resolve_neighborhood(neighborhood)
    if neighborhood and resolved is None:
        # We have no guide data for this area — report honestly, don't fall
        # back to other neighborhoods' text.
        return result

    result["neighborhood"] = resolved or neighborhood

    try:
        embedding = _embed_query(query)
    except Exception as exc:
        result["error"] = f"Embedding failed: {exc}"
        logger.warning("RAG embedding failed: %s", exc)
        return result

    where = {"neighborhood": resolved} if resolved else None
    try:
        hits = collection.query(
            query_embeddings=[embedding],
            n_results=n_results,
            where=where,
            include=["documents", "metadatas", "distances"],
        )
    except Exception as exc:
        result["error"] = f"Vector search failed: {exc}"
        logger.warning("RAG query failed: %s", exc)
        return result

    documents = (hits.get("documents") or [[]])[0]
    metadatas = (hits.get("metadatas") or [[]])[0]
    distances = (hits.get("distances") or [[]])[0]

    chunks = [
        _chunk_from_result(doc, meta or {}, dist)
        for doc, meta, dist in zip(documents, metadatas, distances)
    ]
    relevant = [c for c in chunks if c["similarity_score"] >= min_similarity]

    result["filtered_out"] = len(chunks) - len(relevant)
    result["chunks"] = relevant
    result["has_data"] = bool(relevant)
    return result



def get_section(neighborhood: str, section_title: str) -> Optional[dict]:
    """
    Fetch a specific guide section verbatim (e.g. Koramangala / Safety).

    Used for the fixed neighborhood snapshot on each listing card: the section
    is known, so an exact metadata lookup is both faster and more accurate than
    an embedding search — a semantic query for "overview" was returning the
    Safety chunk.
    """
    try:
        collection = _get_collection()
    except RAGUnavailable as exc:
        logger.warning("RAG unavailable: %s", exc)
        return None

    resolved = _resolve_neighborhood(neighborhood)
    if resolved is None:
        return None

    try:
        hits = collection.get(
            where={"$and": [{"neighborhood": resolved}, {"section_title": section_title}]},
            include=["documents", "metadatas"],
        )
    except Exception as exc:
        logger.warning("Section lookup failed: %s", exc)
        return None

    documents = hits.get("documents") or []
    metadatas = hits.get("metadatas") or []
    if not documents:
        return None

    # Long sections span several chunks; stitch them back in order.
    ordered = sorted(zip(documents, metadatas), key=lambda dm: (dm[1] or {}).get("chunk_index", 0))
    chunk = _chunk_from_result("\n\n".join(d for d, _ in ordered), ordered[0][1] or {}, 0.0)
    chunk["similarity_score"] = None  # exact lookup, not a similarity match
    return chunk


# Alias matching the orchestrator's tool-dispatch name in the plan.
retrieve = retrieve_neighborhood_info
