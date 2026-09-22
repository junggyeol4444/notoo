"""Reference Analyzer 파이프라인 (기획안 6번).

파일 업로드
  ↓ 텍스트 추출          reference.parser
  ↓ 회차 분리            reference.structure.splitter
  ↓ 회차별 지표          analyzer.episode_metrics
  ↓ 기본 통계            analyzer.basic_stats
  ↓ 전개 분석            analyzer.pacing
  ↓ 클리프행어 분석      analyzer.cliffhanger
  ↓ 캐릭터 분석          analyzer.characters
  ↓ 관계 변화 분석       analyzer.relationships
  ↓ 복선 구조 분석       analyzer.foreshadowing
  ↓ 감정곡선 분석        analyzer.emotion
  ↓ 문체 통계 분석       analyzer.style
  ↓ Reference Profile 생성
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from pathlib import Path

from novel_factory.reference.analyzer.basic_stats import BasicStats, analyze_basic_stats
from novel_factory.reference.analyzer.characters import (
    CharacterProfile,
    analyze_characters,
)
from novel_factory.reference.analyzer.cliffhanger import (
    CliffhangerProfile,
    analyze_cliffhangers,
)
from novel_factory.reference.analyzer.emotion import EmotionCurve, analyze_emotion
from novel_factory.reference.analyzer.episode_metrics import (
    EpisodeMetrics,
    compute_all,
)
from novel_factory.reference.analyzer.foreshadowing import (
    ForeshadowProfile,
    analyze_foreshadowing,
)
from novel_factory.reference.analyzer.pacing import PacingProfile, analyze_pacing
from novel_factory.reference.analyzer.relationships import (
    RelationshipProfile,
    analyze_relationships,
)
from novel_factory.reference.analyzer.style import StyleProfile, analyze_style
from novel_factory.reference.parser import ParsedDocument, parse_file
from novel_factory.reference.profile import EpisodeShapeSummary, ReferenceProfile
from novel_factory.reference.structure.episode_shape import PHASES
from novel_factory.reference.structure.splitter import Episode, split_document
from novel_factory.text.lexicon import DEFAULT_LEXICON, LexiconBundle

#: 이 회차 수 미만이면 전개/복선 분석의 신뢰도가 낮다고 경고한다.
MIN_RELIABLE_EPISODES = 10


@dataclass(slots=True)
class AnalysisResult:
    """분석 산출물 일체.

    profile은 저장용(이름·원문 없음), episodes와 metrics는 이번 실행에서만
    쓰는 중간 산물이다. 유사도 지문을 DB에 넣을 때 metrics를 쓴다.
    """

    profile: ReferenceProfile
    episodes: list[Episode] = field(default_factory=list)
    metrics: list[EpisodeMetrics] = field(default_factory=list)
    document: ParsedDocument | None = None


def analyze_text(
    text: str,
    *,
    reference_id: str,
    title: str = "",
    genre: str = "",
    source_format: str = "text",
    lexicon: LexiconBundle | None = None,
    fallback_chars: int = 5000,
    tail_chars: int = 400,
    major_quantile: float = 0.85,
    with_fingerprint: bool = True,
) -> AnalysisResult:
    """이미 추출된 본문 문자열을 분석한다."""
    document = ParsedDocument(text=text, source_format=source_format, title=title or None)
    return analyze_document(
        document,
        reference_id=reference_id,
        title=title,
        genre=genre,
        lexicon=lexicon,
        fallback_chars=fallback_chars,
        tail_chars=tail_chars,
        major_quantile=major_quantile,
        with_fingerprint=with_fingerprint,
    )


def analyze_document(
    document: ParsedDocument,
    *,
    reference_id: str,
    title: str = "",
    genre: str = "",
    lexicon: LexiconBundle | None = None,
    fallback_chars: int = 5000,
    tail_chars: int = 400,
    major_quantile: float = 0.85,
    with_fingerprint: bool = True,
) -> AnalysisResult:
    lex = lexicon or DEFAULT_LEXICON
    warnings: list[str] = list(document.warnings)

    split = split_document(document, fallback_chars=fallback_chars)
    warnings.extend(split.warnings)
    episodes = split.story_episodes

    if not episodes:
        profile = ReferenceProfile(
            reference_id=reference_id,
            title=title or document.title or reference_id,
            genre=genre,
            source_format=document.source_format,
            split_method=split.method,
            warnings=warnings + ["본문에서 회차를 하나도 찾지 못했습니다."],
        )
        return AnalysisResult(profile=profile, document=document)

    if len(episodes) < MIN_RELIABLE_EPISODES:
        warnings.append(
            f"회차가 {len(episodes)}편뿐입니다. 전개 주기·복선 간격 통계는 "
            f"{MIN_RELIABLE_EPISODES}편 이상일 때 의미가 있습니다."
        )

    metrics = compute_all(
        episodes,
        lexicon=lex,
        tail_chars=tail_chars,
        with_fingerprint=with_fingerprint,
    )

    basic: BasicStats = analyze_basic_stats(metrics)
    pacing: PacingProfile = analyze_pacing(metrics, major_quantile=major_quantile)
    cliffhanger: CliffhangerProfile = analyze_cliffhangers(metrics, lexicon=lex)
    characters: CharacterProfile = analyze_characters(episodes, lexicon=lex)
    relationships: RelationshipProfile = analyze_relationships(
        episodes, characters, lexicon=lex
    )
    foreshadowing: ForeshadowProfile = analyze_foreshadowing(
        episodes, stopwords=lex.name_stopwords
    )
    emotion: EmotionCurve = analyze_emotion(metrics)
    style: StyleProfile = analyze_style(metrics, episodes)

    shape = EpisodeShapeSummary(
        ratios={
            phase: statistics.fmean([m.shape.get(phase, 0.0) for m in metrics])
            for phase in PHASES
        }
    )

    profile = ReferenceProfile(
        reference_id=reference_id,
        title=title or document.title or reference_id,
        genre=genre,
        source_format=document.source_format,
        split_method=split.method,
        basic=basic,
        pacing=pacing,
        cliffhanger=cliffhanger,
        characters=characters,
        relationships=relationships,
        foreshadowing=foreshadowing,
        emotion=emotion,
        style=style,
        episode_shape=shape,
        warnings=warnings,
    )
    return AnalysisResult(
        profile=profile, episodes=episodes, metrics=metrics, document=document
    )


def analyze_file(
    path: Path | str,
    *,
    reference_id: str,
    title: str = "",
    genre: str = "",
    **kwargs: object,
) -> AnalysisResult:
    """파일 경로 하나로 전체 파이프라인을 돈다."""
    document = parse_file(Path(path))
    return analyze_document(
        document, reference_id=reference_id, title=title, genre=genre, **kwargs  # type: ignore[arg-type]
    )


__all__ = [
    "AnalysisResult",
    "analyze_file",
    "analyze_document",
    "analyze_text",
    "MIN_RELIABLE_EPISODES",
]
