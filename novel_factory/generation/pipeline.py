"""회차 생성 파이프라인 (기획안 4·42번 중 Phase 3 범위).

    Episode Planner
    ↓ Reference Pattern Retrieval / Context Retrieval
    ↓ Scene Planning
    ↓ Draft (장면 단위, 장면마다 Similarity Check → 겹치는 장면만 재작성)
    ↓ Continuity / Logic / Similarity / Style / Hook 검사
    ↓ Rewrite (FAIL 장면만, quality_fix_rounds번까지)
    ↓ (Reader Simulation, 설정에서 켰을 때)
    ↓ Final
    ↓ Memory Update
    ↓ episode_NNN/ 폴더 저장

검사와 자동 수정은 quality/runner.py에 있다. 기억 갱신은 고친 원고로 한다.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from novel_factory.config import Settings, get_settings
from novel_factory.database.models import (
    Character,
    CharacterKnowledge,
    Episode,
    Foreshadowing,
    MemoryChunk,
    Novel,
    Relationship,
    TimelineEvent,
    WorldEntry,
)
from novel_factory.database.repositories import (
    EpisodeRepository,
    ReferenceLinkRepository,
    ReferenceRepository,
)
from novel_factory.errors import LLM_UNAVAILABLE_MESSAGE, NovelFactoryError
from novel_factory.generation.arc_planner import ArcPlanResult, plan_arcs
from novel_factory.generation.episode_planner import load_schedule, plan_episode
from novel_factory.generation.guidance import build_event_schedule, load_guidance
from novel_factory.generation.memory_update import apply_memory, extract_memory
from novel_factory.generation.scene_planner import plan_scenes
from novel_factory.generation.storage import save_episode_files
from novel_factory.generation.writer import write_episode
from novel_factory.llm.base import LLMProvider
from novel_factory.quality.runner import check_and_fix
from novel_factory.reference.similarity import FingerprintIndex


@dataclass(slots=True)
class EpisodeGenerationResult:
    episode: Episode
    folder: Path | None
    timings: dict[str, float] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    memory: dict[str, object] = field(default_factory=dict)
    quality: dict[str, object] = field(default_factory=dict)

    def as_dict(self) -> dict[str, object]:
        ep = self.episode
        return {
            "number": ep.number,
            "title": ep.title,
            "status": ep.status,
            "char_count": ep.char_count,
            "hook_type": ep.hook_type,
            "summary": ep.summary,
            "folder": str(self.folder) if self.folder else None,
            "timings": {k: round(v, 2) for k, v in self.timings.items()},
            "warnings": self.warnings,
            "memory": self.memory,
            "similarity": (ep.quality_reports or {}).get("similarity"),
            "quality": self.quality,
        }


def similarity_index_for(session: Session, novel: Novel) -> FingerprintIndex:
    """이 작품에 연결된 참고작들의 지문."""
    refs = ReferenceRepository(session)
    ids = []
    for link in ReferenceLinkRepository(session).for_novel(novel.id):
        ref = refs.get(link.reference_id)
        if ref is not None:
            ids.append(ref.reference_id)
    return refs.build_index(ids) if ids else FingerprintIndex()


def rollback_episode_memory(session: Session, novel: Novel, number: int) -> list[str]:
    """그 회차의 Memory Update가 한 일을 정확히 되돌린다.

    apply_memory()가 남긴 되돌리기 기록(만든 행의 id, 바꾼 행의 이전 값)만 쓴다.
    회차 번호로 지우면 안 된다. 사용자가 직접 넣은 1화 등장 주인공, 1화 시간선,
    1화 복선까지 같이 지워진다.

    뒤 회차가 이미 확정됐으면 되돌리지 않는다. 뒤 회차는 이 회차의 사실을
    전제로 쓰였기 때문이다.
    """
    later_final = session.scalar(
        select(Episode.number)
        .where(
            Episode.novel_id == novel.id, Episode.number > number, Episode.status == "final"
        )
        .order_by(Episode.number)
        .limit(1)
    )
    if later_final is not None:
        raise NovelFactoryError(
            f"{later_final}화가 이미 확정돼 있어 {number}화를 다시 만들 수 없습니다. "
            "뒤 회차가 이 회차의 사실을 전제로 쓰였습니다."
        )

    episode = EpisodeRepository(session).get_by_number(novel.id, number)
    applied = ((episode.memory_delta or {}).get("applied") or {}) if episode else {}
    undo = applied.get("undo")
    if not undo:
        return [f"{number}화의 되돌리기 기록이 없어 이전 기억을 그대로 두었습니다."]

    def rows(model, ids):
        return list(session.scalars(select(model).where(model.id.in_(ids)))) if ids else []

    # 바꾼 것을 먼저 되돌리고, 만든 것을 지운다.
    for change in reversed(undo.get("foreshadowing_states", [])):
        item = session.get(Foreshadowing, change["id"])
        if item is not None:
            item.status = change["old"]["status"]
            item.mentions = change["old"]["mentions"]
            item.actual_payoff = change["old"]["actual_payoff"]
    for change in reversed(undo.get("deaths", [])):
        who = session.get(Character, change["character_id"])
        if who is not None:
            who.is_alive = change["old"]["is_alive"]
            who.exit_episode = change["old"]["exit_episode"]
            who.exit_reason = change["old"]["exit_reason"]
    for change in reversed(undo.get("items", [])):
        who = session.get(Character, change["character_id"])
        if who is not None:
            who.items = change["old"]
    for change in undo.get("knowledge_updated", []):
        row = session.get(CharacterKnowledge, change["id"])
        if row is not None:
            for key, value in change["old"].items():
                setattr(row, key, value)
    for change in undo.get("world_descriptions", []):
        entry = session.get(WorldEntry, change["id"])
        if entry is not None:
            entry.description = change["old"]

    for model, key in (
        (CharacterKnowledge, "knowledge_created"),
        (Relationship, "relationships"),
        (TimelineEvent, "timeline"),
        (WorldEntry, "world"),
        (Foreshadowing, "foreshadowings"),
        (Character, "characters"),
    ):
        for row in rows(model, undo.get(key, [])):
            session.delete(row)

    session.execute(
        delete(MemoryChunk).where(
            MemoryChunk.novel_id == novel.id,
            MemoryChunk.episode_number == number,
            MemoryChunk.kind == "summary",
        )
    )
    session.flush()
    return []


def generate_episode(
    session: Session,
    novel: Novel,
    number: int,
    provider: LLMProvider,
    *,
    settings: Settings | None = None,
    replace: bool = False,
    save_files: bool = True,
) -> EpisodeGenerationResult:
    """한 회차를 계획부터 기억 갱신까지 끝낸다."""
    cfg = settings or get_settings()
    if not provider.available:
        raise NovelFactoryError(LLM_UNAVAILABLE_MESSAGE)
    warnings: list[str] = []
    timings: dict[str, float] = {}

    existing = EpisodeRepository(session).get_by_number(novel.id, number)
    if existing is not None and existing.status == "final":
        if not replace:
            raise NovelFactoryError(
                f"{number}화는 이미 확정됐습니다. 다시 만들려면 replace=True를 주세요."
            )
        warnings.extend(rollback_episode_memory(session, novel, number))

    guidance = load_guidance(session, novel)

    started = time.perf_counter()
    planned = plan_episode(
        session, novel, number, provider, guidance=guidance, settings=cfg, replace=True
    )
    timings["plan"] = time.perf_counter() - started
    warnings.extend(planned.warnings)

    started = time.perf_counter()
    scenes = plan_scenes(
        session, novel, planned.episode, provider, guidance=guidance, settings=cfg
    )
    timings["scenes"] = time.perf_counter() - started
    warnings.extend(scenes.warnings)

    started = time.perf_counter()
    index = similarity_index_for(session, novel)
    draft = write_episode(
        session,
        novel,
        planned.episode,
        provider,
        guidance=guidance,
        settings=cfg,
        similarity_index=index,
    )
    timings["write"] = time.perf_counter() - started
    warnings.extend(draft.warnings)

    started = time.perf_counter()
    quality = check_and_fix(
        session,
        novel,
        planned.episode,
        provider,
        settings=cfg,
        similarity_index=index,
        guidance=guidance,
    )
    timings["quality"] = time.perf_counter() - started
    warnings.extend(f"품질 검사: {w}" for w in quality.warnings)
    if quality.report.verdict == "FAIL":
        warnings.append(
            "품질 검사: 고친 뒤에도 FAIL이 남았습니다.\n" + quality.report.summary()
        )

    started = time.perf_counter()
    delta = extract_memory(session, novel, planned.episode, provider, settings=cfg)
    applied = apply_memory(session, novel, planned.episode, delta)
    timings["memory"] = time.perf_counter() - started
    warnings.extend(f"기억 갱신: {r}" for r in applied.rejected)

    folder = save_episode_files(novel, planned.episode, cfg) if save_files else None
    if novel.status == "planning":
        novel.status = "writing"
    session.flush()
    return EpisodeGenerationResult(
        episode=planned.episode,
        folder=folder,
        timings=timings,
        warnings=warnings,
        memory=applied.as_dict(),
        quality=quality.as_dict(),
    )


def plan_story(
    session: Session,
    novel: Novel,
    provider: LLMProvider | None,
    *,
    replace: bool = False,
    settings: Settings | None = None,
) -> ArcPlanResult:
    """사건 일정을 만들고 Arc를 설계한다 (기획안 28번)."""
    if novel.planned_episodes <= 0:
        raise NovelFactoryError("목표 회차 수(planned_episodes)가 없습니다.")
    guidance = load_guidance(session, novel)
    schedule = (
        build_event_schedule(novel.planned_episodes, guidance, seed=novel.slug)
        if replace or not (novel.extra or {}).get("event_schedule")
        else load_schedule(novel, guidance)
    )
    return plan_arcs(
        session,
        novel,
        schedule,
        guidance,
        provider=provider,
        replace=replace,
        settings=settings,
    )
