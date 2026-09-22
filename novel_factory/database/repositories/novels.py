"""작품 / Arc / 회차 / Style Bible 리포지토리."""

from __future__ import annotations

from sqlalchemy import func, select

from novel_factory.database.models import Arc, Episode, Novel, StyleBible
from novel_factory.database.repositories.base import BaseRepository
from novel_factory.errors import NotFoundError


class NovelRepository(BaseRepository[Novel]):
    model = Novel

    def get_by_slug(self, slug: str) -> Novel | None:
        return self.session.scalar(select(Novel).where(Novel.slug == slug))

    def require_by_slug(self, slug: str) -> Novel:
        novel = self.get_by_slug(slug)
        if novel is None:
            raise NotFoundError(f"작품 '{slug}'을 찾지 못했습니다.")
        return novel

    def list_by_status(self, status: str | None = None, limit: int = 100) -> list[Novel]:
        stmt = select(Novel).order_by(Novel.created_at.desc()).limit(limit)
        if status:
            stmt = stmt.where(Novel.status == status)
        return list(self.session.scalars(stmt))


class ArcRepository(BaseRepository[Arc]):
    model = Arc

    def for_novel(self, novel_id: int) -> list[Arc]:
        return list(
            self.session.scalars(
                select(Arc).where(Arc.novel_id == novel_id).order_by(Arc.order)
            )
        )

    def containing_episode(self, novel_id: int, episode_number: int) -> Arc | None:
        return self.session.scalar(
            select(Arc)
            .where(
                Arc.novel_id == novel_id,
                Arc.start_episode <= episode_number,
                Arc.end_episode >= episode_number,
            )
            .order_by(Arc.order)
        )


class EpisodeRepository(BaseRepository[Episode]):
    model = Episode

    def get_by_number(self, novel_id: int, number: int) -> Episode | None:
        return self.session.scalar(
            select(Episode).where(Episode.novel_id == novel_id, Episode.number == number)
        )

    def require_by_number(self, novel_id: int, number: int) -> Episode:
        ep = self.get_by_number(novel_id, number)
        if ep is None:
            raise NotFoundError(f"{number}화를 찾지 못했습니다.")
        return ep

    def last_number(self, novel_id: int) -> int:
        value = self.session.scalar(
            select(func.max(Episode.number)).where(Episode.novel_id == novel_id)
        )
        return int(value or 0)

    def recent(
        self, novel_id: int, count: int = 5, before: int | None = None
    ) -> list[Episode]:
        """최근 회차를 번호 오름차순으로 돌려준다.

        Writer에게 "최근 회차 요약"을 넘길 때 쓴다 (기획안 31번).
        """
        stmt = select(Episode).where(Episode.novel_id == novel_id)
        if before is not None:
            stmt = stmt.where(Episode.number < before)
        stmt = stmt.order_by(Episode.number.desc()).limit(count)
        return sorted(self.session.scalars(stmt), key=lambda e: e.number)

    def by_status(self, novel_id: int, status: str) -> list[Episode]:
        return list(
            self.session.scalars(
                select(Episode)
                .where(Episode.novel_id == novel_id, Episode.status == status)
                .order_by(Episode.number)
            )
        )


class StyleBibleRepository(BaseRepository[StyleBible]):
    model = StyleBible

    def for_novel(self, novel_id: int) -> StyleBible | None:
        return self.session.scalar(
            select(StyleBible).where(StyleBible.novel_id == novel_id)
        )

    def upsert(self, novel_id: int, **fields: object) -> StyleBible:
        bible = self.for_novel(novel_id)
        if bible is None:
            bible = StyleBible(novel_id=novel_id, **fields)  # type: ignore[arg-type]
            return self.add(bible)
        for key, value in fields.items():
            setattr(bible, key, value)
        self.session.flush()
        return bible
