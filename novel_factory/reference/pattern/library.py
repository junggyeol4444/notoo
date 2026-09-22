"""Reference Pattern Library (기획안 19번).

여러 작품에서 찾은 패턴을 재사용 가능한 형태로 보관한다.

패턴은 두 갈래로 들어온다.

  1. 자동 도출  GenreProfile의 수치를 사람이 읽을 수 있는 지시문으로 바꾼다.
                "대형 사건을 평균 24화마다" 같은 구조 규칙.
  2. 수동 등록  "초반 3화 안에 주인공이 미래정보로 첫 성공" 같은 의미 패턴.
                이건 수치에서 나오지 않는다. 사람이 쓰거나 LLM이 제안한다.

기획안 21번 원칙에 따라, 패턴은 Novel Bible을 이기지 못한다.
Episode Planner는 Novel Bible과 충돌하는 패턴을 버린다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from novel_factory.reference.pattern.aggregator import GenreProfile

PATTERN_SOURCE_DERIVED = "derived"
PATTERN_SOURCE_MANUAL = "manual"

#: 자동 도출 패턴에 붙는 신뢰도. 공통 지표에서 나왔는지 여부로 갈린다.
CONFIDENCE_COMMON = 0.8
CONFIDENCE_DIVERGENT = 0.4


@dataclass(slots=True)
class ReferencePattern:
    """재사용 가능한 패턴 하나."""

    pattern_id: str
    genre: str
    aspect: str
    instruction: str              # 새 작품 계획에 그대로 넣을 지시문
    effect: str = ""              # 이 패턴이 노리는 효과
    placement: str = ""           # 추천 위치
    confidence: float = 0.5
    source: str = PATTERN_SOURCE_DERIVED
    evidence: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "pattern_id": self.pattern_id,
            "genre": self.genre,
            "aspect": self.aspect,
            "instruction": self.instruction,
            "effect": self.effect,
            "placement": self.placement,
            "confidence": round(self.confidence, 3),
            "source": self.source,
            "evidence": self.evidence,
        }

    def describe(self) -> str:
        """기획안 19번 예시 형태."""
        lines = [self.pattern_id, "", "장르:", self.genre or "-", "", "패턴:", self.instruction]
        if self.effect:
            lines += ["", "효과:", self.effect]
        if self.placement:
            lines += ["", "추천 위치:", self.placement]
        return "\n".join(lines)


def _band_text(low: float, high: float, unit: str) -> str:
    return f"{low:.0f}~{high:.0f}{unit}"


def derive_patterns(
    genre_profile: GenreProfile, *, prefix: str = "PATTERN"
) -> list[ReferencePattern]:
    """GenreProfile의 수치를 지시문으로 바꾼다.

    수치를 그대로 복사하지 않고 범위로 바꾼다. 참고작의 평균값을 정확히
    맞추면 그게 곧 모방이다. 범위 안에서 새 작품이 자기 리듬을 갖게 한다.
    """
    patterns: list[ReferencePattern] = []
    genre = genre_profile.genre
    counter = 0

    def add(
        aspect: str,
        instruction: str,
        *,
        effect: str = "",
        placement: str = "",
        metric: str | None = None,
    ) -> None:
        nonlocal counter
        counter += 1
        agg = genre_profile.metrics.get(metric) if metric else None
        confidence = CONFIDENCE_COMMON if (agg and agg.is_common) else CONFIDENCE_DIVERGENT
        patterns.append(
            ReferencePattern(
                pattern_id=f"{prefix}_{counter:03d}",
                genre=genre,
                aspect=aspect,
                instruction=instruction,
                effect=effect,
                placement=placement,
                confidence=confidence,
                evidence=agg.as_dict() if agg else {},
            )
        )

    m = genre_profile.metrics

    if "major_event_interval" in m and m["major_event_interval"].weighted_mean > 0:
        low, high = genre_profile.target_band("major_event_interval")
        add(
            "event_interval",
            f"대형 사건을 {_band_text(max(low, 1), high, '화')}마다 배치한다.",
            effect="장편에서 독자가 이탈하지 않을 굵은 리듬 유지",
            placement="작품 전체",
            metric="major_event_interval",
        )

    if "minor_event_interval" in m and m["minor_event_interval"].weighted_mean > 0:
        low, high = genre_profile.target_band("minor_event_interval")
        add(
            "event_interval",
            f"중형 사건을 {_band_text(max(low, 1), high, '화')}마다 배치한다.",
            effect="회차 단위 지루함 방지",
            placement="작품 전체",
            metric="minor_event_interval",
        )

    if "first_major_event" in m and m["first_major_event"].weighted_mean > 0:
        first = m["first_major_event"].weighted_mean
        add(
            "pacing",
            f"첫 사건을 {max(round(first), 1)}화 안에 터뜨린다.",
            effect="초반 이탈 방지. 도입부가 길면 독자가 남지 않는다.",
            placement="1~3화",
            metric="first_major_event",
        )

    if "cliffhanger_rate" in m:
        rate = m["cliffhanger_rate"].weighted_mean
        top = sorted(
            genre_profile.cliffhanger_distribution.items(), key=lambda kv: -kv[1]
        )[:3]
        kinds = ", ".join(f"{k} {v * 100:.0f}%" for k, v in top) or "유형 데이터 없음"
        add(
            "cliffhanger",
            f"회차의 {rate * 100:.0f}%를 클리프행어로 끝낸다. 유형 비중은 {kinds}.",
            effect="다음 화 클릭률 확보",
            placement="매 회차 말미",
            metric="cliffhanger_rate",
        )

    shape = genre_profile.episode_shape
    if shape:
        parts = ", ".join(
            f"{k} {v * 100:.0f}%" for k, v in shape.items() if v > 0.01
        )
        add(
            "episode_shape",
            f"한 회차의 구간 배분을 {parts} 근처로 잡는다.",
            effect="회차 단위 리듬의 일관성",
            placement="매 회차",
        )

    if "foreshadow_avg_span" in m and m["foreshadow_avg_span"].weighted_mean > 0:
        low, high = genre_profile.target_band("foreshadow_avg_span")
        add(
            "foreshadowing",
            f"복선은 설치 후 {_band_text(max(low, 1), high, '화')} 안에 회수한다. "
            "회수 전 최소 한 번 중간 암시를 넣는다.",
            effect="독자가 복선을 기억한 채로 회수를 맞이하게 한다",
            placement="작품 전체",
            metric="foreshadow_avg_span",
        )

    if "dialogue_ratio" in m:
        ratio = m["dialogue_ratio"].weighted_mean
        add(
            "dialogue",
            f"대사 비율을 {ratio * 100:.0f}% 안팎으로 유지한다.",
            effect="장르 독자가 기대하는 읽는 속도",
            placement="매 회차",
            metric="dialogue_ratio",
        )

    if "avg_sentence_length" in m:
        length = m["avg_sentence_length"].weighted_mean
        add(
            "style",
            f"평균 문장 길이를 {length:.0f}자 안팎으로 쓴다.",
            effect="모바일 가독성",
            placement="전체",
            metric="avg_sentence_length",
        )

    if genre_profile.emotion_stage_distribution:
        top = sorted(
            genre_profile.emotion_stage_distribution.items(), key=lambda kv: -kv[1]
        )[:3]
        parts = ", ".join(f"{k} {v * 100:.0f}%" for k, v in top)
        add(
            "emotion",
            f"감정 단계 비중을 {parts} 근처로 분포시킨다.",
            effect="감정곡선이 한쪽에 몰리지 않게 한다",
            placement="Arc 단위",
        )

    return patterns


@dataclass(slots=True)
class PatternLibrary:
    """패턴 보관함. 장르별로 쌓인다 (기획안 56번 일곱째 원칙)."""

    patterns: list[ReferencePattern] = field(default_factory=list)

    def add(self, pattern: ReferencePattern) -> None:
        self.patterns.append(pattern)

    def extend(self, patterns: list[ReferencePattern]) -> None:
        self.patterns.extend(patterns)

    def for_genre(self, genre: str) -> list[ReferencePattern]:
        return [p for p in self.patterns if not p.genre or p.genre == genre]

    def for_aspect(
        self, aspect: str, *, genre: str | None = None, min_confidence: float = 0.0
    ) -> list[ReferencePattern]:
        pool = self.for_genre(genre) if genre else self.patterns
        return [
            p for p in pool if p.aspect == aspect and p.confidence >= min_confidence
        ]

    def select(
        self,
        *,
        genre: str | None = None,
        aspects: list[str] | None = None,
        min_confidence: float = 0.0,
        limit: int | None = None,
    ) -> list[ReferencePattern]:
        """Episode Planner가 이번 화에 쓸 패턴을 고를 때 쓴다."""
        pool = self.for_genre(genre) if genre else list(self.patterns)
        if aspects:
            allowed = set(aspects)
            pool = [p for p in pool if p.aspect in allowed]
        pool = [p for p in pool if p.confidence >= min_confidence]
        pool.sort(key=lambda p: -p.confidence)
        return pool[:limit] if limit else pool

    def as_dict(self) -> dict[str, Any]:
        return {"patterns": [p.as_dict() for p in self.patterns]}
