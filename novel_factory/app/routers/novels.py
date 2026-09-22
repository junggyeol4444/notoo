"""작품 API (기획안 54번) — Novel Bible과 장기기억 DB 조작.

    POST /novels                              작품 생성
    GET  /novels                              목록
    GET  /novels/{slug}                       Novel Bible
    POST /novels/{slug}/references            참고작 연결 + 참고 강도 (기획안 16번)
    GET  /novels/{slug}/genre-profile         연결된 참고작 집계
    POST /novels/{slug}/arcs                  Arc
    POST /novels/{slug}/characters            인물
    POST /novels/{slug}/characters/{code}/knowledge   인물 지식 (기획안 23번)
    POST /novels/{slug}/relationships         관계 변화 (기획안 24번)
    POST /novels/{slug}/world                 세계관
    POST /novels/{slug}/timeline              시간선
    POST /novels/{slug}/foreshadowings        복선
    GET  /novels/{slug}/foreshadowings/open   미회수 복선 (기획안 47번)
    PUT  /novels/{slug}/style-bible           Style Bible
    GET  /novels/{slug}/context/{episode}     Writer Context (기획안 31번)
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from novel_factory.app.deps import get_db, get_novel
from novel_factory.app.schemas import (
    ArcIn,
    ArcOut,
    CharacterIn,
    CharacterOut,
    ForeshadowingIn,
    ForeshadowingOut,
    GenreProfileOut,
    KnowledgeIn,
    NovelCreate,
    NovelOut,
    NovelReferenceLinkIn,
    RelationshipIn,
    StyleBibleIn,
    TimelineEventIn,
    WorldEntryIn,
)
from novel_factory.database.models import (
    Arc,
    Character,
    Foreshadowing,
    Novel,
    TimelineEvent,
)
from novel_factory.database.repositories import (
    ArcRepository,
    CharacterRepository,
    ForeshadowingRepository,
    KnowledgeRepository,
    NovelRepository,
    ReferenceLinkRepository,
    ReferenceRepository,
    RelationshipRepository,
    StyleBibleRepository,
    TimelineRepository,
    WorldRepository,
)
from novel_factory.memory import build_context
from novel_factory.reference.pattern import aggregate_profiles, derive_patterns
from novel_factory.reference.profile import ReferenceProfile

router = APIRouter(prefix="/novels", tags=["novels"])


@router.post("", response_model=NovelOut, status_code=status.HTTP_201_CREATED)
def create_novel(body: NovelCreate, db: Session = Depends(get_db)) -> Novel:
    repo = NovelRepository(db)
    if repo.get_by_slug(body.slug) is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"작품 slug '{body.slug}'은 이미 있습니다."
        )
    novel = repo.add(
        Novel(
            slug=body.slug,
            title=body.title,
            genre=body.genre,
            logline=body.logline,
            premise=body.premise,
            mood=body.mood,
            pov=body.pov,
            target_reader=body.target_reader,
            main_conflict=body.main_conflict,
            ending=body.ending,
            planned_episodes=body.episodes,
            target_chars_per_episode=body.characters_per_episode,
            publishing_mode=body.publishing_mode,
        )
    )

    if body.reference_ids:
        refs = ReferenceRepository(db)
        links = ReferenceLinkRepository(db)
        for ref_id in body.reference_ids:
            ref = refs.get_by_reference_id(ref_id)
            if ref is None:
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST, f"참고소설 '{ref_id}'이 없습니다."
                )
            links.upsert(novel.id, ref.id)
    return novel


@router.get("", response_model=list[NovelOut])
def list_novels(
    status_filter: str | None = None, limit: int = 50, db: Session = Depends(get_db)
) -> list[Novel]:
    return NovelRepository(db).list_by_status(status_filter, limit)


@router.get("/{slug}", response_model=NovelOut)
def get_novel_detail(novel: Novel = Depends(get_novel)) -> Novel:
    return novel


# ---------------------------------------------------------------------------
# 참고작 연결
# ---------------------------------------------------------------------------
@router.post("/{slug}/references", status_code=status.HTTP_201_CREATED)
def link_reference(
    body: NovelReferenceLinkIn,
    novel: Novel = Depends(get_novel),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    """참고작을 붙이고 항목별 참고 강도를 정한다 (기획안 16·50번)."""
    ref = ReferenceRepository(db).get_by_reference_id(body.reference_id)
    if ref is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"참고소설 '{body.reference_id}'이 없습니다."
        )
    link = ReferenceLinkRepository(db).upsert(
        novel.id,
        ref.id,
        weights=body.weights.as_mapping(),
        default_weight=body.default_weight,
    )
    return {
        "novel": novel.slug,
        "reference_id": ref.reference_id,
        "default_weight": link.default_weight,
        "weights": link.weights,
    }


@router.get("/{slug}/genre-profile", response_model=GenreProfileOut)
def novel_genre_profile(
    store_patterns: bool = False,
    novel: Novel = Depends(get_novel),
    db: Session = Depends(get_db),
) -> GenreProfileOut:
    """이 작품에 연결된 참고작들을 참고 강도대로 집계한다."""
    links = ReferenceLinkRepository(db)
    refs = ReferenceRepository(db)
    weights = links.weights_for(novel.id)

    profiles: list[ReferenceProfile] = []
    for link in links.for_novel(novel.id):
        ref = refs.get(link.reference_id)
        if ref is None or not ref.profile:
            continue
        profiles.append(
            ReferenceProfile.from_stored(
                ref.profile,
                reference_id=ref.reference_id,
                title=ref.title,
                genre=ref.genre,
            )
        )

    if not profiles:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "분석이 끝난 참고작이 연결돼 있지 않습니다.",
        )

    genre_profile = aggregate_profiles(profiles, weights, genre=novel.genre)
    patterns = derive_patterns(genre_profile)

    if store_patterns:
        from novel_factory.database.repositories import PatternRepository

        pattern_repo = PatternRepository(db)
        for pattern in patterns:
            pattern_repo.upsert(pattern)

    return GenreProfileOut(
        genre=genre_profile.genre,
        reference_count=len(profiles),
        profile=genre_profile.as_dict(),
        patterns=[p.as_dict() for p in patterns],
    )


# ---------------------------------------------------------------------------
# 구조
# ---------------------------------------------------------------------------
@router.post("/{slug}/arcs", response_model=ArcOut, status_code=status.HTTP_201_CREATED)
def create_arc(
    body: ArcIn, novel: Novel = Depends(get_novel), db: Session = Depends(get_db)
) -> Arc:
    if body.end_episode < body.start_episode:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "end_episode가 start_episode보다 작습니다.",
        )
    return ArcRepository(db).add(Arc(novel_id=novel.id, **body.model_dump()))


@router.get("/{slug}/arcs", response_model=list[ArcOut])
def list_arcs(novel: Novel = Depends(get_novel), db: Session = Depends(get_db)) -> list[Arc]:
    return ArcRepository(db).for_novel(novel.id)


# ---------------------------------------------------------------------------
# 인물
# ---------------------------------------------------------------------------
@router.post(
    "/{slug}/characters", response_model=CharacterOut, status_code=status.HTTP_201_CREATED
)
def create_character(
    body: CharacterIn, novel: Novel = Depends(get_novel), db: Session = Depends(get_db)
) -> Character:
    repo = CharacterRepository(db)
    code = body.code or repo.next_code(novel.id)
    if repo.get_by_code(novel.id, code) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, f"인물 코드 '{code}'는 이미 있습니다.")
    payload = body.model_dump(exclude={"code"})
    return repo.add(Character(novel_id=novel.id, code=code, **payload))


@router.get("/{slug}/characters", response_model=list[CharacterOut])
def list_characters(
    novel: Novel = Depends(get_novel), db: Session = Depends(get_db)
) -> list[Character]:
    return CharacterRepository(db).for_novel(novel.id)


@router.post("/{slug}/characters/{code}/knowledge", status_code=status.HTTP_201_CREATED)
def set_knowledge(
    code: str,
    body: KnowledgeIn,
    novel: Novel = Depends(get_novel),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    """이 인물이 무엇을 아는지 기록한다 (기획안 23번).

    Writer Context는 이 기록을 뒤집어 '이 인물이 모르는 것' 목록으로 넘긴다.
    """
    character = CharacterRepository(db).get_by_code(novel.id, code)
    if character is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"인물 '{code}'이 없습니다.")
    row = KnowledgeRepository(db).set_knowledge(
        character.id,
        body.fact_key,
        body.fact,
        knows=body.knows,
        learned_episode=body.learned_episode,
        source=body.source,
    )
    return {
        "character": character.name,
        "fact_key": row.fact_key,
        "knows": row.knows,
        "learned_episode": row.learned_episode,
    }


@router.post("/{slug}/relationships", status_code=status.HTTP_201_CREATED)
def record_relationship(
    body: RelationshipIn,
    novel: Novel = Depends(get_novel),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    """관계 변화를 한 시점으로 기록한다 (기획안 24번)."""
    repo = CharacterRepository(db)
    a = repo.get_by_code(novel.id, body.source_code)
    b = repo.get_by_code(novel.id, body.target_code)
    if a is None or b is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "인물 코드를 찾지 못했습니다.")
    RelationshipRepository(db).record(
        novel.id,
        a.id,
        b.id,
        from_episode=body.from_episode,
        state=body.state,
        intensity=body.intensity,
        note=body.note,
        symmetric=body.symmetric,
    )
    return {
        "source": a.name,
        "target": b.name,
        "from_episode": body.from_episode,
        "state": body.state,
    }


# ---------------------------------------------------------------------------
# 세계관 / 시간선 / 복선
# ---------------------------------------------------------------------------
@router.post("/{slug}/world", status_code=status.HTTP_201_CREATED)
def upsert_world(
    body: WorldEntryIn, novel: Novel = Depends(get_novel), db: Session = Depends(get_db)
) -> dict[str, object]:
    entry = WorldRepository(db).upsert(
        novel.id,
        body.category,
        body.name,
        description=body.description,
        rules=body.rules,
        attributes=body.attributes,
        first_episode=body.first_episode,
    )
    return {"category": entry.category, "name": entry.name, "id": entry.id}


@router.get("/{slug}/world")
def list_world(
    category: str | None = None,
    novel: Novel = Depends(get_novel),
    db: Session = Depends(get_db),
) -> list[dict[str, object]]:
    return [
        {
            "category": w.category,
            "name": w.name,
            "description": w.description,
            "first_episode": w.first_episode,
        }
        for w in WorldRepository(db).for_novel(novel.id, category)
    ]


@router.post("/{slug}/timeline", status_code=status.HTTP_201_CREATED)
def add_timeline_event(
    body: TimelineEventIn,
    novel: Novel = Depends(get_novel),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    repo = TimelineRepository(db)
    payload = body.model_dump()
    if payload.get("sort_key") is None:
        payload["sort_key"] = repo.next_sort_key(novel.id)
    event = repo.add(TimelineEvent(novel_id=novel.id, **payload))
    return {"id": event.id, "title": event.title, "sort_key": event.sort_key}


@router.get("/{slug}/timeline")
def list_timeline(
    up_to_episode: int | None = None,
    novel: Novel = Depends(get_novel),
    db: Session = Depends(get_db),
) -> list[dict[str, object]]:
    repo = TimelineRepository(db)
    events = (
        repo.up_to_episode(novel.id, up_to_episode)
        if up_to_episode is not None
        else repo.for_novel(novel.id)
    )
    return [
        {
            "occurred_at": e.occurred_at,
            "title": e.title,
            "episode_number": e.episode_number,
            "importance": e.importance,
        }
        for e in events
    ]


@router.post(
    "/{slug}/foreshadowings",
    response_model=ForeshadowingOut,
    status_code=status.HTTP_201_CREATED,
)
def create_foreshadowing(
    body: ForeshadowingIn,
    novel: Novel = Depends(get_novel),
    db: Session = Depends(get_db),
) -> Foreshadowing:
    repo = ForeshadowingRepository(db)
    code = body.code or repo.next_code(novel.id)
    if repo.get_by_code(novel.id, code) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, f"복선 코드 '{code}'는 이미 있습니다.")
    return repo.add(
        Foreshadowing(
            novel_id=novel.id,
            code=code,
            description=body.description,
            setup_episode=body.setup_episode,
            planned_payoff=body.planned_payoff,
            importance=body.importance,
            note=body.note,
        )
    )


@router.get("/{slug}/foreshadowings/open", response_model=list[ForeshadowingOut])
def list_open_foreshadowings(
    overdue_by: int | None = None,
    novel: Novel = Depends(get_novel),
    db: Session = Depends(get_db),
) -> list[Foreshadowing]:
    """미회수 복선. 완결 검사(기획안 47번)가 이 목록이 비기를 요구한다."""
    repo = ForeshadowingRepository(db)
    if overdue_by is not None:
        return repo.due_by(novel.id, overdue_by)
    return repo.open_items(novel.id)


@router.post("/{slug}/foreshadowings/{code}/resolve", response_model=ForeshadowingOut)
def resolve_foreshadowing(
    code: str,
    episode: int,
    novel: Novel = Depends(get_novel),
    db: Session = Depends(get_db),
) -> Foreshadowing:
    repo = ForeshadowingRepository(db)
    item = repo.get_by_code(novel.id, code)
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"복선 '{code}'이 없습니다.")
    return repo.resolve(item, episode)


# ---------------------------------------------------------------------------
# Style Bible / Writer Context
# ---------------------------------------------------------------------------
@router.put("/{slug}/style-bible")
def put_style_bible(
    body: StyleBibleIn, novel: Novel = Depends(get_novel), db: Session = Depends(get_db)
) -> dict[str, object]:
    bible = StyleBibleRepository(db).upsert(novel.id, **body.model_dump())
    return {"novel": novel.slug, "pov": bible.pov, "tense": bible.tense}


@router.get("/{slug}/context/{episode}")
def writer_context(
    episode: int,
    as_prompt: bool = False,
    novel: Novel = Depends(get_novel),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    """이번 화를 쓰는 데 필요한 것만 모아 준다 (기획안 31번).

    참고작 원문은 들어가지 않는다. 이번 화 이후의 사건, 아직 등장하지 않은
    인물, 이미 회수된 복선도 빠진다.
    """
    if episode < 1:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "회차는 1 이상입니다.")
    ctx = build_context(db, novel, episode)
    if as_prompt:
        return {"episode": episode, "prompt": ctx.to_prompt()}
    return ctx.as_dict()
