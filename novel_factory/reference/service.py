"""등록된 참고소설을 분석해 DB에 저장한다.

API(POST /references/{id}/analyze)와 작품 생성 오케스트레이터가 같이 쓴다.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from novel_factory.database.models import ReferenceNovel
from novel_factory.database.repositories import ReferenceRepository
from novel_factory.errors import NovelFactoryError
from novel_factory.reference.pipeline import analyze_file
from novel_factory.reference.profile import ReferenceProfile


def analyze_reference_record(
    session: Session,
    ref: ReferenceNovel,
    *,
    genre: str | None = None,
    fallback_chars: int = 5000,
    major_quantile: float = 0.85,
    store_fingerprints: bool = True,
) -> ReferenceProfile:
    """분석하고 Profile·지문을 저장한다. 실패하면 status를 failed로 두고 다시 던진다."""
    repo = ReferenceRepository(session)
    ref.status = "analyzing"
    session.flush()
    try:
        result = analyze_file(
            ref.stored_path,
            reference_id=ref.reference_id,
            title=ref.title,
            genre=genre or ref.genre,
            fallback_chars=fallback_chars,
            major_quantile=major_quantile,
            with_fingerprint=store_fingerprints,
        )
    except NovelFactoryError as exc:
        ref.status = "failed"
        ref.warnings = [str(exc)]
        session.flush()
        raise

    profile: ReferenceProfile = result.profile
    if genre:
        ref.genre = genre
    repo.save_profile(
        ref,
        profile.as_dict(),
        episode_count=profile.basic.episode_count if profile.basic else 0,
        warnings=profile.warnings,
    )
    if store_fingerprints:
        repo.save_fingerprints(ref, result.metrics)
    ref.analyzed_at = datetime.now(UTC)
    return profile
