"""표지·전자책·출판·완결 검사 테스트 (기획안 43~47번).

EPUB은 우리 EPUB 파서로 다시 읽어 확인하고, epubcheck(W3C 공식 검증기)가 있으면
그것으로도 검증한다. epubcheck는 Java 프로그램이라 저장소에 넣지 않았다.
EPUBCHECK_JAR 환경변수에 epubcheck.jar 경로를 주면 그 테스트가 돈다.
"""

from __future__ import annotations

import io
import os
import subprocess
import zipfile
from pathlib import Path

import pytest
from fake_llm import ScriptedNovelist
from fake_openai_server import build_app, serve
from fastapi import FastAPI, Request
from sqlalchemy import select

from novel_factory.database.base import get_session_factory
from novel_factory.database.models import (
    Character,
    Episode,
    Foreshadowing,
    Novel,
    Publication,
    TimelineEvent,
)
from novel_factory.database.repositories import CharacterRepository, NovelRepository
from novel_factory.generation.pipeline import generate_episode
from novel_factory.publishing.adapters import get_adapter
from novel_factory.publishing.cover import (
    A1111ImageClient,
    OpenAIImageClient,
    compose_cover,
    find_font,
    make_cover,
    wrap_title,
)
from novel_factory.publishing.ebook import build_ebooks, plan_volumes
from novel_factory.publishing.epub import BookMeta, Chapter, book_identifier, write_epub
from novel_factory.publishing.info import get_info, update_info
from novel_factory.publishing.queue import (
    auto_publish,
    enqueue_ebook,
    enqueue_episode,
    process_queue,
)
from novel_factory.quality.completion import check_completion
from novel_factory.reference.parser import parse_file

PIL = pytest.importorskip("PIL")
from PIL import Image  # noqa: E402

EPUBCHECK = os.environ.get("EPUBCHECK_JAR", "")


@pytest.fixture
def novel(db) -> Novel:
    n = NovelRepository(db).add(
        Novel(
            slug="hoegwi",
            title="회귀한 인수합병가",
            genre="현대판타지",
            logline="죽은 인수합병가가 회귀해 다시 판을 짠다.",
            planned_episodes=3,
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


def _final(db, novel: Novel, number: int, text: str, **kw) -> Episode:
    ep = Episode(
        novel_id=novel.id,
        number=number,
        title=kw.pop("title", f"{number}화 제목"),
        status="final",
        final_text=text,
        summary=kw.pop("summary", f"{number}화 줄거리"),
        **kw,
    )
    db.add(ep)
    db.flush()
    return ep


def _write_all(db, novel, settings, llm=None) -> ScriptedNovelist:
    llm = llm or ScriptedNovelist()
    for n in range(1, novel.planned_episodes + 1):
        generate_episode(db, novel, n, llm, settings=settings)
    return llm


# ---------------------------------------------------------------------------
# 표지 (기획안 43번)
# ---------------------------------------------------------------------------
class TestCover:
    def test_text_cover_without_image_server(self, db, novel, settings) -> None:
        result = make_cover(db, novel, None, settings=settings)
        assert result.backend == "text" and not result.used_llm
        cover = Image.open(result.cover)
        thumb = Image.open(result.thumbnail)
        assert cover.size == (settings.cover_width, settings.cover_height)
        assert thumb.size == (settings.thumbnail_width, settings.thumbnail_height)
        assert cover.format == "JPEG"
        assert (result.cover.parent / "cover.json").exists()
        if find_font(settings) is None:
            assert any("글꼴" in w for w in result.warnings)

    def test_llm_writes_prompt_and_text_is_excluded(self, db, novel, settings) -> None:
        llm = ScriptedNovelist()
        result = make_cover(db, novel, llm, settings=settings)
        assert result.used_llm and "rooftop" in result.prompt
        # 모델이 빼먹어도 글자 금지는 넣는다 (이미지 모델은 한글을 망가뜨린다)
        assert "text" in result.negative_prompt
        cover_prompt = next(
            msgs[1].content for stage, msgs, _ in llm.calls if stage == "cover"
        )
        assert "현대판타지" in cover_prompt and "주인공" in cover_prompt

    @pytest.mark.parametrize("api", ["openai", "a1111"])
    def test_image_server(self, db, novel, settings, monkeypatch, api) -> None:
        app = build_app(ScriptedNovelist(), images=True)
        with serve(app) as base_url:
            root = base_url if api == "openai" else base_url.removesuffix("/v1")
            monkeypatch.setattr(settings, "image_base_url", root)
            monkeypatch.setattr(settings, "image_api", api)
            result = make_cover(db, novel, None, settings=settings)
        assert result.backend == api
        assert not any("이미지 생성 실패" in w for w in result.warnings)
        top = Image.open(result.cover).convert("RGB").getpixel((10, 10))
        # 받은 그림(빨강 또는 파랑)이 표지 위쪽에 깔렸다
        assert (top[0] > 150) if api == "openai" else (top[2] > 150)
        body = app.state.fake["requests"][-1]
        if api == "openai":
            assert body["size"] == f"{settings.image_width}x{settings.image_height}"
        else:
            assert body["width"] == settings.image_width

    def test_image_failure_falls_back_to_text(
        self, db, novel, settings, monkeypatch
    ) -> None:
        with serve(build_app(ScriptedNovelist(), images=False)) as base_url:
            monkeypatch.setattr(settings, "image_base_url", base_url)
            result = make_cover(db, novel, None, settings=settings)
        assert result.backend == "text"
        assert any("이미지 생성 실패" in w for w in result.warnings)

    def test_clients_decode(self) -> None:
        app = build_app(ScriptedNovelist(), images=True)
        with serve(app) as base_url:
            png = OpenAIImageClient(base_url, "m").generate("p", "n", 64, 96)
            png2 = A1111ImageClient(base_url.removesuffix("/v1")).generate("p", "n", 64, 96)
        assert Image.open(io.BytesIO(png)).size == (64, 96)
        assert Image.open(io.BytesIO(png2)).size == (64, 96)

    def test_wrap_title(self, settings) -> None:
        from novel_factory.publishing.cover import _load_font

        font = _load_font(find_font(settings), 40)
        lines = wrap_title("아주아주아주아주아주아주긴제목 짧은말", font, 200)
        assert all(font.getlength(line) <= 200 for line in lines)
        assert "".join(lines).replace(" ", "") == "아주아주아주아주아주아주긴제목짧은말"

    def test_compose_fits_art_to_ratio(self, settings) -> None:
        buf = io.BytesIO()
        Image.new("RGB", (1000, 1000), (0, 200, 0)).save(buf, "PNG")
        img = compose_cover(
            buf.getvalue(),
            title="제목",
            author="작가",
            genre="무협",
            seed="s",
            size=(400, 600),
            font_path=find_font(settings),
        )
        assert img.size == (400, 600)


# ---------------------------------------------------------------------------
# EPUB (기획안 44번)
# ---------------------------------------------------------------------------
def _sample_epub(path: Path, *, cover: bool = True) -> Path:
    meta = BookMeta(
        title="회귀한 인수합병가 1권",
        identifier=book_identifier("hoegwi", "vol01"),
        author="정결",
        description='<소개> & "따옴표"',
        subjects=["현대판타지", "회귀"],
    )
    chapters = [
        Chapter(1, "거래", '"이번 계약은 제가 들고 갑니다."\n\n회의실이\n조용해졌다.\x01'),
        Chapter(2, "견제 <1>", "도윤은 서류를 넘겼다.\n\n<끝>"),
    ]
    art = None
    if cover:
        buf = io.BytesIO()
        Image.new("RGB", (60, 90), (10, 10, 10)).save(buf, "JPEG")
        art = buf.getvalue()
    return write_epub(path, meta, chapters, cover_jpeg=art)


class TestEpub:
    def test_container_rules(self, tmp_path) -> None:
        path = _sample_epub(tmp_path / "a.epub")
        with zipfile.ZipFile(path) as z:
            first = z.infolist()[0]
            assert (
                first.filename == "mimetype" and first.compress_type == zipfile.ZIP_STORED
            )
            assert z.read("mimetype") == b"application/epub+zip"
            opf = z.read("OEBPS/content.opf").decode()
            assert 'properties="cover-image"' in opf and "&lt;소개&gt;" in opf
            chapter = z.read("OEBPS/text/ep0001.xhtml").decode()
            assert "\x01" not in chapter  # XML에 쓸 수 없는 문자는 버린다
            assert "<p>회의실이 조용해졌다.</p>" in chapter  # 문단 안 줄바꿈은 합친다

    def test_our_parser_reads_it_back(self, tmp_path) -> None:
        doc = parse_file(_sample_epub(tmp_path / "a.epub"))
        text = doc.text
        assert "이번 계약은 제가 들고 갑니다." in text and "<끝>" in text

    def test_ids_are_stable(self) -> None:
        assert book_identifier("a", "vol01") == book_identifier("a", "vol01")
        assert book_identifier("a", "vol01") != book_identifier("a", "vol02")

    @pytest.mark.skipif(not EPUBCHECK, reason="EPUBCHECK_JAR 미지정")
    @pytest.mark.parametrize("cover", [True, False])
    def test_epubcheck(self, tmp_path, cover) -> None:
        path = _sample_epub(tmp_path / "a.epub", cover=cover)
        run = subprocess.run(
            ["java", "-jar", EPUBCHECK, str(path)],
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert run.returncode == 0, run.stdout + run.stderr
        assert "0 fatals / 0 errors / 0 warnings" in run.stdout


# ---------------------------------------------------------------------------
# 전자책 패키지 (기획안 44·46번)
# ---------------------------------------------------------------------------
class TestEbook:
    @pytest.mark.parametrize(
        ("last", "size", "complete", "expected"),
        [
            (3, 0, False, []),
            (3, 0, True, [(1, 3)]),
            (5, 2, False, [(1, 2), (3, 4)]),
            (5, 2, True, [(1, 2), (3, 4), (5, 5)]),
            (0, 2, True, []),
        ],
    )
    def test_plan_volumes(self, last, size, complete, expected) -> None:
        assert plan_volumes(last, size, complete) == expected

    def test_nothing_before_completion(self, db, novel, settings) -> None:
        _final(db, novel, 1, "본문")
        report = build_ebooks(db, novel, None, settings=settings)
        assert report.volumes == [] and report.warnings

    def test_volumes_blurb_and_metadata(self, db, novel, settings) -> None:
        for n in (1, 2, 3):
            _final(db, novel, n, f"{n}화 본문이다.\n\n둘째 문단.")
        update_info(novel, author="정결", volume_size=2, price=3000)
        llm = ScriptedNovelist()
        report = build_ebooks(db, novel, llm, settings=settings)
        assert [(v.volume, v.start, v.end, v.built) for v in report.volumes] == [
            (1, 1, 2, True)
        ]
        folder = report.volumes[0].folder
        assert {p.name for p in folder.iterdir()} >= {
            "book.epub",
            "cover.jpg",
            "metadata.xml",
        }
        xml = (folder / "metadata.xml").read_text(encoding="utf-8")
        assert '<price currency="KRW">3000</price>' in xml
        assert "<keyword>회귀</keyword>" in xml and "<status>serializing</status>" in xml
        # LLM이 쓴 소개는 저장돼 다음 권에도 쓴다
        info = get_info(novel)
        assert info.keywords == ["회귀"] and "회귀한다" in info.description
        # 같은 권은 다시 만들지 않는다
        again = build_ebooks(db, novel, llm, settings=settings)
        assert [v.built for v in again.volumes] == [False]
        assert llm.stage_counts["blurb"] == 1
        # 사람이 고친 값은 덮어쓰지 않는다
        update_info(novel, description="사람이 쓴 소개")
        build_ebooks(db, novel, llm, settings=settings, force=True)
        assert get_info(novel).description == "사람이 쓴 소개"

    def test_completed_novel_gets_whole_book(self, db, novel, settings) -> None:
        for n in (1, 2, 3):
            _final(db, novel, n, "본문")
        novel.status = "completed"
        report = build_ebooks(db, novel, None, settings=settings)
        assert [(v.start, v.end) for v in report.volumes] == [(1, 3)]
        xml = (report.volumes[0].folder / "metadata.xml").read_text(encoding="utf-8")
        assert "<status>completed</status>" in xml and "<price" not in xml

    def test_explicit_range_and_gap(self, db, novel, settings) -> None:
        from novel_factory.errors import NovelFactoryError

        for n in (1, 2, 4):
            _final(db, novel, n, "본문")
        report = build_ebooks(db, novel, None, settings=settings, episode_range=(1, 2))
        assert report.volumes[0].folder.name == "ep0001-0002"
        with pytest.raises(NovelFactoryError, match="3"):
            build_ebooks(db, novel, None, settings=settings, episode_range=(1, 4))


# ---------------------------------------------------------------------------
# 출판 (기획안 45번)
# ---------------------------------------------------------------------------
def _webhook_app(fail: int = 0) -> FastAPI:
    app = FastAPI()
    state = {"posts": [], "fail": fail}
    app.state.hook = state

    @app.post("/hook")
    async def hook(request: Request):
        if request.headers.get("content-type", "").startswith("multipart/"):
            form = await request.form()
            state["posts"].append({"meta": form["meta"], "files": sorted(form.keys())})
        else:
            state["posts"].append(await request.json())
        state["auth"] = request.headers.get("authorization")
        if state["fail"] > 0:
            state["fail"] -= 1
            from fastapi.responses import JSONResponse

            return JSONResponse({"error": "busy"}, status_code=500)
        return {"id": f"ext-{len(state['posts'])}", "url": "https://example.invalid/p/1"}

    return app


class TestPublishing:
    def test_files_adapter(self, db, novel, settings) -> None:
        ep = _final(db, novel, 1, "첫 문단.\n\n둘째 문단.", title="첫 거래")
        update_info(novel, author_note="읽어 주셔서 감사합니다.")
        make_cover(db, novel, None, settings=settings)
        [row] = enqueue_episode(db, novel, 1)
        report = process_queue(db, settings=settings)
        assert report.succeeded == [row.id] and row.status == "exported"
        folder = Path(row.location)
        assert (folder / "episode_title.txt").read_text(encoding="utf-8") == "첫 거래\n"
        assert (folder / "episode_body.txt").read_text(
            encoding="utf-8"
        ) == ep.final_text + "\n"
        assert "감사" in (folder / "author_note.txt").read_text(encoding="utf-8")
        assert Image.open(folder / "thumbnail.jpg").size == (
            settings.thumbnail_width,
            settings.thumbnail_height,
        )
        # 같은 회차는 다시 넣지 않는다. republish면 넣는다.
        assert enqueue_episode(db, novel, 1) == []
        assert len(enqueue_episode(db, novel, 1, republish=True)) == 1

    def test_thumbnail_is_made_if_missing(self, db, novel, settings) -> None:
        _final(db, novel, 1, "본문")
        enqueue_episode(db, novel, 1)
        process_queue(db, settings=settings)
        assert (settings.novels_dir / "hoegwi" / "cover" / "thumbnail.jpg").exists()

    def test_only_final_episodes(self, db, novel, settings) -> None:
        from novel_factory.errors import NovelFactoryError

        db.add(Episode(novel_id=novel.id, number=1, title="t", status="held"))
        db.flush()
        with pytest.raises(NovelFactoryError, match="확정"):
            enqueue_episode(db, novel, 1)

    def test_automatic_mode_enqueues_on_final(self, db, novel, settings) -> None:
        novel.publishing_mode = "automatic"
        generate_episode(db, novel, 1, ScriptedNovelist(), settings=settings)
        rows = db.scalars(select(Publication)).all()
        assert [(r.kind, r.episode_number, r.status) for r in rows] == [
            ("episode", 1, "queued")
        ]

    def test_manual_mode_does_not(self, db, novel, settings) -> None:
        generate_episode(db, novel, 1, ScriptedNovelist(), settings=settings)
        assert db.scalars(select(Publication)).all() == []

    def test_webhook_retry_then_success(self, db, novel, settings, monkeypatch) -> None:
        _final(db, novel, 1, "본문")
        app = _webhook_app(fail=1)
        with serve(app) as base_url:
            url = base_url.removesuffix("/v1") + "/hook"
            monkeypatch.setattr(
                settings,
                "publish_targets",
                [{"name": "mine", "type": "webhook", "url": url, "token": "secret"}],
            )
            update_info(novel, targets=["mine"])
            [row] = enqueue_episode(db, novel, 1)
            first = process_queue(db, settings=settings)
            assert first.retrying == [row.id] and row.status == "queued"
            second = process_queue(db, settings=settings)
        assert second.succeeded == [row.id]
        assert row.status == "published" and row.external_id == "ext-2"
        post = app.state.hook["posts"][-1]
        assert post["episode"]["number"] == 1 and post["kind"] == "episode"
        assert "thumbnail_jpeg_base64" in post
        assert app.state.hook["auth"] == "Bearer secret"

    def test_webhook_gives_up(self, db, novel, settings, monkeypatch) -> None:
        _final(db, novel, 1, "본문")
        app = _webhook_app(fail=10)
        with serve(app) as base_url:
            monkeypatch.setattr(
                settings,
                "publish_targets",
                [
                    {
                        "name": "mine",
                        "type": "webhook",
                        "url": base_url.removesuffix("/v1") + "/hook",
                    }
                ],
            )
            monkeypatch.setattr(settings, "publish_max_attempts", 2)
            [row] = enqueue_episode(db, novel, 1, targets=["mine"])
            process_queue(db, settings=settings)
            last = process_queue(db, settings=settings)
        assert last.failed == [row.id] and row.status == "failed" and "500" in row.error

    def test_unknown_target_fails_at_once(self, db, novel, settings) -> None:
        _final(db, novel, 1, "본문")
        [row] = enqueue_episode(db, novel, 1, targets=["없는대상"])
        report = process_queue(db, settings=settings)
        assert report.failed == [row.id] and row.attempts == 1

    def test_ebook_to_webhook(self, db, novel, settings, monkeypatch) -> None:
        for n in (1, 2):
            _final(db, novel, n, "본문")
        update_info(novel, volume_size=2)
        build_ebooks(db, novel, None, settings=settings)
        app = _webhook_app()
        with serve(app) as base_url:
            monkeypatch.setattr(
                settings,
                "publish_targets",
                [
                    {
                        "name": "mine",
                        "type": "webhook",
                        "url": base_url.removesuffix("/v1") + "/hook",
                    }
                ],
            )
            [row] = enqueue_ebook(db, novel, 1, targets=["mine"])
            process_queue(db, settings=settings)
        assert row.status == "published"
        post = app.state.hook["posts"][-1]
        assert post["files"] == ["cover", "epub", "meta", "metadata"]

    def test_auto_publish_builds_volume(self, db, novel, settings) -> None:
        novel.publishing_mode = "automatic"
        update_info(novel, volume_size=2)
        _write_all(db, novel, settings)
        result = auto_publish(db, novel, ScriptedNovelist(), settings=settings)
        assert [v["volume"] for v in result["ebooks"]["volumes"]] == [1]
        kinds = sorted((r.kind, r.status) for r in db.scalars(select(Publication)).all())
        assert kinds == [("ebook", "exported")] + [("episode", "exported")] * 3

    def test_get_adapter_errors(self, settings) -> None:
        from novel_factory.errors import NovelFactoryError

        with pytest.raises(NovelFactoryError, match="설정에 없"):
            get_adapter("x", settings)


# ---------------------------------------------------------------------------
# 완결 검사 (기획안 47번)
# ---------------------------------------------------------------------------
def _codes(result) -> list[str]:
    return [i.code for r in result.report.results.values() for i in r.issues]


class TestCompletion:
    def test_missing_and_open_foreshadowing_block(self, db, novel, settings) -> None:
        _final(db, novel, 1, "도윤과 서연.")
        db.add(
            Foreshadowing(
                novel_id=novel.id,
                code="F001",
                description="검은 봉투",
                setup_episode=1,
                status="OPEN",
            )
        )
        db.flush()
        result = check_completion(db, novel, None, settings=settings)
        assert not result.completed and novel.status != "completed"
        assert {"missing_episodes", "unresolved_foreshadowing"} <= set(_codes(result))
        assert novel.extra["completion"]["completed"] is False

    def test_clean_novel_completes(self, db, novel, settings) -> None:
        for n in (1, 2, 3):
            _final(db, novel, n, "도윤과 서연이 만났다.")
        result = check_completion(db, novel, ScriptedNovelist(), settings=settings)
        assert result.completed and novel.status == "completed"
        # 없는 회차를 가리킨 LLM 지적은 버렸다
        assert "setting_conflict" not in _codes(result)

    def test_llm_problems_block_and_force(self, db, novel, settings) -> None:
        novel.ending = "복수를 완성한다"
        for n in (1, 2, 3):
            _final(db, novel, n, "도윤과 서연이 만났다.")
        llm = ScriptedNovelist(completion_problems=True)
        result = check_completion(db, novel, llm, settings=settings)
        codes = _codes(result)
        assert {"unresolved_conflict", "ending_mismatch", "setting_conflict"} <= set(codes)
        assert not result.completed
        forced = check_completion(db, novel, llm, settings=settings, force=True)
        assert forced.completed and forced.forced and novel.status == "completed"

    def test_disappeared_characters(self, db, novel, settings) -> None:
        novel.planned_episodes = 30
        CharacterRepository(db).add(
            Character(
                novel_id=novel.id, code="C003", name="최민석", role="조연", first_episode=1
            )
        )
        for n in range(1, 31):
            text = "도윤이 걸었다." + (" 서연과 민석이 웃었다." if n <= 5 else "")
            _final(db, novel, n, text)
        result = check_completion(db, novel, None, settings=settings, mark=False)
        issues = {
            i.evidence["character"]: i.severity.value
            for i in result.report.results["Characters"].issues
        }
        assert issues == {"C002": "FAIL", "C003": "WARN"}
        assert novel.status != "completed"

    def test_timeline(self, db, novel, settings) -> None:
        seoyeon = CharacterRepository(db).get_by_code(novel.id, "C002")
        seoyeon.is_alive, seoyeon.exit_episode = False, 1
        db.add_all(
            [
                TimelineEvent(
                    novel_id=novel.id,
                    episode_number=1,
                    occurred_at="2026-03-10",
                    title="실사",
                ),
                TimelineEvent(
                    novel_id=novel.id,
                    episode_number=2,
                    occurred_at="2026-03-01",
                    title="회상",
                ),
                TimelineEvent(
                    novel_id=novel.id, episode_number=3, title="재회", participants=["C002"]
                ),
            ]
        )
        db.flush()
        result = check_completion(db, novel, None, settings=settings, mark=False)
        timeline = {
            i.code: i.severity.value for i in result.report.results["Timeline"].issues
        }
        assert timeline == {"date_regression": "WARN", "dead_participant": "FAIL"}

    def test_episode_fail_left(self, db, novel, settings) -> None:
        reports = {
            "quality": {
                "results": {"Continuity": {"verdict": "FAIL"}, "Hook": {"verdict": "FAIL"}}
            }
        }
        for n in (1, 2, 3):
            _final(db, novel, n, "도윤과 서연.", quality_reports=reports if n == 2 else {})
        result = check_completion(db, novel, None, settings=settings, mark=False)
        sev = sorted(i.severity.value for i in result.report.results["Settings"].issues)
        assert sev == ["FAIL", "WARN"]

    def test_scheduler_runs_completion(self, db, novel, settings) -> None:
        from novel_factory.scheduler import jobs

        novel.planned_episodes = 2
        jobs.update_schedule(novel, enabled=True, episodes_per_run=5)
        db.commit()
        result = jobs.run_novel(novel.id, provider=ScriptedNovelist(), settings=settings)
        assert result.written == [1, 2] and "완결 검사" in result.paused
        assert result.completion  # 요약이 남는다


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------
class TestApi:
    def test_create_novel_like_plan_example(self, client) -> None:
        r = client.post(
            "/novels",
            json={
                "genre": "현대판타지",
                "episodes": 250,
                "characters_per_episode": 5000,
                "publishing_mode": "automatic",
            },
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["slug"].startswith("novel-") and body["title"] == "제목 미정"
        bad = client.post("/novels", json={"genre": "x", "publishing_mode": "sometimes"})
        assert bad.status_code == 422

    def test_publishing_flow(self, client) -> None:
        assert (
            client.post(
                "/novels",
                json={"slug": "p", "title": "출판", "genre": "무협", "episodes": 2},
            ).status_code
            == 201
        )
        r = client.put(
            "/novels/p/publishing",
            json={
                "author": "정결",
                "price": 1000,
                "volume_size": 2,
                "publishing_mode": "automatic",
            },
        )
        assert r.status_code == 200 and r.json()["publishing_mode"] == "automatic"
        assert (
            client.put("/novels/p/publishing", json={"targets": ["없음"]}).status_code
            == 422
        )

        r = client.post("/novels/p/cover", params={"use_image": False})
        assert r.status_code == 200 and r.json()["backend"] == "text"
        img = client.get("/novels/p/cover.jpg")
        assert img.status_code == 200 and img.headers["content-type"] == "image/jpeg"

        with get_session_factory()() as s:
            n = NovelRepository(s).require_by_slug("p")
            for k in (1, 2):
                s.add(
                    Episode(
                        novel_id=n.id,
                        number=k,
                        title=f"{k}화",
                        status="final",
                        final_text="본문",
                    )
                )
            s.commit()
        r = client.post("/novels/p/ebooks")
        assert r.status_code == 200 and r.json()["volumes"][0]["built"]
        listing = client.get("/novels/p/ebooks").json()
        assert listing[0]["name"] == "vol01" and "book.epub" in listing[0]["files"]
        epub = client.get("/novels/p/ebooks/vol01/book.epub")
        assert epub.headers["content-type"] == "application/epub+zip"
        assert client.get("/novels/p/ebooks/vol01/secret.txt").status_code == 404

        r = client.post(
            "/novels/p/publications", json={"kind": "episode", "episode_number": 1}
        )
        assert r.status_code == 201 and r.json()[0]["status"] == "queued"
        processed = client.post("/publications/process", params={"novel": "p"}).json()
        assert len(processed["succeeded"]) == 1
        rows = client.get("/novels/p/publications").json()
        assert rows[0]["status"] == "exported"
        assert client.get("/publish-targets").json() == [{"name": "files", "type": "files"}]

    def test_complete_endpoint(self, client) -> None:
        client.post("/novels", json={"slug": "c", "title": "완결", "episodes": 1})
        r = client.post("/novels/c/complete", params={"dry_run": True})
        assert r.status_code == 200 and r.json()["completed"] is False
        assert r.json()["status"] != "completed"
        assert client.get("/novels/c/completion").json()["verdict"] == "FAIL"
        forced = client.post("/novels/c/complete", params={"force": True}).json()
        assert forced["completed"] and forced["forced"] and forced["status"] == "completed"
