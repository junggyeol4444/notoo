"""장기기억 DB 테스트 (기획안 21~28번)."""

from __future__ import annotations

import pytest

from novel_factory.database import get_vector_index
from novel_factory.database.models import (
    Arc,
    Character,
    Episode,
    Foreshadowing,
    Novel,
    TimelineEvent,
)
from novel_factory.database.repositories import (
    ArcRepository,
    CharacterRepository,
    EpisodeRepository,
    ForeshadowingRepository,
    KnowledgeRepository,
    NovelRepository,
    PatternRepository,
    RelationshipRepository,
    StyleBibleRepository,
    TimelineRepository,
    WorldRepository,
)
from novel_factory.errors import NotFoundError
from novel_factory.memory import build_context
from novel_factory.reference.pattern import ReferencePattern


@pytest.fixture
def novel(db) -> Novel:
    return NovelRepository(db).add(
        Novel(
            slug="hoegwi",
            title="회귀한 인수합병가",
            genre="현대판타지",
            logline="죽은 인수합병가가 회귀해 다시 판을 짠다.",
            mood="건조하고 빠른",
            main_conflict="정보 우위 vs 기존 질서",
            planned_episodes=250,
            target_chars_per_episode=5000,
        )
    )


@pytest.fixture
def cast(db, novel) -> dict[str, Character]:
    repo = CharacterRepository(db)
    return {
        "도윤": repo.add(
            Character(
                novel_id=novel.id,
                code="C001",
                name="김도윤",
                role="주인공",
                job="기업 인수 전문가",
                first_episode=1,
            )
        ),
        "서연": repo.add(
            Character(
                novel_id=novel.id,
                code="C002",
                name="박서연",
                role="주요조연",
                first_episode=15,
            )
        ),
        "태호": repo.add(
            Character(
                novel_id=novel.id,
                code="C003",
                name="강태호",
                role="적대자",
                first_episode=90,
            )
        ),
    }


class TestNovelRepository:
    def test_lookup_by_slug(self, db, novel) -> None:
        assert NovelRepository(db).require_by_slug("hoegwi").id == novel.id

    def test_missing_slug_raises(self, db) -> None:
        with pytest.raises(NotFoundError):
            NovelRepository(db).require_by_slug("없는작품")


class TestCharacterKnowledge:
    def test_unknown_facts_default_to_not_known(self, db, cast) -> None:
        """기록이 없으면 '모른다'로 본다. 반대로 가정하면 오류가 조용히 통과한다."""
        repo = KnowledgeRepository(db)
        assert repo.knows(cast["서연"].id, "regression") is False

    def test_knowledge_respects_learning_episode(self, db, cast) -> None:
        repo = KnowledgeRepository(db)
        repo.set_knowledge(
            cast["서연"].id, "plot", "대성그룹의 매각 계획", learned_episode=80
        )
        assert repo.knows(cast["서연"].id, "plot", as_of_episode=50) is False
        assert repo.knows(cast["서연"].id, "plot", as_of_episode=90) is True

    def test_upsert_overwrites(self, db, cast) -> None:
        repo = KnowledgeRepository(db)
        repo.set_knowledge(cast["도윤"].id, "f", "사실", knows=False)
        repo.set_knowledge(cast["도윤"].id, "f", "사실", knows=True, learned_episode=3)
        assert len(repo.for_character(cast["도윤"].id)) == 1
        assert repo.knows(cast["도윤"].id, "f", as_of_episode=5) is True


class TestRelationshipHistory:
    def test_state_at_returns_the_state_in_force(self, db, novel, cast) -> None:
        repo = RelationshipRepository(db)
        a, b = cast["도윤"].id, cast["서연"].id
        repo.record(novel.id, a, b, from_episode=15, state="경계", intensity=-0.3)
        repo.record(novel.id, a, b, from_episode=60, state="협력", intensity=0.2)
        repo.record(novel.id, a, b, from_episode=110, state="신뢰", intensity=0.7)

        assert repo.state_at(novel.id, a, b, 20).state == "경계"
        assert repo.state_at(novel.id, a, b, 70).state == "협력"
        assert repo.state_at(novel.id, a, b, 200).state == "신뢰"

    def test_before_first_record_is_none(self, db, novel, cast) -> None:
        repo = RelationshipRepository(db)
        a, b = cast["도윤"].id, cast["서연"].id
        repo.record(novel.id, a, b, from_episode=15, state="경계")
        assert repo.state_at(novel.id, a, b, 5) is None

    def test_symmetric_records_both_directions(self, db, novel, cast) -> None:
        repo = RelationshipRepository(db)
        a, b = cast["도윤"].id, cast["서연"].id
        repo.record(novel.id, a, b, from_episode=1, state="경계", symmetric=True)
        assert repo.state_at(novel.id, b, a, 1) is not None


class TestForeshadowingLifecycle:
    def test_status_transitions(self, db, novel) -> None:
        repo = ForeshadowingRepository(db)
        item = repo.add(
            Foreshadowing(
                novel_id=novel.id,
                code="F001",
                description="회장의 검은 수첩",
                setup_episode=12,
                planned_payoff=87,
            )
        )
        assert item.status == "OPEN"
        repo.mark_mention(item, 31)
        assert item.status == "DEVELOPING"
        repo.resolve(item, 87)
        assert item.status == "RESOLVED"
        assert repo.open_items(novel.id) == []

    def test_overdue_detection(self, db, novel) -> None:
        repo = ForeshadowingRepository(db)
        repo.add(
            Foreshadowing(
                novel_id=novel.id,
                code="F001",
                description="가까운 복선",
                setup_episode=12,
                planned_payoff=50,
            )
        )
        repo.add(
            Foreshadowing(
                novel_id=novel.id,
                code="F002",
                description="먼 복선",
                setup_episode=20,
                planned_payoff=200,
            )
        )
        assert [f.code for f in repo.due_by(novel.id, 60)] == ["F001"]

    def test_next_code_increments(self, db, novel) -> None:
        repo = ForeshadowingRepository(db)
        assert repo.next_code(novel.id) == "F001"
        repo.add(
            Foreshadowing(novel_id=novel.id, code="F001", description="x", setup_episode=1)
        )
        assert repo.next_code(novel.id) == "F002"


class TestTimeline:
    def test_future_events_are_hidden(self, db, novel) -> None:
        """Writer에게 미래 사건을 주면 인물이 아직 모르는 일을 말한다."""
        repo = TimelineRepository(db)
        for i, (title, episode) in enumerate(
            [("회귀", 1), ("첫 투자", 3), ("대성 반격", 95)]
        ):
            repo.add(
                TimelineEvent(
                    novel_id=novel.id,
                    title=title,
                    episode_number=episode,
                    sort_key=float(i),
                )
            )
        visible = [e.title for e in repo.up_to_episode(novel.id, 10)]
        assert visible == ["회귀", "첫 투자"]


class TestVectorSearch:
    def test_respects_episode_cutoff(self, db, novel) -> None:
        index = get_vector_index()
        index.add(db, novel.id, "15화 장면", episode_number=15, embedding=[1.0, 0.0])
        index.add(db, novel.id, "30화 장면", episode_number=30, embedding=[1.0, 0.0])
        hits = index.search(db, novel.id, [1.0, 0.0], max_episode=20)
        assert [h.content for h in hits] == ["15화 장면"]

    def test_chunks_without_embedding_are_skipped(self, db, novel) -> None:
        index = get_vector_index()
        index.add(db, novel.id, "임베딩 없음", episode_number=1)
        assert index.search(db, novel.id, [1.0, 0.0]) == []


class TestWriterContext:
    @pytest.fixture
    def populated(self, db, novel, cast):
        RelationshipRepository(db).record(
            novel.id,
            cast["도윤"].id,
            cast["서연"].id,
            from_episode=15,
            state="경계",
            intensity=-0.3,
        )
        RelationshipRepository(db).record(
            novel.id,
            cast["도윤"].id,
            cast["서연"].id,
            from_episode=40,
            state="협력",
            intensity=0.3,
        )
        KnowledgeRepository(db).set_knowledge(
            cast["서연"].id, "regression", "김도윤이 회귀했다는 사실", knows=False
        )
        WorldRepository(db).upsert(
            novel.id, "회사", "세광전자", description="첫 인수 대상", first_episode=20
        )
        WorldRepository(db).upsert(
            novel.id, "회사", "대성그룹", description="적대 세력", first_episode=85
        )
        TimelineRepository(db).add(
            TimelineEvent(novel_id=novel.id, title="회귀", episode_number=1, sort_key=0.0)
        )
        TimelineRepository(db).add(
            TimelineEvent(
                novel_id=novel.id, title="대성 반격", episode_number=95, sort_key=1.0
            )
        )
        fs = ForeshadowingRepository(db)
        fs.add(
            Foreshadowing(
                novel_id=novel.id,
                code="F001",
                description="검은 수첩",
                setup_episode=12,
                planned_payoff=55,
            )
        )
        fs.add(
            Foreshadowing(
                novel_id=novel.id,
                code="F002",
                description="서연의 아버지",
                setup_episode=20,
                planned_payoff=200,
            )
        )
        fs.add(
            Foreshadowing(
                novel_id=novel.id,
                code="F003",
                description="아직 설치 전",
                setup_episode=100,
                planned_payoff=150,
            )
        )
        ArcRepository(db).add(
            Arc(
                novel_id=novel.id,
                order=2,
                name="세광전자 인수전",
                start_episode=31,
                end_episode=70,
                goal="첫 대형 인수 성사",
            )
        )
        StyleBibleRepository(db).upsert(
            novel.id, target_sentence_chars=18.0, forbidden=["과도한 비유"]
        )
        PatternRepository(db).upsert(
            ReferencePattern(
                pattern_id="PATTERN_001",
                genre="현대판타지",
                aspect="event_interval",
                instruction="대형 사건을 20~30화마다 배치한다.",
                confidence=0.8,
            )
        )
        for number in (38, 39, 40, 41):
            EpisodeRepository(db).add(
                Episode(
                    novel_id=novel.id,
                    number=number,
                    title=f"{number}화",
                    summary=f"{number}화 줄거리",
                    status="final",
                )
            )
        return novel

    def test_excludes_characters_not_yet_introduced(self, db, populated) -> None:
        ctx = build_context(db, populated, 42)
        names = {c["name"] for c in ctx.characters}
        assert names == {"김도윤", "박서연"}

    def test_excludes_future_world_and_timeline(self, db, populated) -> None:
        ctx = build_context(db, populated, 42)
        assert [w["name"] for w in ctx.world] == ["세광전자"]
        assert [t["title"] for t in ctx.timeline] == ["회귀"]

    def test_only_relevant_foreshadowings(self, db, populated) -> None:
        """아직 설치 전이거나 한참 뒤에 회수할 복선은 빠진다."""
        ctx = build_context(db, populated, 42)
        assert [f["code"] for f in ctx.open_foreshadowings] == ["F001"]

    def test_relationship_state_is_current(self, db, populated) -> None:
        ctx = build_context(db, populated, 42)
        assert all(r["state"] == "협력" for r in ctx.relationships)

    def test_knowledge_limits_are_inverted(self, db, populated) -> None:
        ctx = build_context(db, populated, 42)
        facts = {k["fact"] for k in ctx.knowledge_limits}
        assert "김도윤이 회귀했다는 사실" in facts

    def test_arc_is_selected_by_episode(self, db, populated) -> None:
        assert build_context(db, populated, 42).arc["name"] == "세광전자 인수전"
        assert build_context(db, populated, 5).arc == {}

    def test_prompt_puts_bible_before_patterns(self, db, populated) -> None:
        """기획안 21번: Novel Bible이 Reference Profile을 이긴다."""
        prompt = build_context(db, populated, 42).to_prompt()
        assert prompt.index("Novel Bible") < prompt.find("참고 패턴")

    def test_prompt_has_no_reference_source_text(self, db, populated) -> None:
        prompt = build_context(db, populated, 42).to_prompt()
        assert "참고소설 원문" not in prompt
        assert "42화" in prompt
