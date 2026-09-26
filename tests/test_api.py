"""API 테스트 (기획안 53·54번)."""

from __future__ import annotations

from pathlib import Path

import pytest

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _ok(response, *expected: int):
    codes = expected or (200, 201)
    assert response.status_code in codes, f"{response.status_code} {response.text[:400]}"
    return response.json()


@pytest.fixture
def analyzed_reference(client):
    """등록 + 분석이 끝난 참고작 하나."""
    created = _ok(
        client.post(
            "/references",
            json={
                "path": str(FIXTURES / "sample_novel.txt"),
                "title": "참고작 1",
                "genre": "현대판타지",
            },
        )
    )
    _ok(client.post(f"/references/{created['reference_id']}/analyze", json={}))
    return created["reference_id"]


@pytest.fixture
def novel_slug(client):
    _ok(
        client.post(
            "/novels",
            json={
                "slug": "hoegwi",
                "title": "회귀한 인수합병가",
                "genre": "현대판타지",
                "logline": "죽은 인수합병가가 회귀해 다시 판을 짠다.",
                "episodes": 250,
                "characters_per_episode": 5000,
            },
        )
    )
    return "hoegwi"


class TestHealth:
    def test_reports_configuration(self, client) -> None:
        body = _ok(client.get("/health"))
        assert body["status"] == "ok"
        from conftest import TEST_DATABASE_URL

        expected = TEST_DATABASE_URL.split("://")[0].split("+")[0] or "sqlite"
        assert body["database"] == expected
        assert body["llm_configured"] is False
        assert ".epub" in body["supported_formats"]

    def test_does_not_probe_llm_by_default(self, client) -> None:
        """헬스체크가 매번 외부 서버를 기다리면 안 된다."""
        assert _ok(client.get("/health"))["llm_available"] is None


class TestReferences:
    def test_register_and_analyze(self, client) -> None:
        created = _ok(
            client.post(
                "/references",
                json={"path": str(FIXTURES / "sample_novel.epub"), "genre": "현대판타지"},
            )
        )
        assert created["source_format"] == "epub"
        assert created["status"] == "imported"

        analyzed = _ok(
            client.post(f"/references/{created['reference_id']}/analyze", json={})
        )
        summary = analyzed["profile"]["summary"]
        assert summary["episode_count"] > 1
        assert 0.0 <= summary["cliffhanger_rate"] <= 1.0

    def test_duplicate_content_is_rejected(self, client) -> None:
        payload = {"path": str(FIXTURES / "sample_novel.txt")}
        _ok(client.post("/references", json=payload))
        assert client.post("/references", json=payload).status_code == 409

    def test_missing_file(self, client) -> None:
        assert (
            client.post("/references", json={"path": "/없는/파일.txt"}).status_code == 400
        )

    def test_unsupported_format(self, client, tmp_path) -> None:
        bad = tmp_path / "novel.rtf"
        bad.write_text("내용", encoding="utf-8")
        response = client.post("/references", json={"path": str(bad)})
        assert response.status_code in (400, 415)

    def test_upload(self, client) -> None:
        data = (FIXTURES / "sample_novel.txt").read_bytes()
        response = client.post(
            "/references/upload",
            files={"file": ("novel.txt", data, "text/plain")},
        )
        assert _ok(response)["source_format"] == "txt"

    def test_analyze_missing_reference(self, client) -> None:
        assert client.post("/references/REF999/analyze", json={}).status_code == 404

    def test_profile_carries_no_character_names(self, client, analyzed_reference) -> None:
        """기획안 56번: 참고작의 고유명사는 저장하지 않는다."""
        body = _ok(client.get(f"/references/{analyzed_reference}"))
        serialized = str(body["profile"])
        for name in ("김도윤", "박서연", "최민석", "강태호", "이준혁"):
            assert name not in serialized

    def test_aggregate_and_derive_patterns(self, client, analyzed_reference) -> None:
        body = _ok(
            client.post(
                "/references/aggregate?genre=현대판타지&store_patterns=true",
                json=[analyzed_reference],
            )
        )
        assert body["reference_count"] == 1
        assert body["patterns"]

    def test_aggregate_requires_analyzed(self, client) -> None:
        created = _ok(
            client.post("/references", json={"path": str(FIXTURES / "sample_novel.md")})
        )
        response = client.post("/references/aggregate", json=[created["reference_id"]])
        assert response.status_code == 400


class TestSimilarity:
    def test_copy_is_flagged(self, client, analyzed_reference) -> None:
        text = (FIXTURES / "sample_novel.txt").read_text(encoding="utf-8")[2000:4000]
        body = _ok(client.post("/references/similarity", json={"text": text}))
        assert body["verdict"] == "FAIL"

    def test_unrelated_passes(self, client, analyzed_reference) -> None:
        body = _ok(
            client.post(
                "/references/similarity",
                json={"text": "빗줄기가 유리창을 두드렸다. 소년은 언덕을 올랐다. " * 40},
            )
        )
        assert body["verdict"] == "PASS"

    def test_no_index_is_not_an_error(self, client) -> None:
        body = _ok(client.post("/references/similarity", json={"text": "아무 글"}))
        assert body["verdict"] == "PASS"


class TestNovels:
    def test_create_and_fetch(self, client, novel_slug) -> None:
        body = _ok(client.get(f"/novels/{novel_slug}"))
        assert body["planned_episodes"] == 250

    def test_duplicate_slug(self, client, novel_slug) -> None:
        response = client.post("/novels", json={"slug": novel_slug, "title": "중복"})
        assert response.status_code == 409

    def test_missing_novel(self, client) -> None:
        assert client.get("/novels/없는작품").status_code == 404

    def test_link_reference_with_weights(
        self, client, novel_slug, analyzed_reference
    ) -> None:
        body = _ok(
            client.post(
                f"/novels/{novel_slug}/references",
                json={
                    "reference_id": analyzed_reference,
                    "default_weight": 0.3,
                    "weights": {"pacing": 1.0, "style": 0.0},
                },
            )
        )
        assert body["weights"]["style"] == 0.0

    def test_genre_profile_needs_analyzed_reference(self, client, novel_slug) -> None:
        assert client.get(f"/novels/{novel_slug}/genre-profile").status_code == 400

    def test_genre_profile(self, client, novel_slug, analyzed_reference) -> None:
        _ok(
            client.post(
                f"/novels/{novel_slug}/references",
                json={"reference_id": analyzed_reference},
            )
        )
        body = _ok(client.get(f"/novels/{novel_slug}/genre-profile"))
        assert body["reference_count"] == 1
        assert body["patterns"]

    def test_arc_validation(self, client, novel_slug) -> None:
        response = client.post(
            f"/novels/{novel_slug}/arcs",
            json={"order": 1, "name": "역순 Arc", "start_episode": 50, "end_episode": 10},
        )
        assert response.status_code == 422


class TestMemoryEndpoints:
    @pytest.fixture
    def populated(self, client, novel_slug):
        _ok(
            client.post(
                f"/novels/{novel_slug}/characters",
                json={"name": "김도윤", "role": "주인공", "first_episode": 1},
            )
        )
        _ok(
            client.post(
                f"/novels/{novel_slug}/characters",
                json={"name": "강태호", "role": "적대자", "first_episode": 90},
            )
        )
        _ok(
            client.post(
                f"/novels/{novel_slug}/characters/C001/knowledge",
                json={
                    "fact_key": "regression",
                    "fact": "자신이 회귀했다는 사실",
                    "knows": True,
                    "learned_episode": 1,
                },
            )
        )
        _ok(
            client.post(
                f"/novels/{novel_slug}/foreshadowings",
                json={
                    "description": "회장의 검은 수첩",
                    "setup_episode": 12,
                    "planned_payoff": 55,
                },
            )
        )
        _ok(
            client.post(
                f"/novels/{novel_slug}/timeline",
                json={"title": "회귀", "occurred_at": "2026-03-01", "episode_number": 1},
            )
        )
        _ok(
            client.post(
                f"/novels/{novel_slug}/world",
                json={"category": "회사", "name": "세광전자", "first_episode": 20},
            )
        )
        return novel_slug

    def test_auto_assigned_character_codes(self, client, populated) -> None:
        codes = [c["code"] for c in _ok(client.get(f"/novels/{populated}/characters"))]
        assert codes == ["C001", "C002"]

    def test_context_excludes_future_material(self, client, populated) -> None:
        ctx = _ok(client.get(f"/novels/{populated}/context/42"))
        assert [c["name"] for c in ctx["characters"]] == ["김도윤"]
        assert [w["name"] for w in ctx["world"]] == ["세광전자"]

    def test_context_as_prompt(self, client, populated) -> None:
        body = _ok(client.get(f"/novels/{populated}/context/42?as_prompt=true"))
        assert "Novel Bible" in body["prompt"]
        assert "42화" in body["prompt"]

    def test_context_rejects_episode_zero(self, client, populated) -> None:
        assert client.get(f"/novels/{populated}/context/0").status_code == 422

    def test_open_and_resolve_foreshadowing(self, client, populated) -> None:
        assert [
            f["code"] for f in _ok(client.get(f"/novels/{populated}/foreshadowings/open"))
        ] == ["F001"]
        assert [
            f["code"]
            for f in _ok(
                client.get(f"/novels/{populated}/foreshadowings/open?overdue_by=60")
            )
        ] == ["F001"]
        resolved = _ok(
            client.post(f"/novels/{populated}/foreshadowings/F001/resolve?episode=55")
        )
        assert resolved["status"] == "RESOLVED"
        assert _ok(client.get(f"/novels/{populated}/foreshadowings/open")) == []

    def test_timeline_cutoff(self, client, populated) -> None:
        _ok(
            client.post(
                f"/novels/{populated}/timeline",
                json={"title": "대성 반격", "episode_number": 95},
            )
        )
        visible = _ok(client.get(f"/novels/{populated}/timeline?up_to_episode=10"))
        assert [t["title"] for t in visible] == ["회귀"]

    def test_relationship_requires_existing_characters(self, client, populated) -> None:
        response = client.post(
            f"/novels/{populated}/relationships",
            json={
                "source_code": "C001",
                "target_code": "C999",
                "from_episode": 1,
                "state": "경계",
            },
        )
        assert response.status_code == 404

    def test_style_bible_roundtrip(self, client, populated) -> None:
        _ok(
            client.put(
                f"/novels/{populated}/style-bible",
                json={"target_sentence_chars": 18.0, "forbidden": ["과도한 비유"]},
            )
        )
        prompt = _ok(client.get(f"/novels/{populated}/context/42?as_prompt=true"))["prompt"]
        assert "과도한 비유" in prompt
