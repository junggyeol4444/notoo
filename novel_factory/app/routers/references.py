"""참고소설 API (기획안 53번).

POST /references                        등록 (서버에 있는 파일 경로로)
POST /references/upload                 등록 (파일 업로드, 기획안 49번 화면)
POST /references/{id}/analyze           분석
GET  /references                        목록
GET  /references/{id}                   Profile
POST /references/aggregate              다중 집계 → GenreProfile + 패턴
POST /references/similarity             유사도 검사
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from novel_factory.app.deps import get_db, settings_dep
from novel_factory.app.schemas import (
    GenreProfileOut,
    ReferenceAnalyzeRequest,
    ReferenceCreate,
    ReferenceOut,
    ReferenceProfileOut,
    SimilarityCheckIn,
)
from novel_factory.config import Settings
from novel_factory.database.models import ReferenceNovel
from novel_factory.database.repositories import PatternRepository, ReferenceRepository
from novel_factory.errors import NovelFactoryError, UnsupportedFormatError
from novel_factory.reference.importer import ImportedReference, import_bytes, import_file
from novel_factory.reference.pattern import (
    ReferenceWeights,
    aggregate_profiles,
    derive_patterns,
)
from novel_factory.reference.profile import ReferenceProfile
from novel_factory.reference.service import analyze_reference_record
from novel_factory.reference.similarity import check_text

router = APIRouter(prefix="/references", tags=["references"])


def _to_out(ref: ReferenceNovel) -> ReferenceOut:
    return ReferenceOut(
        reference_id=ref.reference_id,
        title=ref.title,
        author=ref.author,
        genre=ref.genre,
        source_format=ref.source_format,
        encoding=ref.encoding,
        char_count=ref.char_count,
        episode_count=ref.episode_count,
        status=ref.status,
        warnings=list(ref.warnings or []),
    )


def _persist(
    db: Session, imported: ImportedReference, *, title: str, author: str, genre: str
) -> ReferenceNovel:
    repo = ReferenceRepository(db)
    duplicate = repo.get_by_hash(imported.content_hash)
    if duplicate is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"같은 내용의 파일이 이미 '{duplicate.reference_id}'로 등록돼 있습니다.",
        )
    ref = ReferenceNovel(
        reference_id=imported.reference_id,
        title=title or imported.title or imported.reference_id,
        author=author or (imported.author or ""),
        genre=genre,
        source_format=imported.source_format,
        encoding=imported.encoding,
        content_hash=imported.content_hash,
        stored_path=str(imported.stored_path),
        extracted_path=str(imported.extracted_path),
        char_count=imported.char_count,
        status="imported",
        warnings=list(imported.warnings),
    )
    return repo.add(ref)


@router.post("", response_model=ReferenceOut, status_code=status.HTTP_201_CREATED)
def create_reference(
    body: ReferenceCreate,
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
) -> ReferenceOut:
    """서버가 읽을 수 있는 경로의 파일을 등록한다."""
    path = Path(body.path)
    if not path.is_file():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"파일이 없습니다: {path}")
    repo = ReferenceRepository(db)
    reference_id = body.reference_id or repo.next_reference_id()
    if repo.get_by_reference_id(reference_id) is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"'{reference_id}'는 이미 사용 중입니다."
        )
    try:
        imported = import_file(path, reference_id=reference_id, settings=settings)
    except UnsupportedFormatError as exc:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, str(exc)) from exc
    except NovelFactoryError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    return _to_out(
        _persist(db, imported, title=body.title, author=body.author, genre=body.genre)
    )


@router.post("/upload", response_model=ReferenceOut, status_code=status.HTTP_201_CREATED)
async def upload_reference(
    file: UploadFile = File(...),
    genre: str = Form(default=""),
    title: str = Form(default=""),
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
) -> ReferenceOut:
    """파일 업로드로 등록한다 (기획안 49번 화면용)."""
    repo = ReferenceRepository(db)
    reference_id = repo.next_reference_id()
    data = await file.read()
    try:
        imported = import_bytes(
            data,
            file.filename or "upload.txt",
            reference_id=reference_id,
            settings=settings,
        )
    except UnsupportedFormatError as exc:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, str(exc)) from exc
    except NovelFactoryError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    return _to_out(_persist(db, imported, title=title, author="", genre=genre))


@router.get("", response_model=list[ReferenceOut])
def list_references(
    status_filter: str | None = None,
    genre: str | None = None,
    limit: int = 50,
    db: Session = Depends(get_db),
) -> list[ReferenceOut]:
    return [
        _to_out(r)
        for r in ReferenceRepository(db).list_by_status(status_filter, genre, limit)
    ]


@router.post("/{reference_id}/analyze", response_model=ReferenceProfileOut)
def analyze_reference(
    reference_id: str,
    body: ReferenceAnalyzeRequest | None = None,
    db: Session = Depends(get_db),
) -> ReferenceProfileOut:
    """참고소설을 분석해 Reference Profile을 만든다.

    회차 수가 많으면 몇 초 걸린다. 운영에서는 백그라운드로 넘기고 202를
    돌려주는 게 맞지만, 지금은 동기로 처리한다.
    """
    options = body or ReferenceAnalyzeRequest()
    repo = ReferenceRepository(db)
    ref = repo.get_by_reference_id(reference_id)
    if ref is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"참고소설 '{reference_id}'이 없습니다."
        )

    try:
        analyze_reference_record(
            db,
            ref,
            genre=options.genre,
            fallback_chars=options.fallback_chars,
            major_quantile=options.major_quantile,
            store_fingerprints=options.store_fingerprints,
        )
    except NovelFactoryError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc

    return ReferenceProfileOut(
        reference_id=reference_id,
        status=ref.status,
        profile=ref.profile,
        warnings=list(ref.warnings or []),
    )


@router.get("/{reference_id}", response_model=ReferenceProfileOut)
def get_reference(reference_id: str, db: Session = Depends(get_db)) -> ReferenceProfileOut:
    ref = ReferenceRepository(db).get_by_reference_id(reference_id)
    if ref is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"참고소설 '{reference_id}'이 없습니다."
        )
    return ReferenceProfileOut(
        reference_id=ref.reference_id,
        status=ref.status,
        profile=ref.profile or {},
        warnings=list(ref.warnings or []),
    )


@router.post("/aggregate", response_model=GenreProfileOut)
def aggregate_references(
    reference_ids: list[str],
    genre: str = "",
    store_patterns: bool = False,
    db: Session = Depends(get_db),
) -> GenreProfileOut:
    """여러 참고작을 묶어 GenreProfile과 패턴을 만든다 (기획안 13·15·19번)."""
    repo = ReferenceRepository(db)
    profiles: list[ReferenceProfile] = []
    weights: dict[str, ReferenceWeights] = {}
    for ref_id in reference_ids:
        ref = repo.get_by_reference_id(ref_id)
        if ref is None or not ref.profile:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"'{ref_id}'이 없거나 아직 분석되지 않았습니다.",
            )
        profiles.append(
            ReferenceProfile.from_stored(
                ref.profile,
                reference_id=ref.reference_id,
                title=ref.title,
                genre=ref.genre,
            )
        )
        weights[ref_id] = ReferenceWeights.uniform(ref_id)

    genre_profile = aggregate_profiles(profiles, weights, genre=genre)
    patterns = derive_patterns(genre_profile)

    if store_patterns:
        pattern_repo = PatternRepository(db)
        for pattern in patterns:
            pattern_repo.upsert(pattern)

    return GenreProfileOut(
        genre=genre_profile.genre,
        reference_count=len(profiles),
        profile=genre_profile.as_dict(),
        patterns=[p.as_dict() for p in patterns],
    )


@router.post("/similarity")
def check_similarity(
    body: SimilarityCheckIn, db: Session = Depends(get_db)
) -> dict[str, object]:
    """새 원고가 참고작과 얼마나 겹치는지 본다 (기획안 20·36번)."""
    index = ReferenceRepository(db).build_index(body.reference_ids or None)
    if not index.entries:
        return {
            "verdict": "PASS",
            "max_containment": 0.0,
            "max_jaccard": 0.0,
            "hits": [],
            "note": "비교할 지문이 없습니다. 참고작을 먼저 분석하세요.",
        }
    return check_text(body.text, index, top_k=body.top_k).as_dict()
