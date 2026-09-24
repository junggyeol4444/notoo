"""장편 기억 검색 API (기획안 41번).

    GET  /novels/{slug}/memory/search?q=...&before=N   앞 회차 장면 검색 (원문 발췌)
    POST /novels/{slug}/memory/reindex                 확정 회차 전부 다시 색인

before를 비우면 모든 확정 회차에서 찾는다. before=N이면 N화보다 앞 회차만 찾는다
(N화를 쓸 때 Writer가 보는 범위와 같다).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from novel_factory.app.deps import get_db, get_novel
from novel_factory.config import get_settings
from novel_factory.database.models import Novel
from novel_factory.memory.retrieval import reindex_novel, search_memory

router = APIRouter(prefix="/novels", tags=["memory"])

#: before를 비웠을 때 쓰는 값. 회차 번호가 이보다 클 일은 없다.
_ALL_EPISODES = 10**9


@router.get("/{slug}/memory/search")
def memory_search(
    q: str = Query(min_length=1),
    before: int | None = None,
    k: int | None = Query(default=None, ge=1, le=50),
    full: bool = False,
    novel: Novel = Depends(get_novel),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    """full=true면 조각 전체를, 아니면 발췌(NF_MEMORY_EXCERPT_CHARS)를 돌려준다."""
    settings = get_settings()
    result = search_memory(
        db, novel, q, before_episode=before or _ALL_EPISODES, top_k=k, settings=settings
    )
    return {
        "query": q,
        "before": before,
        **result.as_dict(None if full else settings.memory_excerpt_chars),
    }


@router.post("/{slug}/memory/reindex")
def memory_reindex(
    novel: Novel = Depends(get_novel), db: Session = Depends(get_db)
) -> dict[str, object]:
    return reindex_novel(db, novel).as_dict()
