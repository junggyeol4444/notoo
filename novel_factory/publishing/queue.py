"""Publishing Queue (기획안 42번 마지막 단계).

    회차 확정 → (publishing_mode가 automatic이면) 대기열에 넣는다
    스케줄러 실행 → 새로 찬 전자책 권을 만든다 → 대기열을 처리한다

manual 모드에서는 자동으로 넣지 않는다. API로 직접 넣고 처리한다.

같은 회차를 같은 대상에 두 번 넣지 않는다. 다시 보내려면 republish=True.
실패하면 publish_max_attempts번까지 다음 처리 때 다시 시도하고, 넘기면 failed로 둔다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from novel_factory.config import Settings, get_settings
from novel_factory.database.models import Episode, Novel, Publication
from novel_factory.errors import NovelFactoryError
from novel_factory.llm.base import LLMProvider
from novel_factory.publishing.adapters import PublishError, get_adapter
from novel_factory.publishing.cover import cover_dir, make_cover
from novel_factory.publishing.ebook import build_ebooks, ebook_dir
from novel_factory.publishing.info import get_info

logger = logging.getLogger("novel_factory.publishing")

ACTIVE = ("queued", "exported", "published")


def _existing(
    session: Session,
    novel: Novel,
    kind: str,
    target: str,
    *,
    number: int | None,
    volume: int | None,
) -> Publication | None:
    stmt = select(Publication).where(
        Publication.novel_id == novel.id,
        Publication.kind == kind,
        Publication.target == target,
        Publication.status.in_(ACTIVE),
    )
    if number is not None:
        stmt = stmt.where(Publication.episode_number == number)
    if volume is not None:
        stmt = stmt.where(Publication.volume == volume)
    return session.scalars(stmt).first()


def enqueue_episode(
    session: Session,
    novel: Novel,
    number: int,
    *,
    targets: list[str] | None = None,
    republish: bool = False,
) -> list[Publication]:
    episode = session.scalar(
        select(Episode).where(Episode.novel_id == novel.id, Episode.number == number)
    )
    if episode is None or episode.status != "final":
        raise NovelFactoryError(f"{number}화가 확정(final)되지 않아 출판할 수 없습니다.")
    added = []
    for target in targets or get_info(novel).targets:
        if not republish and _existing(
            session, novel, "episode", target, number=number, volume=None
        ):
            continue
        row = Publication(
            novel_id=novel.id, kind="episode", episode_number=number, target=target
        )
        session.add(row)
        added.append(row)
    session.flush()
    return added


def enqueue_ebook(
    session: Session,
    novel: Novel,
    volume: int,
    *,
    targets: list[str] | None = None,
    republish: bool = False,
) -> list[Publication]:
    folder = ebook_dir(novel) / f"vol{volume:02d}"
    if not (folder / "book.epub").exists():
        raise NovelFactoryError(f"{volume}권 전자책이 없습니다. 먼저 만드세요.")
    added = []
    for target in targets or get_info(novel).targets:
        if not republish and _existing(
            session, novel, "ebook", target, number=None, volume=volume
        ):
            continue
        row = Publication(novel_id=novel.id, kind="ebook", volume=volume, target=target)
        session.add(row)
        added.append(row)
    session.flush()
    return added


def on_episode_final(session: Session, novel: Novel, number: int) -> list[Publication]:
    """회차가 확정됐을 때 부른다. automatic 모드면 대기열에 넣는다."""
    if novel.publishing_mode != "automatic":
        return []
    return enqueue_episode(session, novel, number)


@dataclass(slots=True)
class QueueReport:
    processed: int = 0
    succeeded: list[int] = field(default_factory=list)
    retrying: list[int] = field(default_factory=list)
    failed: list[int] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "processed": self.processed,
            "succeeded": self.succeeded,
            "retrying": self.retrying,
            "failed": self.failed,
            "errors": self.errors,
        }


def _thumbnail(session: Session, novel: Novel, settings: Settings):
    path = cover_dir(novel, settings) / "thumbnail.jpg"
    if not path.exists():
        # 표지가 아직 없으면 그림 없는 글자 표지를 만든다. 표지 API로 다시 만들 수 있다.
        make_cover(session, novel, None, settings=settings, use_image=False)
    return path


def process_queue(
    session: Session,
    *,
    settings: Settings | None = None,
    novel_id: int | None = None,
    limit: int | None = None,
) -> QueueReport:
    cfg = settings or get_settings()
    report = QueueReport()
    stmt = (
        select(Publication).where(Publication.status == "queued").order_by(Publication.id)
    )
    if novel_id is not None:
        stmt = stmt.where(Publication.novel_id == novel_id)
    if limit:
        stmt = stmt.limit(limit)
    for row in session.scalars(stmt).all():
        report.processed += 1
        novel = session.get(Novel, row.novel_id)
        row.attempts += 1
        try:
            if novel is None:
                raise NovelFactoryError("작품이 없습니다.")
            adapter = get_adapter(row.target, cfg)
            info = get_info(novel)
            if row.kind == "episode":
                episode = session.scalar(
                    select(Episode).where(
                        Episode.novel_id == novel.id, Episode.number == row.episode_number
                    )
                )
                if episode is None or episode.status != "final":
                    raise NovelFactoryError(
                        f"{row.episode_number}화가 확정되지 않았습니다."
                    )
                outcome = adapter.publish_episode(
                    novel, episode, info, _thumbnail(session, novel, cfg)
                )
            else:
                folder = ebook_dir(novel, cfg) / f"vol{(row.volume or 0):02d}"
                outcome = adapter.publish_ebook(novel, row.volume or 0, folder, info)
        except (PublishError, NovelFactoryError, OSError) as exc:
            row.error = str(exc)
            if row.attempts >= cfg.publish_max_attempts or not isinstance(
                exc, PublishError
            ):
                # 설정 오류·파일 없음은 다시 해도 같다. 전송 실패만 다시 시도한다.
                row.status = "failed"
                report.failed.append(row.id)
            else:
                report.retrying.append(row.id)
            report.errors.append(f"#{row.id} {row.target}: {exc}")
            logger.warning("출판 실패 #%d %s: %s", row.id, row.target, exc)
            continue
        row.status = outcome.status
        row.location = outcome.location
        row.external_id = outcome.external_id
        row.error = ""
        row.published_at = datetime.now(UTC).replace(tzinfo=None)
        report.succeeded.append(row.id)
    session.flush()
    return report


def auto_publish(
    session: Session,
    novel: Novel,
    provider: LLMProvider | None,
    *,
    settings: Settings | None = None,
) -> dict[str, object]:
    """automatic 모드 작품의 출판 단계. 스케줄러가 회차를 쓴 뒤 부른다.

    새로 찬 전자책 권을 만들어 대기열에 넣고, 그 작품의 대기열을 처리한다.
    """
    cfg = settings or get_settings()
    if novel.publishing_mode != "automatic":
        return {"skipped": "publishing_mode가 automatic이 아님"}
    ebooks = build_ebooks(session, novel, provider, settings=cfg)
    for vol in ebooks.volumes:
        if vol.built and vol.volume:
            enqueue_ebook(session, novel, vol.volume)
    queue = process_queue(session, settings=cfg, novel_id=novel.id)
    return {"ebooks": ebooks.as_dict(), "queue": queue.as_dict()}
