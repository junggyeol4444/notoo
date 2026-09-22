"""의미 검색 (기획안 41번).

기획안은 pgvector를 쓴다고 돼 있다. 여기서는 그 자리에 들어갈 인터페이스를
정의하고, SQLite에서도 도는 순수 파이썬 구현을 기본으로 둔다.
PostgreSQL로 옮길 때 PgVectorIndex만 채우면 된다.

임베딩 모델은 이 모듈이 정하지 않는다. 로컬 임베딩 서버든 다른 무엇이든
`Embedder` 프로토콜만 만족하면 된다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from novel_factory.database.models import MemoryChunk


class Embedder(Protocol):
    """문장을 벡터로 바꾸는 무엇."""

    dimension: int

    def embed(self, texts: list[str]) -> list[list[float]]: ...


@dataclass(slots=True)
class SearchHit:
    chunk_id: int
    episode_number: int | None
    kind: str
    content: str
    score: float

    def as_dict(self) -> dict[str, object]:
        return {
            "chunk_id": self.chunk_id,
            "episode_number": self.episode_number,
            "kind": self.kind,
            "content": self.content,
            "score": round(self.score, 4),
        }


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


class VectorIndex:
    """의미 검색 인터페이스."""

    def add(
        self,
        session: Session,
        novel_id: int,
        content: str,
        *,
        kind: str = "scene",
        episode_number: int | None = None,
        embedding: list[float] | None = None,
        meta: dict | None = None,
    ) -> MemoryChunk:
        raise NotImplementedError

    def search(
        self,
        session: Session,
        novel_id: int,
        query_embedding: list[float],
        *,
        kind: str | None = None,
        max_episode: int | None = None,
        top_k: int = 5,
    ) -> list[SearchHit]:
        raise NotImplementedError


class SqliteVectorIndex(VectorIndex):
    """파이썬에서 코사인 유사도를 계산한다.

    회차 수천 편까지는 이걸로 충분하다. 한 작품의 조각만 메모리로 올려
    비교하기 때문에, 250화 * 회차당 수 개 조각이면 수천 건 수준이다.
    그 이상 규모나 여러 작품 동시 검색이 필요해지면 pgvector로 간다.
    """

    def add(
        self,
        session: Session,
        novel_id: int,
        content: str,
        *,
        kind: str = "scene",
        episode_number: int | None = None,
        embedding: list[float] | None = None,
        meta: dict | None = None,
    ) -> MemoryChunk:
        chunk = MemoryChunk(
            novel_id=novel_id,
            episode_number=episode_number,
            kind=kind,
            content=content,
            embedding=embedding,
            meta=meta or {},
        )
        session.add(chunk)
        session.flush()
        return chunk

    def search(
        self,
        session: Session,
        novel_id: int,
        query_embedding: list[float],
        *,
        kind: str | None = None,
        max_episode: int | None = None,
        top_k: int = 5,
    ) -> list[SearchHit]:
        stmt = select(MemoryChunk).where(MemoryChunk.novel_id == novel_id)
        if kind:
            stmt = stmt.where(MemoryChunk.kind == kind)
        if max_episode is not None:
            # 아직 쓰지 않은 회차의 기억을 Writer에게 주면 안 된다.
            stmt = stmt.where(
                (MemoryChunk.episode_number.is_(None))
                | (MemoryChunk.episode_number <= max_episode)
            )

        hits: list[SearchHit] = []
        for chunk in session.scalars(stmt):
            if not chunk.embedding:
                continue
            score = cosine(query_embedding, chunk.embedding)
            if score <= 0:
                continue
            hits.append(
                SearchHit(
                    chunk_id=chunk.id,
                    episode_number=chunk.episode_number,
                    kind=chunk.kind,
                    content=chunk.content,
                    score=score,
                )
            )
        hits.sort(key=lambda h: -h.score)
        return hits[:top_k]


class PgVectorIndex(SqliteVectorIndex):
    """pgvector 자리.

    아직 SQLite 구현을 그대로 쓴다. PostgreSQL로 옮길 때
      1. MemoryChunk.embedding을 Vector(dim) 타입으로 바꾸고
      2. search()를 `ORDER BY embedding <=> :query LIMIT :k`로 교체한다.
    그때까지는 동작은 같고 속도만 느리다.
    """


def get_vector_index(backend: str = "sqlite") -> VectorIndex:
    if backend == "pgvector":
        return PgVectorIndex()
    return SqliteVectorIndex()
