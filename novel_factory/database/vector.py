"""의미 검색 (기획안 41번).

기획안은 pgvector를 쓴다고 돼 있다. SQLite에서는 파이썬으로 코사인 유사도를 계산하고,
PostgreSQL + NF_VECTOR_BACKEND=pgvector면 벡터 테이블을 두고 DB가 순위를 매긴다.

임베딩 모델은 이 모듈이 정하지 않는다. 로컬 임베딩 서버든 다른 무엇이든
`Embedder` 프로토콜만 만족하면 된다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import Connection, Engine, select, text
from sqlalchemy.orm import Session

from novel_factory.config import Settings, get_settings
from novel_factory.database.models import MemoryChunk


class Embedder(Protocol):
    """문장을 벡터로 바꾸는 무엇. model은 벡터를 만든 모델의 이름이다.

    차원은 모델마다 다르므로 조각마다 model을 적어 두고 같은 모델끼리만 비교한다.
    """

    model: str

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


# ---------------------------------------------------------------------------
# pgvector (PostgreSQL)
# ---------------------------------------------------------------------------
PG_VECTOR_TABLE = "memory_vectors"


def pgvector_active(
    bind: Engine | Connection | Session, settings: Settings | None = None
) -> bool:
    """PostgreSQL이고 NF_VECTOR_BACKEND=pgvector일 때만 쓴다."""
    cfg = settings or get_settings()
    if cfg.vector_backend != "pgvector":
        return False
    engine = bind.get_bind() if isinstance(bind, Session) else bind
    return engine.dialect.name == "postgresql"


def ensure_pgvector(engine: Engine) -> None:
    """확장과 벡터 테이블을 만든다.

    벡터는 memory_chunks.embedding(JSON)에도 그대로 남긴다. JSON 쪽은 어느 DB에서나
    읽히고 다시 색인할 때 원본이 된다. 검색 순위만 여기 vector 컬럼으로 DB가 매긴다.
    임베딩 모델마다 차원이 달라 컬럼에 차원을 고정하지 않는다 (그래서 근사 색인은
    쓰지 않고 정확 검색을 한다. 한 작품의 조각 수천 개 규모에서는 충분하다).
    """
    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        conn.execute(
            text(
                f"CREATE TABLE IF NOT EXISTS {PG_VECTOR_TABLE} ("
                " chunk_id integer PRIMARY KEY"
                " REFERENCES memory_chunks(id) ON DELETE CASCADE,"
                " novel_id integer NOT NULL,"
                " model varchar(200) NOT NULL,"
                " embedding vector NOT NULL)"
            )
        )
        conn.execute(
            text(
                f"CREATE INDEX IF NOT EXISTS ix_{PG_VECTOR_TABLE}_novel_model "
                f"ON {PG_VECTOR_TABLE} (novel_id, model)"
            )
        )


def _literal(vector: list[float]) -> str:
    return "[" + ",".join(repr(float(x)) for x in vector) + "]"


def store_vectors(session: Session, rows: list[tuple[int, int, str, list[float]]]) -> None:
    """(chunk_id, novel_id, model, 벡터)를 벡터 테이블에 넣는다."""
    for chunk_id, novel_id, model, vector in rows:
        session.execute(
            text(
                f"INSERT INTO {PG_VECTOR_TABLE} (chunk_id, novel_id, model, embedding) "
                "VALUES (:c, :n, :m, CAST(:v AS vector)) "
                "ON CONFLICT (chunk_id) DO UPDATE SET model = EXCLUDED.model, "
                "embedding = EXCLUDED.embedding"
            ),
            {"c": chunk_id, "n": novel_id, "m": model, "v": _literal(vector)},
        )


def search_vectors(
    session: Session,
    novel_id: int,
    model: str,
    query: list[float],
    *,
    before_episode: int,
    kind: str,
    limit: int,
) -> list[tuple[int, float]]:
    """(chunk_id, 코사인 유사도). 앞 회차 조각만, 가까운 순."""
    rows = session.execute(
        text(
            f"SELECT v.chunk_id, 1 - (v.embedding <=> CAST(:q AS vector)) AS score "
            f"FROM {PG_VECTOR_TABLE} v JOIN memory_chunks c ON c.id = v.chunk_id "
            "WHERE v.novel_id = :n AND v.model = :m AND c.kind = :k "
            "AND c.episode_number < :before AND vector_dims(v.embedding) = :d "
            "ORDER BY v.embedding <=> CAST(:q AS vector) LIMIT :lim"
        ),
        {
            "q": _literal(query),
            "n": novel_id,
            "m": model,
            "k": kind,
            "before": before_episode,
            "d": len(query),
            "lim": limit,
        },
    )
    return [(int(r[0]), float(r[1])) for r in rows]


class PgVectorIndex(SqliteVectorIndex):
    """PostgreSQL + pgvector. 조각을 넣을 때 벡터 테이블에도 넣는다."""

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
        chunk = super().add(
            session,
            novel_id,
            content,
            kind=kind,
            episode_number=episode_number,
            embedding=embedding,
            meta=meta,
        )
        if embedding and pgvector_active(session):
            model = str((meta or {}).get("embed_model") or "")
            store_vectors(session, [(chunk.id, novel_id, model, embedding)])
        return chunk


def get_vector_index(backend: str = "sqlite") -> VectorIndex:
    if backend == "pgvector":
        return PgVectorIndex()
    return SqliteVectorIndex()
