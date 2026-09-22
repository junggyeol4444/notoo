"""복선 패턴 분석 (기획안 11번).

LLM 없이 '복선'을 알아볼 수는 없다. 여기서 찾는 것은 복선 그 자체가 아니라
복선이 남기는 흔적이다.

  어떤 고유한 표현이 한 회차에 등장했다가
  한동안 사라지고
  한참 뒤 회차에서 다시, 그것도 더 집중적으로 나온다

이 패턴을 가진 표현을 후보로 모아 설치-회수 간격 분포를 낸다.
개별 후보는 틀릴 수 있다. 쓸 수 있는 건 "이 작품은 복선을 평균 몇 화만에
회수하는가" 같은 집계값이다.
"""

from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from novel_factory.reference.structure.splitter import Episode
from novel_factory.text.morph import noun_stems

#: 전체 회차의 이 비율을 넘게 등장하면 배경 어휘다. 복선 매개물이 아니다.
MAX_EPISODE_SPREAD = 0.15
#: 등장 회차 수 절대 상한. 긴 작품에서는 비율만으로 못 거른다.
MAX_EPISODE_COUNT = 6
#: 설치와 회수 사이 최소 간격(회차). 이보다 가까우면 그냥 연속 언급이다.
MIN_PAYOFF_GAP = 3
#: 후보로 인정할 총 등장 횟수 범위. 너무 잦으면 소재가 아니라 배경이다.
MIN_TOTAL_MENTIONS = 3
MAX_TOTAL_MENTIONS = 40
#: 회수 회차의 등장 밀도가 설치 회차의 이 배 이상이어야 '회수'로 본다.
PAYOFF_DENSITY_RATIO = 1.0
#: 설치와 회수 사이에 이 회차 수 이상 '언급이 끊긴 구간'이 있어야 한다.
#: 복선은 한동안 사라졌다가 돌아온다. 끊김 없이 이어지면 반복 언급일 뿐이다.
MIN_SILENCE_GAP = 3
#: 설치 회차는 작품의 앞쪽 이 비율 안에 있어야 한다.
MAX_SETUP_POSITION = 0.75


@dataclass(slots=True)
class ForeshadowCandidate:
    term: str
    setup_episode: int
    payoff_episode: int
    mid_mentions: list[int] = field(default_factory=list)
    total_mentions: int = 0
    setup_count: int = 0
    payoff_count: int = 0

    @property
    def span(self) -> int:
        return self.payoff_episode - self.setup_episode

    def as_dict(self, *, include_term: bool = False) -> dict[str, object]:
        payload: dict[str, object] = {
            "setup_episode": self.setup_episode,
            "payoff_episode": self.payoff_episode,
            "span": self.span,
            "mid_mention_episodes": self.mid_mentions,
            "mid_mention_count": len(self.mid_mentions),
            "total_mentions": self.total_mentions,
        }
        if include_term:
            payload["term"] = self.term
        return payload

    def describe(self) -> str:
        """기획안 11번 예시 형태."""
        lines = [f"복선 설치: {self.setup_episode}화"]
        for i, seq in enumerate(self.mid_mentions, start=1):
            lines.append(f"{'중간' if i == 1 else '추가'} 암시: {seq}화")
        lines.append(f"회수: {self.payoff_episode}화")
        return "\n".join(lines)


@dataclass(slots=True)
class ForeshadowProfile:
    candidates: list[ForeshadowCandidate]
    avg_span: float
    median_span: float
    avg_mid_mentions: float
    setup_position_ratio: float   # 설치 회차가 작품 앞쪽 어디쯤인지 평균 (0~1)
    payoff_position_ratio: float
    density_per_100_episodes: float

    def as_dict(self, *, include_terms: bool = False) -> dict[str, object]:
        return {
            "candidate_count": len(self.candidates),
            "avg_span": round(self.avg_span, 2),
            "median_span": round(self.median_span, 2),
            "avg_mid_mentions": round(self.avg_mid_mentions, 2),
            "setup_position_ratio": round(self.setup_position_ratio, 3),
            "payoff_position_ratio": round(self.payoff_position_ratio, 3),
            "density_per_100_episodes": round(self.density_per_100_episodes, 2),
            "candidates": [
                c.as_dict(include_term=include_terms) for c in self.candidates[:50]
            ],
        }


def analyze_foreshadowing(
    episodes: list[Episode],
    *,
    stopwords: set[str] | None = None,
    min_gap: int = MIN_PAYOFF_GAP,
) -> ForeshadowProfile:
    story = [e for e in episodes if e.is_story]
    if len(story) < min_gap + 2:
        return ForeshadowProfile([], 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)

    stop = stopwords or set()
    total_eps = len(story)
    last_story_seq = max(e.seq for e in story)
    # term -> {episode_seq: count}
    occurrences: dict[str, dict[int, int]] = defaultdict(dict)

    for ep in story:
        # 조사를 뗀 명사형 어간만 본다. 원문 문자열을 그대로 세면
        # "전화가"와 "전화를"이 다른 소재가 되고, 서술어까지 후보가 된다.
        counts = Counter(noun_stems(ep.text, min_len=2, max_len=6))
        for term, count in counts.items():
            if term in stop:
                continue
            occurrences[term][ep.seq] = count

    candidates: list[ForeshadowCandidate] = []
    spread_limit = min(max(int(total_eps * MAX_EPISODE_SPREAD), 2), MAX_EPISODE_COUNT)

    for term, by_ep in occurrences.items():
        if len(by_ep) < 2 or len(by_ep) > spread_limit:
            continue
        total = sum(by_ep.values())
        if not MIN_TOTAL_MENTIONS <= total <= MAX_TOTAL_MENTIONS:
            continue

        seqs = sorted(by_ep)
        setup = seqs[0]
        if setup / last_story_seq > MAX_SETUP_POSITION:
            continue

        # 복선은 한동안 자취를 감춘다. 언급이 끊긴 최장 구간이 짧으면
        # 작품 내내 계속 나오는 배경 소재지 복선이 아니다.
        silence = max((b - a for a, b in zip(seqs, seqs[1:])), default=0)
        if silence < MIN_SILENCE_GAP:
            continue

        # 회수는 '설치에서 충분히 멀고, 설치 때보다 더 진하게 나온' 회차.
        payoff = None
        for seq in reversed(seqs):
            if seq - setup < min_gap:
                continue
            if by_ep[seq] >= by_ep[setup] * PAYOFF_DENSITY_RATIO:
                payoff = seq
                break
        if payoff is None:
            continue

        candidates.append(
            ForeshadowCandidate(
                term=term,
                setup_episode=setup,
                payoff_episode=payoff,
                mid_mentions=[s for s in seqs if setup < s < payoff],
                total_mentions=total,
                setup_count=by_ep[setup],
                payoff_count=by_ep[payoff],
            )
        )

    if not candidates:
        return ForeshadowProfile([], 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)

    candidates.sort(key=lambda c: (-c.span, -c.total_mentions))
    spans = [float(c.span) for c in candidates]
    last_seq = last_story_seq

    return ForeshadowProfile(
        candidates=candidates,
        avg_span=statistics.fmean(spans),
        median_span=statistics.median(spans),
        avg_mid_mentions=statistics.fmean([len(c.mid_mentions) for c in candidates]),
        setup_position_ratio=statistics.fmean(
            [c.setup_episode / last_seq for c in candidates]
        ),
        payoff_position_ratio=statistics.fmean(
            [c.payoff_episode / last_seq for c in candidates]
        ),
        density_per_100_episodes=len(candidates) * 100.0 / total_eps,
    )
