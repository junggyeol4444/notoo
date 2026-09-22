"""회차 분리와 회차 내부 구조."""

from novel_factory.reference.structure.episode_shape import (
    PHASES,
    EpisodeShape,
    EpisodeTexture,
    analyze_episode_shape,
    analyze_episode_texture,
)
from novel_factory.reference.structure.splitter import (
    Episode,
    SplitResult,
    assign_sequence,
    split_by_length,
    split_by_markers,
    split_document,
)

__all__ = [
    "PHASES",
    "Episode",
    "EpisodeShape",
    "EpisodeTexture",
    "SplitResult",
    "analyze_episode_shape",
    "analyze_episode_texture",
    "assign_sequence",
    "split_by_length",
    "split_by_markers",
    "split_document",
]
