"""Reference Profile (기획안 14번).

참고소설 한 편의 분석 결과. 원문은 들어가지 않는다.

기획안 56번 원칙에 따라 다음은 저장하지 않는다.
  - 원문 문장
  - 등장인물 이름
  - 고유명사, 지명, 조직명
  - 세계관 설정

저장하는 것은 구조 수치와, 원문을 복원할 수 없는 해시 지문뿐이다.
지문은 나중에 Similarity Checker가 "새 원고가 이 참고작과 너무
비슷한가"를 판정하는 데만 쓴다.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from novel_factory.reference.analyzer.basic_stats import BasicStats
from novel_factory.reference.analyzer.characters import CharacterProfile
from novel_factory.reference.analyzer.cliffhanger import CliffhangerProfile
from novel_factory.reference.analyzer.emotion import EmotionCurve
from novel_factory.reference.analyzer.foreshadowing import ForeshadowProfile
from novel_factory.reference.analyzer.pacing import PacingProfile
from novel_factory.reference.analyzer.relationships import RelationshipProfile
from novel_factory.reference.analyzer.style import StyleProfile
from novel_factory.reference.structure.episode_shape import PHASES

#: Profile 스키마 버전. 분석 로직이 바뀌어 수치 의미가 달라지면 올린다.
PROFILE_SCHEMA_VERSION = 1


@dataclass(slots=True)
class EpisodeShapeSummary:
    """작품 전체의 평균 회차 구조 (기획안 7번)."""

    ratios: dict[str, float] = field(default_factory=dict)

    def as_dict(self) -> dict[str, float]:
        return {p: round(self.ratios.get(p, 0.0), 4) for p in PHASES}

    def describe(self) -> str:
        return "\n".join(
            f"{phase}: {self.ratios.get(phase, 0.0) * 100:.0f}%" for phase in PHASES
        )


@dataclass(slots=True)
class ReferenceProfile:
    """참고소설 한 편의 분석 결과 전체."""

    reference_id: str
    title: str
    genre: str = ""
    source_format: str = ""
    split_method: str = ""
    analyzed_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    )
    schema_version: int = PROFILE_SCHEMA_VERSION

    basic: BasicStats | None = None
    pacing: PacingProfile | None = None
    cliffhanger: CliffhangerProfile | None = None
    characters: CharacterProfile | None = None
    relationships: RelationshipProfile | None = None
    foreshadowing: ForeshadowProfile | None = None
    emotion: EmotionCurve | None = None
    style: StyleProfile | None = None
    episode_shape: EpisodeShapeSummary = field(default_factory=EpisodeShapeSummary)

    warnings: list[str] = field(default_factory=list)

    # ------------------------------------------------------------------
    # 기획안 14번의 납작한 요약. 다중 참고작 집계와 API 응답에 쓴다.
    # ------------------------------------------------------------------
    def summary(self) -> dict[str, Any]:
        b, p, c, s = self.basic, self.pacing, self.cliffhanger, self.style
        return {
            "reference_id": self.reference_id,
            "genre": self.genre,
            "episode_count": b.episode_count if b else 0,
            "avg_episode_length": round(b.avg_episode_chars, 1) if b else 0.0,
            "dialogue_ratio": round(b.dialogue_ratio, 4) if b else 0.0,
            "description_ratio": round(b.narration_ratio, 4) if b else 0.0,
            "inner_ratio": round(b.inner_ratio, 4) if b else 0.0,
            "first_major_event": p.first_event_episode if p else None,
            "minor_event_interval": round(p.minor_event_interval, 2) if p else 0.0,
            "major_event_interval": round(p.major_event_interval, 2) if p else 0.0,
            "cliffhanger_rate": round(c.rate, 4) if c else 0.0,
            "avg_sentence_length": round(b.avg_sentence_chars, 1) if b else 0.0,
            "avg_paragraph_length": round(b.avg_paragraph_chars, 1) if b else 0.0,
            "plot_speed": p.plot_speed if p else "unknown",
            "pov": s.pov if s else "판정불가",
            "protagonist_count": self.characters.protagonist_count if self.characters else 0,
            "antagonist_count": self.characters.antagonist_count if self.characters else 0,
            "foreshadow_avg_span": (
                round(self.foreshadowing.avg_span, 2) if self.foreshadowing else 0.0
            ),
            "emotion_volatility": (
                round(self.emotion.volatility, 4) if self.emotion else 0.0
            ),
        }

    def as_dict(self, *, include_names: bool = False, verbose: bool = False) -> dict[str, Any]:
        """저장·전송용 표현.

        include_names는 관리자 화면에서 분석 결과를 눈으로 확인할 때만 쓴다.
        DB에 넣는 Reference Profile에는 이름을 담지 않는다.
        """
        payload: dict[str, Any] = {
            "reference_id": self.reference_id,
            "title": self.title,
            "genre": self.genre,
            "source_format": self.source_format,
            "split_method": self.split_method,
            "analyzed_at": self.analyzed_at,
            "schema_version": self.schema_version,
            "summary": self.summary(),
            "episode_shape": self.episode_shape.as_dict(),
            "warnings": list(self.warnings),
        }
        if self.basic:
            payload["basic_stats"] = self.basic.as_dict()
        if self.pacing:
            payload["pacing"] = self.pacing.as_dict()
        if self.cliffhanger:
            payload["cliffhanger"] = self.cliffhanger.as_dict()
        if self.characters:
            payload["characters"] = self.characters.as_dict(include_names=include_names)
        if self.relationships:
            payload["relationships"] = self.relationships.as_dict(
                include_names=include_names
            )
        if self.foreshadowing:
            fs = self.foreshadowing.as_dict(include_terms=include_names)
            if not verbose:
                fs.pop("candidates", None)
            payload["foreshadowing"] = fs
        if self.emotion:
            em = self.emotion.as_dict()
            if not verbose:
                em.pop("smoothed", None)
            payload["emotion"] = em
        if self.style:
            payload["style"] = self.style.as_dict()
        return payload

    def to_json(self, *, include_names: bool = False, verbose: bool = False) -> str:
        return json.dumps(
            self.as_dict(include_names=include_names, verbose=verbose),
            ensure_ascii=False,
            indent=2,
        )

    def report(self) -> str:
        """기획안 49번 '참고소설 관리 화면'에 띄울 사람용 요약."""
        lines = [f"[{self.reference_id}] {self.title}", ""]
        if self.basic:
            lines += [
                "■ 기본 통계",
                f"총 분량 {self.basic.total_chars:,}자 / {self.basic.episode_count}화",
                f"평균 회차 {self.basic.avg_episode_chars:,.0f}자",
                f"평균 문장 {self.basic.avg_sentence_chars:.0f}자 / "
                f"평균 문단 {self.basic.avg_paragraph_chars:.0f}자",
                f"대사 {self.basic.dialogue_ratio * 100:.0f}% / "
                f"서술 {self.basic.narration_ratio * 100:.0f}% / "
                f"속마음 {self.basic.inner_ratio * 100:.0f}%",
                "",
            ]
        if self.pacing:
            lines += ["■ 전개", self.pacing.describe(), f"전개 속도: {self.pacing.plot_speed}", ""]
        lines += ["■ 회차 구조", self.episode_shape.describe(), ""]
        if self.cliffhanger:
            lines += ["■ 클리프행어", self.cliffhanger.describe(), ""]
        if self.style:
            lines += ["■ 문체", self.style.describe(), ""]
        if self.emotion:
            lines += ["■ 감정곡선", self.emotion.describe(limit=8), ""]
        if self.foreshadowing and self.foreshadowing.candidates:
            lines += [
                "■ 복선 패턴",
                f"후보 {len(self.foreshadowing.candidates)}건 / "
                f"평균 회수 간격 {self.foreshadowing.avg_span:.0f}화",
                "",
            ]
        if self.characters:
            ch = self.characters
            lines += [
                "■ 캐릭터 구조",
                f"주인공 {ch.protagonist_count} / 조연 {ch.supporting_count} / "
                f"적대자(추정) {ch.antagonist_count}",
                f"평균 등장 간격 {ch.avg_appearance_gap:.1f}화",
                "",
            ]
        if self.warnings:
            lines += ["■ 경고"] + [f"- {w}" for w in self.warnings]
        return "\n".join(lines).rstrip()
