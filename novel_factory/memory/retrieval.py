"""장편 기억 검색 (기획안 41번).

    PostgreSQL/SQLite  정확한 설정 데이터      → context_builder.py
    Vector DB          과거 장면 의미검색      → 여기
    Episode Summary    과거 줄거리            → context_builder.py (최근 회차 요약)
    Raw Manuscript     원문 확인              → 여기 (검색 결과는 원고 발췌다)
    Reference DB       참고 작품 분석 정보     → context_builder.py (참고 패턴)

250화쯤 되면 최근 5화 요약만으로는 30화에 나온 장면을 다시 꺼낼 수 없다. 이번 화
계획과 관련 있는 과거 장면을 찾아 원고 발췌로 Writer에게 보여 준다.

검색 방식 (NF_MEMORY_SEARCH)
  embedding  로컬 /v1/embeddings로 만든 벡터의 코사인 유사도. 뜻이 비슷하면 찾는다.
  lexical    음절 2-gram BM25. 모델 없이 돈다. 표현이 겹치는 장면만 찾는다.
  auto       임베딩 설정이 있고 그 모델로 만든 조각이 있으면 embedding, 아니면
             lexical. 임베딩 호출이 실패해도 lexical로 넘어간다.

미래 정보 차단
  이번 화보다 앞 회차의 조각만 찾는다. 이번 화와 이후 회차는 검색 대상이 아니다.

색인 단위
  확정된 회차의 장면 원문을 문단 경계로 NF_MEMORY_CHUNK_CHARS 안팎씩 나눈다.
  조각마다 어느 모델로 만든 벡터인지 meta.embed_model에 적는다. 모델을 바꾸면
  reindex_novel()로 다시 만든다.
"""

from __future__ import annotations

import logging
import math
import re
from collections import Counter
from dataclasses import dataclass, field

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from novel_factory.config import Settings, get_settings
from novel_factory.database.models import Episode, MemoryChunk, Novel
from novel_factory.database.vector import Embedder, cosine
from novel_factory.errors import LLMError
from novel_factory.llm.embeddings import get_embedder

logger = logging.getLogger("novel_factory.memory")

SCENE = "scene"
_WORD_RE = re.compile(r"[가-힣A-Za-z0-9]+")
# BM25 매개변수. 흔히 쓰는 값 그대로다.
BM25_K1 = 1.2
BM25_B = 0.75


# ---------------------------------------------------------------------------
# 조각 나누기
# ---------------------------------------------------------------------------
def chunk_text(text: str, max_chars: int) -> list[str]:
    """문단 경계로 나눈다. 문단 하나가 max_chars보다 길면 그 문단만 따로 둔다."""
    paragraphs = [p.strip() for p in text.split("\n") if p.strip()]
    chunks: list[str] = []
    current: list[str] = []
    size = 0
    for p in paragraphs:
        if current and size + len(p) > max_chars:
            chunks.append("\n".join(current))
            current, size = [], 0
        current.append(p)
        size += len(p)
    if current:
        chunks.append("\n".join(current))
    return chunks


# ---------------------------------------------------------------------------
# 어휘 검색 (BM25)
# ---------------------------------------------------------------------------
def terms(text: str) -> list[str]:
    """어절마다 음절 2-gram. 한 글자 어절은 그대로.

    "도윤은"과 "도윤이"가 '도윤'을 공유하므로 조사가 달라도 맞는다. 어절 경계를
    넘는 2-gram은 만들지 않는다.
    """
    out: list[str] = []
    for word in _WORD_RE.findall(text.lower()):
        if len(word) == 1:
            out.append(word)
        else:
            out.extend(word[i : i + 2] for i in range(len(word) - 1))
    return out


def bm25_scores(query: str, documents: list[str]) -> list[float]:
    q = set(terms(query))
    if not q or not documents:
        return [0.0] * len(documents)
    docs = [Counter(terms(d)) for d in documents]
    lengths = [sum(d.values()) for d in docs]
    avg = (sum(lengths) / len(lengths)) or 1.0
    n = len(docs)
    df = Counter(t for d in docs for t in q if t in d)
    scores: list[float] = []
    for d, length in zip(docs, lengths, strict=True):
        score = 0.0
        for t in q:
            tf = d.get(t, 0)
            if not tf:
                continue
            idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
            score += (
                idf
                * tf
                * (BM25_K1 + 1)
                / (tf + BM25_K1 * (1 - BM25_B + BM25_B * length / avg))
            )
        scores.append(score)
    return scores


# ---------------------------------------------------------------------------
# 색인
# ---------------------------------------------------------------------------
def _embed_safely(
    embedder: Embedder | None, texts: list[str], warnings: list[str]
) -> list[list[float]] | None:
    if embedder is None or not texts:
        return None
    try:
        return embedder.embed(texts)
    except LLMError as exc:
        warnings.append(f"임베딩 실패, 벡터 없이 저장했습니다: {exc}")
        return None


def _resolve_embedder(
    settings: Settings, embedder: Embedder | None
) -> tuple[Embedder | None, bool]:
    """(쓸 임베더, 이 함수가 만들어서 닫아야 하는가)."""
    if settings.memory_search in ("lexical", "off"):
        return None, False
    if embedder is not None:
        return embedder, False
    made = get_embedder(settings)
    return made, made is not None


def _close(embedder: Embedder | None, owned: bool) -> None:
    if owned and embedder is not None:
        embedder.close()  # type: ignore[attr-defined]


def index_episode(
    session: Session,
    novel: Novel,
    episode: Episode,
    *,
    settings: Settings | None = None,
    embedder: Embedder | None = None,
) -> list[str]:
    """회차의 장면 원문을 조각으로 나눠 저장한다. 경고 목록을 돌려준다.

    같은 회차의 예전 장면 조각은 지운다 (다시 생성한 경우).
    """
    cfg = settings or get_settings()
    warnings: list[str] = []
    session.execute(
        delete(MemoryChunk).where(
            MemoryChunk.novel_id == novel.id,
            MemoryChunk.episode_number == episode.number,
            MemoryChunk.kind == SCENE,
        )
    )
    pieces: list[tuple[int, int, str]] = []
    scenes = episode.scenes or []
    texts = [str(s.get("text") or "") for s in scenes]
    if not any(texts):
        # 장면 원문이 없는 옛 회차는 회차 본문을 한 장면으로 본다.
        texts = [episode.final_text or ""]
    for si, text in enumerate(texts):
        for pi, piece in enumerate(chunk_text(text, cfg.memory_chunk_chars)):
            pieces.append((si, pi, piece))
    if not pieces:
        return warnings

    use, owned = _resolve_embedder(cfg, embedder)
    try:
        vectors = _embed_safely(use, [p[2] for p in pieces], warnings)
    finally:
        _close(use, owned)
    model = use.model if (use is not None and vectors is not None) else ""
    for i, (si, pi, piece) in enumerate(pieces):
        session.add(
            MemoryChunk(
                novel_id=novel.id,
                episode_number=episode.number,
                kind=SCENE,
                content=piece,
                embedding=vectors[i] if vectors is not None else None,
                meta={
                    "scene_index": si,
                    "part": pi,
                    "title": episode.title,
                    "embed_model": model,
                },
            )
        )
    session.flush()
    return warnings


@dataclass(slots=True)
class ReindexReport:
    episodes: int = 0
    chunks: int = 0
    embedded: int = 0
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "episodes": self.episodes,
            "chunks": self.chunks,
            "embedded": self.embedded,
            "warnings": self.warnings,
        }


def reindex_novel(
    session: Session,
    novel: Novel,
    *,
    settings: Settings | None = None,
    embedder: Embedder | None = None,
) -> ReindexReport:
    """확정된 회차 전부를 다시 색인한다. 임베딩 모델을 새로 켜거나 바꾼 뒤에 쓴다."""
    cfg = settings or get_settings()
    report = ReindexReport()
    use, owned = _resolve_embedder(cfg, embedder)
    try:
        episodes = session.scalars(
            select(Episode)
            .where(Episode.novel_id == novel.id, Episode.status == "final")
            .order_by(Episode.number)
        ).all()
        for ep in episodes:
            report.warnings.extend(
                f"{ep.number}화: {w}"
                for w in index_episode(session, novel, ep, settings=cfg, embedder=use)
            )
            report.episodes += 1
    finally:
        _close(use, owned)
    chunks = session.scalars(
        select(MemoryChunk).where(
            MemoryChunk.novel_id == novel.id, MemoryChunk.kind == SCENE
        )
    ).all()
    report.chunks = len(chunks)
    report.embedded = sum(1 for c in chunks if c.embedding)
    return report


# ---------------------------------------------------------------------------
# 검색
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class MemoryHit:
    episode_number: int
    scene_index: int
    title: str
    content: str
    score: float
    query: str = ""

    def as_dict(self, excerpt_chars: int | None = None) -> dict[str, object]:
        text = (
            excerpt(self.content, self.query, excerpt_chars)
            if excerpt_chars
            else self.content
        )
        return {
            "episode_number": self.episode_number,
            "scene_index": self.scene_index,
            "title": self.title,
            "excerpt": text,
            "score": round(self.score, 4),
        }


def excerpt(content: str, query: str, max_chars: int) -> str:
    """조각에서 질의와 겹치는 문단을 중심으로 max_chars 안팎을 잘라 낸다.

    조각 앞부분만 자르면 정작 관련된 문장이 잘려 나간다. 질의 어휘가 가장 많이
    든 문단을 고르고 앞뒤 문단을 한도까지 붙인다. 겹치는 어휘가 없으면(의미
    검색으로만 걸린 경우) 앞부분을 쓴다.
    """
    if len(content) <= max_chars:
        return content
    paragraphs = content.split("\n")
    q = set(terms(query))
    overlap = [len(q & set(terms(p))) for p in paragraphs]
    best = overlap.index(max(overlap)) if q and max(overlap) > 0 else 0
    if len(paragraphs[best]) >= max_chars:
        piece = paragraphs[best][:max_chars].rstrip()
        return ("…" if best > 0 else "") + piece + "…"
    lo = hi = best
    size = len(paragraphs[best])
    grew = True
    while grew:
        grew = False
        if hi + 1 < len(paragraphs) and size + len(paragraphs[hi + 1]) <= max_chars:
            hi += 1
            size += len(paragraphs[hi])
            grew = True
        if lo > 0 and size + len(paragraphs[lo - 1]) <= max_chars:
            lo -= 1
            size += len(paragraphs[lo])
            grew = True
    text = "\n".join(paragraphs[lo : hi + 1])
    return ("…" if lo > 0 else "") + text + ("…" if hi < len(paragraphs) - 1 else "")


@dataclass(slots=True)
class SearchResult:
    method: str  # embedding / lexical / none
    hits: list[MemoryHit] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    #: 이번 임베딩 모델로 만든 벡터가 없는 조각 수. 많으면 reindex가 필요하다.
    unembedded: int = 0

    def as_dict(self, excerpt_chars: int | None = None) -> dict[str, object]:
        return {
            "method": self.method,
            "hits": [h.as_dict(excerpt_chars) for h in self.hits],
            "warnings": self.warnings,
            "unembedded": self.unembedded,
        }


def _distinct_scenes(
    ranked: list[tuple[float, MemoryChunk]], top_k: int, query: str
) -> list[MemoryHit]:
    """같은 장면의 조각이 여러 개 걸리면 가장 높은 것 하나만."""
    seen: set[tuple[int, int]] = set()
    hits: list[MemoryHit] = []
    for score, chunk in ranked:
        if score <= 0:
            break
        meta = chunk.meta or {}
        key = (int(chunk.episode_number or 0), int(meta.get("scene_index") or 0))
        if key in seen:
            continue
        seen.add(key)
        hits.append(
            MemoryHit(
                key[0], key[1], str(meta.get("title") or ""), chunk.content, score, query
            )
        )
        if len(hits) >= top_k:
            break
    return hits


def search_memory(
    session: Session,
    novel: Novel,
    query: str,
    *,
    before_episode: int,
    top_k: int | None = None,
    settings: Settings | None = None,
    embedder: Embedder | None = None,
) -> SearchResult:
    """before_episode보다 앞 회차의 장면 조각에서 query와 가까운 것을 찾는다."""
    cfg = settings or get_settings()
    k = top_k or cfg.memory_top_k
    mode = cfg.memory_search
    if mode == "off" or not query.strip():
        return SearchResult("none")

    chunks = session.scalars(
        select(MemoryChunk).where(
            MemoryChunk.novel_id == novel.id,
            MemoryChunk.kind == SCENE,
            MemoryChunk.episode_number < before_episode,
        )
    ).all()
    if not chunks:
        return SearchResult("none")

    result = SearchResult("none")
    use, owned = _resolve_embedder(cfg, embedder)
    try:
        if use is not None:
            matching = [
                c
                for c in chunks
                if c.embedding and (c.meta or {}).get("embed_model") == use.model
            ]
            result.unembedded = len(chunks) - len(matching)
            if matching:
                try:
                    [qv] = use.embed([query])
                except LLMError as exc:
                    result.warnings.append(f"질의 임베딩 실패: {exc}")
                else:
                    ranked = sorted(
                        ((cosine(qv, c.embedding or []), c) for c in matching),
                        key=lambda x: -x[0],
                    )
                    result.method = "embedding"
                    result.hits = _distinct_scenes(ranked, k, query)
                    return result
            else:
                result.warnings.append(
                    f"임베딩 모델 '{use.model}'로 만든 조각이 없습니다. "
                    "memory/reindex로 다시 색인하세요."
                )
        elif mode == "embedding":
            result.warnings.append(
                "임베딩 모델이 설정되지 않았습니다 (NF_EMBEDDING_MODEL)."
            )
    finally:
        _close(use, owned)

    if mode == "embedding":
        return result  # 의미 검색만 쓰기로 했으면 어휘 검색으로 넘어가지 않는다

    scores = bm25_scores(query, [c.content for c in chunks])
    ranked = sorted(zip(scores, chunks, strict=True), key=lambda x: -x[0])
    result.method = "lexical"
    result.hits = _distinct_scenes(ranked, k, query)
    return result


def plan_query(plan: dict[str, object]) -> str:
    """회차 계획에서 검색어를 만든다. 사건·갈등·보상·훅 내용."""
    parts: list[str] = []
    for key in ("purpose", "conflict", "reward"):
        parts.append(str(plan.get(key) or ""))
    for key in ("required_events",):
        parts.extend(str(x) for x in (plan.get(key) or []))  # type: ignore[union-attr]
    hook = plan.get("hook")
    if isinstance(hook, dict):
        parts.append(str(hook.get("content") or ""))
    return " ".join(p for p in parts if p.strip())


def log_warnings(result: SearchResult) -> None:
    for w in result.warnings:
        logger.warning("기억 검색: %s", w)
