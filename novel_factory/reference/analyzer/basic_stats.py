"""기본 통계 (기획안 7번).

전체 분량, 회차 수, 평균 회차 길이, 평균 문장 길이, 평균 문단 길이,
대사/서술/행동/내면 비율.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass

from novel_factory.reference.analyzer.episode_metrics import EpisodeMetrics


def _mean(values: list[float]) -> float:
    return statistics.fmean(values) if values else 0.0


def _median(values: list[float]) -> float:
    return statistics.median(values) if values else 0.0


@dataclass(slots=True)
class BasicStats:
    total_chars: int
    episode_count: int
    avg_episode_chars: float
    median_episode_chars: float
    episode_chars_stdev: float
    avg_sentence_chars: float
    avg_paragraph_chars: float
    avg_sentences_per_episode: float
    short_sentence_ratio: float
    long_sentence_ratio: float
    dialogue_ratio: float
    inner_ratio: float
    narration_ratio: float
    action_density: float  # 서술 1,000자당 행동 묘사 어휘 등장 수
    psych_density: float  # 서술 1,000자당 내면 묘사 어휘 등장 수
    question_ratio: float

    def as_dict(self) -> dict[str, float | int]:
        return {
            "total_chars": self.total_chars,
            "episode_count": self.episode_count,
            "avg_episode_chars": round(self.avg_episode_chars, 1),
            "median_episode_chars": round(self.median_episode_chars, 1),
            "episode_chars_stdev": round(self.episode_chars_stdev, 1),
            "avg_sentence_chars": round(self.avg_sentence_chars, 2),
            "avg_paragraph_chars": round(self.avg_paragraph_chars, 1),
            "avg_sentences_per_episode": round(self.avg_sentences_per_episode, 1),
            "short_sentence_ratio": round(self.short_sentence_ratio, 4),
            "long_sentence_ratio": round(self.long_sentence_ratio, 4),
            "dialogue_ratio": round(self.dialogue_ratio, 4),
            "inner_ratio": round(self.inner_ratio, 4),
            "narration_ratio": round(self.narration_ratio, 4),
            "action_density": round(self.action_density, 3),
            "psych_density": round(self.psych_density, 3),
            "question_ratio": round(self.question_ratio, 4),
        }


def analyze_basic_stats(metrics: list[EpisodeMetrics]) -> BasicStats:
    if not metrics:
        return BasicStats(0, 0, *([0.0] * 14))

    lengths = [float(m.char_count) for m in metrics]
    # 평균 문장 길이는 회차 평균의 평균이 아니라 전체 문장의 평균이어야 한다.
    # 회차 길이가 제각각일 때 둘은 다른 값이 나온다.
    total_sentences = sum(m.sentence_count for m in metrics)
    weighted_sentence_chars = sum(m.avg_sentence_chars * m.sentence_count for m in metrics)
    total_paragraphs = sum(m.paragraph_count for m in metrics)
    weighted_paragraph_chars = sum(
        m.avg_paragraph_chars * m.paragraph_count for m in metrics
    )
    total_chars = sum(m.char_count for m in metrics)

    def char_weighted(attr: str) -> float:
        if total_chars == 0:
            return 0.0
        return sum(getattr(m, attr) * m.char_count for m in metrics) / total_chars

    return BasicStats(
        total_chars=total_chars,
        episode_count=len(metrics),
        avg_episode_chars=_mean(lengths),
        median_episode_chars=_median(lengths),
        episode_chars_stdev=statistics.pstdev(lengths) if len(lengths) > 1 else 0.0,
        avg_sentence_chars=(
            weighted_sentence_chars / total_sentences if total_sentences else 0.0
        ),
        avg_paragraph_chars=(
            weighted_paragraph_chars / total_paragraphs if total_paragraphs else 0.0
        ),
        avg_sentences_per_episode=total_sentences / len(metrics),
        short_sentence_ratio=_mean([m.short_sentence_ratio for m in metrics]),
        long_sentence_ratio=_mean([m.long_sentence_ratio for m in metrics]),
        dialogue_ratio=char_weighted("dialogue_ratio"),
        inner_ratio=char_weighted("inner_ratio"),
        narration_ratio=char_weighted("narration_ratio"),
        action_density=_mean([m.action_ratio for m in metrics]) * 10,
        psych_density=_mean([m.psych_ratio for m in metrics]) * 10,
        question_ratio=_mean([m.question_ratio for m in metrics]),
    )
