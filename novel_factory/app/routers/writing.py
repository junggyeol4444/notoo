"""집필 API (기획안 28~31, 34~39번).

    GET  /novels/{slug}/guidance                    참고작에서 온 목표값과 사건 일정
    POST /novels/{slug}/story/plan                  사건 일정 + Arc 설계
    POST /novels/{slug}/episodes/{n}/plan           회차 계획
    POST /novels/{slug}/episodes/{n}/scenes         장면 설계
    POST /novels/{slug}/episodes/{n}/write          장면 단위 집필
    POST /novels/{slug}/episodes/{n}/check          품질 검사 (fix=true면 FAIL 장면 수정)
    POST /novels/{slug}/episodes/{n}/memory         기억 갱신
    POST /novels/{slug}/episodes/{n}/generate       위 단계를 한 번에
    GET  /novels/{slug}/episodes                    회차 목록
    GET  /novels/{slug}/episodes/{n}                회차 상세

회차 하나를 만드는 데 LLM 호출이 열 번 넘게 들고, 로컬 모델이면 몇 분이 걸린다.
지금은 요청이 끝날 때까지 기다리는 동기 방식이다. 자동 집필 스케줄러(기획안 42번)를
붙일 때 큐로 옮긴다.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from novel_factory.app.deps import get_db, get_llm, get_novel
from novel_factory.config import get_settings
from novel_factory.database.models import Novel
from novel_factory.database.repositories import EpisodeRepository
from novel_factory.errors import LLM_UNAVAILABLE_MESSAGE
from novel_factory.generation.episode_planner import load_schedule, plan_episode
from novel_factory.generation.guidance import load_guidance
from novel_factory.generation.memory_update import apply_memory, extract_memory
from novel_factory.generation.pipeline import (
    generate_episode,
    plan_story,
    similarity_index_for,
)
from novel_factory.generation.scene_planner import plan_scenes
from novel_factory.generation.storage import save_episode_files
from novel_factory.generation.writer import write_episode
from novel_factory.llm.base import LLMProvider
from novel_factory.quality.runner import check_and_fix

router = APIRouter(prefix="/novels", tags=["writing"])


def _require_llm(llm: LLMProvider) -> LLMProvider:
    if not llm.available:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, LLM_UNAVAILABLE_MESSAGE)
    return llm


def _episode_or_404(db: Session, novel: Novel, number: int):
    episode = EpisodeRepository(db).get_by_number(novel.id, number)
    if episode is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"{number}화가 없습니다.")
    return episode


@router.get("/{slug}/guidance")
def guidance(
    novel: Novel = Depends(get_novel), db: Session = Depends(get_db)
) -> dict[str, object]:
    guide = load_guidance(db, novel)
    return {
        "guidance": guide.as_dict(),
        "pattern_instructions": guide.pattern_instructions,
        "schedule": load_schedule(novel, guide).as_dict(),
    }


@router.post("/{slug}/story/plan")
def story_plan(
    replace: bool = False,
    use_llm: bool = True,
    novel: Novel = Depends(get_novel),
    db: Session = Depends(get_db),
    llm: LLMProvider = Depends(get_llm),
) -> dict[str, object]:
    """사건 일정과 Arc 골격은 LLM 없이도 만든다. LLM이 있으면 Arc 내용을 채운다."""
    provider = llm if use_llm and llm.available else None
    result = plan_story(db, novel, provider, replace=replace)
    return {
        "used_llm": result.used_llm,
        "attempts": result.attempts,
        "warnings": result.warnings,
        "arcs": [
            {
                "order": a.order,
                "name": a.name,
                "start_episode": a.start_episode,
                "end_episode": a.end_episode,
                "goal": a.goal,
                "conflict": a.conflict,
                "emotion_target": a.emotion_target,
            }
            for a in result.arcs
        ],
        "schedule": (novel.extra or {}).get("event_schedule"),
    }


@router.post("/{slug}/episodes/{number}/plan")
def episode_plan(
    number: int,
    replace: bool = False,
    novel: Novel = Depends(get_novel),
    db: Session = Depends(get_db),
    llm: LLMProvider = Depends(get_llm),
) -> dict[str, object]:
    result = plan_episode(db, novel, number, _require_llm(llm), replace=replace)
    return {
        "number": number,
        "plan": result.plan,
        "directives": result.directives.as_dict(),
        "budget": result.budget.as_dict(),
        "attempts": result.attempts,
        "warnings": result.warnings,
    }


@router.post("/{slug}/episodes/{number}/scenes")
def episode_scenes(
    number: int,
    novel: Novel = Depends(get_novel),
    db: Session = Depends(get_db),
    llm: LLMProvider = Depends(get_llm),
) -> dict[str, object]:
    episode = _episode_or_404(db, novel, number)
    result = plan_scenes(db, novel, episode, _require_llm(llm))
    return {"number": number, "scenes": result.scenes, "warnings": result.warnings}


@router.post("/{slug}/episodes/{number}/write")
def episode_write(
    number: int,
    novel: Novel = Depends(get_novel),
    db: Session = Depends(get_db),
    llm: LLMProvider = Depends(get_llm),
) -> dict[str, object]:
    episode = _episode_or_404(db, novel, number)
    result = write_episode(
        db,
        novel,
        episode,
        _require_llm(llm),
        similarity_index=similarity_index_for(db, novel),
    )
    return {
        "number": number,
        "char_count": result.chars,
        "scenes": [d.as_dict() for d in result.scenes],
        "similarity": result.similarity,
        "warnings": result.warnings,
    }


@router.post("/{slug}/episodes/{number}/check")
def episode_check(
    number: int,
    fix: bool = False,
    logic: bool | None = None,
    reader: bool | None = None,
    novel: Novel = Depends(get_novel),
    db: Session = Depends(get_db),
    llm: LLMProvider = Depends(get_llm),
) -> dict[str, object]:
    """품질 검사 (기획안 34~38번).

    LLM이 없으면 규칙 기반 검사(Continuity, Similarity, Style, Hook)만 하고
    Logic·Reader는 SKIPPED로 남긴다. logic/reader를 비우면 설정값을 따른다.
    확정된 회차는 기억 갱신이 이미 끝났으므로 검사만 한다. 고치려면 다시 생성한다.
    """
    episode = _episode_or_404(db, novel, number)
    if not (episode.final_text or "").strip():
        raise HTTPException(status.HTTP_409_CONFLICT, f"{number}화에 원고가 없습니다.")
    if fix and episode.status == "final":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"{number}화는 확정돼 기억 갱신까지 끝났습니다. 원고를 고치면 장기기억과 "
            "어긋나므로 검사만 합니다. 고치려면 generate?replace=true로 다시 만드세요.",
        )
    provider = _require_llm(llm) if fix else (llm if llm.available else None)
    outcome = check_and_fix(
        db,
        novel,
        episode,
        provider,
        similarity_index=similarity_index_for(db, novel),
        fix=fix,
        logic=logic,
        reader=reader,
    )
    return {"number": number, "char_count": episode.char_count, **outcome.as_dict()}


@router.post("/{slug}/episodes/{number}/memory")
def episode_memory(
    number: int,
    novel: Novel = Depends(get_novel),
    db: Session = Depends(get_db),
    llm: LLMProvider = Depends(get_llm),
) -> dict[str, object]:
    episode = _episode_or_404(db, novel, number)
    delta = extract_memory(db, novel, episode, _require_llm(llm))
    applied = apply_memory(db, novel, episode, delta)
    folder = save_episode_files(novel, episode, get_settings())
    return {"number": number, "applied": applied.as_dict(), "folder": str(folder)}


@router.post("/{slug}/episodes/{number}/generate")
def episode_generate(
    number: int,
    replace: bool = False,
    novel: Novel = Depends(get_novel),
    db: Session = Depends(get_db),
    llm: LLMProvider = Depends(get_llm),
) -> dict[str, object]:
    result = generate_episode(db, novel, number, _require_llm(llm), replace=replace)
    return result.as_dict()


@router.get("/{slug}/episodes")
def list_episodes(
    novel: Novel = Depends(get_novel), db: Session = Depends(get_db)
) -> list[dict[str, object]]:
    return [
        {
            "number": e.number,
            "title": e.title,
            "status": e.status,
            "char_count": e.char_count,
            "hook_type": e.hook_type,
            "summary": e.summary,
        }
        for e in EpisodeRepository(db).recent(novel.id, count=100_000)
    ]


@router.get("/{slug}/episodes/{number}")
def episode_detail(
    number: int,
    include_text: bool = True,
    novel: Novel = Depends(get_novel),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    e = _episode_or_404(db, novel, number)
    payload: dict[str, object] = {
        "number": e.number,
        "title": e.title,
        "status": e.status,
        "char_count": e.char_count,
        "hook_type": e.hook_type,
        "summary": e.summary,
        "outline": e.outline,
        "scenes": e.scenes,
        "quality_reports": e.quality_reports,
        "memory_delta": e.memory_delta,
        "reference_usage": e.reference_usage,
    }
    if include_text:
        payload["text"] = e.final_text
    return payload
