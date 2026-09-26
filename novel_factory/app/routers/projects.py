"""작품 생성 API (기획안 57번).

    POST /projects/parse     요청 글을 설정값으로 읽어 보기만 한다
    POST /projects           분석 → 패턴 → 세계관·캐릭터 → 전체 스토리 → 표지 → 집필 예약
    GET  /projects/{slug}    어디까지 왔는가 (집필 진행률, 완결, 출판)

요청 글(text)과 설정값을 같이 줄 수 있다. 설정값이 요청 글보다 우선한다.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from novel_factory.app.deps import get_db, get_llm, get_novel
from novel_factory.database.models import Novel
from novel_factory.llm.base import LLMProvider
from novel_factory.orchestrator.project import ProjectOptions, project_status, start_project
from novel_factory.orchestrator.request import ProjectRequest, ReferenceSpec, parse_request
from novel_factory.reference.pattern.aggregator import ASPECTS
from novel_factory.scheduler.service import get_scheduler

router = APIRouter(prefix="/projects", tags=["projects"])


class ReferenceIn(BaseModel):
    name: str = Field(min_length=1, description="reference_id 또는 제목")
    aspects: list[str] | None = Field(
        default=None, description="참고할 항목만. 비우면 전부"
    )


class ProjectIn(BaseModel):
    text: str = Field(default="", description="자연어 요청 (기획안 57번 예시 형식)")
    slug: str = Field(default="", max_length=64, pattern=r"^[A-Za-z0-9_-]*$")
    genre: str | None = None
    title: str | None = None
    episodes: int | None = Field(default=None, ge=1, le=5000)
    characters_per_episode: int | None = Field(default=None, ge=500, le=50000)
    references: list[ReferenceIn] | None = None
    publishing_mode: Literal["manual", "automatic"] = "automatic"
    episodes_per_run: int = Field(default=1, ge=1, le=50)
    continuous: bool = False
    start_now: bool = Field(default=False, description="만든 뒤 바로 집필을 시작한다")


def _build_request(body: ProjectIn, llm: LLMProvider) -> ProjectRequest:
    req = (
        parse_request(body.text, llm if llm.available else None)
        if body.text.strip()
        else ProjectRequest()
    )
    if body.genre:
        req.genre = body.genre
    if body.title:
        req.title = body.title
    if body.episodes:
        req.episodes = body.episodes
    if body.characters_per_episode:
        req.chars_per_episode = body.characters_per_episode
    if body.references is not None:
        bad = [a for r in body.references for a in (r.aspects or []) if a not in ASPECTS]
        if bad:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"알 수 없는 참고 항목 {bad}. 가능한 항목: {list(ASPECTS)}",
            )
        req.references = [ReferenceSpec(r.name, r.aspects) for r in body.references]
    return req


@router.post("/parse")
def parse_project(
    body: ProjectIn, llm: LLMProvider = Depends(get_llm)
) -> dict[str, object]:
    return _build_request(body, llm).as_dict()


@router.post("", status_code=status.HTTP_201_CREATED)
def create_project(
    body: ProjectIn,
    db: Session = Depends(get_db),
    llm: LLMProvider = Depends(get_llm),
) -> dict[str, object]:
    request = _build_request(body, llm)
    result = start_project(
        db,
        request,
        llm,
        options=ProjectOptions(
            publishing_mode=body.publishing_mode,
            episodes_per_run=body.episodes_per_run,
            continuous=body.continuous,
        ),
        slug=body.slug,
    )
    novel_id = result.novel.id
    # 스케줄러는 자기 세션을 연다. 여기서 먼저 커밋해야 새 작품이 보인다.
    db.commit()
    started = get_scheduler().run_now(novel_id=novel_id) if body.start_now else False
    return {**result.as_dict(), "writing_started": started}


@router.get("/{slug}")
def get_project(novel: Novel = Depends(get_novel), db: Session = Depends(get_db)):
    return project_status(db, novel)
