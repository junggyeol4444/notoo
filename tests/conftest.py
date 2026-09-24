"""테스트 공통 설정.

DB와 데이터 디렉터리를 테스트마다 임시 경로로 갈아끼운다.
설정은 lru_cache로 캐시되므로 환경변수를 바꾼 뒤 캐시를 비워야 한다.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures"
sys.path.insert(0, str(FIXTURES))


@pytest.fixture(scope="session", autouse=True)
def _fixture_files() -> None:
    """픽스처 파일이 없으면 만든다."""
    import make_fixtures

    if not (FIXTURES / "sample_novel.txt").exists():
        make_fixtures.main()


@pytest.fixture
def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from novel_factory.config import get_settings

    monkeypatch.setenv("NF_DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setenv("NF_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("NF_LLM_BASE_URL", raising=False)
    monkeypatch.delenv("NF_LLM_MODEL", raising=False)
    monkeypatch.delenv("NF_EMBEDDING_MODEL", raising=False)
    # 테스트 중에 예약 실행 스레드가 뜨지 않게 한다. 수동 실행은 그대로 된다.
    monkeypatch.setenv("NF_SCHEDULER_ENABLED", "false")
    get_settings.cache_clear()
    from novel_factory.scheduler.service import reset_scheduler

    reset_scheduler()

    from novel_factory.database.base import reset_engine

    reset_engine()
    cfg = get_settings()
    yield cfg
    reset_scheduler()
    reset_engine()
    get_settings.cache_clear()


@pytest.fixture
def db(settings):
    from novel_factory.database.base import create_all, get_session_factory

    create_all(settings)
    session = get_session_factory(settings)()
    try:
        yield session
        session.commit()
    finally:
        session.close()


@pytest.fixture
def client(settings):
    from fastapi.testclient import TestClient

    from novel_factory.app.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="session")
def sample_txt() -> Path:
    return FIXTURES / "sample_novel.txt"


@pytest.fixture(scope="session")
def sample_episodes():
    """분석된 샘플 소설의 회차 목록 (세션 단위 캐시)."""
    from novel_factory.reference.parser import parse_file
    from novel_factory.reference.structure import split_document

    doc = parse_file(FIXTURES / "sample_novel.txt")
    return split_document(doc).story_episodes


@pytest.fixture(scope="session")
def sample_analysis():
    from novel_factory.reference import analyze_file

    return analyze_file(
        FIXTURES / "sample_novel.txt", reference_id="REF_TEST", genre="현대판타지"
    )
