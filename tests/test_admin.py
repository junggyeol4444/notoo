"""관리자 화면(기획안 48~50번)과 그 화면이 쓰는 조회·수정 API.

화면 자체는 브라우저로 확인했다(개발 중 Playwright로 모든 탭을 열고 콘솔 오류,
실패한 요청, 모바일 가로 넘침이 없는지, 슬라이더·Bible 저장이 새로고침 뒤에도
남는지 봤다). 여기서는 정적 파일이 제대로 나가는지와 API를 검사한다.
"""

from __future__ import annotations

import re

import pytest
from fake_llm import ScriptedNovelist

from novel_factory.database.base import get_session_factory
from novel_factory.database.models import Character, Novel, ReferencePatternRow
from novel_factory.database.repositories import (
    CharacterRepository,
    NovelRepository,
    RelationshipRepository,
)
from novel_factory.generation.pipeline import generate_episode


@pytest.fixture
def seeded(client, sample_txt, settings):
    """참고작 하나 + 2화까지 쓴 작품."""
    with open(sample_txt, "rb") as f:
        ref = client.post(
            "/references/upload",
            files={"file": ("a.txt", f)},
            data={"title": "참고A", "genre": "현대판타지"},
        ).json()
    client.post(f"/references/{ref['reference_id']}/analyze", json={})
    with get_session_factory()() as s:
        n = NovelRepository(s).add(
            Novel(
                slug="adm",
                title="관리",
                genre="현대판타지",
                planned_episodes=3,
                target_chars_per_episode=3000,
            )
        )
        chars = CharacterRepository(s)
        a = chars.add(
            Character(
                novel_id=n.id, code="C001", name="김도윤", role="주인공", first_episode=1
            )
        )
        b = chars.add(
            Character(
                novel_id=n.id, code="C002", name="박서연", role="주요조연", first_episode=1
            )
        )
        s.flush()
        RelationshipRepository(s).record(n.id, a.id, b.id, state="경계", from_episode=1)
        s.add(
            ReferencePatternRow(
                pattern_id="P_T1",
                genre="현대판타지",
                aspect="pacing",
                instruction="첫 사건을 1화에 둔다",
            )
        )
        llm = ScriptedNovelist()
        for k in (1, 2):
            generate_episode(s, n, k, llm, settings=settings)
        s.commit()
    return ref["reference_id"]


class TestStatic:
    def test_index_and_assets(self, client) -> None:
        r = client.get("/admin/")
        assert r.status_code == 200 and "text/html" in r.headers["content-type"]
        for asset in re.findall(r'(?:src|href)="([a-z.]+\.(?:js|css))"', r.text):
            assert client.get(f"/admin/{asset}").status_code == 200, asset

    def test_screen_calls_existing_api_paths(self, client) -> None:
        """화면이 부르는 API 경로가 실제로 있는지 (오타로 404가 나지 않게)."""
        js = client.get("/admin/app.js").text
        roots = "novels|references|projects|scheduler|patterns|publications|publish-targets"
        called = set(re.findall(rf"[`\"](/(?:{roots})[^`\"?]*)", js))
        normalized = {re.sub(r"\$\{[^}]+\}", "{x}", p) for p in called}
        known = {
            re.sub(r"\{[^}]+\}", "{x}", p)
            for p in client.get("/openapi.json").json()["paths"]
        }
        missing = sorted(p for p in normalized if p not in known)
        assert missing == [], missing
        assert len(normalized) > 20  # 추출이 실제로 됐는지


class TestBrowseApi:
    def test_bible_patch(self, client, seeded) -> None:
        r = client.patch("/novels/adm", json={"mood": "차가움", "planned_episodes": 5})
        assert r.status_code == 200 and r.json()["mood"] == "차가움"
        bible = client.get("/novels/adm/bible").json()
        assert bible["planned_episodes"] == 5 and bible["publishing_mode"] == "manual"
        assert client.patch("/novels/adm", json={"planned_episodes": -1}).status_code == 422

    def test_lists(self, client, seeded) -> None:
        rel = client.get("/novels/adm/relationships").json()
        assert rel[0]["source"] == "김도윤" and rel[0]["state"] == "경계"
        fs = client.get("/novels/adm/foreshadowings").json()
        assert fs and {"code", "status", "planned_payoff"} <= set(fs[0])
        opened = client.get(
            "/novels/adm/foreshadowings", params={"status_filter": "OPEN"}
        ).json()
        assert all(f["status"] == "OPEN" for f in opened)
        assert client.get("/novels/adm/style-bible").status_code == 404
        sim = client.get("/novels/adm/similarity").json()
        assert [x["number"] for x in sim] == [1, 2]
        pats = client.get("/patterns", params={"genre": "현대판타지"}).json()
        assert pats[0]["pattern_id"] == "P_T1"
        timeline = client.get("/novels/adm/timeline").json()
        assert "participants" in timeline[0]
        chars = client.get("/novels/adm/characters").json()
        assert "exit_episode" in chars[0]

    def test_reference_settings(self, client, seeded) -> None:
        """기획안 50번: 항목별 참고 강도를 보고 고치고 끊는다."""
        ref_id = seeded
        r = client.post(
            "/novels/adm/references",
            json={"reference_id": ref_id, "weights": {"pacing": 0.8, "style": 0}},
        )
        assert r.status_code == 201
        data = client.get("/novels/adm/references").json()
        assert "character_structure" in data["aspects"]
        link = data["links"][0]
        assert link["weights"]["pacing"] == 0.8 and link["weights"]["style"] == 0.0
        assert link["weights"]["emotion"] == link["default_weight"]
        assert client.delete(f"/novels/adm/references/{ref_id}").status_code == 204
        assert client.get("/novels/adm/references").json()["links"] == []
        assert client.delete(f"/novels/adm/references/{ref_id}").status_code == 404

    def test_upload_keeps_title(self, client, seeded) -> None:
        assert client.get(f"/references/{seeded}").json()["status"] == "analyzed"
        assert client.get("/references").json()[0]["title"] == "참고A"
