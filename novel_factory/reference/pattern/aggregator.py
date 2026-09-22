"""다중 참고작 집계 (기획안 13·15·16번).

여러 Reference Profile을 모아 두 가지를 분리한다.

  공통 특징  여러 작품이 같이 가진 값 → 장르 공식
  고유 특징  한 작품만 튀는 값 → 그 작품만의 개성

집계는 가중 평균이다. 사용자가 작품마다 참고 강도를 주면(기획안 16번)
그 값이 가중치가 된다. 항목별로 다른 가중치를 줄 수도 있다.
  소설 A - 전개 방식 강하게, 문체 사용 안 함
  소설 B - 복선 구조 강하게, 전개 방식 약하게
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Any

from novel_factory.reference.profile import ReferenceProfile

#: 참고 항목 (기획안 17번 '참고할 요소'). 가중치를 따로 줄 수 있는 단위.
ASPECTS: tuple[str, ...] = (
    "pacing",  # 전개 속도
    "episode_shape",  # 회차 구조
    "cliffhanger",  # 클리프행어
    "foreshadowing",  # 복선
    "relationship",  # 캐릭터 관계 변화
    "event_interval",  # 사건 발생 주기
    "emotion",  # 감정곡선
    "dialogue",  # 대사 비율
    "style",  # 문체
)

#: 각 집계 항목이 어느 참고 항목에 속하는지
_METRIC_ASPECT: dict[str, str] = {
    "avg_episode_length": "episode_shape",
    "dialogue_ratio": "dialogue",
    "description_ratio": "dialogue",
    "inner_ratio": "dialogue",
    "first_major_event": "pacing",
    "minor_event_interval": "event_interval",
    "major_event_interval": "event_interval",
    "cliffhanger_rate": "cliffhanger",
    "avg_sentence_length": "style",
    "avg_paragraph_length": "style",
    "foreshadow_avg_span": "foreshadowing",
    "emotion_volatility": "emotion",
}

#: 변동계수가 이 값을 넘으면 '작품마다 제각각'이라 공통 공식으로 못 쓴다.
DIVERGENCE_CV = 0.5


@dataclass(slots=True)
class ReferenceWeights:
    """작품 하나에 대한 참고 강도 (기획안 16·50번).

    weights에 항목별 0~1 값을 넣는다. 비어 있으면 default를 쓴다.
    0을 주면 그 항목은 집계에서 완전히 빠진다("문체: 사용 안 함").
    """

    reference_id: str
    default: float = 1.0
    weights: dict[str, float] = field(default_factory=dict)

    def for_aspect(self, aspect: str) -> float:
        return float(self.weights.get(aspect, self.default))

    def for_metric(self, metric: str) -> float:
        return self.for_aspect(_METRIC_ASPECT.get(metric, "pacing"))

    @classmethod
    def uniform(cls, reference_id: str, value: float = 1.0) -> ReferenceWeights:
        return cls(reference_id=reference_id, default=value)


@dataclass(slots=True)
class AggregatedMetric:
    metric: str
    aspect: str
    weighted_mean: float
    plain_mean: float
    stdev: float
    minimum: float
    maximum: float
    sample_count: int

    @property
    def coefficient_of_variation(self) -> float:
        if self.plain_mean == 0:
            return 0.0
        return abs(self.stdev / self.plain_mean)

    @property
    def is_common(self) -> bool:
        """작품들이 비슷한 값을 가지면 장르 공식으로 쓸 수 있다."""
        return self.sample_count >= 2 and self.coefficient_of_variation <= DIVERGENCE_CV

    def as_dict(self) -> dict[str, Any]:
        return {
            "metric": self.metric,
            "aspect": self.aspect,
            "value": round(self.weighted_mean, 4),
            "plain_mean": round(self.plain_mean, 4),
            "stdev": round(self.stdev, 4),
            "min": round(self.minimum, 4),
            "max": round(self.maximum, 4),
            "cv": round(self.coefficient_of_variation, 4),
            "is_common": self.is_common,
            "sample_count": self.sample_count,
        }


@dataclass(slots=True)
class GenreProfile:
    """여러 참고작에서 뽑은 장르 공식 (기획안 13번)."""

    genre: str
    reference_ids: list[str]
    metrics: dict[str, AggregatedMetric]
    episode_shape: dict[str, float]
    cliffhanger_distribution: dict[str, float]
    emotion_stage_distribution: dict[str, float]
    plot_speed: str
    outliers: dict[str, list[str]] = field(default_factory=dict)

    def common_metrics(self) -> dict[str, AggregatedMetric]:
        return {k: v for k, v in self.metrics.items() if v.is_common}

    def as_dict(self) -> dict[str, Any]:
        return {
            "genre": self.genre,
            "reference_ids": self.reference_ids,
            "reference_count": len(self.reference_ids),
            "metrics": {k: v.as_dict() for k, v in self.metrics.items()},
            "common_metric_names": sorted(self.common_metrics()),
            "episode_shape": {k: round(v, 4) for k, v in self.episode_shape.items()},
            "cliffhanger_distribution": {
                k: round(v, 4) for k, v in self.cliffhanger_distribution.items()
            },
            "emotion_stage_distribution": {
                k: round(v, 4) for k, v in self.emotion_stage_distribution.items()
            },
            "plot_speed": self.plot_speed,
            "outliers": self.outliers,
        }

    def target_band(self, metric: str, tolerance: float = 0.25) -> tuple[float, float]:
        """새 작품이 노릴 값의 범위.

        참고작 값을 그대로 베끼지 않기 위해 폭을 둔다. 기획안 28번의
        "참고 작품들의 평균 대형 사건 간격 25화 → 새 작품 20~30화마다"가
        이 함수다.
        """
        agg = self.metrics.get(metric)
        if agg is None:
            return (0.0, 0.0)
        center = agg.weighted_mean
        spread = max(agg.stdev, abs(center) * tolerance)
        return (center - spread, center + spread)

    def describe(self) -> str:
        lines = [
            f"{self.genre or '장르 미지정'} 참고작 {len(self.reference_ids)}편 집계",
            "",
        ]
        for name, agg in sorted(self.metrics.items()):
            mark = "공통" if agg.is_common else "편차큼"
            lines.append(f"  [{mark}] {name}: {agg.weighted_mean:.2f} (±{agg.stdev:.2f})")
        if self.outliers:
            lines += ["", "작품별 고유 특징:"]
            for ref_id, items in self.outliers.items():
                lines.append(f"  {ref_id}: {', '.join(items)}")
        return "\n".join(lines)


def _weighted_mean(values: list[float], weights: list[float]) -> float:
    total_w = sum(weights)
    if total_w <= 0:
        return statistics.fmean(values) if values else 0.0
    return sum(v * w for v, w in zip(values, weights, strict=True)) / total_w


def _aggregate_distribution(
    dists: list[tuple[dict[str, float], float]],
) -> dict[str, float]:
    """가중치가 붙은 분포 여러 개를 하나로 합친다."""
    acc: dict[str, float] = {}
    total_w = sum(w for _, w in dists)
    if total_w <= 0:
        return {}
    for dist, weight in dists:
        for key, value in dist.items():
            acc[key] = acc.get(key, 0.0) + value * weight
    return {k: v / total_w for k, v in acc.items()}


def aggregate_profiles(
    profiles: list[ReferenceProfile],
    weights: dict[str, ReferenceWeights] | None = None,
    *,
    genre: str = "",
    outlier_z: float = 1.5,
) -> GenreProfile:
    """Reference Profile 여러 개를 GenreProfile 하나로 집계한다."""
    if not profiles:
        return GenreProfile(genre, [], {}, {}, {}, {}, "unknown")

    weights = weights or {}

    def w_for(profile: ReferenceProfile) -> ReferenceWeights:
        return weights.get(
            profile.reference_id, ReferenceWeights.uniform(profile.reference_id)
        )

    summaries = [(p, p.summary()) for p in profiles]

    metrics: dict[str, AggregatedMetric] = {}
    for metric, aspect in _METRIC_ASPECT.items():
        pairs = [
            (float(s[metric]), w_for(p).for_metric(metric))
            for p, s in summaries
            if s.get(metric) is not None
        ]
        pairs = [(v, w) for v, w in pairs if w > 0]
        if not pairs:
            continue
        values = [v for v, _ in pairs]
        ws = [w for _, w in pairs]
        metrics[metric] = AggregatedMetric(
            metric=metric,
            aspect=aspect,
            weighted_mean=_weighted_mean(values, ws),
            plain_mean=statistics.fmean(values),
            stdev=statistics.pstdev(values) if len(values) > 1 else 0.0,
            minimum=min(values),
            maximum=max(values),
            sample_count=len(values),
        )

    episode_shape = _aggregate_distribution(
        [(p.episode_shape.ratios, w_for(p).for_aspect("episode_shape")) for p in profiles]
    )
    cliffhanger = _aggregate_distribution(
        [
            (p.cliffhanger.distribution, w_for(p).for_aspect("cliffhanger"))
            for p in profiles
            if p.cliffhanger
        ]
    )
    emotion = _aggregate_distribution(
        [
            (p.emotion.stage_distribution, w_for(p).for_aspect("emotion"))
            for p in profiles
            if p.emotion
        ]
    )

    speeds = [p.pacing.plot_speed for p in profiles if p.pacing]
    plot_speed = statistics.mode(speeds) if speeds else "unknown"

    # 고유 특징: 평균에서 표준편차의 outlier_z배 이상 벗어난 항목
    outliers: dict[str, list[str]] = {}
    for profile, summary in summaries:
        flagged: list[str] = []
        for metric, agg in metrics.items():
            value = summary.get(metric)
            if value is None or agg.stdev == 0:
                continue
            if abs(float(value) - agg.plain_mean) >= outlier_z * agg.stdev:
                direction = "높음" if float(value) > agg.plain_mean else "낮음"
                flagged.append(f"{metric} {direction}")
        if flagged:
            outliers[profile.reference_id] = flagged

    return GenreProfile(
        genre=genre or next((p.genre for p in profiles if p.genre), ""),
        reference_ids=[p.reference_id for p in profiles],
        metrics=metrics,
        episode_shape=episode_shape,
        cliffhanger_distribution=cliffhanger,
        emotion_stage_distribution=emotion,
        plot_speed=plot_speed,
        outliers=outliers,
    )
