"""등록된 참고소설을 분석해 DB에 저장한다.

API(POST /references/{id}/analyze)와 작품 생성 오케스트레이터가 같이 쓴다.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from novel_factory.database.models import Novel, ReferenceNovel
from novel_factory.database.repositories import ReferenceLinkRepository, ReferenceRepository
from novel_factory.errors import NovelFactoryError
from novel_factory.reference.pipeline import analyze_file
from novel_factory.reference.profile import ReferenceProfile
from novel_factory.reference.similarity.names import name_salt, reference_name_hashes


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
    stored = profile.as_dict()
    # 인물 이름은 저장하지 않고 salt 해시만 남긴다 (고유명사 재사용 검사용).
    people = profile.characters.characters if profile.characters else []
    stored["name_hashes"] = reference_name_hashes(
        [(c.name, c.role) for c in people], name_salt()
    )
    repo.save_profile(
        ref,
        stored,
        episode_count=profile.basic.episode_count if profile.basic else 0,
        warnings=profile.warnings,
    )
    if store_fingerprints:
        repo.save_fingerprints(ref, result.metrics)
    ref.analyzed_at = datetime.now(UTC)
    return profile


def linked_name_hashes(session: Session, novel: Novel) -> dict[str, str]:
    """작품에 연결된 참고작들의 인물 이름 해시 → 등급 ("main" / "other")."""
    out: dict[str, str] = {}
    refs = ReferenceRepository(session)
    for link in ReferenceLinkRepository(session).for_novel(novel.id):
        ref = refs.get(link.reference_id)
        hashes = ((ref.profile if ref else None) or {}).get("name_hashes") or {}
        for h in hashes.get("other") or []:
            out.setdefault(h, "other")
        for h in hashes.get("main") or []:
            out[h] = "main"
    return out
