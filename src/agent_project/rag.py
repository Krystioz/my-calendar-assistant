"""Local RAG index: document chunking, FastEmbed embeddings, and cosine search."""

import json
import math
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from fastembed import TextEmbedding

DOCS_DIR = Path("data/docs")
INDEX_PATH = Path("data/index.json")
FASTEMBED_CACHE = Path(".cache/fastembed")
EMBEDDING_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
MAX_TOP_K = 8

EmbedFn = Callable[[list[str]], list[list[float]]]


@dataclass(frozen=True)
class Chunk:
    id: str
    source: str
    heading: str
    text: str
    embedding: list[float]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "source": self.source,
            "heading": self.heading,
            "text": self.text,
            "embedding": [round(v, 6) for v in self.embedding],
        }


@lru_cache(maxsize=2)
def _get_model(
    model_name: str = EMBEDDING_MODEL, cache_dir: str = str(FASTEMBED_CACHE)
) -> TextEmbedding:
    Path(cache_dir).mkdir(parents=True, exist_ok=True)
    return TextEmbedding(model_name=model_name, cache_dir=cache_dir)


def fastembed_texts(
    texts: list[str],
    model_name: str = EMBEDDING_MODEL,
    cache_dir: Path = FASTEMBED_CACHE,
) -> list[list[float]]:
    """Embed texts locally with FastEmbed, keeping model files in .cache/fastembed."""
    if not texts:
        return []
    model = _get_model(model_name, str(cache_dir))
    vectors = list(model.embed(texts))
    return [_normalize([float(x) for x in vec]) for vec in vectors]


def _normalize(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vec))
    if norm == 0.0:
        return vec
    return [x / norm for x in vec]


def _cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=False))


def split_markdown(source: str, content: str) -> list[tuple[str, str]]:
    """Split a Markdown file by '## ' sections while keeping the document title."""
    lines = content.strip().splitlines()
    doc_title = ""
    sections: list[tuple[str, list[str]]] = []
    current_heading = "Wstęp"
    current_lines: list[str] = []

    for line in lines:
        if line.startswith("# ") and not doc_title:
            doc_title = line[2:].strip()
        elif line.startswith("## "):
            if current_lines:
                sections.append((current_heading, current_lines))
            current_heading = line[3:].strip()
            current_lines = []
        else:
            current_lines.append(line)

    if current_lines:
        sections.append((current_heading, current_lines))

    fragments: list[tuple[str, str]] = []
    for heading, body_lines in sections:
        body = "\n".join(body_lines).strip()
        if not body:
            continue
        header = f"{doc_title} — {heading}" if doc_title else heading
        fragments.append((heading, f"{header}\n{body}"))
    return fragments


def chunk_directory(docs_dir: Path = DOCS_DIR) -> list[tuple[str, str, str, str]]:
    """Return (chunk_id, source_name, heading, text) for every markdown file."""
    raw_chunks: list[tuple[str, str, str, str]] = []
    if not docs_dir.exists():
        return raw_chunks
    for path in sorted(docs_dir.glob("*.md")):
        source = path.name
        sections = split_markdown(source, path.read_text(encoding="utf-8"))
        for idx, (heading, text) in enumerate(sections, start=1):
            chunk_id = f"{source}#chunk-{idx}"
            raw_chunks.append((chunk_id, source, heading, text))
    return raw_chunks


class DocumentIndex:
    """In-memory searchable index backed by a reproducible JSON file on disk."""

    def __init__(
        self,
        chunks: list[Chunk],
        embed_fn: EmbedFn = fastembed_texts,
        model_name: str = EMBEDDING_MODEL,
    ) -> None:
        self.chunks = chunks
        self.embed_fn = embed_fn
        self.model_name = model_name

    @classmethod
    def build(
        cls,
        docs_dir: Path = DOCS_DIR,
        index_path: Path | None = INDEX_PATH,
        embed_fn: EmbedFn = fastembed_texts,
        model_name: str = EMBEDDING_MODEL,
    ) -> "DocumentIndex":
        raw = chunk_directory(docs_dir)
        vectors = embed_fn([text for _, _, _, text in raw])
        chunks = [
            Chunk(
                id=chunk_id,
                source=source,
                heading=heading,
                text=text,
                embedding=_normalize(vec),
            )
            for (chunk_id, source, heading, text), vec in zip(raw, vectors, strict=True)
        ]
        index = cls(chunks=chunks, embed_fn=embed_fn, model_name=model_name)
        if index_path is not None:
            index.save(index_path)
        return index

    @classmethod
    def load(
        cls,
        index_path: Path = INDEX_PATH,
        embed_fn: EmbedFn = fastembed_texts,
    ) -> "DocumentIndex":
        payload = json.loads(index_path.read_text(encoding="utf-8"))
        chunks = [
            Chunk(
                id=item["id"],
                source=item["source"],
                heading=item.get("heading", ""),
                text=item["text"],
                embedding=_normalize([float(x) for x in item["embedding"]]),
            )
            for item in payload["chunks"]
        ]
        return cls(
            chunks=chunks,
            embed_fn=embed_fn,
            model_name=payload.get("model", EMBEDDING_MODEL),
        )

    @classmethod
    def load_or_build(
        cls,
        docs_dir: Path = DOCS_DIR,
        index_path: Path = INDEX_PATH,
        embed_fn: EmbedFn = fastembed_texts,
    ) -> "DocumentIndex":
        if index_path.exists():
            return cls.load(index_path, embed_fn=embed_fn)
        return cls.build(docs_dir=docs_dir, index_path=index_path, embed_fn=embed_fn)

    def save(self, index_path: Path = INDEX_PATH) -> None:
        index_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "model": self.model_name,
            "chunk_count": len(self.chunks),
            "chunks": [c.to_dict() for c in self.chunks],
        }
        index_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def search(self, query: str, top_k: int = 3) -> dict[str, Any]:
        """Embed the query and return the top_k most similar fragments with sources."""
        if not self.chunks:
            return {"query": query, "returned": 0, "fragments": []}
        q_vec = _normalize(self.embed_fn([query])[0])
        scored = [(_cosine(q_vec, chunk.embedding), chunk) for chunk in self.chunks]
        scored.sort(key=lambda pair: pair[0], reverse=True)
        top = scored[: max(1, min(top_k, MAX_TOP_K))]
        return {
            "query": query,
            "returned": len(top),
            "fragments": [
                {
                    "id": chunk.id,
                    "source": chunk.source,
                    "heading": chunk.heading,
                    "score": round(score, 4),
                    "text": chunk.text,
                }
                for score, chunk in top
            ],
        }
