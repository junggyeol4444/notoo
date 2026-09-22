"""관계 변화 분석 (기획안 10번).

인물 쌍이 함께 등장한 회차를 모으고, 그 회차에서 두 인물 주변 문장의
감정 극성을 보고 관계 상태를 붙인다. 상태가 바뀐 지점 사이의 회차 수가
곧 '관계 변화 속도'다.

상태: 경계 / 대립 / 협력 / 신뢰 / 갈등 / 재결합

정확도는 낮다. 두 이름이 한 문장에 있다는 것과 그 문장이 부정적이라는 것,
둘뿐인 근거다. 개별 쌍의 판정을 믿지 말고, 작품 전체의 '관계가 몇 화마다
움직이는가'라는 통계로만 쓰는 게 맞다.
"""

from __future__ import annotations

import itertools
import statistics
from dataclasses import dataclass, field

from novel_factory.reference.analyzer.characters import CharacterProfile
from novel_factory.reference.structure.splitter import Episode
from novel_factory.text.lexicon import DEFAULT_LEXICON, LexiconBundle, count_hits
from novel_factory.text.sentence import split_sentences

RELATION_STATES: tuple[str, ...] = ("대립", "경계", "협력", "신뢰")

#: 관계를 추적할 상위 인물 수. 쌍은 N*(N-1)/2개로 불어난다.
MAX_TRACKED_CHARACTERS = 8
#: 이 횟수 미만으로 함께 나오면 관계로 보지 않는다.
MIN_COOCCURRENCE = 2


@dataclass(slots=True)
class RelationPoint:
    episode_seq: int
    state: str
    sentiment: float
    shared_sentences: int


@dataclass(slots=True)
class RelationTrack:
    a: str
    b: str
    points: list[RelationPoint] = field(default_factory=list)

    @property
    def transitions(self) -> list[tuple[int, str, str]]:
        """(회차, 이전 상태, 다음 상태) 목록."""
        out: list[tuple[int, str, str]] = []
        for prev, cur in zip(self.points, self.points[1:]):
            if prev.state != cur.state:
                out.append((cur.episode_seq, prev.state, cur.state))
        return out

    @property
    def change_speed(self) -> float:
        """상태가 바뀌기까지 걸린 평균 회차 수."""
        seqs = [seq for seq, _, _ in self.transitions]
        if len(seqs) < 2:
            return 0.0
        return statistics.fmean(b - a for a, b in zip(seqs, seqs[1:]))

    def as_dict(self) -> dict[str, object]:
        return {
            "pair": [self.a, self.b],
            "cooccurrence": len(self.points),
            "first_episode": self.points[0].episode_seq if self.points else None,
            "last_episode": self.points[-1].episode_seq if self.points else None,
            "state_chain": [p.state for p in self.points],
            "transitions": [
                {"episode": seq, "from": src, "to": dst}
                for seq, src, dst in self.transitions
            ],
            "change_speed": round(self.change_speed, 2),
        }

    def describe(self) -> str:
        """기획안 10번 예시 형태."""
        lines = [f"{self.a} → {self.b}", ""]
        last: str | None = None
        for point in self.points:
            if point.state != last:
                lines.append(f"{point.episode_seq}화:")
                lines.append(point.state)
                lines.append("")
                last = point.state
        return "\n".join(lines).rstrip()


@dataclass(slots=True)
class RelationshipProfile:
    tracks: list[RelationTrack]
    avg_change_speed: float
    avg_transitions_per_pair: float
    state_distribution: dict[str, float]

    def as_dict(self, *, include_names: bool = False) -> dict[str, object]:
        payload: dict[str, object] = {
            "tracked_pairs": len(self.tracks),
            "avg_change_speed": round(self.avg_change_speed, 2),
            "avg_transitions_per_pair": round(self.avg_transitions_per_pair, 2),
            "state_distribution": {
                k: round(v, 4) for k, v in self.state_distribution.items()
            },
        }
        if include_names:
            payload["tracks"] = [t.as_dict() for t in self.tracks]
        return payload


def _state(sentiment: float, cooccurred_before: bool) -> str:
    if sentiment <= -0.35:
        return "대립"
    if sentiment < -0.05:
        return "경계"
    if sentiment < 0.35:
        return "협력" if cooccurred_before else "경계"
    return "신뢰"


def analyze_relationships(
    episodes: list[Episode],
    characters: CharacterProfile,
    *,
    lexicon: LexiconBundle | None = None,
    max_characters: int = MAX_TRACKED_CHARACTERS,
) -> RelationshipProfile:
    lex = lexicon or DEFAULT_LEXICON
    names = [c.name for c in characters.characters[:max_characters]]
    if len(names) < 2:
        return RelationshipProfile([], 0.0, 0.0, {})

    # 짧은 이름(성 뗀 형태)도 같은 인물로 인식한다.
    aliases: dict[str, list[str]] = {}
    for name in names:
        forms = [name]
        if len(name) == 3:
            forms.append(name[1:])
        aliases[name] = forms

    tracks: dict[tuple[str, str], RelationTrack] = {
        pair: RelationTrack(pair[0], pair[1])
        for pair in itertools.combinations(names, 2)
    }

    for ep in episodes:
        sentences = split_sentences(ep.text)
        present = {
            name for name, forms in aliases.items() if any(f in ep.text for f in forms)
        }
        for (a, b), track in tracks.items():
            if a not in present or b not in present:
                continue
            shared = [
                s
                for s in sentences
                if any(f in s for f in aliases[a]) and any(f in s for f in aliases[b])
            ]
            # 한 문장에 같이 안 나오면 회차 전체 분위기로 대신한다.
            scope = "\n".join(shared) if shared else ep.text
            pos = count_hits(scope, lex.positive)
            neg = count_hits(scope, lex.negative)
            sentiment = (pos - neg) / (pos + neg + 3.0) if pos + neg else 0.0
            track.points.append(
                RelationPoint(
                    episode_seq=ep.seq,
                    state=_state(sentiment, bool(track.points)),
                    sentiment=sentiment,
                    shared_sentences=len(shared),
                )
            )

    kept = [t for t in tracks.values() if len(t.points) >= MIN_COOCCURRENCE]
    speeds = [t.change_speed for t in kept if t.change_speed > 0]
    transition_counts = [len(t.transitions) for t in kept]

    state_counts: dict[str, int] = {}
    total_points = 0
    for track in kept:
        for point in track.points:
            state_counts[point.state] = state_counts.get(point.state, 0) + 1
            total_points += 1

    return RelationshipProfile(
        tracks=sorted(kept, key=lambda t: -len(t.points)),
        avg_change_speed=statistics.fmean(speeds) if speeds else 0.0,
        avg_transitions_per_pair=(
            statistics.fmean(transition_counts) if transition_counts else 0.0
        ),
        state_distribution=(
            {k: v / total_points for k, v in state_counts.items()} if total_points else {}
        ),
    )
