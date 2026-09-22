"""회차 내부 구조 분석 (기획안 7번 '회차 구조 분석').

한 회차를 도입 / 전개 / 갈등 / 보상 / 반전 / 클리프행어로 나누고
각 구간이 회차에서 차지하는 비율을 낸다.

방법은 문단 단위 분류다. 문단마다 여섯 구간에 대한 점수를 매기고
가장 높은 구간에 배정한다. 점수는 두 가지를 더한 값이다.

  1. 어휘 신호 - 감정 극성, 사건 가중치, 구간 고유 표현
  2. 위치 사전확률 - 도입은 앞에, 보상/반전은 뒤에 나온다는 일반 경향

이건 추정이지 정답이 아니다. 회차 하나만 놓고 보면 틀릴 수 있다.
수백 회차를 평균 냈을 때 작품 간 비교가 되는 수준을 목표로 한다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from novel_factory.text.dialogue import SegmentKind, segment_text
from novel_factory.text.lexicon import (
    DEFAULT_LEXICON,
    LexiconBundle,
    count_hits,
    weighted_hits,
)
from novel_factory.text.sentence import split_paragraphs
from novel_factory.text.tokens import syllables

PHASES: tuple[str, ...] = ("도입", "전개", "갈등", "보상", "반전", "클리프행어")

# 구간별 고유 표현
_PHASE_CUES: dict[str, tuple[str, ...]] = {
    "도입": (
        "아침", "새벽", "저녁", "밤", "다음 날", "이튿날", "그날", "며칠", "주말",
        "사무실", "회의실", "집으로", "창밖", "거리", "도착했", "들어섰", "눈을 떴",
        "일어났", "출근", "로비", "엘리베이터",
    ),
    "전개": (
        "설명했", "물었", "대답했", "말했", "이어", "덧붙", "확인했", "검토",
        "살펴", "정리", "준비", "진행", "논의", "제안",
    ),
    "갈등": (
        "반발", "거부", "맞섰", "쏘아", "노려", "언성", "고함", "따졌", "몰아",
        "압박", "충돌", "대립", "버텼", "물러서지", "싸늘", "굳었",
    ),
    "보상": (
        "성공", "해냈", "이겼", "체결", "성사", "확보", "획득", "인정", "칭찬",
        "안도", "웃었", "미소", "박수", "축하", "드디어", "마침내", "끝났다",
    ),
    "반전": (
        "하지만", "그러나", "사실은", "알고 보니", "설마", "뜻밖", "예상과",
        "믿을 수 없", "말도 안", "뒤집", "착각", "속았", "함정", "이었다니",
        "였다니", "그런데",
    ),
}

# 위치 사전확률. (구간 -> 회차 내 상대 위치 0~1에서의 가중치 함수 구간)
# 값은 "이 구간이 그 위치에서 얼마나 그럴듯한가"를 0~1로 본 것이다.
_POSITION_PRIOR: dict[str, tuple[tuple[float, float], ...]] = {
    # (위치 상한, 가중치)
    "도입": ((0.15, 1.0), (0.30, 0.5), (1.00, 0.05)),
    "전개": ((0.15, 0.3), (0.70, 1.0), (1.00, 0.5)),
    "갈등": ((0.20, 0.2), (0.50, 0.7), (0.90, 1.0), (1.00, 0.7)),
    "보상": ((0.50, 0.2), (0.80, 0.7), (1.00, 1.0)),
    "반전": ((0.50, 0.2), (0.85, 0.8), (1.00, 1.0)),
}

_PRIOR_WEIGHT = 1.2       # 위치 사전확률을 어휘 신호와 얼마나 비슷하게 볼지
_CUE_WEIGHT = 1.0
_VALENCE_WEIGHT = 0.8
_EVENT_WEIGHT = 0.35
_DIALOGUE_WEIGHT = 0.9    # 대사로만 이루어진 문단은 도입일 수 없다

_QUESTION_RE = re.compile(r"[?？]")


def _prior(phase: str, position: float) -> float:
    table = _POSITION_PRIOR.get(phase)
    if table is None:
        return 0.0
    for upper, weight in table:
        if position <= upper:
            return weight
    return table[-1][1]


@dataclass(slots=True)
class PhaseSpan:
    phase: str
    text: str
    chars: int
    position: float


@dataclass(slots=True)
class EpisodeShape:
    """한 회차의 구간 비율."""

    ratios: dict[str, float]
    spans: list[PhaseSpan]
    total_chars: int

    def as_dict(self) -> dict[str, float]:
        return {p: round(self.ratios.get(p, 0.0), 4) for p in PHASES}

    def dominant(self) -> str:
        return max(PHASES, key=lambda p: self.ratios.get(p, 0.0))


def _dialogue_share(text: str) -> float:
    segments = segment_text(text)
    spoken = sum(
        s.length for s in segments if s.kind in (SegmentKind.DIALOGUE, SegmentKind.INNER)
    )
    total = sum(s.length for s in segments)
    return spoken / total if total else 0.0


def _score_paragraph(
    text: str, position: float, lex: LexiconBundle
) -> dict[str, float]:
    pos_hits = count_hits(text, lex.positive)
    neg_hits = count_hits(text, lex.negative)
    event_score, _ = weighted_hits(text, lex.events)
    length = max(syllables(text), 1)
    # 길이로 정규화한다. 긴 문단이 단어 수만으로 이기는 걸 막는다.
    norm = 100.0 / length
    dialogue = _dialogue_share(text)

    scores: dict[str, float] = {}
    for phase in ("도입", "전개", "갈등", "보상", "반전"):
        cue = sum(text.count(c) for c in _PHASE_CUES[phase]) * norm
        s = _CUE_WEIGHT * cue + _PRIOR_WEIGHT * _prior(phase, position)
        if phase == "갈등":
            s += _VALENCE_WEIGHT * neg_hits * norm + _EVENT_WEIGHT * event_score * norm
            s += _DIALOGUE_WEIGHT * dialogue * 0.5
        elif phase == "보상":
            s += _VALENCE_WEIGHT * pos_hits * norm
        elif phase == "반전":
            s += _EVENT_WEIGHT * event_score * norm
        elif phase == "도입":
            # 도입은 상황을 세우는 서술이다. 사건이 많거나 대사뿐이면 도입이 아니다.
            s -= _EVENT_WEIGHT * event_score * norm
            s -= _DIALOGUE_WEIGHT * dialogue
        elif phase == "전개":
            s += _DIALOGUE_WEIGHT * dialogue * 0.6
        scores[phase] = s
    return scores


def analyze_episode_shape(
    text: str,
    *,
    tail_chars: int = 400,
    lexicon: LexiconBundle | None = None,
) -> EpisodeShape:
    """회차 본문에서 구간 비율을 뽑는다."""
    lex = lexicon or DEFAULT_LEXICON
    paragraphs = split_paragraphs(text)
    total = syllables(text)
    if not paragraphs or total == 0:
        return EpisodeShape({p: 0.0 for p in PHASES}, [], 0)

    lengths = [max(syllables(p), 1) for p in paragraphs]
    cumulative = 0
    total_len = sum(lengths)

    # 뒤에서부터 tail_chars만큼을 클리프행어 구간으로 떼어 둔다.
    # 회차가 짧으면 전체의 1/4을 넘지 않게 제한한다.
    tail_budget = min(tail_chars, max(total_len // 4, 1))
    tail_start_idx = len(paragraphs) - 1
    acc = 0
    for i in range(len(paragraphs) - 1, -1, -1):
        nxt = acc + lengths[i]
        if acc > 0 and nxt > tail_budget:
            # 이 문단까지 넣으면 예산 초과. 넣는 쪽과 빼는 쪽 중
            # 예산에 더 가까운 쪽을 고른다.
            if abs(nxt - tail_budget) < abs(acc - tail_budget):
                tail_start_idx = i
            break
        acc = nxt
        tail_start_idx = i
        if acc >= tail_budget:
            break

    spans: list[PhaseSpan] = []
    for i, (para, plen) in enumerate(zip(paragraphs, lengths)):
        position = cumulative / total_len
        cumulative += plen
        if i >= tail_start_idx:
            spans.append(PhaseSpan("클리프행어", para, plen, position))
            continue
        scores = _score_paragraph(para, position, lex)
        phase = max(scores, key=lambda k: scores[k])
        spans.append(PhaseSpan(phase, para, plen, position))

    ratios = {p: 0.0 for p in PHASES}
    for span in spans:
        ratios[span.phase] += span.chars
    for p in ratios:
        ratios[p] /= total_len

    return EpisodeShape(ratios=ratios, spans=spans, total_chars=total)


@dataclass(slots=True)
class EpisodeTexture:
    """회차의 표면 질감. 구간 비율과 함께 회차 구조를 설명한다."""

    dialogue_ratio: float
    inner_ratio: float
    narration_ratio: float
    question_count: int
    paragraph_count: int
    avg_paragraph_chars: float

    def as_dict(self) -> dict[str, float | int]:
        return {
            "dialogue_ratio": round(self.dialogue_ratio, 4),
            "inner_ratio": round(self.inner_ratio, 4),
            "narration_ratio": round(self.narration_ratio, 4),
            "question_count": self.question_count,
            "paragraph_count": self.paragraph_count,
            "avg_paragraph_chars": round(self.avg_paragraph_chars, 1),
        }


def analyze_episode_texture(text: str) -> EpisodeTexture:
    segments = segment_text(text)
    dialogue = sum(s.length for s in segments if s.kind is SegmentKind.DIALOGUE)
    inner = sum(s.length for s in segments if s.kind is SegmentKind.INNER)
    narration = sum(s.length for s in segments if s.kind is SegmentKind.NARRATION)
    total = max(dialogue + inner + narration, 1)
    paragraphs = split_paragraphs(text)
    return EpisodeTexture(
        dialogue_ratio=dialogue / total,
        inner_ratio=inner / total,
        narration_ratio=narration / total,
        question_count=len(_QUESTION_RE.findall(text)),
        paragraph_count=len(paragraphs),
        avg_paragraph_chars=(
            sum(syllables(p) for p in paragraphs) / len(paragraphs) if paragraphs else 0.0
        ),
    )
