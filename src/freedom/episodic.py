"""Episodic memory (Qdrant + fastembed). Principle 2: silence over noise.
Retrieval returns [] unless score >= min_score. Writes always happen (data preserved
even when injection is ablated). Metadata mandatory per design doc 3.4.

Change 7 (20/7/2026): chunking. L'embedder tronca a 128 token (~500 caratteri misurati):
qualunque scambio piu' lungo era indicizzato solo sul proprio incipit, e i cicli Genesis
- che iniziano con 480 caratteri di preambolo fisso - producevano vettori identici tra
loro (similarita' misurata 1.0). Nessun ciclo Genesis era recuperabile.

Ora ogni scambio viene spezzato: `text` e' il frammento indicizzato, `context` e' la
finestra allargata restituita al chiamante. Il retrieve deduplica per scambio, cosi'
k memorie restano k scambi distinti e non k frammenti dello stesso.
"""
import os
import uuid
from datetime import datetime, timezone

from fastembed import TextEmbedding
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct

_client: QdrantClient | None = None
_embedder: TextEmbedding | None = None
_collection = "freedom_episodic_v2"
_dim = 384
_chunk = 400
_overlap = 80


def init(collection: str, embed_model: str, chunk_chars: int = 400, chunk_overlap: int = 80):
    global _client, _embedder, _collection, _chunk, _overlap
    _collection = collection
    _chunk = chunk_chars
    _overlap = chunk_overlap
    _client = QdrantClient(url=os.environ.get("QDRANT_URL", "http://qdrant:6333"))
    _embedder = TextEmbedding(model_name=embed_model)
    if not _client.collection_exists(_collection):
        _client.create_collection(
            _collection, vectors_config=VectorParams(size=_dim, distance=Distance.COSINE))


def _embed(text: str):
    return list(_embedder.embed([text]))[0].tolist()


GENESIS_BOILERPLATE = "Inizia la risposta con una sola riga: ACTION:"


def strip_boilerplate(text: str) -> str:
    """Il prompt Genesis e' un preambolo fisso: indicizzato produce frammenti identici
    per ogni ciclo, che competono con il contenuto in ogni ricerca. Si toglie da cio' che
    viene indicizzato; il testo integrale resta in full_text."""
    if GENESIS_BOILERPLATE not in text:
        return text
    tail = text.split("Ultimo output Genesis:", 1)
    body = tail[1] if len(tail) > 1 else text
    i = body.find("\nF: ")
    return ("[genesis] " + body[i + 1:]).strip() if i >= 0 else text


def split(text: str, size: int | None = None, overlap: int | None = None):
    """Restituisce [(frammento_indicizzato, finestra_di_contesto), ...].
    La finestra e' larga tre volte il frammento e centrata su di esso: chi legge la memoria
    riceve un pezzo comprensibile, non una frase tagliata a meta'."""
    size = size or _chunk
    overlap = overlap or _overlap
    text = text.strip()
    if len(text) <= size:
        return [(text, text)]
    step = max(1, size - overlap)
    out = []
    for i in range(0, len(text), step):
        piece = text[i:i + size]
        if not piece.strip():
            continue
        if i > 0:                                  # non iniziare a meta' parola
            sp = piece.find(" ")
            if 0 < sp < 40:
                piece = piece[sp + 1:]
        out.append((piece, text[max(0, i - size):min(len(text), i + 2 * size)]))
        if i + size >= len(text):
            break
    return out


def write(text: str, source: str, substrate: str, profile_version: str, ts: str | None = None):
    """Uno scambio diventa N punti con lo stesso parent_id. Il testo integrale resta nel
    payload del primo frammento: nessun contenuto viene perso dal chunking."""
    parent = str(uuid.uuid4())
    stamp = ts or datetime.now(timezone.utc).isoformat()
    pieces = split(strip_boilerplate(text))
    points = []
    for idx, (piece, ctx) in enumerate(pieces):
        payload = {
            "text": piece,
            "context": ctx,
            "parent_id": parent,
            "chunk_index": idx,
            "chunk_total": len(pieces),
            "ts": stamp,
            "source": source,
            "substrate": substrate,
            "profile_version": profile_version,
        }
        if idx == 0:
            payload["full_text"] = text
        points.append(PointStruct(id=str(uuid.uuid4()), vector=_embed(piece), payload=payload))
    _client.upsert(_collection, points)
    return parent


def retrieve(query: str, k: int, min_score: float) -> list[str]:
    """k memorie distinte, non k frammenti. Si cercano piu' candidati del necessario e si
    tiene il frammento migliore per ciascuno scambio."""
    hits = _client.search(_collection, query_vector=_embed(query),
                          limit=max(k * 4, k), score_threshold=min_score)
    seen: dict[str, object] = {}
    for h in hits:
        pid = h.payload.get("parent_id") or h.id
        if pid not in seen:
            seen[pid] = h
        if len(seen) >= k:
            break
    return [h.payload.get("context") or h.payload["text"] for h in seen.values()]
