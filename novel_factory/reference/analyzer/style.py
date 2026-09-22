"""문체 통계 (기획안 18번).

특정 작가의 문장을 재현하기 위한 게 아니다. 새 작품의 Style Bible이
참고할 수 있는 수치만 뽑는다. 원문 문장은 하나도 저장하지 않는다.
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass

from novel_factory.reference.analyzer.episode_metrics import EpisodeMetrics
from novel_factory.reference.structure.splitter import Episode
from novel_factory.text.dialogue import SegmentKind, segment_text

# 1인칭 서술 표지. 서술부에서만 센다. 대사 안의 "나는"은 시점과 무관하다.
_FIRST_PERSON_RE = re.compile(
    r"(?<![가-힣])(?:나는|내가|나를|나에게|내|나의|우리는|저는|제가)(?![가-힣])"
)
# 3인칭 서술 표지
_THIRD_PERSON_RE = re.compile(
    r"(?<![가-힣])(?:그는|그가|그를|그에게|그의|그녀는|그녀가|그녀를|그녀의)(?![가-힣])"
)

POV_FIRST = "1인칭"
POV_THIRD = "3인칭"
POV_MIXED = "혼합"
POV_UNKNOWN = "판정불가"

#: 한쪽이 전체의 이 비율을 넘어야 시점을 확정한다.
POV_CONFIDENCE = 0.7


@dataclass(slots=True)
class StyleProfile:
    avg_sentence_chars: float
    sentence_chars_stdev: float
    short_sentence_ratio: float
    long_sentence_ratio: float
    avg_paragraph_chars: float
    paragraph_chars_stdev: float
    dialogue_ratio: float
    inner_ratio: float
    narration_ratio: float
    action_density: float
    psych_density: float
    simile_per_1k: float
    onomatopoeia_per_1k: float
    question_ratio: float
    pov: str
    pov_first_hits: int
    pov_third_hits: int

    def as_dict(self) -> dict[str, float | str | int]:
        return {
            "avg_sentence_chars": round(self.avg_sentence_chars, 2),
            "sentence_chars_stdev": round(self.sentence_chars_stdev, 2),
            "short_sentence_ratio": round(self.short_sentence_ratio, 4),
            "long_sentence_ratio": round(self.long_sentence_ratio, 4),
            "avg_paragraph_chars": round(self.avg_paragraph_chars, 1),
            "paragraph_chars_stdev": round(self.paragraph_chars_stdev, 1),
            "dialogue_ratio": round(self.dialogue_ratio, 4),
            "inner_ratio": round(self.inner_ratio, 4),
            "narration_ratio": round(self.narration_ratio, 4),
            "action_density": round(self.action_density, 3),
            "psych_density": round(self.psych_density, 3),
            "simile_per_1k": round(self.simile_per_1k, 2),
            "onomatopoeia_per_1k": round(self.onomatopoeia_per_1k, 2),
            "question_ratio": round(self.question_ratio, 4),
            "pov": self.pov,
            "pov_first_hits": self.pov_first_hits,
            "pov_third_hits": self.pov_third_hits,
        }

    def describe(self) -> str:
        def band(value: float, low: float, high: float) -> str:
            return "짧음" if value < low else ("김" if value > high else "보통")

        return "\n".join(
            [
                f"문장 길이: {band(self.avg_sentence_chars, 18, 32)} "
                f"(평균 {self.avg_sentence_chars:.0f}자)",
                f"대화 비율: {self.dialogue_ratio * 100:.0f}%",
                f"서술 비율: {self.narration_ratio * 100:.0f}%",
                f"속마음 비율: {self.inner_ratio * 100:.0f}%",
                f"시점: {self.pov}",
                f"문단 길이: {band(self.avg_paragraph_chars, 60, 150)} "
                f"(평균 {self.avg_paragraph_chars:.0f}자)",
            ]
        )


def detect_pov(episodes: list[Episode]) -> tuple[str, int, int]:
    """서술부만 보고 시점을 판정한다."""
    first = third = 0
    for ep in episodes:
        narration = "\n".join(
            s.text for s in segment_text(ep.text) if s.kind is SegmentKind.NARRATION
        )
        first += len(_FIRST_PERSON_RE.findall(narration))
        third += len(_THIRD_PERSON_RE.findall(narration))

    total = first + third
    if total == 0:
        # 인물 이름만으로 서술하는 3인칭도 있다. 대명사가 전혀 없으면 단정하지 않는다.
        return POV_UNKNOWN, first, third
    if first / total >= POV_CONFIDENCE:
        return POV_FIRST, first, third
    if third / total >= POV_CONFIDENCE:
        return POV_THIRD, first, third
    return POV_MIXED, first, third


def analyze_style(
    metrics: list[EpisodeMetrics], episodes: list[Episode] | None = None
) -> StyleProfile:
    if not metrics:
        return StyleProfile(*([0.0] * 14), POV_UNKNOWN, 0, 0)

    total_chars = max(sum(m.char_count for m in metrics), 1)

    def char_weighted(attr: str) -> float:
        return sum(getattr(m, attr) * m.char_count for m in metrics) / total_chars

    def mean(attr: str) -> float:
        return statistics.fmean([getattr(m, attr) for m in metrics])

    sentence_means = [m.avg_sentence_chars for m in metrics]
    paragraph_means = [m.avg_paragraph_chars for m in metrics]

    pov, first_hits, third_hits = (
        detect_pov(episodes) if episodes else (POV_UNKNOWN, 0, 0)
    )

    return StyleProfile(
        avg_sentence_chars=statistics.fmean(sentence_means),
        sentence_chars_stdev=(
            statistics.pstdev(sentence_means) if len(sentence_means) > 1 else 0.0
        ),
        short_sentence_ratio=mean("short_sentence_ratio"),
        long_sentence_ratio=mean("long_sentence_ratio"),
        avg_paragraph_chars=statistics.fmean(paragraph_means),
        paragraph_chars_stdev=(
            statistics.pstdev(paragraph_means) if len(paragraph_means) > 1 else 0.0
        ),
        dialogue_ratio=char_weighted("dialogue_ratio"),
        inner_ratio=char_weighted("inner_ratio"),
        narration_ratio=char_weighted("narration_ratio"),
        action_density=mean("action_ratio") * 10,
        psych_density=mean("psych_ratio") * 10,
        simile_per_1k=mean("simile_per_1k"),
        onomatopoeia_per_1k=mean("onomatopoeia_per_1k"),
        question_ratio=mean("question_ratio"),
        pov=pov,
        pov_first_hits=first_hits,
        pov_third_hits=third_hits,
    )
