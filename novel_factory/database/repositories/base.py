"""리포지토리 공통."""

from __future__ import annotations

from typing import Generic, TypeVar

from sqlalchemy import select
from sqlalchemy.orm import Session

from novel_factory.database.base import Base
from novel_factory.errors import NotFoundError

ModelT = TypeVar("ModelT", bound=Base)


class BaseRepository(Generic[ModelT]):
    """세션 하나를 들고 한 모델을 다룬다.

    세션을 만들지도 커밋하지도 않는다. 트랜잭션 경계는 호출하는 쪽
    (session_scope 또는 FastAPI 의존성)이 정한다. 리포지토리가 제멋대로
    커밋하면 여러 테이블을 한 트랜잭션으로 묶을 수 없다.
    """

    model: type[ModelT]

    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, pk: int) -> ModelT | None:
        return self.session.get(self.model, pk)

    def require(self, pk: int) -> ModelT:
        obj = self.get(pk)
        if obj is None:
            raise NotFoundError(f"{self.model.__name__} id={pk}를 찾지 못했습니다.")
        return obj

    def add(self, obj: ModelT) -> ModelT:
        self.session.add(obj)
        self.session.flush()
        return obj

    def delete(self, obj: ModelT) -> None:
        self.session.delete(obj)
        self.session.flush()

    def list_all(self, limit: int = 100, offset: int = 0) -> list[ModelT]:
        stmt = select(self.model).limit(limit).offset(offset)
        return list(self.session.scalars(stmt))
