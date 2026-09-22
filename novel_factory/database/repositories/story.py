"""세계관 / 시간선 / 복선 리포지토리 (기획안 25~27번)."""

from __future__ import annotations

from sqlalchemy import func, select

from novel_factory.database.models import Foreshadowing, TimelineEvent, WorldEntry
from novel_factory.database.repositories.base import BaseRepository

FORESHADOW_OPEN_STATES: tuple[str, ...] = ("OPEN", "DEVELOPING")


class WorldRepository(BaseRepository[WorldEntry]):
    model = WorldEntry

    def for_novel(self, novel_id: int, category: str | None = None) -> list[WorldEntry]:
        stmt = select(WorldEntry).where(WorldEntry.novel_id == novel_id)
        if category:
            stmt = stmt.where(WorldEntry.category == category)
        return list(
            self.session.scalars(stmt.order_by(WorldEntry.category, WorldEntry.name))
        )

    def get_by_name(self, novel_id: int, category: str, name: str) -> WorldEntry | None:
        return self.session.scalar(
            select(WorldEntry).where(
                WorldEntry.novel_id == novel_id,
                WorldEntry.category == category,
                WorldEntry.name == name,
            )
        )

    def upsert(
        self, novel_id: int, category: str, name: str, **fields: object
    ) -> WorldEntry:
        entry = self.get_by_name(novel_id, category, name)
        if entry is None:
            entry = WorldEntry(
                novel_id=novel_id,
                category=category,
                name=name,
                **fields,  # type: ignore[arg-type]
            )
            return self.add(entry)
        for key, value in fields.items():
            setattr(entry, key, value)
        self.session.flush()
        return entry

    def categories(self, novel_id: int) -> dict[str, int]:
        rows = self.session.execute(
            select(WorldEntry.category, func.count())
            .where(WorldEntry.novel_id == novel_id)
            .group_by(WorldEntry.category)
        )
        return dict(rows)


class TimelineRepository(BaseRepository[TimelineEvent]):
    model = TimelineEvent

    def for_novel(self, novel_id: int, limit: int = 500) -> list[TimelineEvent]:
        return list(
            self.session.scalars(
                select(TimelineEvent)
                .where(TimelineEvent.novel_id == novel_id)
                .order_by(TimelineEvent.sort_key, TimelineEvent.id)
                .limit(limit)
            )
        )

    def up_to_episode(self, novel_id: int, episode_number: int) -> list[TimelineEvent]:
        """그 회차까지 일어난 사건만.

        Writer에게 미래 사건을 보여 주면 인물이 아직 모르는 일을 말한다.
        """
        return list(
            self.session.scalars(
                select(TimelineEvent)
                .where(
                    TimelineEvent.novel_id == novel_id,
                    TimelineEvent.episode_number.is_not(None),
                    TimelineEvent.episode_number <= episode_number,
                )
                .order_by(TimelineEvent.sort_key, TimelineEvent.id)
            )
        )

    def next_sort_key(self, novel_id: int) -> float:
        value = self.session.scalar(
            select(func.max(TimelineEvent.sort_key)).where(
                TimelineEvent.novel_id == novel_id
            )
        )
        return float(value or 0.0) + 1.0


class ForeshadowingRepository(BaseRepository[Foreshadowing]):
    model = Foreshadowing

    def for_novel(self, novel_id: int, status: str | None = None) -> list[Foreshadowing]:
        stmt = select(Foreshadowing).where(Foreshadowing.novel_id == novel_id)
        if status:
            stmt = stmt.where(Foreshadowing.status == status)
        return list(self.session.scalars(stmt.order_by(Foreshadowing.setup_episode)))

    def get_by_code(self, novel_id: int, code: str) -> Foreshadowing | None:
        return self.session.scalar(
            select(Foreshadowing).where(
                Foreshadowing.novel_id == novel_id, Foreshadowing.code == code
            )
        )

    def next_code(self, novel_id: int) -> str:
        codes = [f.code for f in self.for_novel(novel_id) if f.code.startswith("F")]
        numbers = [int(c[1:]) for c in codes if c[1:].isdigit()]
        return f"F{max(numbers, default=0) + 1:03d}"

    def open_items(self, novel_id: int) -> list[Foreshadowing]:
        """아직 회수되지 않은 복선. 완결 검사(기획안 47번)의 핵심 질의."""
        return list(
            self.session.scalars(
                select(Foreshadowing)
                .where(
                    Foreshadowing.novel_id == novel_id,
                    Foreshadowing.status.in_(FORESHADOW_OPEN_STATES),
                )
                .order_by(Foreshadowing.setup_episode)
            )
        )

    def due_by(self, novel_id: int, episode_number: int) -> list[Foreshadowing]:
        """회수 예정 회차가 지났는데 아직 안 걷힌 복선."""
        return [
            f
            for f in self.open_items(novel_id)
            if f.planned_payoff is not None and f.planned_payoff <= episode_number
        ]

    def mark_mention(self, item: Foreshadowing, episode_number: int) -> Foreshadowing:
        mentions = list(item.mentions or [])
        if episode_number not in mentions:
            mentions.append(episode_number)
            item.mentions = sorted(mentions)
        if item.status == "OPEN":
            item.status = "DEVELOPING"
        self.session.flush()
        return item

    def resolve(self, item: Foreshadowing, episode_number: int) -> Foreshadowing:
        item.actual_payoff = episode_number
        item.status = "RESOLVED"
        self.session.flush()
        return item
