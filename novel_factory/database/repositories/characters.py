"""인물 / 지식 / 관계 리포지토리 (기획안 22~24번)."""

from __future__ import annotations

from sqlalchemy import select

from novel_factory.database.models import Character, CharacterKnowledge, Relationship
from novel_factory.database.repositories.base import BaseRepository
from novel_factory.errors import NotFoundError


class CharacterRepository(BaseRepository[Character]):
    model = Character

    def for_novel(self, novel_id: int) -> list[Character]:
        return list(
            self.session.scalars(
                select(Character)
                .where(Character.novel_id == novel_id)
                .order_by(Character.code)
            )
        )

    def get_by_code(self, novel_id: int, code: str) -> Character | None:
        return self.session.scalar(
            select(Character).where(Character.novel_id == novel_id, Character.code == code)
        )

    def get_by_name(self, novel_id: int, name: str) -> Character | None:
        return self.session.scalar(
            select(Character).where(Character.novel_id == novel_id, Character.name == name)
        )

    def require_by_code(self, novel_id: int, code: str) -> Character:
        ch = self.get_by_code(novel_id, code)
        if ch is None:
            raise NotFoundError(f"인물 '{code}'을 찾지 못했습니다.")
        return ch

    def next_code(self, novel_id: int) -> str:
        existing = [c.code for c in self.for_novel(novel_id) if c.code.startswith("C")]
        numbers = [int(c[1:]) for c in existing if c[1:].isdigit()]
        return f"C{max(numbers, default=0) + 1:03d}"

    def alive_in(self, novel_id: int, episode_number: int) -> list[Character]:
        """해당 회차 시점에 살아 있고 이미 등장한 인물."""
        return [
            c
            for c in self.for_novel(novel_id)
            if (c.first_episode is None or c.first_episode <= episode_number)
            and (c.is_alive or (c.exit_episode or 10**9) > episode_number)
        ]


class KnowledgeRepository(BaseRepository[CharacterKnowledge]):
    """누가 무엇을 아는가 (기획안 23번)."""

    model = CharacterKnowledge

    def for_character(self, character_id: int) -> list[CharacterKnowledge]:
        return list(
            self.session.scalars(
                select(CharacterKnowledge).where(
                    CharacterKnowledge.character_id == character_id
                )
            )
        )

    def for_characters(self, character_ids: list[int]) -> list[CharacterKnowledge]:
        """여러 인물의 지식을 한 번에 (인물 순서는 호출한 쪽이 맞춘다)."""
        if not character_ids:
            return []
        return list(
            self.session.scalars(
                select(CharacterKnowledge)
                .where(CharacterKnowledge.character_id.in_(list(character_ids)))
                .order_by(CharacterKnowledge.id)
            )
        )

    def knows(
        self, character_id: int, fact_key: str, as_of_episode: int | None = None
    ) -> bool:
        """이 인물이 해당 사실을 (그 시점에) 아는가.

        기록이 없으면 모르는 것으로 본다. 모른다고 보는 쪽이 안전하다.
        틀리면 Logic Checker가 경고를 띄울 뿐이지만, 반대로 안다고 가정하면
        인물이 알 수 없는 정보를 말하는 원고가 그대로 통과한다.
        """
        row = self.session.scalar(
            select(CharacterKnowledge).where(
                CharacterKnowledge.character_id == character_id,
                CharacterKnowledge.fact_key == fact_key,
            )
        )
        if row is None or not row.knows:
            return False
        if as_of_episode is not None and row.learned_episode is not None:
            return row.learned_episode <= as_of_episode
        return True

    def set_knowledge(
        self,
        character_id: int,
        fact_key: str,
        fact: str,
        *,
        knows: bool = True,
        learned_episode: int | None = None,
        source: str = "",
    ) -> CharacterKnowledge:
        row = self.session.scalar(
            select(CharacterKnowledge).where(
                CharacterKnowledge.character_id == character_id,
                CharacterKnowledge.fact_key == fact_key,
            )
        )
        if row is None:
            row = CharacterKnowledge(
                character_id=character_id,
                fact_key=fact_key,
                fact=fact,
                knows=knows,
                learned_episode=learned_episode,
                source=source,
            )
            return self.add(row)
        row.fact = fact
        row.knows = knows
        if learned_episode is not None:
            row.learned_episode = learned_episode
        if source:
            row.source = source
        self.session.flush()
        return row


class RelationshipRepository(BaseRepository[Relationship]):
    """관계의 시점별 기록 (기획안 24번)."""

    model = Relationship

    def history(self, novel_id: int, a_id: int, b_id: int) -> list[Relationship]:
        return list(
            self.session.scalars(
                select(Relationship)
                .where(
                    Relationship.novel_id == novel_id,
                    Relationship.source_id == a_id,
                    Relationship.target_id == b_id,
                )
                .order_by(Relationship.from_episode)
            )
        )

    def state_at(
        self, novel_id: int, a_id: int, b_id: int, episode_number: int
    ) -> Relationship | None:
        """그 회차 시점에 유효한 관계 한 줄."""
        return self.session.scalar(
            select(Relationship)
            .where(
                Relationship.novel_id == novel_id,
                Relationship.source_id == a_id,
                Relationship.target_id == b_id,
                Relationship.from_episode <= episode_number,
            )
            .order_by(Relationship.from_episode.desc())
            .limit(1)
        )

    def states_at(
        self, novel_id: int, character_ids: list[int], episode_number: int
    ) -> dict[tuple[int, int], Relationship]:
        """여러 인물 사이의 관계를 한 번에. (source_id, target_id) → 그 시점의 한 줄.

        인물 쌍마다 state_at()을 부르면 인물이 50명일 때 쿼리가 2,000번을 넘는다.
        """
        ids = list(character_ids)
        if not ids:
            return {}
        rows = self.session.scalars(
            select(Relationship)
            .where(
                Relationship.novel_id == novel_id,
                Relationship.source_id.in_(ids),
                Relationship.target_id.in_(ids),
                Relationship.from_episode <= episode_number,
            )
            .order_by(Relationship.from_episode, Relationship.id)
        )
        latest: dict[tuple[int, int], Relationship] = {}
        for row in rows:  # 회차 순이라 뒤의 것이 이긴다
            latest[(row.source_id, row.target_id)] = row
        return latest

    def record(
        self,
        novel_id: int,
        a_id: int,
        b_id: int,
        *,
        from_episode: int,
        state: str,
        intensity: float = 0.0,
        note: str = "",
        symmetric: bool = True,
    ) -> list[Relationship]:
        """관계 변화를 기록한다.

        기본은 양방향이다. 한쪽만 마음이 바뀐 관계라면 symmetric=False로
        한 방향만 넣는다.
        """
        rows = [
            Relationship(
                novel_id=novel_id,
                source_id=a_id,
                target_id=b_id,
                from_episode=from_episode,
                state=state,
                intensity=intensity,
                note=note,
            )
        ]
        if symmetric:
            rows.append(
                Relationship(
                    novel_id=novel_id,
                    source_id=b_id,
                    target_id=a_id,
                    from_episode=from_episode,
                    state=state,
                    intensity=intensity,
                    note=note,
                )
            )
        for row in rows:
            self.session.add(row)
        self.session.flush()
        return rows
