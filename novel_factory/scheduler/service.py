"""서버 내장 스케줄러 (기획안 42번 "매일 03:00").

API 서버가 뜰 때 스레드 하나를 띄워 NF_SCHEDULER_TIME(기본 03:00)마다
jobs.run_all()을 부른다. 서버가 꺼져 있던 시각의 실행은 건너뛴다(밀린 실행을
몰아서 하지 않는다).

시계를 한 번에 길게 기다리지 않고 최대 60초씩 나눠 기다린다. 서버 시계가 바뀌거나
절전에서 깨어났을 때도 제시각 근처에 돈다.

수동 실행(run_now)은 별도 스레드에서 돈다. 이미 도는 중이면 새로 시작하지 않는다.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, time, timedelta, tzinfo
from zoneinfo import ZoneInfo

from novel_factory.config import Settings, get_settings
from novel_factory.scheduler import jobs

logger = logging.getLogger("novel_factory.scheduler")

#: 한 번에 기다리는 최대 시간(초)
TICK_SECONDS = 60.0


def _zone(settings: Settings) -> tzinfo:
    if settings.scheduler_timezone:
        return ZoneInfo(settings.scheduler_timezone)
    local = datetime.now().astimezone().tzinfo
    assert local is not None
    return local


def next_run_after(now: datetime, at: str) -> datetime:
    """now 이후 처음 오는 HH:MM. now는 시간대가 있는 시각이어야 한다."""
    hour, minute = (int(x) for x in at.split(":"))
    candidate = datetime.combine(now.date(), time(hour, minute), tzinfo=now.tzinfo)
    if candidate <= now:
        candidate = datetime.combine(
            now.date() + timedelta(days=1), time(hour, minute), tzinfo=now.tzinfo
        )
    return candidate


class SchedulerService:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._run_lock = threading.Lock()
        self.next_run_at: datetime | None = None
        self.last_run_at: str = ""
        self.last_results: list[dict[str, object]] = []
        self.running = False

    # ------------------------------------------------------------------
    def now(self) -> datetime:
        return datetime.now(_zone(self.settings))

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self.next_run_at = next_run_after(self.now(), self.settings.scheduler_time)
        self._thread = threading.Thread(
            target=self._loop, name="novel-factory-scheduler", daemon=True
        )
        self._thread.start()
        logger.info("자동 집필 스케줄러 시작. 다음 실행: %s", self.next_run_at.isoformat())

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)
        self._thread = None
        self.next_run_at = None

    @property
    def active(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _loop(self) -> None:
        while not self._stop.is_set():
            now = self.now()
            due = self.next_run_at
            if due is None:
                due = self.next_run_at = next_run_after(now, self.settings.scheduler_time)
            if now >= due:
                self._run(scheduled_date=due.date().isoformat())
                self.next_run_at = next_run_after(self.now(), self.settings.scheduler_time)
                continue
            self._stop.wait(min((due - now).total_seconds(), TICK_SECONDS))

    # ------------------------------------------------------------------
    def _run(
        self, *, scheduled_date: str | None = None, novel_id: int | None = None
    ) -> bool:
        if not self._run_lock.acquire(blocking=False):
            return False
        self.running = True
        try:
            self.last_run_at = self.now().isoformat(timespec="seconds")
            if novel_id is None:
                results = jobs.run_all(
                    settings=self.settings, scheduled_date=scheduled_date
                )
            else:
                results = [
                    jobs.run_novel(novel_id, settings=self.settings, require_enabled=False)
                ]
            self.last_results = [r.as_dict() for r in results]
        except Exception:
            logger.exception("자동 집필 실행 실패")
        finally:
            self.running = False
            self._run_lock.release()
        return True

    def run_now(self, *, novel_id: int | None = None, wait: bool = False) -> bool:
        """지금 실행한다. 이미 도는 중이면 False.

        wait=False면 백그라운드 스레드에서 돌리고 바로 돌아온다.
        """
        if self.running:
            return False
        if wait:
            return self._run(novel_id=novel_id)
        threading.Thread(
            target=self._run,
            kwargs={"novel_id": novel_id},
            name="novel-factory-run-now",
            daemon=True,
        ).start()
        return True

    def status(self) -> dict[str, object]:
        return {
            "enabled": self.settings.scheduler_enabled,
            "active": self.active,
            "time": self.settings.scheduler_time,
            "timezone": self.settings.scheduler_timezone or "서버 로컬 시간",
            "next_run_at": self.next_run_at.isoformat() if self.next_run_at else None,
            "running": self.running,
            "last_run_at": self.last_run_at or None,
            "last_results": self.last_results,
        }


_service: SchedulerService | None = None


def get_scheduler() -> SchedulerService:
    global _service
    if _service is None:
        _service = SchedulerService()
    return _service


def reset_scheduler() -> None:
    """테스트용. 설정이 바뀌면 새로 만든다."""
    global _service
    if _service is not None:
        _service.stop()
    _service = None
