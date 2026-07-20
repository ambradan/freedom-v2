"""Episodic memory (Qdrant + fastembed). Principle 2: silence over noise.
Retrieval returns [] unless score >= min_score. Writes always happen (data preserved
even when injection is ablated). Metadata mandatory per design doc 3.4.

Change 7 (20/7/2026): chunking. L'embedder denso tronca a 128 token (~500 caratteri
misurati): gli scambi lunghi erano indicizzati solo sul proprio incipit, e i cicli Genesis
- aperti da 480 caratteri di preambolo fisso - producevano vettori identici tra loro
(similarita' misurata 1.0). Nessun ciclo Genesis era recuperabile.

Change 8 (20/7/2026): ricerca ibrida. Il modello denso e' un bi-encoder addestrato su
parafrasi: una domanda tende a matchare testi che pongono la stessa domanda piu' che
testi che la rispondono (asimmetria query/documento). Si affianca un indice lessicale
BM25, che aggancia le parole invece del significato, e si fondono i due ranking con RRF.
Il modello denso non cambia: la semantica resta quella pre-registrata, si aggiunge un
secondo canale. `hybrid: false` in config riporta al comportamento solo-denso.
"""
import os
import uuid
from datetime import datetime, timezone

from fastembed import SparseTextEmbedding, TextEmbedding
from qdrant_client import QdrantClient, models
from qdrant_client.models import Distance, PointStruct, VectorParams

_client: QdrantClient | None = None
_embedder: TextEmbedding | None = None
_sparse: SparseTextEmbedding | None = None
_collection = "freedom_episodic_v3"
_dim = 384
_chunk = 400
_overlap = 80
_hybrid = True
_sparse_limit = 20

DENSE, SPARSE = "dense", "bm25"
GENESIS_BOILERPLATE = "Inizia la risposta con una sola riga: ACTION:"


def init(collection: str, embed_model: str, chunk_chars: int = 400, chunk_overlap: int = 80,
         hybrid: bool = True, sparse_model: str = "Qdrant/bm25",
         sparse_language: str = "italian", sparse_limit: int = 20):
    global _client, _embedder, _sparse, _collection, _chunk, _overlap, _hybrid, _sparse_limit
    _collection, _chunk, _overlap = collection, chunk_chars, chunk_overlap
    _hybrid, _sparse_limit = hybrid, sparse_limit
    _client = QdrantClient(url=os.environ.get("QDRANT_URL", "http://qdrant:6333"))
    _embedder = TextEmbedding(model_name=embed_model)
    _sparse = SparseTextEmbedding(model_name=sparse_model, language=sparse_language) if hybrid else None
    if not _client.collection_exists(_collection):
        _client.create_collection(
            _collection,
            vectors_config={DENSE: VectorParams(size=_dim, distance=Distance.COSINE)},
            sparse_vectors_config={
                SPARSE: models.SparseVectorParams(modifier=models.Modifier.IDF)},
        )


def _embed(text: str):
    return list(_embedder.embed([text]))[0].tolist()


def _embed_sparse(text: str, is_query: bool = False):
    gen = _sparse.query_embed(text) if is_query else _sparse.embed([text])
    e = list(gen)[0]
    return models.SparseVector(indices=e.indices.tolist(), values=e.values.tolist())


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
            "text": piece, "context": ctx, "parent_id": parent,
            "chunk_index": idx, "chunk_total": len(pieces), "ts": stamp,
            "source": source, "substrate": substrate, "profile_version": profile_version,
        }
        if idx == 0:
            payload["full_text"] = text
        vec = {DENSE: _embed(piece)}
        if _hybrid:
            vec[SPARSE] = _embed_sparse(piece)
        points.append(PointStruct(id=str(uuid.uuid4()), vector=vec, payload=payload))
    _client.upsert(_collection, points)
    return parent


def _dedup(points, k: int) -> list[str]:
    """k memorie distinte, non k frammenti dello stesso scambio."""
    seen: dict[str, object] = {}
    for h in points:
        pid = h.payload.get("parent_id") or str(h.id)
        if pid not in seen:
            seen[pid] = h
        if len(seen) >= k:
            break
    return [h.payload.get("context") or h.payload["text"] for h in seen.values()]


def retrieve(query: str, k: int, min_score: float) -> list[str]:
    """Ramo denso: soglia min_score, identica al comportamento pre-change 8.
    Ramo lessicale: BM25, per le domande dove conta la parola e non la parafrasi.
    Fusione RRF: il punteggio fuso non e' una similarita' coseno, quindi min_score non
    si applica al risultato finale ma al solo ramo denso."""
    if not _hybrid:
        hits = _client.query_points(
            _collection, query=_embed(query), using=DENSE,
            limit=max(k * 4, k), score_threshold=min_score, with_payload=True).points
        return _dedup(hits, k)
    res = _client.query_points(
        _collection,
        prefetch=[
            models.Prefetch(query=_embed(query), using=DENSE,
                            limit=max(k * 4, k), score_threshold=min_score),
            models.Prefetch(query=_embed_sparse(query, is_query=True), using=SPARSE,
                            limit=_sparse_limit),
        ],
        query=models.FusionQuery(fusion=models.Fusion.RRF),
        limit=max(k * 4, k), with_payload=True,
    ).points
    return _dedup(res, k)
