"""Per-user resume index on ChromaDB.

Every chunk is tagged with `user_id`, and every search filters on it, so one
user's resume can never show up in another user's results.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

import chromadb
from chromadb.config import Settings as ChromaSettings
from app.config import get_settings
from app.rag.embeddings import Embedder, SentenceTransformerEmbedder

COLLECTION = "resume_chunks"
CHUNK_CHARS = 600
OVERLAP_CHARS = 100


def chunk_text(text: str, size: int = CHUNK_CHARS, overlap: int = OVERLAP_CHARS) -> list[str]:
    """Split on paragraphs/lines first (keeps bullets whole), then pack into ~size-char chunks."""
    pieces: list[str] = []
    for block in re.split(r"\n\s*\n", text):
        block = block.strip()
        if not block:
            continue
        if len(block) <= size:
            pieces.append(block)
            continue
        for line in block.splitlines():  # long section -> go line by line
            line = line.strip()
            while len(line) > size:  # a single huge line: hard split
                pieces.append(line[:size])
                line = line[size - overlap:]
            if line:
                pieces.append(line)

    chunks: list[str] = []
    current = ""
    for piece in pieces:
        if current and len(current) + len(piece) + 1 > size:
            chunks.append(current)
            current = current[-overlap:] + "\n" + piece  # carry a little context forward
        else:
            current = f"{current}\n{piece}" if current else piece
    if current:
        chunks.append(current)
    return chunks


@dataclass
class Hit:
    text: str
    score: float  # cosine similarity, 1.0 = identical


class ResumeIndex:
    def __init__(self, client: chromadb.ClientAPI, embedder: Embedder):
        self.embedder = embedder
        self.col = client.get_or_create_collection(COLLECTION, metadata={"hnsw:space": "cosine"})

    def index_resume(self, user_id: int, resume_id: int, text: str) -> int:
        """Replace this user's vectors with chunks from the new resume. Returns chunk count.

        Only the derived vectors are replaced; the resume rows themselves are kept in SQL.
        """
        chunks = chunk_text(text)
        self.col.delete(where={"user_id": user_id})
        if chunks:
            self.col.add(
                ids=[f"u{user_id}-r{resume_id}-c{i}" for i in range(len(chunks))],
                documents=chunks,
                embeddings=self.embedder.embed(chunks),
                metadatas=[{"user_id": user_id, "resume_id": resume_id, "chunk": i} for i in range(len(chunks))],
            )
        return len(chunks)

    def search(self, user_id: int, query: str, k: int = 4) -> list[Hit]:
        if not query.strip():
            return []
        res = self.col.query(
            query_embeddings=self.embedder.embed([query]),
            n_results=k,
            where={"user_id": user_id},
        )
        docs = res.get("documents", [[]])[0]
        dists = res.get("distances", [[]])[0]
        return [Hit(text=d, score=round(1 - dist, 4)) for d, dist in zip(docs, dists)]


@lru_cache
def get_resume_index() -> ResumeIndex:
    s = get_settings()
    client = chromadb.PersistentClient(path=s.chroma_path, settings=ChromaSettings(anonymized_telemetry=False))
    return ResumeIndex(client, SentenceTransformerEmbedder(s.embedding_model))
