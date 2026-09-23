"""집필 목표 수치 (Reference Pattern → 새 작품 지시).

이 작품에 연결된 참고작들을 참고 강도대로 집계해서, 계획 단계가 쓸 목표값을
만든다. 참고작이 없으면 기획안에 적힌 예시값을 기본으로 쓴다.

LLM이 정하게 두지 않는 것들이 여기 있다. "대형 사건을 몇 화마다", "이번 화는
어떤 클리프행어로", "이번 화에 회수할 복선" 같은 결정은 수치로 정해야 250화 동안
흔들리지 않는다. LLM은 그 틀 안에서 내용을 채운다.
"""

from __future__ import annotations

import random
from collections import Counter
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from novel_factory.database.models import Novel
from novel_factory.database.repositories import (
    EpisodeRepository,
    ReferenceLinkRepository,
    ReferenceRepository,
)
from novel_factory.reference.analyzer.cliffhanger import NO_CLIFFHANGER
from novel_factory.reference.pattern import aggregate_profiles
from novel_factory.reference.profile import ReferenceProfile
from novel_factory.reference.structure.episode_shape import PHASES
from novel_factory.text.lexicon import CLIFFHANGER_PATTERNS

CLIFFHANGER_KINDS: tuple[str, ...] = tuple(CLIFFHANGER_PATTERNS)

# 참고작이 없을 때의 기본값. 출처는 기획안의 예시 수치다.
#   회차 구조 (7번):   도입 10 / 전개 45 / 갈등 20 / 보상 15 / 클리프행어 10
#   클리프행어 (8번):  사용률 82%
#   사건 주기 (7번):   중형 4.2화 / 대형 24화 (28번: 20~30화)
DEFAULT_SHAPE: dict[str, float] = {
    "도입": 0.10,
    "전개": 0.45,
    "갈등": 0.20,
    "보상": 0.15,
    "반전": 0.0,
    "클리프행어": 0.10,
}
DEFAULT_CLIFFHANGER_RATE = 0.82
DEFAULT_MINOR_INTERVAL = (3.0, 5.0)
DEFAULT_MAJOR_INTERVAL = (20.0, 30.0)
DEFAULT_FORESHADOW_SPAN = (15.0, 60.0)
DEFAULT_DIALOGUE_RATIO = 0.40


@dataclass(slots=True)
class GenreGuidance:
    """새 작품이 따라갈 목표값. 전부 범위나 분포다."""

    source: str  # "references" | "defaults"
    reference_ids: list[str] = field(default_factory=list)
    episode_shape: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_SHAPE))
    cliffhanger_rate: float = DEFAULT_CLIFFHANGER_RATE
    cliffhanger_distribution: dict[str, float] = field(default_factory=dict)
    minor_interval: tuple[float, float] = DEFAULT_MINOR_INTERVAL
    major_interval: tuple[float, float] = DEFAULT_MAJOR_INTERVAL
    foreshadow_span: tuple[float, float] = DEFAULT_FORESHADOW_SPAN
    dialogue_ratio: float = DEFAULT_DIALOGUE_RATIO
    pattern_instructions: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.cliffhanger_distribution:
            share = 1.0 / len(CLIFFHANGER_KINDS)
            self.cliffhanger_distribution = dict.fromkeys(CLIFFHANGER_KINDS, share)
        self.episode_shape = _normalized(self.episode_shape)

    def as_dict(self) -> dict[str, object]:
        return {
            "source": self.source,
            "reference_ids": self.reference_ids,
            "episode_shape": {k: round(v, 4) for k, v in self.episode_shape.items()},
            "cliffhanger_rate": round(self.cliffhanger_rate, 4),
            "cliffhanger_distribution": {
                k: round(v, 4) for k, v in self.cliffhanger_distribution.items()
            },
            "minor_interval": [round(x, 2) for x in self.minor_interval],
            "major_interval": [round(x, 2) for x in self.major_interval],
            "foreshadow_span": [round(x, 2) for x in self.foreshadow_span],
            "dialogue_ratio": round(self.dialogue_ratio, 4),
        }


def _normalized(shape: dict[str, float]) -> dict[str, float]:
    values = {p: max(float(shape.get(p, 0.0)), 0.0) for p in PHASES}
    total = sum(values.values())
    if total <= 0:
        return dict(DEFAULT_SHAPE)
    return {p: v / total for p, v in values.items()}


def _band(low: float, high: float, floor: float) -> tuple[float, float]:
    low = max(low, floor)
    high = max(high, low + 1.0)
    return (low, high)


def load_guidance(session: Session, novel: Novel) -> GenreGuidance:
    """작품에 연결된 참고작으로 목표값을 만든다. 없으면 기본값."""
    links = ReferenceLinkRepository(session)
    refs = ReferenceRepository(session)
    profiles: list[ReferenceProfile] = []
    for link in links.for_novel(novel.id):
        ref = refs.get(link.reference_id)
        if ref is not None and ref.profile:
            profiles.append(
                ReferenceProfile.from_stored(
                    ref.profile,
                    reference_id=ref.reference_id,
                    title=ref.title,
                    genre=ref.genre,
                )
            )
    if not profiles:
        return GenreGuidance(source="defaults")

    genre = aggregate_profiles(profiles, links.weights_for(novel.id), genre=novel.genre)
    guidance = GenreGuidance(source="references", reference_ids=genre.reference_ids)

    # 참고 강도 0으로 빠진 항목은 metrics에 없다. 그러면 기본값을 그대로 둔다.
    if genre.episode_shape and sum(genre.episode_shape.values()) > 0:
        guidance.episode_shape = _normalized(genre.episode_shape)
    if "cliffhanger_rate" in genre.metrics:
        guidance.cliffhanger_rate = min(
            max(genre.metrics["cliffhanger_rate"].weighted_mean, 0.0), 1.0
        )
    if genre.cliffhanger_distribution:
        dist = {
            k: v
            for k, v in genre.cliffhanger_distribution.items()
            if k in CLIFFHANGER_KINDS
        }
        total = sum(dist.values())
        if total > 0:
            guidance.cliffhanger_distribution = {k: v / total for k, v in dist.items()}
    if "minor_event_interval" in genre.metrics:
        guidance.minor_interval = _band(
            *genre.target_band("minor_event_interval"), floor=1.0
        )
    if "major_event_interval" in genre.metrics:
        guidance.major_interval = _band(
            *genre.target_band("major_event_interval"), floor=2.0
        )
    if "foreshadow_avg_span" in genre.metrics:
        guidance.foreshadow_span = _band(
            *genre.target_band("foreshadow_avg_span"), floor=3.0
        )
    if "dialogue_ratio" in genre.metrics:
        guidance.dialogue_ratio = genre.metrics["dialogue_ratio"].weighted_mean

    from novel_factory.reference.pattern import derive_patterns

    guidance.pattern_instructions = [p.instruction for p in derive_patterns(genre)]
    return guidance


# ---------------------------------------------------------------------------
# 사건 일정 (기획안 28번)
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class EventSchedule:
    """회차별 사건 규모. 결정적으로 정한다."""

    total_episodes: int
    major: list[int]
    minor: list[int]

    def kind_of(self, episode: int) -> str:
        if episode in self.major:
            return "major"
        if episode in self.minor:
            return "minor"
        return "rest"

    def as_dict(self) -> dict[str, object]:
        return {
            "total_episodes": self.total_episodes,
            "major": self.major,
            "minor": self.minor,
        }


def build_event_schedule(
    total_episodes: int,
    guidance: GenreGuidance,
    *,
    seed: str = "",
    first_major: int | None = None,
) -> EventSchedule:
    """대형·중형 사건을 몇 화에 둘지 정한다.

    간격은 목표 범위 안에서 매번 조금씩 흔든다. 정확히 같은 간격으로 두면
    독자가 리듬을 읽는다. 시드를 작품 식별자로 고정해서 같은 작품은 언제
    다시 계산해도 같은 일정이 나온다.
    """
    if total_episodes <= 0:
        return EventSchedule(0, [], [])
    rng = random.Random(f"schedule:{seed}:{total_episodes}")

    major: list[int] = []
    low, high = guidance.major_interval
    cursor = first_major or max(1, round(low / 2))
    while cursor <= total_episodes:
        major.append(cursor)
        cursor += max(1, round(rng.uniform(low, high)))
    # 마지막 대형 사건이 결말에서 멀면 마지막 화를 대형 사건으로 더한다.
    # 가까우면(간격 하한의 절반 미만) 그 사건이 절정이고 남은 회차는 마무리다.
    if (
        major and major[-1] != total_episodes and total_episodes - major[-1] >= low / 2
    ) or not major:
        major.append(total_episodes)

    minor: list[int] = []
    mlow, mhigh = guidance.minor_interval
    cursor = max(1, round(mlow))
    major_set = set(major)
    while cursor <= total_episodes:
        if cursor not in major_set:
            minor.append(cursor)
        cursor += max(1, round(rng.uniform(mlow, mhigh)))
    return EventSchedule(total_episodes, major, minor)


# ---------------------------------------------------------------------------
# 클리프행어 선택 (기획안 8번)
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class HookDirective:
    required: bool
    kind: str | None
    reason: str

    def as_dict(self) -> dict[str, object]:
        return {"required": self.required, "kind": self.kind, "reason": self.reason}


def choose_hook(
    guidance: GenreGuidance,
    history: list[str],
    *,
    is_last_episode: bool = False,
) -> HookDirective:
    """이번 화 말미에 클리프행어를 둘지, 둔다면 어떤 유형인지.

    지금까지 쓴 회차의 유형 분포를 목표 분포와 비교해서 가장 모자란 유형을
    고른다. 같은 유형이 연달아 나오는 것도 막는다. LLM에게 맡기면 '위기발생'
    하나로 수렴하는 경향이 있다.
    """
    if is_last_episode:
        return HookDirective(False, None, "마지막 화는 결말이다.")

    used = [h for h in history if h and h != NO_CLIFFHANGER]
    written = len(history)
    # 비율이 아니라 누적 개수로 판단한다. 비율로 보면 1화에 훅을 한 번 쓰는 순간
    # 사용률이 100%가 되어 2화부터 훅을 막아 버린다.
    # 이번 화까지의 목표 개수(목표율 × 회차 수)보다 이미 많이 썼을 때만 쉰다.
    target_count = guidance.cliffhanger_rate * (written + 1)
    if len(used) >= target_count:
        return HookDirective(
            False,
            None,
            f"지금까지 {written}화 중 {len(used)}화를 클리프행어로 끝냈다. 목표 사용률 "
            f"{guidance.cliffhanger_rate:.0%}를 맞추려면 이번 화는 여운으로 끝낸다.",
        )

    counts = Counter(used)
    total = max(len(used), 1)
    last = used[-1] if used else None

    def deficit(kind: str) -> float:
        return guidance.cliffhanger_distribution.get(kind, 0.0) - counts[kind] / total

    candidates = [k for k in guidance.cliffhanger_distribution if k != last] or list(
        guidance.cliffhanger_distribution
    )
    kind = max(candidates, key=lambda k: (deficit(k), guidance.cliffhanger_distribution[k]))
    return HookDirective(
        True,
        kind,
        f"목표 분포 대비 '{kind}' 유형이 가장 부족하다"
        + (f" (직전 화는 '{last}')." if last else "."),
    )


def hook_history(session: Session, novel: Novel, before: int) -> list[str]:
    """이전 회차들의 클리프행어 유형."""
    episodes = EpisodeRepository(session).recent(novel.id, count=10_000, before=before)
    return [
        e.hook_type or NO_CLIFFHANGER for e in episodes if e.status in ("final", "checked")
    ]
