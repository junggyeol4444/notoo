"""회차 지표와 개별 분석기.

이 패키지는 reference.profile을 import하지 않는다. profile이 여기의
결과 타입들을 쓰기 때문에, 여기서 profile을 부르면 순환 import가 된다.
둘을 묶는 파이프라인은 reference.pipeline에 있다.
"""

from novel_factory.reference.analyzer.basic_stats import BasicStats, analyze_basic_stats
from novel_factory.reference.analyzer.characters import (
    CharacterProfile,
    CharacterStat,
    analyze_characters,
)
from novel_factory.reference.analyzer.cliffhanger import (
    CliffhangerProfile,
    CliffhangerVerdict,
    analyze_cliffhangers,
    classify_cliffhanger,
)
from novel_factory.reference.analyzer.emotion import EmotionCurve, analyze_emotion
from novel_factory.reference.analyzer.episode_metrics import (
    EpisodeMetrics,
    compute_all,
    compute_metrics,
)
from novel_factory.reference.analyzer.foreshadowing import (
    ForeshadowCandidate,
    ForeshadowProfile,
    analyze_foreshadowing,
)
from novel_factory.reference.analyzer.pacing import PacingProfile, analyze_pacing
from novel_factory.reference.analyzer.relationships import (
    RelationshipProfile,
    RelationTrack,
    analyze_relationships,
)
from novel_factory.reference.analyzer.style import StyleProfile, analyze_style

__all__ = [
    "BasicStats",
    "analyze_basic_stats",
    "PacingProfile",
    "analyze_pacing",
    "CliffhangerProfile",
    "CliffhangerVerdict",
    "analyze_cliffhangers",
    "classify_cliffhanger",
    "CharacterProfile",
    "CharacterStat",
    "analyze_characters",
    "RelationshipProfile",
    "RelationTrack",
    "analyze_relationships",
    "ForeshadowProfile",
    "ForeshadowCandidate",
    "analyze_foreshadowing",
    "EmotionCurve",
    "analyze_emotion",
    "StyleProfile",
    "analyze_style",
    "EpisodeMetrics",
    "compute_metrics",
    "compute_all",
]
