"""관리자 화면(기획안 48~50번)이 쓰는 조회·수정 API.

GET    /novels/{slug}/bible                       Novel Bible 전체
PATCH  /novels/{slug}                             Novel Bible 고치기
GET    /novels/{slug}/relationships               관계 기록 (시점별)
GET    /novels/{slug}/foreshadowings              복선 전체 (status로 거르기)
GET    /novels/{slug}/style-bible                 Style Bible
GET    /novels/{slug}/references                  연결된 참고작과 참고 강도 (기획안 50번)
DELETE /novels/{slug}/references/{reference_id}   연결 끊기
GET    /novels/{slug}/similarity                  회차별 유사도 보고
GET    /patterns                                  Reference Pattern Library (기획안 19번)
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from novel_factory.app.deps import get_db, get_novel
from novel_factory.database.models import (
    Character,
    Episode,
    Novel,
    ReferenceLink,
    ReferenceNovel,
    ReferencePatternRow,
    Relationship,
)
from novel_factory.database.repositories import (
    ForeshadowingRepository,
    ReferenceLinkRepository,
    ReferenceRepository,
    StyleBibleRepository,
)
from novel_factory.reference.pattern.aggregator import ASPECTS

router = APIRouter(tags=["browse"])

BIBLE_FIELDS = (
    "title",
    "genre",
    "logline",
    "premise",
    "mood",
    "pov",
    "target_reader",
    "main_conflict",
    "ending",
    "planned_episodes",
    "target_chars_per_episode",
)


class BibleIn(BaseModel):
    title: str | None = None
    genre: str | None = None
    logline: str | None = None
    premise: str | None = None
    mood: str | None = None
    pov: str | None = None
    target_reader: str | None = None
    main_conflict: str | None = None
    ending: str | None = None
    planned_episodes: int | None = Field(default=None, ge=0, le=5000)
    target_chars_per_episode: int | None = Field(default=None, ge=500, le=50000)


def _bible(novel: Novel) -> dict[str, object]:
    return {
        "slug": novel.slug,
        "status": novel.status,
        "publishing_mode": novel.publishing_mode,
        **{f: getattr(novel, f) for f in BIBLE_FIELDS},
    }


@router.get("/novels/{slug}/bible")
def get_bible(novel: Novel = Depends(get_novel)) -> dict[str, object]:
    return _bible(novel)


@router.patch("/novels/{slug}")
def patch_bible(
    body: BibleIn, novel: Novel = Depends(get_novel), db: Session = Depends(get_db)
) -> dict[str, object]:
    for key, value in body.model_dump(exclude_none=True).items():
        setattr(novel, key, value)
    db.flush()
    return _bible(novel)


@router.get("/novels/{slug}/relationships")
def list_relationships(
    novel: Novel = Depends(get_novel), db: Session = Depends(get_db)
) -> list[dict[str, object]]:
    names = {
        c.id: (c.code, c.name)
        for c in db.scalars(select(Character).where(Character.novel_id == novel.id))
    }
    rows = db.scalars(
        select(Relationship)
        .where(Relationship.novel_id == novel.id)
        .order_by(Relationship.source_id, Relationship.target_id, Relationship.from_episode)
    )
    return [
        {
            "source": names.get(r.source_id, ("?", "?"))[1],
            "source_code": names.get(r.source_id, ("?", "?"))[0],
            "target": names.get(r.target_id, ("?", "?"))[1],
            "target_code": names.get(r.target_id, ("?", "?"))[0],
            "from_episode": r.from_episode,
            "state": r.state,
            "intensity": r.intensity,
            "note": r.note,
        }
        for r in rows
    ]


@router.get("/novels/{slug}/foreshadowings")
def list_foreshadowings(
    status_filter: str | None = None,
    novel: Novel = Depends(get_novel),
    db: Session = Depends(get_db),
) -> list[dict[str, object]]:
    return [
        {
            "code": f.code,
            "description": f.description,
            "setup_episode": f.setup_episode,
            "planned_payoff": f.planned_payoff,
            "status": f.status,
            "mentions": list(f.mentions or []),
        }
        for f in ForeshadowingRepository(db).for_novel(novel.id, status_filter)
    ]


@router.get("/novels/{slug}/style-bible")
def get_style_bible(
    novel: Novel = Depends(get_novel), db: Session = Depends(get_db)
) -> dict[str, object]:
    bible = StyleBibleRepository(db).for_novel(novel.id)
    if bible is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Style Bible이 아직 없습니다.")
    return {
        "target_sentence_chars": bible.target_sentence_chars,
        "target_paragraph_chars": bible.target_paragraph_chars,
        "target_dialogue_ratio": bible.target_dialogue_ratio,
        "target_narration_ratio": bible.target_narration_ratio,
        "target_inner_ratio": bible.target_inner_ratio,
        "pov": bible.pov,
        "tense": bible.tense,
        "forbidden": list(bible.forbidden or []),
        "preferred": list(bible.preferred or []),
        "throttled_phrases": dict(bible.throttled_phrases or {}),
    }


@router.get("/novels/{slug}/references")
def list_links(
    novel: Novel = Depends(get_novel), db: Session = Depends(get_db)
) -> dict[str, object]:
    """기획안 50번 참고 설정 화면. 항목마다 참고 강도(0~1)."""
    out = []
    for link in ReferenceLinkRepository(db).for_novel(novel.id):
        ref = db.get(ReferenceNovel, link.reference_id)
        if ref is None:
            continue
        weights = link.weights or {}
        out.append(
            {
                "reference_id": ref.reference_id,
                "title": ref.title,
                "status": ref.status,
                "default_weight": link.default_weight,
                "weights": {a: float(weights.get(a, link.default_weight)) for a in ASPECTS},
            }
        )
    return {"aspects": list(ASPECTS), "links": out}


@router.delete("/novels/{slug}/references/{reference_id}", status_code=204)
def delete_link(
    reference_id: str, novel: Novel = Depends(get_novel), db: Session = Depends(get_db)
) -> None:
    ref = ReferenceRepository(db).get_by_reference_id(reference_id)
    link = (
        db.scalar(
            select(ReferenceLink).where(
                ReferenceLink.novel_id == novel.id, ReferenceLink.reference_id == ref.id
            )
        )
        if ref is not None
        else None
    )
    if link is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "연결되지 않은 참고작입니다.")
    db.delete(link)
    db.flush()


@router.get("/novels/{slug}/similarity")
def similarity_reports(
    novel: Novel = Depends(get_novel), db: Session = Depends(get_db)
) -> list[dict[str, object]]:
    episodes = db.scalars(
        select(Episode).where(Episode.novel_id == novel.id).order_by(Episode.number)
    )
    out = []
    for e in episodes:
        report = (e.quality_reports or {}).get("similarity") or {}
        if not report:
            continue
        out.append(
            {
                "number": e.number,
                "title": e.title,
                "verdict": report.get("verdict"),
                "max_containment": report.get("max_containment"),
                "max_jaccard": report.get("max_jaccard"),
                "hits": report.get("hits") or [],
            }
        )
    return out


@router.get("/patterns")
def list_patterns(
    genre: str | None = None, limit: int = 200, db: Session = Depends(get_db)
) -> list[dict[str, object]]:
    stmt = select(ReferencePatternRow).order_by(
        ReferencePatternRow.genre,
        ReferencePatternRow.aspect,
        ReferencePatternRow.pattern_id,
    )
    if genre:
        stmt = stmt.where(ReferencePatternRow.genre == genre)
    return [
        {
            "pattern_id": p.pattern_id,
            "genre": p.genre,
            "aspect": p.aspect,
            "instruction": p.instruction,
            "effect": p.effect,
            "placement": p.placement,
            "confidence": p.confidence,
            "source": p.source,
        }
        for p in db.scalars(stmt.limit(limit))
    ]
