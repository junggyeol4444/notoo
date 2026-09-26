"""자동 집필 작업 (기획안 42번).

    Episode Planner → Reference Pattern Retrieval → Context Retrieval → Scene Planning
    → Draft → Continuity / Logic / Similarity / Style Check → Rewrite → Final
    → Memory Update → Publishing Queue (publishing_mode가 automatic인 작품)

위 흐름은 generate_episode()가 한 회차씩 한다. 여기서는 어느 작품의 몇 화를 쓸지와
언제 멈출지만 정한다.

작품별 설정 (novel.extra["schedule"])
  enabled           스케줄러가 이 작품을 쓸지. 기본 꺼짐.
  episodes_per_run  한 번 돌 때 쓰는 회차 수.
  paused            멈춤. 사람이 확인하고 resume해야 다시 쓴다.
  pause_reason      왜 멈췄는지.

멈추는 경우 (paused=True)
  - 고친 뒤에도 품질 검사 FAIL이 남았다. 그 회차는 기억 갱신을 하지 않고
    status "held"로 남긴다. 사람이 확인해서 /memory로 확정하거나 다시 만든다.
  - 목표 회차(planned_episodes)까지 다 썼다. 이때 완결 검사(기획안 47번)를 돌려
    통과하면 작품을 completed로 바꾸고 자동 집필을 끈다. 실패하면 사유를 남긴다.
멈추지 않고 이번 실행만 끝내는 경우 (다음 실행에 다시 시도)
  - LLM 서버에 연결할 수 없다.
  - 생성 도중 예외가 났다. 그 회차는 롤백된다.

회차마다 커밋한다. 중간에 멈춰도 앞 회차는 남는다.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from novel_factory.config import Settings, get_settings
from novel_factory.database.base import get_session_factory
from novel_factory.database.models import Episode, Novel
from novel_factory.database.repositories import ArcRepository
from novel_factory.errors import NovelFactoryError
from novel_factory.generation.pipeline import generate_episode, plan_story
from novel_factory.llm import LLMProvider, get_provider
from novel_factory.publishing.queue import auto_publish
from novel_factory.quality.completion import check_completion

logger = logging.getLogger("novel_factory.scheduler")

DEFAULT_SCHEDULE: dict[str, object] = {
    "enabled": False,
    "episodes_per_run": 1,
    # True면 한 번 돌 때 멈출 이유(목표 회차, FAIL, 오류)가 생길 때까지 계속 쓴다
    "continuous": False,
    "paused": False,
    "pause_reason": "",
}
MAX_EPISODES_PER_RUN = 50
#: novel.extra["schedule"]["runs"]에 남기는 최근 실행 기록 수
RUN_HISTORY = 20

# 같은 작품을 두 스레드가 동시에 쓰지 않게 한다 (예약 실행과 수동 실행이 겹칠 때).
_running: set[int] = set()
_running_lock = threading.Lock()


def get_schedule(novel: Novel) -> dict[str, object]:
    return {**DEFAULT_SCHEDULE, **((novel.extra or {}).get("schedule") or {})}


def _save_schedule(novel: Novel, schedule: dict[str, object]) -> None:
    # JSON 컬럼은 안쪽 값이 바뀐 것을 모른다. 새 dict로 갈아 끼워 알린다.
    novel.extra = {**(novel.extra or {}), "schedule": schedule}


def update_schedule(
    novel: Novel,
    *,
    enabled: bool | None = None,
    episodes_per_run: int | None = None,
    continuous: bool | None = None,
) -> dict[str, object]:
    schedule = get_schedule(novel)
    if enabled is not None:
        schedule["enabled"] = enabled
    if continuous is not None:
        schedule["continuous"] = continuous
    if episodes_per_run is not None:
        if not 1 <= episodes_per_run <= MAX_EPISODES_PER_RUN:
            raise NovelFactoryError(
                f"episodes_per_run은 1~{MAX_EPISODES_PER_RUN} 사이여야 합니다."
            )
        schedule["episodes_per_run"] = episodes_per_run
    _save_schedule(novel, schedule)
    return schedule


def pause(novel: Novel, reason: str) -> None:
    schedule = get_schedule(novel)
    schedule["paused"] = True
    schedule["pause_reason"] = reason
    _save_schedule(novel, schedule)


def held_episode(session: Session, novel: Novel) -> Episode | None:
    return session.scalar(
        select(Episode)
        .where(Episode.novel_id == novel.id, Episode.status == "held")
        .order_by(Episode.number)
        .limit(1)
    )


def resume(session: Session, novel: Novel) -> dict[str, object]:
    """멈춤을 푼다. 검토 대기(held) 회차가 남아 있으면 풀지 않는다."""
    held = held_episode(session, novel)
    if held is not None:
        raise NovelFactoryError(
            f"{held.number}화가 검토 대기(held) 중입니다. /memory로 확정하거나 "
            "generate로 다시 만든 뒤 재개하세요."
        )
    schedule = get_schedule(novel)
    schedule["paused"] = False
    schedule["pause_reason"] = ""
    _save_schedule(novel, schedule)
    return schedule


def next_episode_number(session: Session, novel: Novel) -> int:
    last = session.scalar(
        select(func.max(Episode.number)).where(
            Episode.novel_id == novel.id, Episode.status == "final"
        )
    )
    return int(last or 0) + 1


@dataclass(slots=True)
class NovelRunResult:
    slug: str
    started_at: str
    finished_at: str = ""
    written: list[int] = field(default_factory=list)
    held: int | None = None
    skipped: str = ""  # 쓰지 않은 이유 (꺼짐, 멈춤, 오늘 이미 실행, LLM 없음)
    paused: str = ""  # 이번 실행에서 멈춘 이유
    error: str = ""
    warnings: list[str] = field(default_factory=list)
    publishing: dict[str, object] = field(default_factory=dict)
    completion: str = ""

    def as_dict(self) -> dict[str, object]:
        return {
            "completion": self.completion,
            "publishing": self.publishing,
            "slug": self.slug,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "written": self.written,
            "held": self.held,
            "skipped": self.skipped,
            "paused": self.paused,
            "error": self.error,
            "warnings": self.warnings,
        }


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _record(session: Session, novel_id: int, result: NovelRunResult) -> None:
    novel = session.get(Novel, novel_id)
    if novel is None:
        return
    schedule = get_schedule(novel)
    runs = list(schedule.get("runs") or [])  # type: ignore[call-overload]
    runs.append(result.as_dict())
    schedule["runs"] = runs[-RUN_HISTORY:]
    schedule["last_run"] = result.as_dict()
    _save_schedule(novel, schedule)


def run_novel(
    novel_id: int,
    *,
    provider: LLMProvider | None = None,
    settings: Settings | None = None,
    scheduled_date: str | None = None,
    require_enabled: bool = True,
) -> NovelRunResult:
    """한 작품을 episodes_per_run화만큼 쓴다.

    scheduled_date는 예약 실행일 때 그 날짜(YYYY-MM-DD). 같은 날짜로 이미 돌았으면
    건너뛴다. 수동 실행은 None으로 부르고, require_enabled=False면 꺼진 작품도 쓴다.
    멈춘(paused) 작품은 어느 경우에도 쓰지 않는다.
    """
    cfg = settings or get_settings()
    factory = get_session_factory(cfg)
    with _running_lock:
        if novel_id in _running:
            return NovelRunResult("", _now_iso(), skipped="이미 실행 중")
        _running.add(novel_id)
    try:
        return _run_novel(novel_id, factory, provider, cfg, scheduled_date, require_enabled)
    finally:
        with _running_lock:
            _running.discard(novel_id)


def _run_novel(
    novel_id: int,
    factory: Callable[[], Session],
    provider: LLMProvider | None,
    cfg: Settings,
    scheduled_date: str | None,
    require_enabled: bool,
) -> NovelRunResult:
    with factory() as session:
        novel = session.get(Novel, novel_id)
        if novel is None:
            return NovelRunResult("", _now_iso(), skipped="작품이 없음")
        result = NovelRunResult(novel.slug, _now_iso())
        schedule = get_schedule(novel)
        if require_enabled and not schedule["enabled"]:
            result.skipped = "자동 집필이 꺼져 있음"
            return result
        if schedule["paused"]:
            result.skipped = f"멈춤: {schedule['pause_reason']}"
            return result
        if scheduled_date and schedule.get("last_scheduled_date") == scheduled_date:
            result.skipped = f"{scheduled_date} 예약 실행은 이미 했음"
            return result
        if scheduled_date:
            # 먼저 기록하고 커밋한다. 다른 워커가 같은 날 또 쓰는 것을 줄인다.
            schedule["last_scheduled_date"] = scheduled_date
            _save_schedule(novel, schedule)
            session.commit()
        per_run = int(schedule["episodes_per_run"])  # type: ignore[call-overload]
        if schedule.get("continuous"):
            # 남은 회차 + 1 (마지막 한 번은 완결 검사로 끝난다)
            per_run = max(
                novel.planned_episodes - next_episode_number(session, novel) + 2, 1
            )

    llm = provider or get_provider(cfg)
    if not llm.available:
        result.skipped = "LLM 서버에 연결할 수 없음"
        _finish(factory, novel_id, result)
        return result

    try:
        with factory() as session, session.begin():
            novel = session.get(Novel, novel_id)
            assert novel is not None
            if not ArcRepository(session).for_novel(novel.id):
                arcs = plan_story(session, novel, llm, settings=cfg)
                result.warnings.extend(f"Arc 설계: {w}" for w in arcs.warnings)

        for _ in range(per_run):
            with factory() as session, session.begin():
                novel = session.get(Novel, novel_id)
                assert novel is not None
                held = held_episode(session, novel)
                if held is not None:
                    reason = f"{held.number}화가 검토 대기(held) 중"
                    pause(novel, reason)
                    result.paused = reason
                    break
                number = next_episode_number(session, novel)
                if number > novel.planned_episodes:
                    # 다 썼으면 완결 검사 (기획안 47번). 통과해야 completed가 된다.
                    completion = check_completion(session, novel, llm, settings=cfg)
                    result.completion = completion.report.summary()
                    if completion.completed:
                        reason = f"목표 {novel.planned_episodes}화까지 쓰고 완결 검사 통과"
                        update_schedule(novel, enabled=False)
                    else:
                        reason = (
                            f"목표 {novel.planned_episodes}화까지 썼으나 완결 검사 FAIL ("
                            + completion.report.summary().replace("\n", ", ")
                            + ")"
                        )
                    pause(novel, reason)
                    result.paused = reason
                    break
                started = time.perf_counter()
                generated = generate_episode(
                    session, novel, number, llm, settings=cfg, hold_on_fail=True
                )
                logger.info(
                    "%s %d화 %s (%.0f초)",
                    novel.slug,
                    number,
                    "보류" if generated.held else "확정",
                    time.perf_counter() - started,
                )
                result.warnings.extend(f"{number}화: {w}" for w in generated.warnings)
                if generated.held:
                    summary = str(generated.quality.get("summary") or "").replace(
                        "\n", ", "
                    )
                    reason = f"{number}화 품질 검사 FAIL ({summary})"
                    pause(novel, reason)
                    result.held = number
                    result.paused = reason
                    break
                result.written.append(number)
    except Exception as exc:  # 어떤 실패든 기록하고 다음 실행을 기다린다
        logger.exception("자동 집필 실패: %s", result.slug)
        result.error = f"{type(exc).__name__}: {exc}"

    # 출판 (Publishing Queue). 집필이 실패해도 앞서 확정된 회차는 내보낸다.
    try:
        with factory() as session, session.begin():
            novel = session.get(Novel, novel_id)
            assert novel is not None
            result.publishing = auto_publish(session, novel, llm, settings=cfg)
    except Exception as exc:  # 출판 실패가 집필 기록을 막지 않게 한다
        logger.exception("자동 출판 실패: %s", result.slug)
        result.warnings.append(f"출판 단계 실패: {type(exc).__name__}: {exc}")

    _finish(factory, novel_id, result)
    return result


def _finish(factory: Callable[[], Session], novel_id: int, result: NovelRunResult) -> None:
    result.finished_at = _now_iso()
    with factory() as session, session.begin():
        _record(session, novel_id, result)


def scheduled_novel_ids(settings: Settings | None = None) -> list[int]:
    """자동 집필이 켜진 작품. 멈춘 작품도 포함한다 (건너뛴 기록을 남기려고)."""
    factory = get_session_factory(settings or get_settings())
    with factory() as session:
        novels = session.scalars(select(Novel).order_by(Novel.id)).all()
        return [n.id for n in novels if get_schedule(n)["enabled"]]


def run_all(
    *,
    provider: LLMProvider | None = None,
    settings: Settings | None = None,
    scheduled_date: str | None = None,
) -> list[NovelRunResult]:
    """자동 집필이 켜진 작품을 차례로 쓴다. 한 작품이 실패해도 다음 작품은 쓴다."""
    cfg = settings or get_settings()
    return [
        run_novel(nid, provider=provider, settings=cfg, scheduled_date=scheduled_date)
        for nid in scheduled_novel_ids(cfg)
    ]


def novel_status(session: Session, novel: Novel) -> dict[str, object]:
    held = held_episode(session, novel)
    return {
        **{k: v for k, v in get_schedule(novel).items() if k != "runs"},
        "next_episode": next_episode_number(session, novel),
        "planned_episodes": novel.planned_episodes,
        "held_episode": held.number if held is not None else None,
        "final_episodes": int(
            session.scalar(
                select(func.count(Episode.id)).where(
                    Episode.novel_id == novel.id, Episode.status == "final"
                )
            )
            or 0
        ),
    }
