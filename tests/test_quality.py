"""품질 검사 테스트 (기획안 34~38번).

규칙 기반 검사(Continuity, Style, Hook)는 원고를 직접 만들어 넣고 확인한다.
LLM 검사(Logic, Reader)와 자동 수정은 대본 LLM(tests/fake_llm.py)으로 확인한다.
대본 LLM은 원고에 없는 문장을 인용하고 정해 두지 않은 kind를 섞어서, 그런 지적이
버려지는지까지 본다.
"""

from __future__ import annotations

import pytest
from fake_llm import ScriptedNovelist

from novel_factory.database.models import Character, Episode, Novel, TimelineEvent
from novel_factory.database.repositories import (
    CharacterRepository,
    EpisodeRepository,
    NovelRepository,
    StyleBibleRepository,
)
from novel_factory.generation.pipeline import generate_episode
from novel_factory.generation.schemas import ReaderScores
from novel_factory.generation.writer import assemble_text
from novel_factory.llm import EchoProvider
from novel_factory.quality.continuity import check_continuity, parse_dates
from novel_factory.quality.logic import check_logic, verified_quote
from novel_factory.quality.reader import simulate_readers
from novel_factory.quality.report import (
    CheckResult,
    Issue,
    QualityReport,
    Severity,
    locate_scene,
    quote_exists,
)
from novel_factory.quality.runner import check_and_fix
from novel_factory.quality.style_check import check_hook, check_style


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
        Character(
            novel_id=n.id,
            code="C001",
            name="김도윤",
            role="주인공",
            age=34,
            first_episode=1,
        )
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
    chars.add(
        Character(
            novel_id=n.id,
            code="C004",
            name="최민석",
            role="조연",
            first_episode=1,
            is_alive=False,
            exit_episode=3,
        )
    )
    db.flush()
    return n


def _episode(
    db,
    novel: Novel,
    number: int,
    scenes: list[str],
    *,
    characters: list[str] | None = None,
    hook: str = "",
    avoid: list[str] | None = None,
) -> Episode:
    ep = Episode(
        novel_id=novel.id,
        number=number,
        title=f"{number}화",
        status="drafted",
        outline={
            "plan": {
                "characters": characters or [],
                "hook": {"type": hook, "content": "봉투 속 이름"},
            },
            "directives": {
                "hook": {"required": bool(hook), "kind": hook},
                "avoid_phrases": avoid or [],
                "target_chars": 0,
            },
        },
        scenes=[{"index": i, "target_chars": 900, "text": t} for i, t in enumerate(scenes)],
        final_text=assemble_text(scenes),
    )
    return EpisodeRepository(db).add(ep)


def _codes(result: CheckResult) -> list[str]:
    return [i.code for i in result.issues]


# ---------------------------------------------------------------------------
# 보고 구조
# ---------------------------------------------------------------------------
class TestReport:
    def test_verdicts_and_failing_scenes(self) -> None:
        report = QualityReport()
        report.add(CheckResult("Continuity"))
        warn = CheckResult("Style", [Issue("Style", "pov", Severity.WARN, "시점")])
        fail = CheckResult(
            "Similarity",
            [
                Issue("Similarity", "x", Severity.FAIL, "겹침", scene_index=2),
                Issue("Similarity", "x", Severity.FAIL, "어디인지 모름"),
            ],
        )
        report.add(warn)
        report.add(fail)
        report.add(CheckResult("Logic", skipped="LLM 없음"))
        assert report.verdict == "FAIL"
        assert report.summary() == (
            "Continuity: PASS\nStyle: WARN\nSimilarity: FAIL\nLogic: SKIPPED"
        )
        # 장면을 모르는 FAIL은 자동 수정 대상이 아니다
        assert list(report.failing_scenes()) == [2]

    def test_locate_scene_ignores_whitespace(self) -> None:
        scenes = ["첫 장면이다.", "둘째 장면에서\n그가 웃었다."]
        assert locate_scene("장면에서 그가 웃었다", scenes) == 1
        assert locate_scene("없는 문장", scenes) is None
        assert locate_scene("", scenes) is None

    def test_quote_exists_needs_length(self) -> None:
        assert quote_exists("그가 웃었다", "그때 그가 웃었다.")
        assert not quote_exists("그가", "그때 그가 웃었다.")  # 너무 짧다

    def test_verified_quote(self) -> None:
        text = "도윤은 서류철을 덮었다. 그리고 창밖을 내려다보았다."
        assert (
            verified_quote("“도윤은 서류철을 덮었다.”", text) == "도윤은 서류철을 덮었다."
        )
        # 말줄임표로 줄인 인용은 조각마다 확인한다
        assert verified_quote("도윤은 서류철을…창밖을 내려다보았다", text)
        assert verified_quote("도윤은 서류철을…창밖으로 뛰어내렸다", text) == ""
        assert verified_quote("", text) == ""


# ---------------------------------------------------------------------------
# Continuity (기획안 34번)
# ---------------------------------------------------------------------------
class TestContinuity:
    def test_clean_episode_passes(self, db, novel) -> None:
        ep = _episode(
            db,
            novel,
            5,
            ["도윤은 서류를 넘겼다.", '서연이 말했다. "준비됐어요."'],
            characters=["C001", "C002"],
        )
        result = check_continuity(db, novel, ep)
        assert result.verdict == "PASS", result.as_dict()

    def test_dead_character_speaking_is_fail(self, db, novel) -> None:
        ep = _episode(db, novel, 5, ["도윤은 서류를 넘겼다.", "민석이 웃으며 말했다."])
        issues = [
            i for i in check_continuity(db, novel, ep).issues if i.code == "dead_character"
        ]
        assert issues[0].severity is Severity.FAIL
        assert issues[0].scene_index == 1

    def test_dead_character_in_plan_is_only_warn(self, db, novel) -> None:
        """계획에 있으면 회상 장면일 수 있다."""
        ep = _episode(db, novel, 5, ["민석이 웃으며 말했다."], characters=["C001", "C004"])
        issues = [
            i for i in check_continuity(db, novel, ep).issues if i.code == "dead_character"
        ]
        assert issues[0].severity is Severity.WARN

    def test_dead_character_mentioned_is_warn(self, db, novel) -> None:
        ep = _episode(db, novel, 5, ["도윤은 민석의 사진을 오래 보았다."])
        issues = [
            i for i in check_continuity(db, novel, ep).issues if i.code == "dead_character"
        ]
        assert issues[0].severity is Severity.WARN

    def test_dead_only_after_exit(self, db, novel) -> None:
        ep = _episode(db, novel, 2, ["민석이 웃으며 말했다."])
        assert "dead_character" not in _codes(check_continuity(db, novel, ep))

    def test_future_character(self, db, novel) -> None:
        ep = _episode(db, novel, 5, ["강태호가 회의실에 들어왔다."])
        assert "future_character" in _codes(check_continuity(db, novel, ep))

    def test_name_inside_word_is_ignored(self, db, novel) -> None:
        """'태호'가 다른 단어 안에 들어 있으면 인물이 아니다."""
        ep = _episode(db, novel, 5, ["대태호수 공원에서 만났다."])
        assert "future_character" not in _codes(check_continuity(db, novel, ep))

    @pytest.mark.parametrize(
        ("sentence", "flagged"),
        [
            ("도윤은 서른네 살이었다.", False),
            ("도윤은 34살이었다.", False),
            ("도윤은 서른다섯 살이었다.", True),
            ("김도윤, 41세.", True),
        ],
    )
    def test_age(self, db, novel, sentence: str, flagged: bool) -> None:
        ep = _episode(db, novel, 5, [sentence])
        assert ("age_mismatch" in _codes(check_continuity(db, novel, ep))) is flagged

    def test_time_backwards(self, db, novel) -> None:
        db.add(
            TimelineEvent(
                novel_id=novel.id, episode_number=4, occurred_at="2026-03-10", title="실사"
            )
        )
        db.flush()
        ep = _episode(db, novel, 5, ["2026년 3월 2일, 도윤은 서류를 받았다."])
        assert "time_backwards" in _codes(check_continuity(db, novel, ep))
        later = _episode(db, novel, 6, ["2026년 3월 12일, 도윤은 서류를 받았다."])
        assert "time_backwards" not in _codes(check_continuity(db, novel, later))

    def test_planned_absent(self, db, novel) -> None:
        ep = _episode(db, novel, 5, ["도윤은 혼자였다."], characters=["C001", "C002"])
        issues = [
            i for i in check_continuity(db, novel, ep).issues if i.code == "planned_absent"
        ]
        assert [i.evidence["character"] for i in issues] == ["C002"]

    def test_parse_dates(self) -> None:
        dates = parse_dates("2026년 3월 2일과 2026-03-10, 그리고 2026.13.40")
        assert [d.isoformat() for d in dates] == ["2026-03-02", "2026-03-10"]


# ---------------------------------------------------------------------------
# Style / Hook (기획안 32·33·8번)
# ---------------------------------------------------------------------------
class TestStyle:
    def test_forbidden_phrase_is_fail(self, db, novel) -> None:
        StyleBibleRepository(db).upsert(novel.id, forbidden=["피식 웃었다"])
        ep = _episode(db, novel, 1, ["도윤은 서류를 넘겼다.", "그는 피식 웃었다."])
        result = check_style(db, novel, ep)
        issue = next(i for i in result.issues if i.code == "forbidden_phrase")
        assert issue.severity is Severity.FAIL and issue.scene_index == 1

    def test_avoided_phrase_only_in_narration(self, db, novel) -> None:
        ep = _episode(
            db,
            novel,
            1,
            ['"입꼬리가 올라갔다고?" 그가 물었다.', "서연의 입꼬리가 올라갔다."],
            avoid=["입꼬리가 올라갔다"],
        )
        issue = next(
            i for i in check_style(db, novel, ep).issues if i.code == "avoided_phrase"
        )
        assert issue.evidence["count"] == 1  # 대사 안의 것은 세지 않는다
        assert issue.severity is Severity.WARN

    def test_targets_from_style_bible(self, db, novel) -> None:
        StyleBibleRepository(db).upsert(
            novel.id, target_dialogue_ratio=0.9, target_paragraph_chars=400.0
        )
        ep = _episode(db, novel, 1, ["도윤은 서류를 넘겼다.\n\n창밖은 어두웠다."])
        codes = _codes(check_style(db, novel, ep))
        assert "dialogue_ratio" in codes and "paragraph_length" in codes
        assert "length" in codes  # 목표 3,000자에 한참 못 미친다

    def test_empty_text_is_skipped(self, db, novel) -> None:
        ep = _episode(db, novel, 1, [])
        assert check_style(db, novel, ep).verdict == "SKIPPED"


class TestHook:
    def test_missing_hook_is_fail_on_last_scene(self, db, novel) -> None:
        ep = _episode(
            db,
            novel,
            1,
            ["도윤은 서류를 넘겼다.", "그는 집에 가서 잠을 잤다."],
            hook="반전",
        )
        result = check_hook(ep)
        assert _codes(result) == ["hook_missing"]
        assert result.issues[0].scene_index == 1
        assert result.issues[0].severity is Severity.FAIL

    def test_not_required(self, db, novel) -> None:
        ep = _episode(db, novel, 1, ["그는 집에 가서 잠을 잤다."])
        assert check_hook(ep).verdict == "PASS"

    def test_present_hook_passes_or_warns_on_type(self, db, novel) -> None:
        ep = _episode(
            db,
            novel,
            1,
            [
                "도윤은 서류를 넘겼다.",
                "봉투 속 이름을 본 순간 그는 숨을 멈췄다. 그 사람이 바로 그였다니.",
            ],
            hook="반전",
        )
        result = check_hook(ep)
        assert result.metrics["detected"] == "정보공개"
        # 훅은 있다. 유형이 계획과 달라서 WARN만.
        assert _codes(result) == ["hook_type"]
        assert result.verdict == "WARN"

    def test_lexicon_misses_are_possible(self, db, novel) -> None:
        """어휘 사전에 없는 표현으로 끝낸 훅은 못 알아본다 (규칙 기반의 한계)."""
        ep = _episode(
            db,
            novel,
            1,
            ["봉투 속 이름을 본 순간, 죽은 줄 알았던 사람이었다."],
            hook="반전",
        )
        assert _codes(check_hook(ep)) == ["hook_missing"]


# ---------------------------------------------------------------------------
# Logic (기획안 35번) / Reader (기획안 37번)
# ---------------------------------------------------------------------------
FLAG = "서연은 태호가 내일 인수를 발표한다는 사실을 이미 알고 있었다."


class TestLogic:
    def test_filters_fabricated_and_unknown(self, db, novel, settings) -> None:
        ep = _episode(db, novel, 1, ["도윤은 서류를 넘겼다.", f"회의가 끝났다. {FLAG}"])
        result = check_logic(
            db, novel, ep, ScriptedNovelist(logic_flag=FLAG), settings=settings
        )
        assert result.metrics["reported"] == 3
        assert result.metrics["dropped_unverified_quote"] == 1
        assert result.metrics["dropped_unknown_kind"] == 1
        [issue] = result.issues
        assert issue.code == "knowledge_leak" and issue.severity is Severity.FAIL
        assert issue.quote == FLAG  # 둥근 따옴표를 벗겼다
        assert issue.scene_index == 1

    def test_messy_answer_is_retried(self, db, novel, settings) -> None:
        ep = _episode(db, novel, 1, ["도윤은 서류를 넘겼다."])
        result = check_logic(db, novel, ep, ScriptedNovelist(messy=True), settings=settings)
        assert result.verdict == "PASS" and result.metrics["attempts"] == 2

    def test_no_llm_is_skipped(self, db, novel, settings) -> None:
        ep = _episode(db, novel, 1, ["도윤은 서류를 넘겼다."])
        assert check_logic(db, novel, ep, None, settings=settings).verdict == "SKIPPED"

    def test_unusable_answer_is_skipped(self, db, novel, settings) -> None:
        ep = _episode(db, novel, 1, ["도윤은 서류를 넘겼다."])
        llm = EchoProvider(["모르겠다"] * 5)
        result = check_logic(db, novel, ep, llm, settings=settings)
        assert result.verdict == "SKIPPED" and "형식" in result.skipped

    def test_prompt_carries_knowledge_limits(self, db, novel, settings) -> None:
        from novel_factory.database.models import CharacterKnowledge

        seoyeon = CharacterRepository(db).get_by_code(novel.id, "C002")
        db.add(
            CharacterKnowledge(
                character_id=seoyeon.id,
                fact_key="takeover",
                fact="태호의 인수 발표 일정",
                knows=False,
            )
        )
        db.flush()
        ep = _episode(db, novel, 1, ["도윤은 서류를 넘겼다."])
        llm = ScriptedNovelist()
        check_logic(db, novel, ep, llm, settings=settings)
        prompt = llm.calls[0][1][1].content
        assert "정보 제한" in prompt and "태호의 인수 발표 일정" in prompt


class TestReader:
    def test_scores_and_boring_parts(self, db, novel, settings) -> None:
        ep = _episode(db, novel, 1, ["도윤은 서류를 넘겼다.", "창밖은 어두웠다."])
        result = simulate_readers(
            db,
            novel,
            ep,
            ScriptedNovelist(),
            personas=["웹소설 독자", "까다로운 독자"],
            settings=settings,
        )
        personas = result.metrics["personas"]
        assert set(personas) == {"웹소설 독자", "까다로운 독자"}
        scores = personas["웹소설 독자"]["scores"]
        assert scores["immersion"] == 7.0 and scores["pacing"] == 6.0
        assert scores["reward"] == 10.0  # 범위 밖 값은 잘라 낸다
        assert set(result.metrics["average"]) == set(ReaderScores.model_fields)
        # 지어낸 인용은 버리고 실제 문장만 WARN으로
        assert len(result.issues) == 2
        assert all(
            i.severity is Severity.WARN and i.scene_index == 0 for i in result.issues
        )
        assert result.verdict == "WARN"

    def test_no_llm(self, db, novel, settings) -> None:
        ep = _episode(db, novel, 1, ["도윤은 서류를 넘겼다."])
        assert simulate_readers(db, novel, ep, None, settings=settings).verdict == "SKIPPED"


# ---------------------------------------------------------------------------
# 자동 수정 (기획안 38번)
# ---------------------------------------------------------------------------
class TestAutoFix:
    def test_only_failing_scene_is_rewritten(self, db, novel, settings) -> None:
        StyleBibleRepository(db).upsert(novel.id, forbidden=["피식 웃었다"])
        scenes = ["도윤은 서류를 넘겼다.", "그는 피식 웃었다.", "창밖은 어두웠다."]
        ep = _episode(db, novel, 1, scenes)
        llm = ScriptedNovelist()
        outcome = check_and_fix(db, novel, ep, llm, settings=settings)

        assert outcome.fixed_scenes == [1] and outcome.chosen_round == 1
        texts = [s["text"] for s in ep.scenes]
        assert texts[0] == scenes[0] and texts[2] == scenes[2]  # 나머지는 그대로
        assert "피식 웃었다" not in ep.final_text
        assert ep.final_text == assemble_text(texts)
        assert outcome.report.results["Style"].verdict != "FAIL"
        assert [h["round"] for h in outcome.history] == [0, 1]
        # 고치기 요청에 문제와 인용 구절이 들어갔다
        fix_prompt = next(
            m.content
            for stage, msgs, _ in llm.calls
            if stage == "writer"
            for m in msgs
            if m.content.startswith("# 장면 고치기")
        )
        assert "피식 웃었다" in fix_prompt and "Style" in fix_prompt
        assert ep.quality_reports["quality"]["chosen_round"] == 1

    def test_worse_rewrite_is_not_kept(self, db, novel, settings) -> None:
        StyleBibleRepository(db).upsert(
            novel.id, forbidden=["피식 웃었다", "입꼬리가 올라갔다"]
        )
        scenes = ["도윤은 서류를 넘겼다.", "그는 피식 웃었다."]
        ep = _episode(db, novel, 1, scenes)
        llm = ScriptedNovelist(fix_text="그는 피식 웃었다. 입꼬리가 올라갔다. " * 40)
        outcome = check_and_fix(db, novel, ep, llm, settings=settings)
        assert outcome.chosen_round == 0
        assert [s["text"] for s in ep.scenes] == scenes
        assert any("나아지지 않아서" in w for w in outcome.warnings)

    def test_rewrite_that_does_not_help_is_not_kept(self, db, novel, settings) -> None:
        """고쳐도 FAIL 수가 같으면 원래 원고를 둔다. 오탐으로 원고가 바뀌지 않게."""
        scenes = ["도윤은 서류를 넘겼다.", "그는 집에 가서 잠을 잤다."]
        ep = _episode(db, novel, 1, scenes, hook="반전")
        llm = ScriptedNovelist(
            fix_text="그는 다시 잠을 잤다. " * 120
        )  # 이어 쓰기가 안 붙게 충분히 길게
        outcome = check_and_fix(db, novel, ep, llm, settings=settings, logic=False)
        assert len(outcome.history) == 3  # 0차 + 수정 2번
        assert outcome.chosen_round == 0
        assert [s["text"] for s in ep.scenes] == scenes

    def test_check_only_does_not_touch_text(self, db, novel, settings) -> None:
        StyleBibleRepository(db).upsert(novel.id, forbidden=["피식 웃었다"])
        ep = _episode(db, novel, 1, ["그는 피식 웃었다."])
        before = ep.final_text
        outcome = check_and_fix(
            db, novel, ep, ScriptedNovelist(), settings=settings, fix=False
        )
        assert outcome.report.verdict == "FAIL" and ep.final_text == before

    def test_without_llm_reports_but_cannot_fix(self, db, novel, settings) -> None:
        StyleBibleRepository(db).upsert(novel.id, forbidden=["피식 웃었다"])
        ep = _episode(db, novel, 1, ["그는 피식 웃었다."])
        outcome = check_and_fix(db, novel, ep, None, settings=settings)
        assert outcome.report.results["Logic"].verdict == "SKIPPED"
        assert any("LLM이 없어서" in w for w in outcome.warnings)


class TestPipeline:
    def test_generate_fixes_before_memory(self, db, novel, settings) -> None:
        StyleBibleRepository(db).upsert(novel.id, forbidden=["피식 웃었다"])
        llm = ScriptedNovelist(plant="도윤은 피식 웃었다.")
        result = generate_episode(db, novel, 1, llm, settings=settings)
        ep = result.episode
        assert "피식 웃었다" in ep.draft  # 초고는 남긴다
        assert "피식 웃었다" not in ep.final_text
        assert result.quality["fixed_scenes"] == [0]
        # 기억 갱신은 고친 원고로 했다
        memory_prompt = next(
            msgs[-1].content for stage, msgs, _ in llm.calls if stage == "memory"
        )
        assert "피식 웃었다" not in memory_prompt
        # 단계 순서: 집필 → 검사(logic) → 수정 → 재검사 → 기억
        stages = [c[0] for c in llm.calls]
        assert stages.index("logic") < stages.index("memory")
        assert "quality" in result.timings

    def test_logic_fail_is_fixed(self, db, novel, settings) -> None:
        llm = ScriptedNovelist(plant=FLAG, logic_flag=FLAG)
        result = generate_episode(db, novel, 1, llm, settings=settings)
        assert FLAG not in result.episode.final_text
        history = result.quality["history"]
        assert "Logic: FAIL" in history[0]["summary"]
        assert "Logic: FAIL" not in history[-1]["summary"]

    def test_reader_runs_once_when_enabled(self, db, novel, settings, monkeypatch) -> None:
        monkeypatch.setattr(settings, "quality_reader", True)
        monkeypatch.setattr(settings, "quality_reader_personas", ["웹소설 독자"])
        llm = ScriptedNovelist()
        result = generate_episode(db, novel, 1, llm, settings=settings)
        assert llm.stage_counts["reader"] == 1
        assert result.quality["results"]["Reader"]["metrics"]["personas"]


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------
class TestCheckApi:
    def _make(self, client) -> None:
        from novel_factory.database.base import get_session_factory

        r = client.post(
            "/novels",
            json={"slug": "api", "title": "검사", "genre": "현대판타지", "episodes": 10},
        )
        assert r.status_code in (200, 201), r.text
        session = get_session_factory()()
        try:
            novel = NovelRepository(session).require_by_slug("api")
            StyleBibleRepository(session).upsert(novel.id, forbidden=["피식 웃었다"])
            _episode(session, novel, 1, ["도윤은 서류를 넘겼다.", "그는 피식 웃었다."])
            session.commit()
        finally:
            session.close()

    def test_check_without_llm(self, client) -> None:
        self._make(client)
        r = client.post("/novels/api/episodes/1/check")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["verdict"] == "FAIL"
        assert body["results"]["Logic"]["verdict"] == "SKIPPED"
        assert "Style: FAIL" in body["summary"]
        # 저장됐다
        detail = client.get("/novels/api/episodes/1").json()
        assert detail["quality_reports"]["quality"]["verdict"] == "FAIL"

    def test_fix_needs_llm(self, client) -> None:
        self._make(client)
        assert client.post("/novels/api/episodes/1/check?fix=true").status_code == 503

    def test_final_episode_is_check_only(self, client) -> None:
        """확정 회차는 기억 갱신이 끝났다. 원고를 고치면 장기기억과 어긋난다."""
        from novel_factory.database.base import get_session_factory

        self._make(client)
        session = get_session_factory()()
        try:
            novel = NovelRepository(session).require_by_slug("api")
            EpisodeRepository(session).get_by_number(novel.id, 1).status = "final"
            session.commit()
        finally:
            session.close()
        assert client.post("/novels/api/episodes/1/check?fix=true").status_code == 409
        assert client.post("/novels/api/episodes/1/check").status_code == 200

    def test_missing_episode(self, client) -> None:
        self._make(client)
        assert client.post("/novels/api/episodes/9/check").status_code == 404
