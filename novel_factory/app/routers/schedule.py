"""자동 집필 스케줄러 API (기획안 42번).

    GET  /scheduler                       스케줄러 상태 (다음 실행 시각, 마지막 결과)
    POST /scheduler/run                   켜진 작품 전부 지금 실행
    GET  /novels/{slug}/schedule          작품별 설정과 상태
    PUT  /novels/{slug}/schedule          켜기/끄기, 한 번에 쓸 회차 수
    POST /novels/{slug}/schedule/resume   멈춤 풀기 (검토 대기 회차가 없어야 한다)
    POST /novels/{slug}/schedule/run      이 작품만 지금 실행 (꺼져 있어도 실행)

실행은 몇 분에서 몇 시간이 걸린다. 기본은 백그라운드로 돌리고 202를 돌려준다.
wait=true면 끝날 때까지 기다린다.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from novel_factory.app.deps import get_db, get_novel
from novel_factory.database.models import Novel
from novel_factory.scheduler import jobs
from novel_factory.scheduler.service import get_scheduler

router = APIRouter(tags=["scheduler"])


class ScheduleIn(BaseModel):
    enabled: bool | None = None
    episodes_per_run: int | None = Field(default=None, ge=1, le=jobs.MAX_EPISODES_PER_RUN)
    continuous: bool | None = None


def _started(started: bool, wait: bool) -> JSONResponse:
    service = get_scheduler()
    if not started:
        raise HTTPException(status.HTTP_409_CONFLICT, "이미 실행 중입니다.")
    code = status.HTTP_200_OK if wait else status.HTTP_202_ACCEPTED
    return JSONResponse(status_code=code, content=service.status())


@router.get("/scheduler")
def scheduler_status() -> dict[str, object]:
    return get_scheduler().status()


@router.post("/scheduler/run")
def scheduler_run(wait: bool = False) -> JSONResponse:
    return _started(get_scheduler().run_now(wait=wait), wait)


@router.get("/novels/{slug}/schedule")
def novel_schedule(
    novel: Novel = Depends(get_novel), db: Session = Depends(get_db)
) -> dict[str, object]:
    return {
        **jobs.novel_status(db, novel),
        "runs": jobs.get_schedule(novel).get("runs") or [],
    }


@router.put("/novels/{slug}/schedule")
def update_novel_schedule(
    body: ScheduleIn, novel: Novel = Depends(get_novel), db: Session = Depends(get_db)
) -> dict[str, object]:
    jobs.update_schedule(
        novel,
        enabled=body.enabled,
        episodes_per_run=body.episodes_per_run,
        continuous=body.continuous,
    )
    db.flush()
    return jobs.novel_status(db, novel)


@router.post("/novels/{slug}/schedule/resume")
def resume_novel_schedule(
    novel: Novel = Depends(get_novel), db: Session = Depends(get_db)
) -> dict[str, object]:
    held = jobs.held_episode(db, novel)
    if held is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"{held.number}화가 검토 대기(held) 중입니다. "
            f"POST /novels/{novel.slug}/episodes/{held.number}/memory로 확정하거나 "
            "generate?replace=true로 다시 만든 뒤 재개하세요.",
        )
    jobs.resume(db, novel)
    db.flush()
    return jobs.novel_status(db, novel)


@router.post("/novels/{slug}/schedule/run")
def run_novel_now(
    wait: bool = False, novel: Novel = Depends(get_novel), db: Session = Depends(get_db)
) -> JSONResponse:
    novel_id = novel.id
    # 요청 세션의 변경을 먼저 끝낸다. 실행은 자기 세션을 따로 연다.
    db.commit()
    return _started(get_scheduler().run_now(novel_id=novel_id, wait=wait), wait)
