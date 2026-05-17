"""Embedding generation and semantic search.

Supports two providers:
  - "openai" (default): Uses OpenAI text-embedding-3-small (requires OPENAI_API_KEY)
  - "local": Uses sentence-transformers on CPU/GPU (requires sentence-transformers pip package)
"""

import asyncio
import logging

import numpy as np
import openai

from .config import OPENAI_API_KEY, EMBEDDING_PROVIDER, LOCAL_EMBEDDING_MODEL, SEMANTIC_SIMILARITY_THRESHOLD

log = logging.getLogger("oracle.embeddings")

SKIP_MARKERS = {"(no audio)", "(no speech)", "(not a video)", "(too long)", "(unavailable)"}

openai_client: openai.AsyncOpenAI | None = None
local_model = None
embedding_dim: int | None = None


def get_openai_client():
    global openai_client
    if openai_client is None:
        openai_client = openai.AsyncOpenAI(api_key=OPENAI_API_KEY)
    return openai_client


def get_local_model():
    global local_model, embedding_dim
    if local_model is None:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError:
            raise RuntimeError(
                "sentence-transformers is required for EMBEDDING_PROVIDER=local. "
                "Install it with: pip install sentence-transformers"
            )
        log.info("Loading local embedding model: %s", LOCAL_EMBEDDING_MODEL)
        local_model = SentenceTransformer(LOCAL_EMBEDDING_MODEL)
        embedding_dim = local_model.get_sentence_embedding_dimension()
        log.info("Local embedding model loaded (dim=%d)", embedding_dim)
    return local_model


def get_embedding_dim():
    if EMBEDDING_PROVIDER == "local":
        get_local_model()
        return embedding_dim
    return 1536


def should_embed(text: str | None):
    if not text or text.strip() in SKIP_MARKERS:
        return False
    return len(text.strip()) > 5


def to_blob(vec):
    return np.array(vec, dtype=np.float32).tobytes()


def handle_openai_error(e, context="embedding"):
    """Shared error handler for OpenAI embedding calls. Returns True if handled."""
    if isinstance(e, openai.AuthenticationError):
        log.error("OpenAI API key is invalid -- %s disabled until restart", context)
        return True
    if isinstance(e, openai.RateLimitError):
        msg = str(e).lower()
        if "insufficient_quota" in msg or "billing" in msg or "exceeded" in msg:
            log.error("OpenAI account has no credits -- %s skipped", context)
        else:
            log.warning("Embedding rate limited, skipping: %s", e)
        return True
    return False


async def generate_embedding(text: str):
    """Generate an embedding for text, returned as raw bytes for SQLite BLOB storage."""
    if not should_embed(text):
        return None
    try:
        if EMBEDDING_PROVIDER == "local":
            model = get_local_model()
            vec = await asyncio.to_thread(model.encode, text[:8000])
            return to_blob(vec)
        else:
            resp = await get_openai_client().embeddings.create(
                model="text-embedding-3-small", input=text[:8000],
            )
            return to_blob(resp.data[0].embedding)
    except Exception as e:
        if not handle_openai_error(e, "embedding"):
            log.warning("Failed to generate embedding", exc_info=True)
        return None


async def generate_embeddings_batch(texts: list[str]):
    """Generate embeddings for multiple texts in a single call."""
    valid = [(i, t[:8000]) for i, t in enumerate(texts) if should_embed(t)]
    results: list[bytes | None] = [None] * len(texts)
    if not valid:
        return results

    try:
        if EMBEDDING_PROVIDER == "local":
            model = get_local_model()
            batch_texts = [t for _, t in valid]
            vecs = await asyncio.to_thread(model.encode, batch_texts)
            for (orig_idx, _), vec in zip(valid, vecs):
                results[orig_idx] = to_blob(vec)
        else:
            resp = await get_openai_client().embeddings.create(
                model="text-embedding-3-small", input=[t for _, t in valid],
            )
            for (orig_idx, _), emb_data in zip(valid, resp.data):
                results[orig_idx] = to_blob(emb_data.embedding)
    except Exception as e:
        if not handle_openai_error(e, "batch embeddings"):
            log.warning("Failed to generate batch embeddings", exc_info=True)

    return results


def semantic_search(
    query_embedding: bytes,
    candidates: list[tuple[int, bytes]],
    top_k: int = 10,
):
    """Find the closest embeddings by cosine similarity.

    Args:
        query_embedding: raw bytes of the query vector
        candidates: list of (row_id, embedding_bytes) from the database
        top_k: number of results to return

    Returns:
        list of (row_id, similarity_score) sorted by descending similarity
    """
    if not candidates:
        return []

    q = np.frombuffer(query_embedding, dtype=np.float32)
    q_norm = np.linalg.norm(q)
    if q_norm == 0:
        return []

    dim = len(q)
    ids = []
    vecs = []
    for row_id, emb_bytes in candidates:
        v = np.frombuffer(emb_bytes, dtype=np.float32)
        if len(v) == dim:
            ids.append(row_id)
            vecs.append(v)

    if not vecs:
        return []

    matrix = np.stack(vecs)
    norms = np.linalg.norm(matrix, axis=1)
    norms[norms == 0] = 1
    similarities = matrix @ q / (norms * q_norm)

    top_indices = np.argsort(similarities)[::-1][:top_k]
    return [(ids[i], float(similarities[i])) for i in top_indices if similarities[i] > SEMANTIC_SIMILARITY_THRESHOLD]
