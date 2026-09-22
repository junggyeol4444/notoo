"""회차별 지표 1회 추출.

전개·감정·문체·클리프행어 분석기가 모두 같은 원문을 훑는 걸 막기 위해,
회차마다 필요한 수치를 한 번에 뽑아 이 구조체에 담는다. 이후 분석기는
원문이 아니라 이 구조체 목록만 보고 집계한다.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from novel_factory.reference.structure.episode_shape import (
    EpisodeShape,
    EpisodeTexture,
    analyze_episode_shape,
    analyze_episode_texture,
)
from novel_factory.reference.structure.splitter import Episode
from novel_factory.text.dialogue import SegmentKind, segment_text
from novel_factory.text.lexicon import (
    DEFAULT_LEXICON,
    LexiconBundle,
    count_hits,
    count_onomatopoeia,
    count_similes,
    find_hits,
    first_hit_position,
    weighted_hits,
)
from novel_factory.text.sentence import split_paragraphs, split_sentences
from novel_factory.text.tokens import shingle_hashes, syllables

#: 회차 지문에 남길 최대 shingle 개수. 유사도 검사용이며 원문 복원은 불가능하다.
FINGERPRINT_LIMIT = 2048


@dataclass(slots=True)
class EpisodeMetrics:
    """회차 하나에서 뽑은 수치 전부."""

    seq: int
    number: int | None
    title: str
    char_count: int

    sentence_count: int
    avg_sentence_chars: float
    short_sentence_ratio: float       # 15자 이하 문장 비율
    long_sentence_ratio: float        # 40자 이상 문장 비율

    paragraph_count: int
    avg_paragraph_chars: float

    dialogue_ratio: float
    inner_ratio: float
    narration_ratio: float
    dialogue_line_count: int

    action_ratio: float               # 서술 안에서 행동 묘사가 차지하는 비중
    psych_ratio: float                # 서술 안에서 내면 묘사가 차지하는 비중

    event_score: float                # 길이로 정규화한 사건 강도
    event_hits: Counter[str]
    valence: float                    # -1(절망) ~ +1(안정/보상)

    simile_per_1k: float
    onomatopoeia_per_1k: float
    question_ratio: float             # 문장 중 의문문 비율

    shape: dict[str, float]           # 도입/전개/갈등/보상/반전/클리프행어 비율
    tail_text: str                    # 클리프행어 판정에 쓸 회차 말미
    first_event_position: float | None  # 회차 안에서 첫 사건어가 나온 상대 위치 0~1

    name_hits: Counter[str] = field(default_factory=Counter)
    fingerprint: set[int] = field(default_factory=set)

    def as_dict(self) -> dict[str, object]:
        return {
            "seq": self.seq,
            "number": self.number,
            "title": self.title,
            "char_count": self.char_count,
            "sentence_count": self.sentence_count,
            "avg_sentence_chars": round(self.avg_sentence_chars, 2),
            "short_sentence_ratio": round(self.short_sentence_ratio, 4),
            "long_sentence_ratio": round(self.long_sentence_ratio, 4),
            "paragraph_count": self.paragraph_count,
            "avg_paragraph_chars": round(self.avg_paragraph_chars, 1),
            "dialogue_ratio": round(self.dialogue_ratio, 4),
            "inner_ratio": round(self.inner_ratio, 4),
            "narration_ratio": round(self.narration_ratio, 4),
            "dialogue_line_count": self.dialogue_line_count,
            "action_ratio": round(self.action_ratio, 4),
            "psych_ratio": round(self.psych_ratio, 4),
            "event_score": round(self.event_score, 3),
            "valence": round(self.valence, 3),
            "simile_per_1k": round(self.simile_per_1k, 2),
            "onomatopoeia_per_1k": round(self.onomatopoeia_per_1k, 2),
            "question_ratio": round(self.question_ratio, 4),
            "first_event_position": (
                round(self.first_event_position, 3)
                if self.first_event_position is not None
                else None
            ),
            "shape": {k: round(v, 4) for k, v in self.shape.items()},
        }


SHORT_SENTENCE_CHARS = 15
LONG_SENTENCE_CHARS = 40

#: 1,000자당 비율을 낼 때의 분모 하한.
#: 이보다 짧은 회차(프롤로그 등)에서 단어 한 번이 비율을 폭발시키는 걸 막는다.
MIN_NORMALIZATION_CHARS = 500

#: 감정 극성 평활화 상수. 등장 횟수가 적을 때 +1/-1로 튀지 않게 한다.
VALENCE_SMOOTHING = 3.0


def _valence(text: str, lex: LexiconBundle) -> float:
    """감정 극성 -1 ~ +1.

    (pos - neg) / (pos + neg + k) 형태의 가산 평활화를 쓴다. 분모에 k를
    더하지 않으면 부정어 한 번만 나와도 -1.0이 되어, 감정곡선이 회차마다
    바닥과 천장을 오가는 톱니가 된다.
    """
    pos = count_hits(text, lex.positive)
    neg = count_hits(text, lex.negative)
    if pos + neg == 0:
        return 0.0
    return (pos - neg) / (pos + neg + VALENCE_SMOOTHING)


def compute_metrics(
    episode: Episode,
    *,
    lexicon: LexiconBundle | None = None,
    tail_chars: int = 400,
    with_fingerprint: bool = True,
) -> EpisodeMetrics:
    lex = lexicon or DEFAULT_LEXICON
    text = episode.text
    chars = max(syllables(text), 1)
    # 비율 계산용 분모. 짧은 회차에서 과대 추정되지 않도록 하한을 둔다.
    norm_chars = max(chars, MIN_NORMALIZATION_CHARS)

    sentences = split_sentences(text)
    sent_lens = [syllables(s) for s in sentences] or [0]
    paragraphs = split_paragraphs(text)

    segments = segment_text(text)
    narration_text = "\n".join(
        s.text for s in segments if s.kind is SegmentKind.NARRATION
    )
    narration_chars = max(syllables(narration_text), 1)

    texture: EpisodeTexture = analyze_episode_texture(text)
    shape: EpisodeShape = analyze_episode_shape(text, tail_chars=tail_chars, lexicon=lex)

    event_raw, event_hits = weighted_hits(text, lex.events)

    return EpisodeMetrics(
        seq=episode.seq,
        number=episode.number,
        title=episode.title,
        char_count=episode.char_count,
        sentence_count=len(sentences),
        avg_sentence_chars=sum(sent_lens) / len(sent_lens),
        short_sentence_ratio=(
            sum(1 for n in sent_lens if n <= SHORT_SENTENCE_CHARS) / len(sent_lens)
        ),
        long_sentence_ratio=(
            sum(1 for n in sent_lens if n >= LONG_SENTENCE_CHARS) / len(sent_lens)
        ),
        paragraph_count=len(paragraphs),
        avg_paragraph_chars=texture.avg_paragraph_chars,
        dialogue_ratio=texture.dialogue_ratio,
        inner_ratio=texture.inner_ratio,
        narration_ratio=texture.narration_ratio,
        dialogue_line_count=sum(
            1 for s in segments if s.kind is SegmentKind.DIALOGUE
        ),
        # 행동/내면 묘사는 서술 안에서만 센다. 대사 안의 동사는 묘사가 아니다.
        action_ratio=count_hits(narration_text, lex.action) * 100.0 / narration_chars,
        psych_ratio=count_hits(narration_text, lex.psych) * 100.0 / narration_chars,
        # 사건 강도는 1,000자당 점수로 정규화한다. 회차 길이가 제각각이기 때문이다.
        event_score=event_raw * 1000.0 / norm_chars,
        event_hits=event_hits,
        valence=_valence(text, lex),
        simile_per_1k=count_similes(text) * 1000.0 / norm_chars,
        onomatopoeia_per_1k=count_onomatopoeia(text) * 1000.0 / norm_chars,
        question_ratio=(
            sum(1 for s in sentences if s.rstrip().endswith(("?", '?"', "?'")))
            / max(len(sentences), 1)
        ),
        shape=dict(shape.ratios),
        tail_text=_tail(text, tail_chars),
        first_event_position=_first_event_position(text, lex),
        name_hits=Counter(),
        fingerprint=(
            shingle_hashes(text, limit=FINGERPRINT_LIMIT) if with_fingerprint else set()
        ),
    )


def _first_event_position(text: str, lex: LexiconBundle) -> float | None:
    """회차 안에서 사건어가 처음 나온 지점의 상대 위치.

    기획안 7번의 "첫 사건: 1화 20% 지점" 표기를 위한 값이다.
    """
    if not text:
        return None
    hit = first_hit_position(text, frozenset(lex.events))
    return hit[0] / len(text) if hit else None


def _tail(text: str, tail_chars: int) -> str:
    """회차 말미. 문단 경계에서 끊어 문장이 잘리지 않게 한다."""
    if syllables(text) <= tail_chars:
        return text
    paragraphs = split_paragraphs(text)
    picked: list[str] = []
    acc = 0
    for para in reversed(paragraphs):
        picked.append(para)
        acc += syllables(para)
        if acc >= tail_chars:
            break
    return "\n\n".join(reversed(picked))


def compute_all(
    episodes: list[Episode],
    *,
    lexicon: LexiconBundle | None = None,
    tail_chars: int = 400,
    with_fingerprint: bool = True,
) -> list[EpisodeMetrics]:
    return [
        compute_metrics(
            e, lexicon=lexicon, tail_chars=tail_chars, with_fingerprint=with_fingerprint
        )
        for e in episodes
    ]
