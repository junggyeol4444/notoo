"""클리프행어 분석 (기획안 8번).

회차 말미를 보고 여덟 가지 유형 중 하나로 분류하고, 작품 전체의
클리프행어 사용률과 유형 분포를 낸다.

유형: 정보공개 / 위기발생 / 새로운적 / 반전 / 약속 / 미스터리 /
      보상직전 / 전투직전

어휘 신호만으로는 부족해서 구조 신호를 함께 본다.
  - 마지막 줄이 대사인가
  - 물음표로 끝나는가
  - 말줄임표로 끝나는가
  - 마지막 문단이 유난히 짧은가 (한 방 먹이고 끊는 형태)
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from novel_factory.reference.analyzer.episode_metrics import EpisodeMetrics
from novel_factory.text.dialogue import SegmentKind, segment_text
from novel_factory.text.lexicon import DEFAULT_LEXICON, LexiconBundle, iter_hits
from novel_factory.text.sentence import split_paragraphs, split_sentences
from novel_factory.text.tokens import syllables

NO_CLIFFHANGER = "없음"

#: 이 점수를 못 넘으면 클리프행어가 아니라고 본다.
CLIFFHANGER_THRESHOLD = 1.0

# 구조 신호가 특정 유형에 주는 보너스
_STRUCTURE_BONUS: dict[str, dict[str, float]] = {
    "ends_with_dialogue": {"정보공개": 0.8, "약속": 0.8, "새로운적": 0.3},
    "ends_with_question": {"미스터리": 1.2, "반전": 0.4},
    "ends_with_ellipsis": {"미스터리": 0.8, "위기발생": 0.3},
    "short_last_paragraph": {"위기발생": 0.5, "반전": 0.5, "전투직전": 0.4},
}

#: 마지막 문단이 이 글자 수 이하면 '짧게 끊었다'고 본다.
SHORT_PARAGRAPH_CHARS = 60


@dataclass(slots=True)
class CliffhangerVerdict:
    episode_seq: int
    kind: str
    score: float
    runner_up: str | None
    signals: list[str] = field(default_factory=list)
    # 1위 유형의 점수 중 어휘 근거에서 온 몫. 0이면 구조 신호뿐이다.
    lexical_score: float = 0.0

    @property
    def grounded(self) -> bool:
        """훅이라고 볼 근거가 있는가: 어휘 근거가 있거나 구조 신호가 둘 이상."""
        return self.lexical_score > 0.0 or len(self.signals) >= 2

    @property
    def has_cliffhanger(self) -> bool:
        return self.kind != NO_CLIFFHANGER

    def as_dict(self) -> dict[str, object]:
        return {
            "episode_seq": self.episode_seq,
            "kind": self.kind,
            "score": round(self.score, 3),
            "runner_up": self.runner_up,
            "signals": self.signals,
        }


@dataclass(slots=True)
class CliffhangerProfile:
    rate: float
    distribution: dict[str, float]
    counts: Counter[str]
    verdicts: list[CliffhangerVerdict] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "rate": round(self.rate, 4),
            "distribution": {k: round(v, 4) for k, v in self.distribution.items()},
            "counts": dict(self.counts),
        }

    def describe(self) -> str:
        """기획안 8번 예시 형태의 요약."""
        lines = [f"회차말 클리프행어 사용률: {self.rate * 100:.0f}%", "", "주요 유형:"]
        for kind, ratio in sorted(self.distribution.items(), key=lambda kv: -kv[1]):
            if ratio > 0:
                lines.append(f"{kind} {ratio * 100:.0f}%")
        return "\n".join(lines)


def _structure_signals(tail: str) -> list[str]:
    signals: list[str] = []
    stripped = tail.rstrip()
    if not stripped:
        return signals

    segments = segment_text(tail)
    if segments and segments[-1].kind in (SegmentKind.DIALOGUE, SegmentKind.INNER):
        signals.append("ends_with_dialogue")

    sentences = split_sentences(tail)
    last = sentences[-1].rstrip() if sentences else stripped
    if last.endswith(("?", '?"', "?'")):
        signals.append("ends_with_question")
    if last.endswith(("…", '…"', "…'")):
        signals.append("ends_with_ellipsis")

    paragraphs = split_paragraphs(tail)
    if paragraphs and syllables(paragraphs[-1]) <= SHORT_PARAGRAPH_CHARS:
        signals.append("short_last_paragraph")
    return signals


def classify_cliffhanger(
    tail: str,
    episode_seq: int = 0,
    *,
    lexicon: LexiconBundle | None = None,
) -> CliffhangerVerdict:
    lex = lexicon or DEFAULT_LEXICON
    if not tail.strip():
        return CliffhangerVerdict(episode_seq, NO_CLIFFHANGER, 0.0, None)

    # 어휘 신호는 말미의 뒤쪽 절반에 가중치를 더 준다. 훅은 맨 끝에 있다.
    # 매칭은 어절 경계를 지키는 iter_hits를 쓴다. 단순 부분문자열 검색이면
    # "왜"가 "왜곡"에, "적"이 "실적"에 걸린다.
    midpoint = len(tail) // 2
    lexical: dict[str, float] = {}
    for kind, cues in lex.cliffhangers.items():
        score = 0.0
        for idx, _surface in iter_hits(tail, frozenset(cues)):
            score += 1.5 if idx >= midpoint else 1.0
        lexical[kind] = score

    signals = _structure_signals(tail)
    scores = dict(lexical)
    for signal in signals:
        for kind, bonus in _STRUCTURE_BONUS.get(signal, {}).items():
            scores[kind] = scores.get(kind, 0.0) + bonus

    ranked = sorted(scores.items(), key=lambda kv: -kv[1])
    top_kind, top_score = ranked[0]
    runner_up = ranked[1][0] if len(ranked) > 1 and ranked[1][1] > 0 else None

    # 구조 신호만으로 클리프행어라고 하지 않는다. 짧은 마지막 문단은
    # 그 자체로는 훅이 아니다. 어휘 근거가 있거나 구조 신호가 둘 이상이어야 한다.
    top_lexical = lexical.get(top_kind, 0.0)
    grounded = top_lexical > 0.0 or len(signals) >= 2
    if top_score < CLIFFHANGER_THRESHOLD or not grounded:
        return CliffhangerVerdict(
            episode_seq, NO_CLIFFHANGER, top_score, None, signals, top_lexical
        )
    return CliffhangerVerdict(
        episode_seq, top_kind, top_score, runner_up, signals, top_lexical
    )


def analyze_cliffhangers(
    metrics: list[EpisodeMetrics],
    *,
    lexicon: LexiconBundle | None = None,
) -> CliffhangerProfile:
    verdicts = [classify_cliffhanger(m.tail_text, m.seq, lexicon=lexicon) for m in metrics]
    counts: Counter[str] = Counter(v.kind for v in verdicts)
    with_hook = sum(1 for v in verdicts if v.has_cliffhanger)
    total_hooks = max(with_hook, 1)
    distribution = {
        kind: count / total_hooks
        for kind, count in counts.items()
        if kind != NO_CLIFFHANGER
    }
    return CliffhangerProfile(
        rate=with_hook / len(verdicts) if verdicts else 0.0,
        distribution=distribution,
        counts=counts,
        verdicts=verdicts,
    )
