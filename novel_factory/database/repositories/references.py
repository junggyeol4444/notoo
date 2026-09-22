"""참고작 리포지토리 (기획안 14·16·19·20번)."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from novel_factory.database.models import (
    ReferenceFingerprint,
    ReferenceLink,
    ReferenceNovel,
    ReferencePatternRow,
)
from novel_factory.database.repositories.base import BaseRepository
from novel_factory.errors import NotFoundError
from novel_factory.reference.pattern.aggregator import ReferenceWeights
from novel_factory.reference.pattern.library import ReferencePattern
from novel_factory.reference.similarity.fingerprint import FingerprintIndex


class ReferenceRepository(BaseRepository[ReferenceNovel]):
    model = ReferenceNovel

    def get_by_reference_id(self, reference_id: str) -> ReferenceNovel | None:
        return self.session.scalar(
            select(ReferenceNovel).where(ReferenceNovel.reference_id == reference_id)
        )

    def require_by_reference_id(self, reference_id: str) -> ReferenceNovel:
        ref = self.get_by_reference_id(reference_id)
        if ref is None:
            raise NotFoundError(f"참고소설 '{reference_id}'을 찾지 못했습니다.")
        return ref

    def get_by_hash(self, content_hash: str) -> ReferenceNovel | None:
        """같은 파일을 두 번 올렸는지 확인한다."""
        return self.session.scalar(
            select(ReferenceNovel).where(ReferenceNovel.content_hash == content_hash)
        )

    def next_reference_id(self) -> str:
        existing = [
            r.reference_id
            for r in self.session.scalars(select(ReferenceNovel))
            if r.reference_id.startswith("REF")
        ]
        numbers = [int(r[3:]) for r in existing if r[3:].isdigit()]
        return f"REF{max(numbers, default=0) + 1:03d}"

    def list_by_status(
        self, status: str | None = None, genre: str | None = None, limit: int = 100
    ) -> list[ReferenceNovel]:
        stmt = select(ReferenceNovel).order_by(ReferenceNovel.created_at.desc())
        if status:
            stmt = stmt.where(ReferenceNovel.status == status)
        if genre:
            stmt = stmt.where(ReferenceNovel.genre == genre)
        return list(self.session.scalars(stmt.limit(limit)))

    def save_profile(
        self,
        ref: ReferenceNovel,
        profile_dict: dict,
        *,
        episode_count: int,
        warnings: list[str] | None = None,
    ) -> ReferenceNovel:
        ref.profile = profile_dict
        ref.episode_count = episode_count
        ref.warnings = warnings or []
        ref.status = "analyzed"
        ref.analyzed_at = datetime.now(timezone.utc)
        self.session.flush()
        return ref

    def save_fingerprints(
        self, ref: ReferenceNovel, metrics: list, *, shingle_size: int = 8
    ) -> int:
        """분석 산출물의 지문을 DB에 넣는다. 원문은 넣지 않는다."""
        existing = {fp.episode_seq: fp for fp in ref.fingerprints}
        saved = 0
        for m in metrics:
            hashes = sorted(getattr(m, "fingerprint", None) or [])
            if not hashes:
                continue
            row = existing.get(m.seq)
            if row is None:
                row = ReferenceFingerprint(
                    reference_id=ref.id,
                    episode_seq=m.seq,
                    shingle_size=shingle_size,
                    hashes=hashes,
                )
                self.session.add(row)
            else:
                row.hashes = hashes
                row.shingle_size = shingle_size
            saved += 1
        self.session.flush()
        return saved

    def build_index(self, reference_ids: list[str] | None = None) -> FingerprintIndex:
        """유사도 검사용 색인을 만든다."""
        stmt = select(ReferenceFingerprint, ReferenceNovel.reference_id).join(
            ReferenceNovel, ReferenceFingerprint.reference_id == ReferenceNovel.id
        )
        if reference_ids:
            stmt = stmt.where(ReferenceNovel.reference_id.in_(reference_ids))
        index = FingerprintIndex()
        for fp, ref_id in self.session.execute(stmt):
            index.add(ref_id, fp.episode_seq, set(fp.hashes or []))
        return index


class ReferenceLinkRepository(BaseRepository[ReferenceLink]):
    """작품-참고작 연결과 참고 강도 (기획안 16번)."""

    model = ReferenceLink

    def for_novel(self, novel_id: int) -> list[ReferenceLink]:
        return list(
            self.session.scalars(
                select(ReferenceLink).where(ReferenceLink.novel_id == novel_id)
            )
        )

    def upsert(
        self,
        novel_id: int,
        reference_pk: int,
        *,
        weights: dict[str, float] | None = None,
        default_weight: float = 1.0,
    ) -> ReferenceLink:
        link = self.session.scalar(
            select(ReferenceLink).where(
                ReferenceLink.novel_id == novel_id,
                ReferenceLink.reference_id == reference_pk,
            )
        )
        if link is None:
            link = ReferenceLink(
                novel_id=novel_id,
                reference_id=reference_pk,
                weights=weights or {},
                default_weight=default_weight,
            )
            return self.add(link)
        if weights is not None:
            link.weights = weights
        link.default_weight = default_weight
        self.session.flush()
        return link

    def weights_for(self, novel_id: int) -> dict[str, ReferenceWeights]:
        """집계에 넘길 형태로 변환한다."""
        out: dict[str, ReferenceWeights] = {}
        for link in self.for_novel(novel_id):
            ref = self.session.get(ReferenceNovel, link.reference_id)
            if ref is None:
                continue
            out[ref.reference_id] = ReferenceWeights(
                reference_id=ref.reference_id,
                default=link.default_weight,
                weights=dict(link.weights or {}),
            )
        return out


class PatternRepository(BaseRepository[ReferencePatternRow]):
    model = ReferencePatternRow

    def upsert(self, pattern: ReferencePattern) -> ReferencePatternRow:
        row = self.session.scalar(
            select(ReferencePatternRow).where(
                ReferencePatternRow.pattern_id == pattern.pattern_id
            )
        )
        fields = {
            "genre": pattern.genre,
            "aspect": pattern.aspect,
            "instruction": pattern.instruction,
            "effect": pattern.effect,
            "placement": pattern.placement,
            "confidence": pattern.confidence,
            "source": pattern.source,
            "evidence": pattern.evidence,
        }
        if row is None:
            row = ReferencePatternRow(pattern_id=pattern.pattern_id, **fields)
            return self.add(row)
        for key, value in fields.items():
            setattr(row, key, value)
        self.session.flush()
        return row

    def select_patterns(
        self,
        *,
        genre: str | None = None,
        aspects: list[str] | None = None,
        min_confidence: float = 0.0,
        limit: int = 20,
    ) -> list[ReferencePatternRow]:
        stmt = select(ReferencePatternRow).where(
            ReferencePatternRow.confidence >= min_confidence
        )
        if genre:
            stmt = stmt.where(
                ReferencePatternRow.genre.in_([genre, ""])
            )
        if aspects:
            stmt = stmt.where(ReferencePatternRow.aspect.in_(aspects))
        stmt = stmt.order_by(ReferencePatternRow.confidence.desc()).limit(limit)
        return list(self.session.scalars(stmt))
