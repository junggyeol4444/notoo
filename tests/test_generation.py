"""Phase 3 집필 테스트.

실제 모델 대신 대본 LLM(tests/fake_llm.py)을 쓴다. 대본 LLM은 실제 로컬 모델의
버릇(<think> 블록, 코드펜스, 형식 틀린 첫 응답, 없는 코드 섞기)을 흉내 낸다.
마지막 부분은 진짜 HTTP 서버로 띄워 OpenAICompatProvider 경로까지 검증한다.
"""

from __future__ import annotations

import json
from itertools import pairwise
from pathlib import Path

import pytest
from fake_llm import ScriptedNovelist
from fake_openai_server import build_app, serve
from pydantic import BaseModel

from novel_factory.database.models import Character, Foreshadowing, Novel
from novel_factory.database.repositories import (
    ArcRepository,
    CharacterRepository,
    EpisodeRepository,
    ForeshadowingRepository,
    KnowledgeRepository,
    NovelRepository,
    RelationshipRepository,
    StyleBibleRepository,
    TimelineRepository,
)
from novel_factory.errors import NovelFactoryError
from novel_factory.generation.arc_planner import build_arc_skeleton
from novel_factory.generation.budget import fit_context
from novel_factory.generation.episode_planner import (
    EpisodeDirectives,
    compute_directives,
    sanitize_plan,
)
from novel_factory.generation.guidance import (
    GenreGuidance,
    HookDirective,
    build_event_schedule,
    choose_hook,
    load_guidance,
)
from novel_factory.generation.memory_update import apply_memory
from novel_factory.generation.pipeline import generate_episode, plan_story
from novel_factory.generation.scene_planner import allocate_scenes
from novel_factory.generation.schemas import EpisodePlanOut, MemoryDeltaOut
from novel_factory.generation.structured import (
    StructuredOutputError,
    extract_json,
    request_structured,
)
from novel_factory.generation.writer import clean_prose
from novel_factory.llm import EchoProvider, Message, OpenAICompatProvider
from novel_factory.memory import build_context
from novel_factory.quality.repetition import find_repetitions
from novel_factory.text.tokens import syllables

DEFAULTS = GenreGuidance(source="defaults")


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
    chars.add(
        Character(
            novel_id=n.id, code="C003", name="강태호", role="적대자", first_episode=90
        )
    )
    db.flush()
    return n


# ---------------------------------------------------------------------------
# 구조화 출력
# ---------------------------------------------------------------------------
class TestStructured:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ('```json\n{"a": 1}\n```', {"a": 1}),
            ('<think>{"x": 0}</think>\n{"a": 2}', {"a": 2}),
            ('{"a": 3}\n<think>잘린 생각', {"a": 3}),
            ('앞말 {"a": [1, 2,], } 뒷말', {"a": [1, 2]}),
            ('[참고] 결과는 {"a": 4}', {"a": 4}),
            ('{"line": "그는 {비밀}을 \\"말했다\\""}', {"line": '그는 {비밀}을 "말했다"'}),
        ],
    )
    def test_extract_json(self, raw: str, expected: object) -> None:
        assert extract_json(raw) == expected

    def test_extract_json_failure(self) -> None:
        with pytest.raises(ValueError):
            extract_json("JSON이 없는 답")

    def test_retry_carries_the_error(self) -> None:
        class Plan(BaseModel):
            title: str
            scenes: int

        llm = EchoProvider(['{"title": "x"}', '{"title": "첫 만남", "scenes": 4}'])
        value, result = request_structured(llm, [Message("user", "계획")], Plan)
        assert value.scenes == 4 and result.attempts == 2
        retry = llm.calls[1][-1].content
        assert "scenes" in retry  # 무엇이 틀렸는지 알려 준다

    def test_gives_up_after_retries(self) -> None:
        class Plan(BaseModel):
            title: str

        with pytest.raises(StructuredOutputError) as exc:
            request_structured(
                EchoProvider(["아니", "아니", "아니"]),
                [Message("user", "x")],
                Plan,
                retries=2,
            )
        assert len(exc.value.attempts) == 3

    def test_single_string_becomes_list(self) -> None:
        """로컬 모델은 목록 자리에 문자열 하나를 자주 준다."""
        plan = EpisodePlanOut.model_validate(
            {
                "title": "t",
                "purpose": "p",
                "required_events": "사건 하나",
                "emotion_flow": "긴장",
            }
        )
        assert plan.required_events == ["사건 하나"]
        assert plan.emotion_flow == ["긴장"]


# ---------------------------------------------------------------------------
# 결정적 계획
# ---------------------------------------------------------------------------
class TestSchedule:
    def test_major_intervals_stay_in_band(self) -> None:
        schedule = build_event_schedule(250, DEFAULTS, seed="x")
        gaps = [b - a for a, b in pairwise(schedule.major)][:-1]
        low, high = DEFAULTS.major_interval
        assert all(low <= g <= high for g in gaps), gaps

    def test_deterministic(self) -> None:
        a = build_event_schedule(250, DEFAULTS, seed="같은작품")
        b = build_event_schedule(250, DEFAULTS, seed="같은작품")
        assert a.major == b.major and a.minor == b.minor

    def test_minor_never_overlaps_major(self) -> None:
        s = build_event_schedule(250, DEFAULTS, seed="x")
        assert not set(s.major) & set(s.minor)

    @pytest.mark.parametrize("total", [250, 100, 30, 12, 1])
    def test_arc_skeleton_is_contiguous(self, total: int) -> None:
        skeleton = build_arc_skeleton(build_event_schedule(total, DEFAULTS, seed="x"))
        assert skeleton[0].start_episode == 1 and skeleton[-1].end_episode == total
        for a, b in pairwise(skeleton):
            assert a.end_episode + 1 == b.start_episode
        assert skeleton[-1].is_final


class TestHookChoice:
    @pytest.mark.parametrize("rate", [0.5, 0.82, 1.0])
    def test_rate_matches_target(self, rate: float) -> None:
        guide = GenreGuidance(source="defaults", cliffhanger_rate=rate)
        history: list[str] = []
        for _ in range(40):
            history.append(choose_hook(guide, history).kind or "없음")
        used = sum(1 for h in history if h != "없음")
        assert abs(used / 40 - rate) <= 0.03

    def test_second_episode_is_not_blocked(self) -> None:
        """1화에 훅을 썼다고 2화 훅을 막으면 안 된다 (비율 계산의 함정)."""
        assert choose_hook(DEFAULTS, ["정보공개"]).required

    def test_no_immediate_repeat(self) -> None:
        history: list[str] = []
        kinds = []
        for _ in range(30):
            h = choose_hook(DEFAULTS, history)
            history.append(h.kind or "없음")
            if h.kind:
                kinds.append(h.kind)
        assert all(a != b for a, b in pairwise(kinds))

    def test_last_episode_has_no_hook(self) -> None:
        assert not choose_hook(DEFAULTS, [], is_last_episode=True).required


class TestSceneAllocation:
    def test_first_is_intro_and_last_is_hook(self) -> None:
        slots = allocate_scenes(
            5000, DEFAULTS.episode_shape, min_chars=700, max_chars=1600, hook_required=True
        )
        assert slots[0].role == "도입" and slots[-1].role == "클리프행어"
        assert sum(s.target_chars for s in slots) == 5000

    def test_no_hook_ends_with_closing(self) -> None:
        slots = allocate_scenes(
            5000, DEFAULTS.episode_shape, min_chars=700, max_chars=1600, hook_required=False
        )
        assert slots[-1].role == "마무리"
        assert "클리프행어" not in [s.role for s in slots]

    def test_scene_sizes_within_range(self) -> None:
        for target in (2000, 5000, 9000, 15000):
            slots = allocate_scenes(
                target,
                DEFAULTS.episode_shape,
                min_chars=700,
                max_chars=1600,
                hook_required=True,
            )
            assert all(700 <= s.target_chars <= 1600 for s in slots), (target, slots)


class TestSanitizePlan:
    def _directives(self, **kw) -> EpisodeDirectives:
        base: dict[str, object] = {
            "episode_number": 10,
            "event_kind": "minor",
            "hook": HookDirective(True, "미스터리", "부족"),
            "target_chars": 3000,
            "cast_codes": ["C001", "C002"],
            "open_foreshadowing_codes": ["F001"],
        }
        base.update(kw)
        return EpisodeDirectives(**base)  # type: ignore[arg-type]

    def test_unknown_codes_removed_and_hook_forced(self) -> None:
        plan = EpisodePlanOut.model_validate(
            {
                "title": "t",
                "purpose": "p",
                "required_events": ["e"],
                "characters": ["C001", "C999"],
                "foreshadowings_used": ["F001", "F404"],
                "hook": {"type": "반전", "content": "c"},
                "new_foreshadowings": [{"description": "새 복선"}],
            }
        )
        data, warnings = sanitize_plan(
            plan, self._directives(), DEFAULTS, total_episodes=250
        )
        assert data["characters"] == ["C001"]
        assert data["foreshadowings_used"] == ["F001"]
        assert data["hook"]["type"] == "미스터리"
        assert data["new_foreshadowings"][0]["planned_payoff"] > 10
        assert len(warnings) == 3

    def test_hook_cleared_when_not_required(self) -> None:
        plan = EpisodePlanOut.model_validate(
            {
                "title": "t",
                "purpose": "p",
                "required_events": ["e"],
                "hook": {"type": "반전", "content": "c"},
            }
        )
        data, _ = sanitize_plan(
            plan,
            self._directives(hook=HookDirective(False, None, "쉼")),
            DEFAULTS,
            total_episodes=250,
        )
        assert data["hook"]["type"] == ""


class TestDirectives:
    def test_cast_excludes_not_yet_introduced(self, db, novel) -> None:
        d = compute_directives(db, novel, 10, DEFAULTS)
        assert d.cast_codes == ["C001", "C002"]  # C003은 90화 등장

    def test_due_foreshadowing(self, db, novel) -> None:
        fs = ForeshadowingRepository(db)
        fs.add(
            Foreshadowing(
                novel_id=novel.id,
                code="F001",
                description="수첩",
                setup_episode=2,
                planned_payoff=11,
            )
        )
        fs.add(
            Foreshadowing(
                novel_id=novel.id,
                code="F002",
                description="먼 복선",
                setup_episode=2,
                planned_payoff=80,
            )
        )
        d = compute_directives(db, novel, 10, DEFAULTS)
        assert [f["code"] for f in d.due_foreshadowings] == ["F001"]

    def test_last_episode_lists_every_open_foreshadowing(self, db, novel) -> None:
        ForeshadowingRepository(db).add(
            Foreshadowing(
                novel_id=novel.id,
                code="F001",
                description="x",
                setup_episode=5,
                planned_payoff=None,
            )
        )
        d = compute_directives(db, novel, 250, DEFAULTS)
        assert d.is_last_episode and [f["code"] for f in d.due_foreshadowings] == ["F001"]
        assert not d.hook.required

    def test_style_bible_phrases_are_avoided(self, db, novel) -> None:
        StyleBibleRepository(db).upsert(novel.id, throttled_phrases={"피식 웃었다": 5})
        assert "피식 웃었다" in compute_directives(db, novel, 3, DEFAULTS).avoid_phrases


class TestRepetition:
    def test_counts_narration_only(self) -> None:
        texts = [
            '도윤이 고개를 끄덕였다. "고개를 끄덕였다고요?"',
            "서연이 고개를 끄덕였다.",
            "민석이 고개를 끄덕였다. 태호도 고개를 끄덕였다.",
        ]
        found = {r.phrase: r.count for r in find_repetitions(texts, threshold=3)}
        assert found.get("고개를 끄덕였다") == 4

    def test_known_cliches(self) -> None:
        texts = ["그는 피식 웃었다.", "그녀도 피식 웃었다."]
        assert any(r.known_cliche for r in find_repetitions(texts))


class TestBudget:
    def test_trims_old_summaries_first(self, db, novel) -> None:
        from novel_factory.database.models import Episode

        for n in range(1, 8):
            EpisodeRepository(db).add(
                Episode(
                    novel_id=novel.id, number=n, summary="줄거리 " * 400, status="final"
                )
            )
        ctx = build_context(db, novel, 8, recent_count=6)
        full = len(ctx.to_prompt())
        fitted, report = fit_context(ctx, budget_tokens=full // 2, tokens_per_char=1.0)
        assert len(fitted.recent_summaries) < len(ctx.recent_summaries)
        assert len(fitted.recent_summaries) >= 2
        assert fitted.characters == ctx.characters  # 인물은 줄이지 않는다
        assert report.trimmed


class TestCleanProse:
    def test_strips_wrappers(self) -> None:
        raw = '<think>계획</think>\n## 장면 2\n\n```\n"뭐?" 그가 물었다.\n\n장면 전환이 빨랐다.\n```'
        assert clean_prose(raw) == '"뭐?" 그가 물었다.\n\n장면 전환이 빨랐다.'


# ---------------------------------------------------------------------------
# 기억 갱신 (LLM 없이)
# ---------------------------------------------------------------------------
class TestApplyMemory:
    def test_validates_before_writing(self, db, novel) -> None:
        from novel_factory.database.models import Episode

        ep = EpisodeRepository(db).add(
            Episode(novel_id=novel.id, number=5, final_text="본문. 그는 왜 거기 있었을까?")
        )
        ForeshadowingRepository(db).add(
            Foreshadowing(
                novel_id=novel.id, code="F001", description="수첩", setup_episode=1
            )
        )
        delta = MemoryDeltaOut.model_validate(
            {
                "summary": "요약",
                "new_characters": [
                    {"name": "박서연"},
                    {"name": "새인물"},
                    {"name": "서연"},
                ],
                "relationships": [
                    {"source": "C001", "target": "서연", "state": "협력"},
                    {"source": "C001", "target": "유령", "state": "적대"},
                ],
                "knowledge": [{"character": "도윤", "fact_key": "k", "fact": "비밀"}],
                "deaths": [{"character": "C002", "cause": "사고"}],
                "resolved_foreshadowings": ["F001", "F404"],
                "hook_type": "엉뚱한값",
            }
        )
        report = apply_memory(db, novel, ep, delta)

        names = [c.name for c in CharacterRepository(db).for_novel(novel.id)]
        assert names.count("박서연") == 1 and "새인물" in names  # 중복 인물 안 만듦
        assert len(report.relationships) == 1
        seoyeon = CharacterRepository(db).get_by_code(novel.id, "C002")
        assert seoyeon is not None and not seoyeon.is_alive and seoyeon.exit_episode == 5
        doyun = CharacterRepository(db).get_by_code(novel.id, "C001")
        assert KnowledgeRepository(db).knows(doyun.id, "k", as_of_episode=5)  # type: ignore[union-attr]
        assert report.resolved_foreshadowings == ["F001"]
        assert report.hook_type == "미스터리"  # 목록 밖 값은 원고 말미로 판정
        assert ep.status == "final" and ep.summary == "요약"
        assert len(report.rejected) >= 3


# ---------------------------------------------------------------------------
# 파이프라인 전체
# ---------------------------------------------------------------------------
class TestPipeline:
    def test_story_plan_without_llm(self, db, novel) -> None:
        result = plan_story(db, novel, None)
        assert not result.used_llm
        arcs = ArcRepository(db).for_novel(novel.id)
        assert arcs[0].name == "ARC 1" and arcs[-1].end_episode == 250
        assert (novel.extra or {}).get("event_schedule")

    def test_story_plan_with_messy_llm(self, db, novel) -> None:
        llm = ScriptedNovelist(messy=True)
        result = plan_story(db, novel, llm)
        assert result.used_llm and result.attempts == 2
        assert ArcRepository(db).for_novel(novel.id)[0].name == "제1막"

    def test_existing_arcs_are_kept(self, db, novel) -> None:
        plan_story(db, novel, None)
        again = plan_story(db, novel, ScriptedNovelist())
        assert again.warnings and not again.used_llm

    def test_three_episodes(self, db, novel, settings) -> None:
        llm = ScriptedNovelist(messy=True)
        plan_story(db, novel, llm)
        results = [
            generate_episode(db, novel, n, llm, settings=settings) for n in (1, 2, 3)
        ]

        episodes = EpisodeRepository(db).recent(novel.id, 10)
        assert [e.status for e in episodes] == ["final"] * 3
        for e in episodes:
            assert e.char_count >= 3000 * settings.writer_min_length_ratio
            assert e.summary
        # 기억이 실제로 쌓였다
        chars = CharacterRepository(db).for_novel(novel.id)
        assert {"조연1", "조연2", "조연3"} <= {c.name for c in chars}
        doyun, seoyeon = chars[0], chars[1]
        assert (
            RelationshipRepository(db).state_at(novel.id, doyun.id, seoyeon.id, 3).state
            == "협력"
        )
        assert len(TimelineRepository(db).for_novel(novel.id)) == 3
        assert (
            ForeshadowingRepository(db).get_by_code(novel.id, "F001").status == "DEVELOPING"
        )
        # 2화 컨텍스트에 1화 요약이 들어간다
        assert "1화" in build_context(db, novel, 2).to_prompt()
        # 잘못된 코드는 걸러지고 경고로 남는다
        assert any("C999" in w for w in results[0].warnings)
        # 기획안 40번 저장 구조
        files = sorted(p.name for p in results[2].folder.iterdir())
        assert files == sorted(
            [
                "outline.json",
                "scenes.json",
                "draft.md",
                "final.md",
                "summary.json",
                "memory_delta.json",
                "reference_usage.json",
                "similarity_report.json",
            ]
        )
        outline = json.loads(
            (results[2].folder / "outline.json").read_text(encoding="utf-8")
        )
        assert outline["directives"]["episode_number"] == 3

    def test_short_scene_is_continued(self, db, novel, settings) -> None:
        llm = ScriptedNovelist(short_first=True)
        result = generate_episode(db, novel, 1, llm, settings=settings)
        first_scene = result.episode.scenes[0]["draft"]
        assert first_scene["continuations"] >= 1
        assert (
            first_scene["chars"]
            >= first_scene["target_chars"] * settings.writer_min_length_ratio
        )

    def test_final_episode_is_protected(self, db, novel, settings) -> None:
        llm = ScriptedNovelist()
        generate_episode(db, novel, 1, llm, settings=settings)
        with pytest.raises(NovelFactoryError, match="이미 확정"):
            generate_episode(db, novel, 1, llm, settings=settings)

    def test_regeneration_rolls_back_memory(self, db, novel, settings) -> None:
        llm = ScriptedNovelist()
        generate_episode(db, novel, 1, llm, settings=settings)
        counts = (
            len(CharacterRepository(db).for_novel(novel.id)),
            len(TimelineRepository(db).for_novel(novel.id)),
            len(ForeshadowingRepository(db).for_novel(novel.id)),
        )
        generate_episode(db, novel, 1, llm, settings=settings, replace=True)
        again = (
            len(CharacterRepository(db).for_novel(novel.id)),
            len(TimelineRepository(db).for_novel(novel.id)),
            len(ForeshadowingRepository(db).for_novel(novel.id)),
        )
        assert again == counts  # 중복으로 쌓이지 않는다

    def test_cannot_regenerate_before_a_final_episode(self, db, novel, settings) -> None:
        llm = ScriptedNovelist()
        generate_episode(db, novel, 1, llm, settings=settings)
        generate_episode(db, novel, 2, llm, settings=settings)
        with pytest.raises(NovelFactoryError, match="2화가 이미 확정"):
            generate_episode(db, novel, 1, llm, settings=settings, replace=True)

    def test_similarity_rewrites_copied_scene(
        self, db, novel, settings, sample_analysis
    ) -> None:
        """참고작 문장을 그대로 쓴 장면은 다시 쓴다 (기획안 20·38번)."""
        from novel_factory.database.models import ReferenceLink, ReferenceNovel
        from novel_factory.database.repositories import ReferenceRepository

        repo = ReferenceRepository(db)
        ref = repo.add(ReferenceNovel(reference_id="REF_TEST", title="참고"))
        repo.save_profile(ref, sample_analysis.profile.as_dict(), episode_count=31)
        repo.save_fingerprints(ref, sample_analysis.metrics)
        db.add(ReferenceLink(novel_id=novel.id, reference_id=ref.id, weights={}))
        db.flush()

        copied = "\n\n".join(e.text for e in sample_analysis.episodes[3:9])
        llm = ScriptedNovelist(copy_text=copied)
        result = generate_episode(db, novel, 1, llm, settings=settings)
        first = result.episode.scenes[0]["draft"]
        assert first["rewrites"] >= 1
        assert first["similarity"]["verdict"] != "FAIL"
        assert result.episode.quality_reports["similarity"]["verdict"] != "FAIL"
        assert load_guidance(db, novel).source == "references"

    def test_unavailable_llm_is_refused(self, db, novel, settings) -> None:
        from novel_factory.llm import NullProvider

        with pytest.raises(NovelFactoryError, match="LLM에 연결할 수 없습니다"):
            generate_episode(db, novel, 1, NullProvider(), settings=settings)


# ---------------------------------------------------------------------------
# 진짜 HTTP로
# ---------------------------------------------------------------------------
class TestOverHttp:
    def test_pipeline_through_openai_compatible_server(self, db, novel, settings) -> None:
        novelist = ScriptedNovelist(messy=True)
        app = build_app(novelist, fail_first=1)  # 첫 요청 503 → 재시도
        with serve(app) as base_url:
            provider = OpenAICompatProvider(
                base_url, "fake-novelist", max_retries=2, timeout=30
            )
            assert provider.available
            plan_story(db, novel, provider)
            result = generate_episode(db, novel, 1, provider, settings=settings)
            provider.close()

        assert result.episode.status == "final"
        assert (
            syllables(result.episode.final_text) >= 3000 * settings.writer_min_length_ratio
        )
        requests = app.state.fake["requests"]
        assert requests[0]["model"] == "fake-novelist"
        assert all("max_tokens" in r for r in requests[1:])
        # JSON 모드는 기본으로 꺼져 있다 (지원 안 하는 서버에 보내면 400)
        assert not any("response_format" in r for r in requests)

    def test_json_mode_is_sent_when_enabled(self) -> None:
        novelist = ScriptedNovelist()
        app = build_app(novelist)
        with serve(app) as base_url:
            provider = OpenAICompatProvider(base_url, "m", json_mode=True)
            provider.complete([Message("user", "x")], json_mode=True)
            provider.complete([Message("user", "y")], json_mode=False)
            provider.close()
        requests = app.state.fake["requests"]
        assert requests[0]["response_format"] == {"type": "json_object"}
        assert "response_format" not in requests[1]


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------
class TestWritingApi:
    @pytest.fixture
    def api(self, client):
        from novel_factory.app.deps import get_llm
        from novel_factory.app.main import app

        novelist = ScriptedNovelist()
        app.dependency_overrides[get_llm] = lambda: novelist
        client.post(
            "/novels",
            json={
                "slug": "hoegwi",
                "title": "회귀",
                "genre": "현대판타지",
                "episodes": 50,
                "characters_per_episode": 3000,
            },
        )
        client.post(
            "/novels/hoegwi/characters", json={"name": "김도윤", "first_episode": 1}
        )
        client.post(
            "/novels/hoegwi/characters", json={"name": "박서연", "first_episode": 1}
        )
        yield client
        app.dependency_overrides.pop(get_llm, None)

    def test_full_flow(self, api) -> None:
        guidance = api.get("/novels/hoegwi/guidance").json()
        assert guidance["guidance"]["source"] == "defaults"
        story = api.post("/novels/hoegwi/story/plan").json()
        assert story["used_llm"] and story["arcs"]
        generated = api.post("/novels/hoegwi/episodes/1/generate")
        assert generated.status_code == 200, generated.text
        body = generated.json()
        assert body["status"] == "final" and body["summary"]
        listing = api.get("/novels/hoegwi/episodes").json()
        assert [e["number"] for e in listing] == [1]
        detail = api.get("/novels/hoegwi/episodes/1").json()
        assert detail["text"] and detail["outline"]["plan"]["title"]
        again = api.post("/novels/hoegwi/episodes/1/generate")
        assert again.status_code == 400

    def test_step_by_step(self, api) -> None:
        assert api.post("/novels/hoegwi/episodes/1/plan").status_code == 200
        assert api.post("/novels/hoegwi/episodes/1/scenes").status_code == 200
        written = api.post("/novels/hoegwi/episodes/1/write").json()
        assert written["char_count"] > 0
        memory = api.post("/novels/hoegwi/episodes/1/memory").json()
        assert Path(memory["folder"]).is_dir()

    def test_missing_episode(self, api) -> None:
        assert api.post("/novels/hoegwi/episodes/9/scenes").status_code == 404

    def test_llm_unavailable_gives_503(self, client) -> None:
        from novel_factory.app.deps import get_llm
        from novel_factory.app.main import app
        from novel_factory.llm import NullProvider

        app.dependency_overrides[get_llm] = lambda: NullProvider()
        client.post("/novels", json={"slug": "n2", "title": "t", "episodes": 10})
        try:
            assert client.post("/novels/n2/episodes/1/plan").status_code == 503
            # Arc 골격은 LLM 없이도 된다
            assert client.post("/novels/n2/story/plan").json()["used_llm"] is False
        finally:
            app.dependency_overrides.pop(get_llm, None)

    def test_model_that_never_complies_gives_502(self, client) -> None:
        """형식에 맞는 답을 끝내 못 받으면 요청 탓이 아니라 상류(모델) 탓이다."""
        from novel_factory.app.deps import get_llm
        from novel_factory.app.main import app

        stubborn = EchoProvider(["계획은 비밀입니다."] * 10)
        app.dependency_overrides[get_llm] = lambda: stubborn
        client.post("/novels", json={"slug": "n3", "title": "t", "episodes": 10})
        try:
            response = client.post("/novels/n3/episodes/1/plan")
            assert response.status_code == 502
            assert "형식에 맞는 답" in response.json()["detail"]
        finally:
            app.dependency_overrides.pop(get_llm, None)
