"""DB 엔진과 세션.

SQLite가 기본이고 PostgreSQL로 갈아끼울 수 있게 만들었다.
바꾸는 지점은 NF_DATABASE_URL 하나뿐이다.

SQLite에서만 필요한 설정(외래키 강제, WAL)은 여기서 처리한다.
SQLite는 기본적으로 외래키를 검사하지 않기 때문에, 이 설정을 빼면
PostgreSQL에서만 터지는 무결성 오류가 생긴다.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool

from novel_factory.config import Settings, get_settings


class Base(DeclarativeBase):
    """모든 모델의 기반."""


_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def _is_sqlite(url: str) -> bool:
    return url.startswith("sqlite")


def create_db_engine(settings: Settings | None = None, **kwargs: Any) -> Engine:
    cfg = settings or get_settings()
    url = cfg.database_url
    options: dict[str, Any] = {"echo": cfg.echo_sql, "future": True}

    if _is_sqlite(url):
        options["connect_args"] = {"check_same_thread": False}
        if ":memory:" in url:
            # 인메모리 SQLite는 연결마다 다른 DB가 된다. 테스트에서 한 DB를
            # 공유하려면 연결을 하나로 묶어야 한다.
            options["poolclass"] = StaticPool
    options.update(kwargs)

    engine = create_engine(url, **options)

    if _is_sqlite(url):

        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_conn: Any, _record: Any) -> None:
            cur = dbapi_conn.cursor()
            # 외래키를 켜지 않으면 PostgreSQL로 옮겼을 때만 터지는
            # 무결성 오류가 생긴다.
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA synchronous=NORMAL")
            cur.close()

    return engine


def get_engine(settings: Settings | None = None) -> Engine:
    global _engine
    if _engine is None:
        _engine = create_db_engine(settings)
    return _engine


def get_session_factory(settings: Settings | None = None) -> sessionmaker[Session]:
    global _session_factory
    if _session_factory is None:
        _session_factory = sessionmaker(
            bind=get_engine(settings), expire_on_commit=False, future=True
        )
    return _session_factory


def reset_engine() -> None:
    """테스트에서 DB를 갈아끼울 때 쓴다."""
    global _engine, _session_factory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _session_factory = None


@contextmanager
def session_scope(settings: Settings | None = None) -> Iterator[Session]:
    """트랜잭션 경계. 예외가 나면 롤백한다."""
    session = get_session_factory(settings)()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def create_all(settings: Settings | None = None) -> None:
    """테이블을 만든다.

    운영에서는 Alembic 마이그레이션을 쓰는 게 맞지만, 스키마가 아직
    굳지 않은 단계라 create_all로 시작한다.
    """
    from novel_factory.database import models  # noqa: F401  모델 등록

    Base.metadata.create_all(bind=get_engine(settings))


def drop_all(settings: Settings | None = None) -> None:
    from novel_factory.database import models  # noqa: F401

    Base.metadata.drop_all(bind=get_engine(settings))
