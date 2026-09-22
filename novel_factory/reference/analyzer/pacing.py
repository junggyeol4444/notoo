"""전개 분석 (기획안 7번 '전개 분석').

첫 사건 / 첫 갈등 / 첫 보상 / 첫 반전 위치, 사건 발생 주기,
대형 사건 주기, 휴식 회차 빈도, 클라이맥스 위치, 전개 속도 등급.

사건 판정은 회차별 사건 강도(event_score) 분포의 분위수로 한다.
절대 기준값을 박아두면 장르마다 어긋난다. 전투 장르는 사건어가
기본적으로 많고 로맨스는 적기 때문이다.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field

from novel_factory.reference.analyzer.episode_metrics import EpisodeMetrics

#: 사건 발생으로 볼 분위수 (분포의 중앙값 위)
MINOR_EVENT_QUANTILE = 0.50
#: 휴식 회차로 볼 분위수
REST_EVENT_QUANTILE = 0.25
#: 전개 속도 등급 경계. 대형 사건 간격(회차) 기준.
PLOT_SPEED_BOUNDS = ((15.0, "fast"), (30.0, "medium"))


def _quantile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    pos = q * (len(ordered) - 1)
    low = int(pos)
    high = min(low + 1, len(ordered) - 1)
    frac = pos - low
    return ordered[low] * (1 - frac) + ordered[high] * frac


def _intervals(seqs: list[int]) -> list[int]:
    return [b - a for a, b in zip(seqs, seqs[1:])]


@dataclass(slots=True)
class PacingProfile:
    first_event_episode: int | None
    first_event_position_in_episode: float | None
    first_conflict_episode: int | None
    first_reward_episode: int | None
    first_twist_episode: int | None

    minor_event_episodes: list[int] = field(default_factory=list)
    major_event_episodes: list[int] = field(default_factory=list)
    rest_episodes: list[int] = field(default_factory=list)

    minor_event_interval: float = 0.0
    major_event_interval: float = 0.0
    rest_episode_ratio: float = 0.0
    climax_episode: int | None = None
    climax_position: float | None = None   # 작품 전체에서의 상대 위치 0~1
    plot_speed: str = "unknown"

    def as_dict(self) -> dict[str, object]:
        return {
            "first_event_episode": self.first_event_episode,
            "first_event_position_in_episode": (
                round(self.first_event_position_in_episode, 3)
                if self.first_event_position_in_episode is not None
                else None
            ),
            "first_conflict_episode": self.first_conflict_episode,
            "first_reward_episode": self.first_reward_episode,
            "first_twist_episode": self.first_twist_episode,
            "minor_event_interval": round(self.minor_event_interval, 2),
            "major_event_interval": round(self.major_event_interval, 2),
            "major_event_episodes": self.major_event_episodes,
            "rest_episode_ratio": round(self.rest_episode_ratio, 4),
            "climax_episode": self.climax_episode,
            "climax_position": (
                round(self.climax_position, 3)
                if self.climax_position is not None
                else None
            ),
            "plot_speed": self.plot_speed,
        }

    def describe(self) -> str:
        """기획안 7번 예시 형태의 사람이 읽는 요약."""
        lines = []
        if self.first_event_episode is not None:
            pos = self.first_event_position_in_episode
            where = f" {pos * 100:.0f}% 지점" if pos is not None else ""
            lines.append(f"첫 사건: {self.first_event_episode}화{where}")
        if self.first_reward_episode is not None:
            lines.append(f"첫 보상: {self.first_reward_episode}화")
        if self.first_twist_episode is not None:
            lines.append(f"첫 반전: {self.first_twist_episode}화")
        if self.minor_event_interval:
            lines.append(f"중형 사건: 평균 {self.minor_event_interval:.1f}화마다")
        if self.major_event_interval:
            lines.append(f"대형 사건: 평균 {self.major_event_interval:.1f}화마다")
        return "\n".join(lines)


def analyze_pacing(
    metrics: list[EpisodeMetrics],
    *,
    major_quantile: float = 0.85,
) -> PacingProfile:
    if not metrics:
        return PacingProfile(None, None, None, None, None)

    scores = [m.event_score for m in metrics]
    minor_cut = _quantile(scores, MINOR_EVENT_QUANTILE)
    major_cut = _quantile(scores, major_quantile)
    rest_cut = _quantile(scores, REST_EVENT_QUANTILE)

    # 분포가 완전히 평평하면(전부 같은 점수) 분위수가 의미 없다.
    flat = max(scores) - min(scores) < 1e-9

    minor = [m.seq for m in metrics if not flat and m.event_score >= minor_cut]
    major = [m.seq for m in metrics if not flat and m.event_score >= major_cut]
    rest = [m.seq for m in metrics if not flat and m.event_score <= rest_cut]

    first_event = next((m for m in metrics if m.event_score > 0), None)
    first_conflict = next(
        (m.seq for m in metrics if m.shape.get("갈등", 0.0) > 0.0), None
    )
    first_reward = next(
        (m.seq for m in metrics if m.shape.get("보상", 0.0) > 0.0), None
    )
    first_twist = next(
        (m.seq for m in metrics if m.shape.get("반전", 0.0) > 0.0), None
    )

    climax = max(metrics, key=lambda m: m.event_score) if not flat else None
    last_seq = metrics[-1].seq or len(metrics)

    minor_intervals = _intervals(minor)
    major_intervals = _intervals(major)

    major_interval = statistics.fmean(major_intervals) if major_intervals else 0.0
    plot_speed = "unknown"
    if major_interval:
        plot_speed = "slow"
        for bound, label in PLOT_SPEED_BOUNDS:
            if major_interval <= bound:
                plot_speed = label
                break

    return PacingProfile(
        first_event_episode=first_event.seq if first_event else None,
        first_event_position_in_episode=(
            first_event.first_event_position if first_event else None
        ),
        first_conflict_episode=first_conflict,
        first_reward_episode=first_reward,
        first_twist_episode=first_twist,
        minor_event_episodes=minor,
        major_event_episodes=major,
        rest_episodes=rest,
        minor_event_interval=(
            statistics.fmean(minor_intervals) if minor_intervals else 0.0
        ),
        major_event_interval=major_interval,
        rest_episode_ratio=len(rest) / len(metrics),
        climax_episode=climax.seq if climax else None,
        climax_position=(climax.seq / last_seq) if climax and last_seq else None,
        plot_speed=plot_speed,
    )
