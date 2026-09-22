"""감정곡선 분석 (기획안 12번).

회차별 감정 극성을 이어 붙여 곡선을 만들고, 곡선의 모양을
안정 / 불안 / 위기 / 절망 / 반격 / 보상 여섯 단계로 라벨링한다.

단계 라벨은 두 축으로 정한다.
  - 감정 극성(valence): 지금 분위기가 좋은가 나쁜가
  - 사건 강도(event_score): 지금 뭔가 벌어지고 있는가
같은 valence -0.4라도 사건이 터지는 중이면 '위기', 조용하면 '불안'이다.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field

from novel_factory.reference.analyzer.episode_metrics import EpisodeMetrics

EMOTION_STAGES: tuple[str, ...] = ("안정", "불안", "위기", "절망", "반격", "보상")

#: 이동평균 창 크기. 회차 단위 잡음을 줄인다.
SMOOTHING_WINDOW = 3
#: 직전 저점 대비 이만큼 올라오면 '반격'으로 본다.
REBOUND_DELTA = 0.25


def moving_average(values: list[float], window: int) -> list[float]:
    if window <= 1 or len(values) <= 1:
        return list(values)
    out: list[float] = []
    half = window // 2
    for i in range(len(values)):
        lo = max(0, i - half)
        hi = min(len(values), i + half + 1)
        out.append(statistics.fmean(values[lo:hi]))
    return out


@dataclass(slots=True)
class EmotionCurve:
    series: list[float]                  # 회차별 원 감정 극성
    smoothed: list[float]                # 이동평균
    stages: list[str]                    # 회차별 단계 라벨
    volatility: float                    # 곡선의 표준편차
    swing_count: int                     # 부호가 뒤집힌 횟수
    lowest_episode: int | None
    highest_episode: int | None
    stage_distribution: dict[str, float] = field(default_factory=dict)

    def as_dict(self) -> dict[str, object]:
        return {
            "series": [round(v, 3) for v in self.series],
            "smoothed": [round(v, 3) for v in self.smoothed],
            "stages": self.stages,
            "volatility": round(self.volatility, 4),
            "swing_count": self.swing_count,
            "lowest_episode": self.lowest_episode,
            "highest_episode": self.highest_episode,
            "stage_distribution": {
                k: round(v, 4) for k, v in self.stage_distribution.items()
            },
        }

    def describe(self, limit: int = 12) -> str:
        """기획안 12번 예시처럼 단계 흐름만 뽑아 보여 준다."""
        compressed: list[str] = []
        for stage in self.stages:
            if not compressed or compressed[-1] != stage:
                compressed.append(stage)
        if len(compressed) > limit:
            compressed = compressed[:limit] + ["…"]
        return "\n↓\n".join(compressed)


def _stage(valence: float, event: float, event_median: float, rebound: bool) -> str:
    busy = event > event_median
    if rebound and valence > -0.2:
        return "반격"
    if valence >= 0.25:
        return "보상" if busy else "안정"
    if valence >= -0.1:
        return "안정" if not busy else "불안"
    if valence >= -0.4:
        return "위기" if busy else "불안"
    return "절망"


def analyze_emotion(
    metrics: list[EpisodeMetrics], *, window: int = SMOOTHING_WINDOW
) -> EmotionCurve:
    if not metrics:
        return EmotionCurve([], [], [], 0.0, 0, None, None, {})

    series = [m.valence for m in metrics]
    smoothed = moving_average(series, window)
    events = [m.event_score for m in metrics]
    event_median = statistics.median(events) if events else 0.0

    stages: list[str] = []
    trough = smoothed[0]
    for i, value in enumerate(smoothed):
        trough = min(trough, value)
        rebound = (value - trough) >= REBOUND_DELTA
        stages.append(_stage(value, events[i], event_median, rebound))
        if rebound:
            # 반격 이후에는 저점을 현재 위치로 다시 잡는다.
            trough = value

    swings = sum(
        1
        for a, b in zip(smoothed, smoothed[1:])
        if (a < 0 <= b) or (b < 0 <= a)
    )
    lowest = min(metrics, key=lambda m: m.valence)
    highest = max(metrics, key=lambda m: m.valence)

    counts: dict[str, int] = {}
    for stage in stages:
        counts[stage] = counts.get(stage, 0) + 1
    distribution = {k: v / len(stages) for k, v in counts.items()}

    return EmotionCurve(
        series=series,
        smoothed=smoothed,
        stages=stages,
        volatility=statistics.pstdev(series) if len(series) > 1 else 0.0,
        swing_count=swings,
        lowest_episode=lowest.seq,
        highest_episode=highest.seq,
        stage_distribution=distribution,
    )
