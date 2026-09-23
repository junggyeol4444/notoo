"""FastAPI 의존성."""

from __future__ import annotations

from collections.abc import Iterator

from fastapi import Depends, HTTPException, status
from sqlalchemy.orm import Session

from novel_factory.config import Settings, get_settings
from novel_factory.database.base import get_session_factory
from novel_factory.database.models import Novel
from novel_factory.database.repositories import NovelRepository
from novel_factory.errors import NotFoundError
from novel_factory.llm import LLMProvider, get_provider


def get_db() -> Iterator[Session]:
    """요청 하나가 트랜잭션 하나다.

    예외가 나면 롤백한다. 리포지토리가 커밋하지 않는 이유가 이것이다.
    한 요청 안에서 여러 테이블을 건드려도 전부 같이 커밋되거나 같이 취소된다.
    """
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def settings_dep() -> Settings:
    return get_settings()


def get_novel(slug: str, db: Session = Depends(get_db)) -> Novel:
    try:
        return NovelRepository(db).require_by_slug(slug)
    except NotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


def get_llm() -> LLMProvider:
    """집필용 LLM. 테스트에서는 app.dependency_overrides로 바꿔 끼운다."""
    return get_provider(get_settings())
