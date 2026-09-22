"""리포지토리 모음.

모든 리포지토리는 세션을 받고, 커밋하지 않는다.
트랜잭션 경계는 호출하는 쪽이 정한다.
"""

from novel_factory.database.repositories.base import BaseRepository
from novel_factory.database.repositories.characters import (
    CharacterRepository,
    KnowledgeRepository,
    RelationshipRepository,
)
from novel_factory.database.repositories.novels import (
    ArcRepository,
    EpisodeRepository,
    NovelRepository,
    StyleBibleRepository,
)
from novel_factory.database.repositories.references import (
    PatternRepository,
    ReferenceLinkRepository,
    ReferenceRepository,
)
from novel_factory.database.repositories.story import (
    ForeshadowingRepository,
    TimelineRepository,
    WorldRepository,
)

__all__ = [
    "ArcRepository",
    "BaseRepository",
    "CharacterRepository",
    "EpisodeRepository",
    "ForeshadowingRepository",
    "KnowledgeRepository",
    "NovelRepository",
    "PatternRepository",
    "ReferenceLinkRepository",
    "ReferenceRepository",
    "RelationshipRepository",
    "StyleBibleRepository",
    "TimelineRepository",
    "WorldRepository",
]
