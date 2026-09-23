"""FastAPI 앱.

    uvicorn novel_factory.app.main:app --reload

Phase 1(참고작 분석), Phase 2(장기기억 DB), Phase 3(집필)이 여기 붙어 있다.
품질 검사(Continuity/Logic/Style), EPUB, 출판은 아직 없다.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from novel_factory.app.routers import novels, references, writing
from novel_factory.app.schemas import HealthOut
from novel_factory.config import get_settings
from novel_factory.database.base import create_all
from novel_factory.errors import (
    LLMError,
    LLMNotConfiguredError,
    MissingDependencyError,
    NotFoundError,
    NovelFactoryError,
    ParseError,
    UnsupportedFormatError,
)
from novel_factory.llm import get_provider
from novel_factory.reference.parser import SUPPORTED_EXTENSIONS

logger = logging.getLogger("novel_factory")

DESCRIPTION = """
참고소설을 구조로 분석하고, 그 구조를 바탕으로 장편소설을 쓰기 위한 시스템.

현재 구현 범위
- Phase 1: 참고소설 분석 (TXT/Markdown/EPUB/DOCX/PDF/HWP/HWPX → Reference Profile)
- Phase 2: 장기기억 DB (Novel Bible, 인물, 지식, 관계, 세계관, 시간선, 복선)
- Phase 3: 집필 (사건 일정 → Arc → 회차 계획 → 장면 설계 → 장면 단위 집필 → 기억 갱신)

원칙
- 참고작 원문은 저장하지 않는다. 구조 수치와 복원 불가능한 해시 지문만 남긴다.
- 참고작의 인물 이름, 고유명사, 세계관은 새 작품으로 옮기지 않는다.
- Novel Bible이 Reference Profile을 이긴다.
"""


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    settings.ensure_dirs()
    create_all(settings)
    logger.info("데이터베이스 준비 완료: %s", settings.database_url)
    if not settings.llm_enabled:
        logger.info(
            "LLM 미설정. 참고작 분석과 장기기억 DB는 그대로 동작합니다. "
            "집필 기능을 쓰려면 NF_LLM_BASE_URL과 NF_LLM_MODEL을 지정하세요."
        )
    yield


app = FastAPI(
    title="AI Novel Factory",
    description=DESCRIPTION,
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(references.router)
app.include_router(novels.router)
app.include_router(writing.router)


@app.exception_handler(NotFoundError)
async def _not_found(request: Request, exc: NotFoundError) -> JSONResponse:
    return JSONResponse(status_code=status.HTTP_404_NOT_FOUND, content={"detail": str(exc)})


@app.exception_handler(UnsupportedFormatError)
async def _unsupported(request: Request, exc: UnsupportedFormatError) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, content={"detail": str(exc)}
    )


@app.exception_handler(MissingDependencyError)
async def _missing_dep(request: Request, exc: MissingDependencyError) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_501_NOT_IMPLEMENTED, content={"detail": str(exc)}
    )


@app.exception_handler(ParseError)
async def _parse_error(request: Request, exc: ParseError) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, content={"detail": str(exc)}
    )


@app.exception_handler(LLMNotConfiguredError)
async def _llm_not_configured(request: Request, exc: LLMNotConfiguredError) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE, content={"detail": str(exc)}
    )


@app.exception_handler(LLMError)
async def _llm_error(request: Request, exc: LLMError) -> JSONResponse:
    # 모델 서버가 실패했거나 형식에 맞는 답을 끝내 주지 않은 경우. 요청이 아니라
    # 상류(모델)의 문제라서 502로 돌려준다.
    return JSONResponse(
        status_code=status.HTTP_502_BAD_GATEWAY, content={"detail": str(exc)}
    )


@app.exception_handler(NovelFactoryError)
async def _domain_error(request: Request, exc: NovelFactoryError) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST, content={"detail": str(exc)}
    )


@app.get("/health", response_model=HealthOut, tags=["system"])
def health(check_llm: bool = False) -> HealthOut:
    """상태 확인.

    check_llm=true를 주면 LLM 서버에 실제로 붙어 본다. 기본은 설정 여부만
    본다. 헬스체크가 매번 외부 서버를 기다리면 안 되기 때문이다.
    """
    settings = get_settings()
    llm_available: bool | None = None
    if check_llm and settings.llm_enabled:
        llm_available = get_provider(settings).available
    return HealthOut(
        status="ok",
        database=settings.database_url.split("://", 1)[0],
        llm_configured=settings.llm_enabled,
        llm_available=llm_available,
        supported_formats=sorted(SUPPORTED_EXTENSIONS),
    )
