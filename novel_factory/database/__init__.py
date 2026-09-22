"""장기기억 저장소.

SQLite가 기본. NF_DATABASE_URL을 바꾸면 PostgreSQL로 간다.
"""

from novel_factory.database.base import (
    Base,
    create_all,
    drop_all,
    get_engine,
    get_session_factory,
    reset_engine,
    session_scope,
)
from novel_factory.database.vector import (
    SearchHit,
    VectorIndex,
    cosine,
    get_vector_index,
)

__all__ = [
    "Base",
    "SearchHit",
    "VectorIndex",
    "cosine",
    "create_all",
    "drop_all",
    "get_engine",
    "get_session_factory",
    "get_vector_index",
    "reset_engine",
    "session_scope",
]
