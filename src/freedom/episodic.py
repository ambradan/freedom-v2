"""Episodic memory (Qdrant + fastembed). Principle 2: silence over noise.
Retrieval returns [] unless score >= min_score. Writes always happen (data preserved
even when injection is ablated). Metadata mandatory per design doc 3.4."""
import os
import uuid
from datetime import datetime, timezone
from fastembed import TextEmbedding
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct

_client: QdrantClient | None = None
_embedder: TextEmbedding | None = None
_collection = "freedom_episodic"
_dim = 384


def init(collection: str, embed_model: str):
    global _client, _embedder, _collection
    _collection = collection
    _client = QdrantClient(url=os.environ.get("QDRANT_URL", "http://qdrant:6333"))
    _embedder = TextEmbedding(model_name=embed_model)
    if not _client.collection_exists(_collection):
        _client.create_collection(
            _collection, vectors_config=VectorParams(size=_dim, distance=Distance.COSINE))


def _embed(text: str):
    return list(_embedder.embed([text]))[0].tolist()


def write(text: str, source: str, substrate: str, profile_version: str):
    point = PointStruct(
        id=str(uuid.uuid4()),
        vector=_embed(text),
        payload={
            "text": text,
            "ts": datetime.now(timezone.utc).isoformat(),
            "source": source,
            "substrate": substrate,
            "profile_version": profile_version,
        },
    )
    _client.upsert(_collection, [point])


def retrieve(query: str, k: int, min_score: float) -> list[str]:
    hits = _client.search(_collection, query_vector=_embed(query), limit=k, score_threshold=min_score)
    return [h.payload["text"] for h in hits]
