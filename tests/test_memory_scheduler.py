"""장편 기억 검색(기획안 41번)과 자동 집필 스케줄러(기획안 42번) 테스트.

의미 검색은 가짜 임베더(tests/fake_llm.py FakeEmbedder)로 확인한다. 가짜 임베더는
'협약'과 '계약'을 같은 개념으로 보기 때문에, 어휘 검색으로는 못 찾고 의미 검색으로만
찾는 장면을 만들 수 있다. 그래서 어느 경로를 탔는지 결과로 가를 수 있다.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fake_llm import FakeEmbedder, ScriptedNovelist
from fake_openai_server import build_app, serve
from sqlalchemy import select

from novel_factory.database.base import get_session_factory
from novel_factory.database.models import Character, Episode, MemoryChunk, Novel
from novel_factory.database.repositories import (
    CharacterRepository,
    EpisodeRepository,
    NovelRepository,
    StyleBibleRepository,
)
from novel_factory.errors import LLMError, NovelFactoryError
from novel_factory.generation.budget import fit_context
from novel_factory.generation.pipeline import generate_episode
from novel_factory.llm.embeddings import OpenAICompatEmbedder, get_embedder
from novel_factory.memory import WriterContext
from novel_factory.memory.retrieval import (
    bm25_scores,
    chunk_text,
    index_episode,
    plan_query,
    reindex_novel,
    search_memory,
    terms,
)
from novel_factory.scheduler import jobs
from novel_factory.scheduler.service import SchedulerService, next_run_after


@pytest.fixture
def novel(db) -> Novel:
    n = NovelRepository(db).add(
        Novel(
            slug="hoegwi",
            title="회귀한 인수합병가",
            genre="현대판타지",
            logline="죽은 인수합병가가 회귀해 다시 판을 짠다.",
            planned_episodes=250,
            target_chars_per_episode=3000,
        )
    )
    chars = CharacterRepository(db)
    chars.add(
        Character(novel_id=n.id, code="C001", name="김도윤", role="주인공", first_episode=1)
    )
    chars.add(
        Character(
            novel_id=n.id, code="C002", name="박서연", role="주요조연", first_episode=1
        )
    )
    db.flush()
    return n


def _episode(db, novel: Novel, number: int, scenes: list[str]) -> Episode:
    ep = Episode(
        novel_id=novel.id,
        number=number,
        title=f"{number}화",
        status="final",
        scenes=[{"index": i, "text": t} for i, t in enumerate(scenes)],
        final_text="\n\n".join(scenes),
    )
    return EpisodeRepository(db).add(ep)


# ---------------------------------------------------------------------------
# 어휘 검색 부품
# ---------------------------------------------------------------------------
class TestLexical:
    def test_chunk_text_respects_paragraphs(self) -> None:
        text = "\n\n".join(["가" * 300, "나" * 300, "다" * 300])
        chunks = chunk_text(text, 700)
        assert chunks == ["가" * 300 + "\n" + "나" * 300, "다" * 300]
        # 한 문단이 한도보다 길면 그 문단만 따로 둔다
        assert chunk_text("라" * 1000, 700) == ["라" * 1000]

    def test_terms_share_stem_across_particles(self) -> None:
        assert "도윤" in terms("도윤은") and "도윤" in terms("도윤이")
        # 어절 경계를 넘는 2-gram은 없다
        assert "은서" not in terms("도윤은 서류")

    def test_bm25_ranks_matching_document_first(self) -> None:
        docs = ["비가 내리는 거리", "도윤은 계약서를 검토했다", "커피가 식었다"]
        scores = bm25_scores("계약서 검토", docs)
        assert scores.index(max(scores)) == 1
        assert scores[0] == 0.0 and scores[2] == 0.0

    def test_excerpt_centers_on_match(self) -> None:
        from novel_factory.memory.retrieval import excerpt

        paragraphs = [f"평범한 문단 {i}번이다." + "잡담" * 20 for i in range(10)]
        paragraphs[7] = "도윤은 실사 자료를 확보했다."
        text = "\n".join(paragraphs)
        out = excerpt(text, "실사 자료", 200)
        assert "실사 자료" in out and out.startswith("…")
        assert len(out.replace("…", "")) <= 200
        # 겹치는 어휘가 없으면 앞부분
        assert excerpt(text, "협약", 200).startswith("평범한 문단 0번")
        assert excerpt("짧다", "무엇", 200) == "짧다"

    def test_plan_query(self) -> None:
        q = plan_query(
            {
                "purpose": "첫 인수 대상을 좁힌다",
                "required_events": ["실사 자료 확보"],
                "hook": {"type": "반전", "content": "봉투 속 이름"},
                "title": "제목은 넣지 않는다",
            }
        )
        assert "실사 자료 확보" in q and "봉투 속 이름" in q and "제목" not in q


# ---------------------------------------------------------------------------
# 색인과 검색
# ---------------------------------------------------------------------------
class TestSearch:
    def test_index_and_future_block(self, db, novel, settings) -> None:
        for n in (1, 2, 3):
            ep = _episode(
                db, novel, n, [f"{n}화에서 도윤은 계약서를 검토했다.", "비가 왔다."]
            )
            index_episode(db, novel, ep, settings=settings)
        hits = search_memory(db, novel, "계약서 검토", before_episode=3, settings=settings)
        assert hits.method == "lexical"
        assert {h.episode_number for h in hits.hits} == {1, 2}  # 3화 이후는 안 본다
        assert (
            search_memory(db, novel, "계약서", before_episode=1, settings=settings).hits
            == []
        )

    def test_reindex_replaces_old_chunks(self, db, novel, settings) -> None:
        ep = _episode(db, novel, 1, ["도윤은 계약서를 검토했다."])
        index_episode(db, novel, ep, settings=settings)
        index_episode(db, novel, ep, settings=settings)
        chunks = db.scalars(select(MemoryChunk).where(MemoryChunk.kind == "scene")).all()
        assert len(chunks) == 1
        assert chunks[0].meta["scene_index"] == 0 and chunks[0].embedding is None

    def test_embedding_finds_synonym_lexical_does_not(
        self, db, novel, settings, monkeypatch
    ) -> None:
        embedder = FakeEmbedder()
        ep = _episode(db, novel, 1, ["도윤은 계약 조건을 다시 읽었다.", "비가 그쳤다."])
        index_episode(db, novel, ep, settings=settings, embedder=embedder)
        chunk = db.scalars(select(MemoryChunk)).first()
        assert chunk.embedding and chunk.meta["embed_model"] == "fake-embed"

        semantic = search_memory(
            db, novel, "협약", before_episode=2, settings=settings, embedder=embedder
        )
        assert semantic.method == "embedding"
        assert semantic.hits[0].scene_index == 0

        monkeypatch.setattr(settings, "memory_search", "lexical")
        lexical = search_memory(
            db, novel, "협약", before_episode=2, settings=settings, embedder=embedder
        )
        assert lexical.method == "lexical" and lexical.hits == []

    def test_other_model_falls_back_and_reindex_fixes(self, db, novel, settings) -> None:
        ep = _episode(db, novel, 1, ["도윤은 계약 조건을 다시 읽었다."])
        index_episode(db, novel, ep, settings=settings)  # 임베더 없이 색인
        embedder = FakeEmbedder()
        before = search_memory(
            db, novel, "계약 조건", before_episode=2, settings=settings, embedder=embedder
        )
        assert before.method == "lexical" and before.unembedded == 1
        assert any("reindex" in w for w in before.warnings)

        report = reindex_novel(db, novel, settings=settings, embedder=embedder)
        assert report.episodes == 1 and report.embedded == report.chunks == 1
        after = search_memory(
            db, novel, "협약", before_episode=2, settings=settings, embedder=embedder
        )
        assert after.method == "embedding" and after.hits

    def test_embedding_only_mode_without_model(
        self, db, novel, settings, monkeypatch
    ) -> None:
        ep = _episode(db, novel, 1, ["도윤은 계약 조건을 다시 읽었다."])
        index_episode(db, novel, ep, settings=settings)
        monkeypatch.setattr(settings, "memory_search", "embedding")
        result = search_memory(db, novel, "계약", before_episode=2, settings=settings)
        assert result.method == "none" and result.hits == []
        assert any("NF_EMBEDDING_MODEL" in w for w in result.warnings)

    def test_off(self, db, novel, settings, monkeypatch) -> None:
        ep = _episode(db, novel, 1, ["도윤은 계약 조건을 다시 읽었다."])
        index_episode(db, novel, ep, settings=settings)
        monkeypatch.setattr(settings, "memory_search", "off")
        assert (
            search_memory(db, novel, "계약", before_episode=2, settings=settings).hits == []
        )

    def test_failed_embedding_still_stores_chunk(self, db, novel, settings) -> None:
        class Broken(FakeEmbedder):
            def embed(self, texts):
                raise LLMError("서버 없음")

        ep = _episode(db, novel, 1, ["도윤은 계약 조건을 다시 읽었다."])
        warnings = index_episode(db, novel, ep, settings=settings, embedder=Broken())
        assert warnings and "임베딩 실패" in warnings[0]
        chunk = db.scalars(select(MemoryChunk)).one()
        assert chunk.embedding is None and chunk.meta["embed_model"] == ""


class TestWriterContext:
    def test_related_scene_reaches_writer(self, db, novel, settings) -> None:
        planted = "도윤은 실사 자료를 확보하려고 상대의 견제를 정면으로 뚫었다."
        llm = ScriptedNovelist(plant=planted)
        generate_episode(db, novel, 1, llm, settings=settings)
        first_calls = len(llm.calls)
        generate_episode(db, novel, 2, llm, settings=settings)
        prompts = [
            msgs[1].content
            for stage, msgs, _ in llm.calls[first_calls:]
            if stage == "writer"
        ]
        assert "관련된 과거 장면" in prompts[0]
        assert planted in prompts[0]  # 1화 원고 발췌
        # 1화를 쓸 때는 과거가 없다
        first = [
            msgs[1].content
            for stage, msgs, _ in llm.calls[:first_calls]
            if stage == "writer"
        ]
        assert "관련된 과거 장면" not in first[0]

    def test_budget_trims_related_scenes_first(self) -> None:
        ctx = WriterContext(
            episode_number=10,
            recent_summaries=[{"number": i, "summary": "요약"} for i in range(5)],
            related_scenes=[
                {"episode_number": i, "title": "", "excerpt": "발췌 " * 200}
                for i in range(4)
            ],
        )
        fitted, report = fit_context(ctx, 300, tokens_per_char=1.0)
        assert len(fitted.related_scenes) == 1
        assert report.trimmed[0].startswith("과거 장면")


# ---------------------------------------------------------------------------
# 임베딩 HTTP 클라이언트
# ---------------------------------------------------------------------------
class TestEmbedderHttp:
    def test_round_trip_through_server(self) -> None:
        fake = FakeEmbedder()
        app = build_app(ScriptedNovelist(), embedder=fake)
        with serve(app) as base_url:
            client = OpenAICompatEmbedder(base_url, "fake-embed", batch_size=2)
            texts = ["계약", "비가 왔다", "협약", "커피"]
            vectors = client.embed(texts)
            client.close()
        # 서버는 순서를 뒤집어 보낸다. 클라이언트가 index로 바로잡아야 한다.
        assert vectors == fake.embed(texts)
        assert vectors[0] == vectors[2]  # 계약 = 협약
        assert [len(r["input"]) for r in app.state.fake["requests"]] == [2, 2]

    def test_server_without_model(self) -> None:
        with serve(build_app(ScriptedNovelist())) as base_url:
            client = OpenAICompatEmbedder(base_url, "none", max_retries=0)
            with pytest.raises(LLMError):
                client.embed(["계약"])
            client.close()

    def test_get_embedder_needs_model(self, settings, monkeypatch) -> None:
        assert get_embedder(settings) is None
        monkeypatch.setattr(settings, "llm_base_url", "http://127.0.0.1:1/v1")
        monkeypatch.setattr(settings, "embedding_model", "e5")
        embedder = get_embedder(settings)
        assert embedder is not None and embedder.base_url == "http://127.0.0.1:1/v1"

    def test_pipeline_uses_http_embeddings(self, db, novel, settings, monkeypatch) -> None:
        app = build_app(ScriptedNovelist(), embedder=FakeEmbedder())
        with serve(app) as base_url:
            monkeypatch.setattr(settings, "embedding_base_url", base_url)
            monkeypatch.setattr(settings, "embedding_model", "fake-embed")
            generate_episode(db, novel, 1, ScriptedNovelist(), settings=settings)
        chunks = db.scalars(select(MemoryChunk).where(MemoryChunk.kind == "scene")).all()
        assert chunks and all(c.embedding for c in chunks)
        assert {c.meta["embed_model"] for c in chunks} == {"fake-embed"}


# ---------------------------------------------------------------------------
# 스케줄러
# ---------------------------------------------------------------------------
def _fresh(novel_id: int) -> tuple[Novel, list[Episode]]:
    """스케줄러가 따로 연 세션으로 쓴 결과를 새 세션으로 읽는다."""
    with get_session_factory()() as s:
        novel = s.get(Novel, novel_id)
        episodes = s.scalars(
            select(Episode).where(Episode.novel_id == novel_id).order_by(Episode.number)
        ).all()
        s.expunge_all()
        return novel, list(episodes)


class TestNextRun:
    def test_later_today_or_tomorrow(self) -> None:
        tz = timezone(timedelta(hours=9))
        now = datetime(2026, 9, 24, 2, 30, tzinfo=tz)
        assert next_run_after(now, "03:00") == datetime(2026, 9, 24, 3, 0, tzinfo=tz)
        now = datetime(2026, 9, 24, 3, 0, tzinfo=tz)
        assert next_run_after(now, "03:00") == datetime(2026, 9, 25, 3, 0, tzinfo=tz)


class TestJobs:
    def test_disabled_is_skipped_unless_manual(self, db, novel, settings) -> None:
        db.commit()
        llm = ScriptedNovelist()
        result = jobs.run_novel(novel.id, provider=llm, settings=settings)
        assert result.skipped and not result.written
        manual = jobs.run_novel(
            novel.id, provider=llm, settings=settings, require_enabled=False
        )
        assert manual.written == [1]

    def test_writes_per_run_and_records(self, db, novel, settings) -> None:
        jobs.update_schedule(novel, enabled=True, episodes_per_run=2)
        db.commit()
        result = jobs.run_novel(novel.id, provider=ScriptedNovelist(), settings=settings)
        assert result.written == [1, 2] and not result.error
        again = jobs.run_novel(novel.id, provider=ScriptedNovelist(), settings=settings)
        assert again.written == [3, 4]
        fresh, episodes = _fresh(novel.id)
        assert [e.status for e in episodes] == ["final"] * 4
        schedule = jobs.get_schedule(fresh)
        assert len(schedule["runs"]) == 2 and schedule["last_run"]["written"] == [3, 4]

    def test_story_is_planned_first(self, db, novel, settings) -> None:
        from novel_factory.database.repositories import ArcRepository

        jobs.update_schedule(novel, enabled=True)
        db.commit()
        jobs.run_novel(novel.id, provider=ScriptedNovelist(), settings=settings)
        with get_session_factory()() as s:
            assert ArcRepository(s).for_novel(novel.id)

    def test_quality_fail_holds_and_pauses(self, db, novel, settings) -> None:
        StyleBibleRepository(db).upsert(novel.id, forbidden=["피식 웃었다"])
        jobs.update_schedule(novel, enabled=True, episodes_per_run=3)
        db.commit()
        # 고쳐 써도 금지 표현이 계속 나오게 한다
        llm = ScriptedNovelist(
            plant="도윤은 피식 웃었다.", fix_text="그는 피식 웃었다. " * 120
        )
        result = jobs.run_novel(novel.id, provider=llm, settings=settings)
        assert result.written == [] and result.held == 1
        assert "품질 검사 FAIL" in result.paused
        assert llm.stage_counts["memory"] == 0  # 기억 갱신을 하지 않았다

        fresh, episodes = _fresh(novel.id)
        assert episodes[0].status == "held" and not episodes[0].summary
        assert jobs.get_schedule(fresh)["paused"]

        # 멈춘 동안은 쓰지 않는다
        skipped = jobs.run_novel(novel.id, provider=ScriptedNovelist(), settings=settings)
        assert skipped.skipped.startswith("멈춤")
        # 검토 대기 회차가 있으면 재개할 수 없다
        with get_session_factory()() as s:
            n = s.get(Novel, novel.id)
            with pytest.raises(NovelFactoryError, match="held"):
                jobs.resume(s, n)
            # 사람이 확정하면 재개할 수 있다
            s.get(Episode, episodes[0].id).status = "final"
            jobs.resume(s, n)
            s.commit()
        after = jobs.run_novel(novel.id, provider=ScriptedNovelist(), settings=settings)
        assert after.written == [2, 3, 4]

    def test_same_scheduled_date_runs_once(self, db, novel, settings) -> None:
        jobs.update_schedule(novel, enabled=True)
        db.commit()
        llm = ScriptedNovelist()
        first = jobs.run_novel(
            novel.id, provider=llm, settings=settings, scheduled_date="2026-09-24"
        )
        second = jobs.run_novel(
            novel.id, provider=llm, settings=settings, scheduled_date="2026-09-24"
        )
        assert first.written == [1] and "이미" in second.skipped
        third = jobs.run_novel(
            novel.id, provider=llm, settings=settings, scheduled_date="2026-09-25"
        )
        assert third.written == [2]

    def test_stops_at_planned_episodes(self, db, novel, settings) -> None:
        novel.planned_episodes = 2
        jobs.update_schedule(novel, enabled=True, episodes_per_run=5)
        db.commit()
        result = jobs.run_novel(novel.id, provider=ScriptedNovelist(), settings=settings)
        assert result.written == [1, 2] and "목표 2화" in result.paused

    def test_llm_down_is_not_a_pause(self, db, novel, settings) -> None:
        from novel_factory.llm import NullProvider

        jobs.update_schedule(novel, enabled=True)
        db.commit()
        result = jobs.run_novel(novel.id, provider=NullProvider(), settings=settings)
        assert "LLM" in result.skipped
        fresh, _ = _fresh(novel.id)
        assert not jobs.get_schedule(fresh)["paused"]

    def test_error_rolls_back_episode_and_keeps_schedule(
        self, db, novel, settings, monkeypatch
    ) -> None:
        jobs.update_schedule(novel, enabled=True, episodes_per_run=3)
        db.commit()
        llm = ScriptedNovelist()
        calls = {"n": 0}
        real = llm._memory

        def flaky(user, messages, is_retry):
            calls["n"] += 1
            if calls["n"] == 2:
                raise RuntimeError("서버가 죽었다")
            return real(user, messages, is_retry)

        monkeypatch.setattr(llm, "_memory", flaky)
        result = jobs.run_novel(novel.id, provider=llm, settings=settings)
        assert result.written == [1] and "RuntimeError" in result.error
        fresh, episodes = _fresh(novel.id)
        assert [e.number for e in episodes] == [1]  # 2화는 롤백됐다
        assert not jobs.get_schedule(fresh)["paused"]

    def test_run_all_only_enabled(self, db, novel, settings) -> None:
        other = NovelRepository(db).add(
            Novel(slug="other", title="다른 작품", genre="현대판타지", planned_episodes=10)
        )
        jobs.update_schedule(novel, enabled=True)
        db.commit()
        results = jobs.run_all(provider=ScriptedNovelist(), settings=settings)
        assert [r.slug for r in results] == ["hoegwi"]
        assert other.id not in jobs.scheduled_novel_ids(settings)


class TestService:
    def test_loop_runs_when_due(self, settings, monkeypatch) -> None:
        """실제 루프가 예정 시각에 run_all을 부르고 다음 실행을 내일로 넘긴다."""
        import threading

        from novel_factory.scheduler import service as service_module

        ran = threading.Event()
        seen: dict[str, object] = {}

        def fake_run_all(*, settings=None, scheduled_date=None, provider=None):
            seen["date"] = scheduled_date
            ran.set()
            return []

        real_next = service_module.next_run_after
        first = {"done": False}

        def soon_then_real(now, at):
            if not first["done"]:
                first["done"] = True
                return now + timedelta(milliseconds=200)
            return real_next(now, at)

        monkeypatch.setattr(jobs, "run_all", fake_run_all)
        monkeypatch.setattr(service_module, "next_run_after", soon_then_real)
        monkeypatch.setattr(service_module, "TICK_SECONDS", 0.05)
        service = SchedulerService(settings)
        service.start()
        try:
            assert ran.wait(5)
        finally:
            service.stop()
        # 예약 실행은 그 회차 날짜를 넘긴다 (같은 날 두 번 쓰지 않게)
        assert seen["date"] == service.now().date().isoformat()
        assert service.last_run_at

    def test_run_now_rejects_overlap(self, settings, monkeypatch) -> None:
        import threading

        gate = threading.Event()
        monkeypatch.setattr(jobs, "run_all", lambda **_: gate.wait(5) and [])
        service = SchedulerService(settings)
        assert service.run_now() is True
        deadline = datetime.now() + timedelta(seconds=5)
        while not service.running and datetime.now() < deadline:
            pass
        assert service.run_now() is False
        gate.set()


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------
class TestApi:
    def _make(self, client) -> None:
        r = client.post(
            "/novels",
            json={"slug": "api", "title": "자동", "genre": "현대판타지", "episodes": 10},
        )
        assert r.status_code in (200, 201), r.text

    def test_schedule_endpoints(self, client, monkeypatch) -> None:
        self._make(client)
        body = client.get("/novels/api/schedule").json()
        assert body["enabled"] is False and body["next_episode"] == 1

        r = client.put(
            "/novels/api/schedule", json={"enabled": True, "episodes_per_run": 2}
        )
        assert r.status_code == 200 and r.json()["episodes_per_run"] == 2
        assert (
            client.put("/novels/api/schedule", json={"episodes_per_run": 0}).status_code
            == 422
        )

        monkeypatch.setattr(jobs, "get_provider", lambda cfg=None: ScriptedNovelist())
        r = client.post("/novels/api/schedule/run?wait=true")
        assert r.status_code == 200, r.text
        assert r.json()["last_results"][0]["written"] == [1, 2]
        body = client.get("/novels/api/schedule").json()
        assert body["next_episode"] == 3 and body["runs"][-1]["written"] == [1, 2]

        status = client.get("/scheduler").json()
        assert status["enabled"] is False and status["time"] == "03:00"

    def test_resume_blocked_by_held(self, client) -> None:
        self._make(client)
        with get_session_factory()() as s:
            n = NovelRepository(s).require_by_slug("api")
            s.add(Episode(novel_id=n.id, number=1, title="1화", status="held"))
            jobs.pause(n, "1화 품질 검사 FAIL")
            s.commit()
        r = client.post("/novels/api/schedule/resume")
        assert r.status_code == 409 and "held" in r.json()["detail"]

    def test_memory_search_and_reindex(self, client) -> None:
        self._make(client)
        with get_session_factory()() as s:
            n = NovelRepository(s).require_by_slug("api")
            ep = _episode(s, n, 1, ["도윤은 계약서를 검토했다.", "비가 왔다."])
            index_episode(s, n, ep)
            s.commit()
        r = client.get("/novels/api/memory/search", params={"q": "계약서"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["method"] == "lexical" and body["hits"][0]["episode_number"] == 1
        assert (
            client.get(
                "/novels/api/memory/search", params={"q": "계약서", "before": 1}
            ).json()["hits"]
            == []
        )
        r = client.post("/novels/api/memory/reindex")
        assert r.json()["episodes"] == 1 and r.json()["embedded"] == 0


class TestPgVector:
    """PostgreSQL + pgvector에서만 돈다 (NF_TEST_DATABASE_URL, NF_TEST_VECTOR_BACKEND)."""

    def test_vectors_go_to_pgvector_and_db_ranks(self, db, novel, settings, monkeypatch):
        from sqlalchemy import text

        from novel_factory.database import vector as vector_module
        from novel_factory.database.vector import PG_VECTOR_TABLE, pgvector_active

        if not pgvector_active(db, settings):
            pytest.skip("PostgreSQL + pgvector가 아니다")
        embedder = FakeEmbedder()
        ep1 = _episode(db, novel, 1, ["도윤은 계약 조건을 다시 읽었다.", "비가 그쳤다."])
        ep2 = _episode(db, novel, 2, ["서연은 커피를 마셨다."])
        index_episode(db, novel, ep1, settings=settings, embedder=embedder)
        index_episode(db, novel, ep2, settings=settings, embedder=embedder)
        count = db.execute(text(f"SELECT count(*) FROM {PG_VECTOR_TABLE}")).scalar_one()
        assert count == 3

        calls = []
        real = vector_module.search_vectors

        def spy(*args, **kwargs):
            calls.append(kwargs)
            return real(*args, **kwargs)

        monkeypatch.setattr("novel_factory.memory.retrieval.search_vectors", spy)
        result = search_memory(
            db, novel, "협약", before_episode=3, settings=settings, embedder=embedder
        )
        assert calls and result.method == "embedding"
        assert (result.hits[0].episode_number, result.hits[0].scene_index) == (1, 0)
        # 다시 색인하면 벡터 행도 갈아 끼워진다 (조각이 지워지면 함께 지워진다)
        index_episode(db, novel, ep1, settings=settings, embedder=embedder)
        assert db.execute(text(f"SELECT count(*) FROM {PG_VECTOR_TABLE}")).scalar_one() == 3
