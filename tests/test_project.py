"""작품 생성 오케스트레이터 테스트 (기획안 57번).

기획안 57번 예시 요청을 그대로 넣고, 참고소설 분석부터 완결·EPUB·출판까지 대본 LLM으로
끝까지 돌린다. 참고소설은 합성 픽스처(sample_novel.txt)를 이름만 바꿔 세 편으로 쓴다.
"""

from __future__ import annotations

import pytest
from fake_llm import ScriptedNovelist
from sqlalchemy import select

from novel_factory.database.base import get_session_factory
from novel_factory.database.models import (
    Character,
    Foreshadowing,
    Novel,
    Publication,
    ReferenceNovel,
)
from novel_factory.database.repositories import (
    CharacterRepository,
    NovelRepository,
    ReferenceLinkRepository,
    StyleBibleRepository,
)
from novel_factory.errors import NovelFactoryError
from novel_factory.generation.genesis import design_novel
from novel_factory.generation.guidance import GenreGuidance
from novel_factory.orchestrator.project import (
    ProjectOptions,
    project_status,
    resolve_reference,
    start_project,
)
from novel_factory.orchestrator.request import (
    ReferenceSpec,
    aspects_in,
    parse_request,
    parse_rules,
)
from novel_factory.scheduler import jobs

PLAN_EXAMPLE = """현대판타지 소설을 만들어줘.

참고소설:
A
B
C

A에서는 빠른 전개만 참고.
B에서는 복선 구조 참고.
C에서는 캐릭터 관계 변화 참고.

4화.
회차당 약 3,000자.
"""


@pytest.fixture
def references(db, sample_txt) -> list[ReferenceNovel]:
    from novel_factory.reference.importer import import_file

    refs = []
    for key in ("A", "B", "C"):
        imported = import_file(sample_txt, reference_id=f"REF_{key}")
        ref = ReferenceNovel(
            reference_id=f"REF_{key}",
            title=key,
            genre="현대판타지",
            stored_path=str(imported.stored_path),
        )
        db.add(ref)
        refs.append(ref)
    db.flush()
    return refs


# ---------------------------------------------------------------------------
# 요청 해석
# ---------------------------------------------------------------------------
class TestRequest:
    def test_plan_example(self) -> None:
        req = parse_rules(PLAN_EXAMPLE.replace("4화", "250화").replace("3,000", "5,000"))
        assert (req.genre, req.episodes, req.chars_per_episode) == ("현대판타지", 250, 5000)
        assert [(r.name, r.aspects) for r in req.references] == [
            ("A", ["pacing"]),
            ("B", ["foreshadowing"]),
            ("C", ["relationship"]),
        ]
        weights = req.references[0].weights()
        assert weights["pacing"] == 1.0 and weights["style"] == 0.0

    @pytest.mark.parametrize(
        ("phrase", "expected"),
        [
            ("캐릭터 관계 변화", ["relationship"]),
            ("캐릭터 구조와 문체", ["character_structure", "style"]),
            ("회차 구조랑 클리프행어", ["episode_shape", "cliffhanger"]),
            ("감정곡선", ["emotion"]),
            ("날씨", []),
        ],
    )
    def test_aspect_keywords(self, phrase, expected) -> None:
        assert sorted(aspects_in(phrase)) == sorted(expected)

    def test_reference_without_statement_means_everything(self) -> None:
        req = parse_rules("무협 소설 써줘. 참고소설: 가, 나\n가에서는 문체만 참고. 30화")
        specs = {r.name: r for r in req.references}
        assert specs["가"].aspects == ["style"] and specs["나"].aspects is None
        assert specs["나"].weights() == {}

    def test_llm_fills_gaps(self) -> None:
        llm = ScriptedNovelist()
        req = parse_request("재미있는 걸로 하나. 참고는 REF_A의 템포", llm)
        assert req.used_llm and req.genre == "무협" and req.episodes == 30
        assert [(r.name, r.aspects) for r in req.references] == [("REF_A", ["pacing"])]
        assert any("날씨" in w for w in req.warnings)

    def test_without_llm_reports_missing(self) -> None:
        req = parse_request("아무거나 써줘", None)
        assert not req.used_llm and len(req.warnings) == 2


# ---------------------------------------------------------------------------
# 작품 설계 (세계관·캐릭터)
# ---------------------------------------------------------------------------
class TestGenesis:
    def _novel(self, db, **kw) -> Novel:
        return NovelRepository(db).add(
            Novel(
                slug="g",
                title=kw.pop("title", "제목 미정"),
                genre="현대판타지",
                planned_episodes=kw.pop("planned", 40),
                **kw,
            )
        )

    def test_validates_llm_output(self, db, settings) -> None:
        novel = self._novel(db, main_conflict="사용자가 정한 갈등")
        guide = GenreGuidance(
            source="defaults", character_intro_interval=5.0, sentence_chars=18.0
        )
        result = design_novel(
            db, novel, ScriptedNovelist(messy=True), settings=settings, guidance=guide
        )
        assert result.attempts == 2
        assert novel.title == "두 번째 인수합병"
        assert novel.main_conflict == "사용자가 정한 갈등"  # 사용자가 정한 값은 그대로
        chars = {c.name: c for c in CharacterRepository(db).for_novel(novel.id)}
        assert set(chars) == {"한지오", "윤채원", "서민호", "백도경"}  # 겹친 이름은 뺐다
        assert chars["한지오"].role == "주인공" and chars["한지오"].first_episode == 1
        assert chars["한지오"].age == 34 and chars["한지오"].code == "C001"
        assert chars["서민호"].role == "조연"  # 없는 역할은 조연으로
        assert chars["백도경"].first_episode == 1 + 5  # 범위 밖 → 등장 간격으로 배치
        fs = db.scalars(select(Foreshadowing).order_by(Foreshadowing.code)).all()
        assert [(f.code, f.setup_episode) for f in fs] == [("F001", 1), ("F002", 2)]
        low, _high = guide.foreshadow_span
        assert all(f.setup_episode + low <= f.planned_payoff <= 40 for f in fs)
        bible = StyleBibleRepository(db).for_novel(novel.id)
        assert bible.forbidden == ["피식 웃었다"] and bible.target_sentence_chars == 18.0
        assert any("비서" in w for w in result.warnings)

    def test_user_title_is_kept(self, db, settings) -> None:
        novel = self._novel(db, title="내가 지은 제목")
        design_novel(db, novel, ScriptedNovelist(), settings=settings)
        assert novel.title == "내가 지은 제목"

    def test_refuses_when_cast_exists(self, db, settings) -> None:
        novel = self._novel(db)
        CharacterRepository(db).add(
            Character(novel_id=novel.id, code="C001", name="누구", role="주인공")
        )
        with pytest.raises(NovelFactoryError, match="이미 인물"):
            design_novel(db, novel, ScriptedNovelist(), settings=settings)


# ---------------------------------------------------------------------------
# 전체 흐름
# ---------------------------------------------------------------------------
class TestProject:
    def test_resolve_reference(self, db, references) -> None:
        assert resolve_reference(db, ReferenceSpec("REF_B")).title == "B"
        assert resolve_reference(db, ReferenceSpec("C")).reference_id == "REF_C"
        with pytest.raises(NovelFactoryError, match="찾지 못"):
            resolve_reference(db, ReferenceSpec("없는작품"))

    def test_plan_example_end_to_end(self, db, settings, references) -> None:
        """기획안 57번: 요청 → 분석 → 패턴 → 생성 → 집필 → 완결 → EPUB → 표지 → 출판."""
        llm = ScriptedNovelist(resolve_foreshadowing=True)
        request = parse_request(PLAN_EXAMPLE, llm)
        result = start_project(
            db,
            request,
            llm,
            options=ProjectOptions(continuous=True),
            settings=settings,
            slug="auto",
        )
        novel = result.novel
        steps = [s["step"] for s in result.steps]
        assert steps == [
            "참고소설 분석",
            "공통 패턴 추출",
            "세계관·캐릭터 생성",
            "전체 스토리 생성",
            "표지",
            "집필 예약",
        ]
        assert result.steps[0]["analyzed"] == ["REF_A", "REF_B", "REF_C"]
        assert result.steps[1]["source"] == "references"
        links = {
            link.reference_id: link.weights
            for link in ReferenceLinkRepository(db).for_novel(novel.id)
        }
        a = next(r.id for r in references if r.reference_id == "REF_A")
        assert links[a]["pacing"] == 1.0 and links[a]["foreshadowing"] == 0.0
        db.commit()

        run = jobs.run_novel(novel.id, provider=llm, settings=settings)
        assert run.written == [1, 2, 3, 4], run.as_dict()
        assert "완결 검사 통과" in run.paused, run.as_dict()

        with get_session_factory()() as s:
            fresh = s.get(Novel, novel.id)
            assert fresh.status == "completed"
            status = project_status(s, fresh)
            assert status["written"] == 4 and status["progress"] == 1.0
            assert status["completion"]["completed"] is True
            kinds = sorted({(p.kind, p.status) for p in s.scalars(select(Publication))})
            assert kinds == [("ebook", "exported"), ("episode", "exported")]
        book = settings.novels_dir / "auto" / "ebook" / "vol01" / "book.epub"
        assert book.exists()
        assert (settings.novels_dir / "auto" / "cover" / "cover.jpg").exists()

    def test_last_episode_plants_no_foreshadowing(self, db, settings) -> None:
        """마지막 화에 복선을 심으면 회수할 자리가 없어 완결 검사가 영영 막힌다."""
        from novel_factory.generation.pipeline import generate_episode

        novel = NovelRepository(db).add(
            Novel(
                slug="last",
                title="t",
                genre="현대판타지",
                planned_episodes=2,
                target_chars_per_episode=3000,
            )
        )
        CharacterRepository(db).add(
            Character(
                novel_id=novel.id,
                code="C001",
                name="김도윤",
                role="주인공",
                first_episode=1,
            )
        )
        llm = ScriptedNovelist()
        generate_episode(db, novel, 1, llm, settings=settings)
        result = generate_episode(db, novel, 2, llm, settings=settings)
        assert "마지막 화라 새 복선을 심지 않습니다." in result.warnings
        setups = [f.setup_episode for f in db.scalars(select(Foreshadowing))]
        assert 2 not in setups

    def test_needs_genre_and_episodes(self, db, settings) -> None:
        req = parse_rules("아무거나")
        with pytest.raises(NovelFactoryError, match="장르"):
            start_project(db, req, ScriptedNovelist(), settings=settings)

    def test_unknown_reference_stops_before_creating(self, db, settings) -> None:
        req = parse_rules("무협 소설 써줘. 참고소설: 없는작품\n10화")
        with pytest.raises(NovelFactoryError, match="찾지 못"):
            start_project(db, req, ScriptedNovelist(), settings=settings)
        assert db.scalars(select(Novel)).all() == []


class TestApi:
    def test_parse_and_create(self, client, monkeypatch, sample_txt) -> None:
        from novel_factory.app import deps

        llm = ScriptedNovelist()
        client.app.dependency_overrides[deps.get_llm] = lambda: llm
        try:
            with open(sample_txt, "rb") as f:
                r = client.post(
                    "/references/upload",
                    files={"file": ("a.txt", f)},
                    data={"title": "A"},
                )
            assert r.status_code == 201, r.text
            parsed = client.post("/projects/parse", json={"text": PLAN_EXAMPLE}).json()
            assert parsed["genre"] == "현대판타지" and parsed["episodes"] == 4

            bad = client.post(
                "/projects",
                json={
                    "genre": "무협",
                    "episodes": 3,
                    "references": [{"name": "A", "aspects": ["없는항목"]}],
                },
            )
            assert bad.status_code == 422

            r = client.post(
                "/projects",
                json={
                    "slug": "p1",
                    "genre": "무협",
                    "episodes": 3,
                    "references": [{"name": "A", "aspects": ["pacing"]}],
                },
            )
            assert r.status_code == 201, r.text
            assert r.json()["writing_started"] is False
            status = client.get("/projects/p1").json()
            assert status["written"] == 0 and status["schedule"]["enabled"] is True
            assert [s["step"] for s in status["steps"]][-1] == "집필 예약"
        finally:
            client.app.dependency_overrides.clear()

    def test_without_llm(self, client) -> None:
        r = client.post("/projects", json={"genre": "무협", "episodes": 3})
        assert r.status_code == 400 and "LLM" in r.json()["detail"]


class TestReferenceNames:
    """기획안 36번 고유 설정 유사 · 56번 셋째 원칙. 픽스처 참고작의 인물은
    이준혁(주인공), 최민석(주요조연), 박서연, 김도윤, 강태호다."""

    def _linked(self, db, references) -> Novel:
        from novel_factory.reference.service import analyze_reference_record

        ref = references[0]
        analyze_reference_record(db, ref)
        novel = NovelRepository(db).add(
            Novel(slug="names", title="t", genre="현대판타지", planned_episodes=20)
        )
        ReferenceLinkRepository(db).upsert(novel.id, ref.id)
        db.flush()
        return novel

    def test_profile_keeps_hashes_not_names(self, db, references) -> None:
        import json

        self._linked(db, references)
        stored = json.dumps(references[0].profile, ensure_ascii=False)
        assert "이준혁" not in stored and "최민석" not in stored
        hashes = references[0].profile["name_hashes"]
        assert len(hashes["main"]) >= 1 and all(len(h) == 32 for h in hashes["main"])

    def test_genesis_drops_reference_names(self, db, settings, references) -> None:
        from novel_factory.generation.genesis import apply_genesis
        from novel_factory.generation.schemas import GenesisOut
        from novel_factory.reference.service import linked_name_hashes
        from novel_factory.reference.similarity.names import name_salt

        novel = self._linked(db, references)
        out = GenesisOut.model_validate(
            {
                "logline": "새 작품의 로그라인",
                "main_conflict": "새 작품의 갈등",
                "characters": [
                    {"name": "이준혁", "role": "주인공"},
                    {"name": "한지오", "role": "주인공"},
                ],
                "world": [{"category": "인물", "name": "최민석", "description": "x"}],
            }
        )
        result = apply_genesis(
            db,
            novel,
            out,
            GenreGuidance(source="defaults"),
            reference_names=linked_name_hashes(db, novel),
            salt=name_salt(settings),
        )
        names = [c.name for c in CharacterRepository(db).for_novel(novel.id)]
        assert names == ["한지오"] and result.world == []
        assert any("이준혁" in w for w in result.warnings)

    def test_similarity_flags_reused_name(self, db, settings, references) -> None:
        from novel_factory.database.models import Episode
        from novel_factory.quality.runner import run_checks

        novel = self._linked(db, references)
        ep = Episode(
            novel_id=novel.id,
            number=1,
            title="1화",
            status="drafted",
            scenes=[
                {"text": "한지오는 서류를 넘겼다."},
                {"text": "그때 이준혁이 문을 열었다. 김도윤도 뒤따랐다."},
            ],
            final_text="한지오는 서류를 넘겼다.\n\n그때 이준혁이 문을 열었다. 김도윤도 뒤따랐다.",
        )
        db.add(ep)
        db.flush()
        report = run_checks(db, novel, ep, None, settings=settings, logic=False)
        issues = {
            i.evidence["name"]: (i.severity.value, i.scene_index)
            for i in report.results["Similarity"].issues
            if i.code == "reference_name"
        }
        assert issues == {"이준혁": ("FAIL", 1), "김도윤": ("WARN", 1)}
        assert report.failing_scenes() == {1: report.failing_scenes()[1]}
