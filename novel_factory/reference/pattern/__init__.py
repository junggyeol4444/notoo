"""다중 참고작 집계와 패턴 보관함."""

from novel_factory.reference.pattern.aggregator import (
    ASPECTS,
    AggregatedMetric,
    GenreProfile,
    ReferenceWeights,
    aggregate_profiles,
)
from novel_factory.reference.pattern.library import (
    PatternLibrary,
    ReferencePattern,
    derive_patterns,
)

__all__ = [
    "ASPECTS",
    "AggregatedMetric",
    "GenreProfile",
    "ReferenceWeights",
    "aggregate_profiles",
    "PatternLibrary",
    "ReferencePattern",
    "derive_patterns",
]
