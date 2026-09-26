"""장기 연속 생성 (기획안 55번 Phase 5·6).

    Phase 5  30화 연속 생성 — 설정 오류와 Reference Pattern 적용을 평가한다.
    Phase 6  100화 이상 장기 테스트 — 장기 기억과 복선 회수 성능을 검증한다.

대본 LLM으로 돈다. 글의 품질이 아니라 긴 연재에서 시스템이 무너지지 않는지를 본다:
계획이 목표 비율을 지키는가, 기억이 쌓이고 검색되는가, 복선이 제때 닫히는가.

100화 테스트는 느려서(수십 초) 기본 실행에서 빠진다.
    pytest -m long
"""

from __future__ import annotations

import time

import pytest
from fake_llm import ScriptedNovelist

from novel_factory.database.base import get_session_factory
from novel_factory.database.models import Character, Novel
from novel_factory.database.repositories import CharacterRepository, NovelRepository
from novel_factory.generation.guidance import CLIFFHANGER_KINDS, GenreGuidance
from novel_factory.generation.pipeline import plan_story
from novel_factory.quality.evaluation import evaluate_novel
from novel_factory.scheduler import jobs


def _novel(db, episodes: int) -> Novel:
    n = NovelRepository(db).add(
        Novel(
            slug=f"long{episodes}",
            title="장기",
            genre="현대판타지",
            planned_episodes=episodes,
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


def _run(db, settings, episodes: int, llm: ScriptedNovelist) -> tuple[dict, float]:
    novel = _novel(db, episodes)
    plan_story(db, novel, llm)
    jobs.update_schedule(novel, enabled=True, continuous=True)
    db.commit()
    started = time.perf_counter()
    result = jobs.run_novel(novel.id, provider=llm, settings=settings)
    elapsed = time.perf_counter() - started
    assert not result.error, result.error
    assert result.written == list(range(1, episodes + 1)), result.as_dict()
    with get_session_factory()() as s:
        report = evaluate_novel(s, s.get(Novel, novel.id))
    return report, elapsed


def test_phase5_thirty_episodes(db, settings) -> None:
    report, _ = _run(db, settings, 30, ScriptedNovelist())
    assert report["episodes"]["final"] == 30 and not report["episodes"]["held"]

    # 설정 오류: 기록이 모이고, 고친 뒤 남은 Continuity·Logic FAIL 회차가 없다
    errors = report["setting_errors"]
    assert errors["episodes_fail_after_fix"] == {}
    assert errors["memory_rejected"] > 0  # 대본 LLM이 일부러 섞은 잘못된 항목을 버렸다

    # Reference Pattern 적용: 계획한 클리프행어 비율이 목표를 따라간다
    rate = report["pattern"]["cliffhanger_rate"]
    target = GenreGuidance(source="defaults").cliffhanger_rate
    assert abs(rate["planned"] - target) <= 0.05, rate
    assert report["pattern"]["length"]["within_30pct"] == 30
    kinds = report["pattern"]["event_kinds"]
    assert kinds.get("major", 0) >= 1 and kinds.get("minor", 0) >= 1

    # 장기 기억: 2화부터는 앞 회차 장면을 찾아 썼다
    assert report["memory"]["episodes_with_recalled_scenes"] >= 28


def test_planned_hooks_cycle_through_kinds(db, settings) -> None:
    """회귀: 우연히 훅처럼 끝난 회차를 이력으로 세면 6화 이후 훅 지시가 끊겼다."""
    novel = _novel(db, 20)
    llm = ScriptedNovelist()
    from novel_factory.generation.pipeline import generate_episode

    planned = []
    for n in range(1, 21):
        hook = generate_episode(db, novel, n, llm, settings=settings).episode.outline[
            "directives"
        ]["hook"]
        planned.append(hook["kind"] if hook["required"] else None)
    used = [k for k in planned if k]
    assert len(used) >= 15  # 목표 82%
    assert set(used) == set(CLIFFHANGER_KINDS)


@pytest.mark.long
def test_phase6_hundred_episodes(db, settings) -> None:
    llm = ScriptedNovelist(resolve_foreshadowing=True)
    report, elapsed = _run(db, settings, 100, llm)
    print(f"100화: {elapsed:.1f}초")
    fs = report["foreshadowing"]
    assert fs["planted"] >= 90
    # 복선 회수: 전부 닫혔고, 예정을 넘긴 채 열린 것이 없다
    assert fs["open"] == 0 and fs["overdue_open"] == []
    assert fs["resolved_on_time"] == fs["resolved"]
    assert report["memory"]["episodes_with_recalled_scenes"] >= 98
    assert report["episodes"]["final"] == 100
    rate = report["pattern"]["cliffhanger_rate"]
    assert abs(rate["planned"] - GenreGuidance(source="defaults").cliffhanger_rate) <= 0.03
