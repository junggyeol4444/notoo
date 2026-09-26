"""작품 생성 전체 흐름 (기획안 57번).

    참고소설 분석 → 공통 패턴 추출 → 새로운 세계관 생성 → 새로운 캐릭터 생성
    → 새로운 전체 스토리 생성 → 250화 집필 → 설정 검수 → 복선 관리 → 유사성 검사
    → 수정 → 완결 → EPUB → 표지 → 출판

start_project()가 집필 전까지(분석 ~ 전체 스토리 ~ 표지)를 한 번에 하고, 집필부터는
자동 집필 스케줄러에 맡긴다. 250화 집필은 로컬 모델로 며칠이 걸리므로 요청 하나로
끝까지 기다리지 않는다.

  집필 뒤의 단계가 어디서 도는가
    설정 검수·복선 관리·유사성 검사·수정   회차마다 generate_episode (기획안 34~41번)
    완결                                   목표 회차를 다 쓰면 스케줄러가 완결 검사
    EPUB·출판                              publishing_mode=automatic이면 스케줄러가
                                           권을 만들고 대기열을 처리한다
  표지는 기획안 흐름에서는 EPUB 뒤지만, 연재 썸네일에도 필요해서 집필 전에 만든다.

진행 상황은 novel.extra["project"]에 단계별로 남는다 (project_status()).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from novel_factory.config import Settings, get_settings
from novel_factory.database.models import Episode, Novel, Publication, ReferenceNovel
from novel_factory.database.repositories import (
    NovelRepository,
    ReferenceLinkRepository,
    ReferenceRepository,
)
from novel_factory.errors import NovelFactoryError
from novel_factory.generation.genesis import UNTITLED, design_novel
from novel_factory.generation.guidance import load_guidance
from novel_factory.generation.pipeline import plan_story
from novel_factory.llm.base import LLMProvider
from novel_factory.orchestrator.request import ProjectRequest, ReferenceSpec
from novel_factory.publishing.cover import make_cover
from novel_factory.reference.service import analyze_reference_record
from novel_factory.scheduler import jobs

DEFAULT_CHARS_PER_EPISODE = 5000


@dataclass(slots=True)
class ProjectOptions:
    publishing_mode: str = "automatic"
    episodes_per_run: int = 1
    # True면 한 번 돌 때 남은 회차를 멈출 때까지 전부 쓴다 (episodes_per_run 무시)
    continuous: bool = False
    enable_schedule: bool = True
    make_cover: bool = True


@dataclass(slots=True)
class ProjectResult:
    novel: Novel
    steps: list[dict[str, object]] = field(default_factory=list)
    request: dict[str, object] = field(default_factory=dict)

    def as_dict(self) -> dict[str, object]:
        return {
            "slug": self.novel.slug,
            "title": self.novel.title,
            "steps": self.steps,
            "request": self.request,
        }


def resolve_reference(session: Session, spec: ReferenceSpec) -> ReferenceNovel:
    """참고작 이름을 DB의 참고소설로. reference_id → 제목 → 제목 일부 순으로 찾는다."""
    repo = ReferenceRepository(session)
    ref = repo.get_by_reference_id(spec.name)
    if ref is not None:
        return ref
    rows = session.scalars(select(ReferenceNovel)).all()
    exact = [r for r in rows if r.title == spec.name]
    if len(exact) == 1:
        return exact[0]
    partial = [r for r in rows if spec.name in (r.title or "")]
    if len(partial) == 1:
        return partial[0]
    if len(exact) + len(partial) > 1:
        raise NovelFactoryError(
            f"참고작 '{spec.name}'에 맞는 것이 여러 개입니다: "
            f"{[r.reference_id for r in (exact or partial)]}. reference_id로 적어 주세요."
        )
    raise NovelFactoryError(
        f"참고작 '{spec.name}'을 찾지 못했습니다. 먼저 POST /references/upload로 올리세요."
    )


def _step(result: ProjectResult, name: str, status: str, **detail: object) -> None:
    result.steps.append(
        {
            "step": name,
            "status": status,
            "at": datetime.now(UTC).isoformat(timespec="seconds"),
            **detail,
        }
    )


def start_project(
    session: Session,
    request: ProjectRequest,
    provider: LLMProvider,
    *,
    options: ProjectOptions | None = None,
    settings: Settings | None = None,
    slug: str = "",
) -> ProjectResult:
    """분석부터 표지까지. 실패하면 예외가 나고 트랜잭션은 호출한 쪽이 롤백한다."""
    cfg = settings or get_settings()
    opts = options or ProjectOptions()
    if not provider.available:
        raise NovelFactoryError(
            "작품 생성에는 LLM이 필요합니다. NF_LLM_BASE_URL/NF_LLM_MODEL."
        )
    if not request.genre:
        raise NovelFactoryError("장르가 없습니다. 요청에 장르를 적어 주세요.")
    if not request.episodes or request.episodes < 1:
        raise NovelFactoryError("목표 회차 수가 없습니다. 요청에 'N화'를 적어 주세요.")

    # 1. 참고소설: 찾고, 분석이 안 된 것은 분석한다.
    refs = [(spec, resolve_reference(session, spec)) for spec in request.references]
    novel = NovelRepository(session).add(
        Novel(
            slug=slug or f"novel-{uuid.uuid4().hex[:8]}",
            title=request.title or UNTITLED,
            genre=request.genre,
            planned_episodes=request.episodes,
            target_chars_per_episode=request.chars_per_episode or DEFAULT_CHARS_PER_EPISODE,
            publishing_mode=opts.publishing_mode,
        )
    )
    result = ProjectResult(novel, request=request.as_dict())
    analyzed = []
    for _spec, ref in refs:
        if not ref.profile:
            analyze_reference_record(session, ref, genre=request.genre)
            analyzed.append(ref.reference_id)
    _step(
        result,
        "참고소설 분석",
        "done",
        analyzed=analyzed,
        references=[r.reference_id for _, r in refs],
    )

    # 2. 공통 패턴 추출: 참고 강도를 붙여 연결하면 load_guidance가 가중 집계한다.
    links = ReferenceLinkRepository(session)
    for spec, ref in refs:
        links.upsert(novel.id, ref.id, weights=spec.weights())
    session.flush()
    guidance = load_guidance(session, novel)
    _step(
        result,
        "공통 패턴 추출",
        "done",
        source=guidance.source,
        patterns=len(guidance.pattern_instructions),
    )

    # 3~4. 새로운 세계관·캐릭터 (Novel Bible 포함)
    genesis = design_novel(
        session, novel, provider, request=request.text, settings=cfg, guidance=guidance
    )
    _step(
        result,
        "세계관·캐릭터 생성",
        "done",
        title=novel.title,
        characters=len(genesis.characters),
        world=len(genesis.world),
        foreshadowings=len(genesis.foreshadowings),
        warnings=genesis.warnings,
    )

    # 5. 새로운 전체 스토리 (사건 일정 + Arc)
    arcs = plan_story(session, novel, provider, settings=cfg)
    _step(result, "전체 스토리 생성", "done", arcs=len(arcs.arcs), warnings=arcs.warnings)

    # 표지 (연재 썸네일에도 쓰므로 집필 전에)
    if opts.make_cover:
        cover = make_cover(session, novel, provider, settings=cfg)
        _step(result, "표지", "done", backend=cover.backend, warnings=cover.warnings)

    # 6. 집필은 스케줄러에 맡긴다.
    jobs.update_schedule(
        novel,
        enabled=opts.enable_schedule,
        episodes_per_run=max(1, min(opts.episodes_per_run, jobs.MAX_EPISODES_PER_RUN)),
        continuous=opts.continuous,
    )
    _step(
        result,
        "집필 예약",
        "done" if opts.enable_schedule else "skipped",
        episodes_per_run=opts.episodes_per_run,
        continuous=opts.continuous,
    )

    novel.extra = {
        **(novel.extra or {}),
        "project": {"request": result.request, "steps": result.steps},
    }
    session.flush()
    return result


def project_status(session: Session, novel: Novel) -> dict[str, object]:
    """기획안 57번 흐름에서 지금 어디까지 왔는가."""
    finals = int(
        session.scalar(
            select(func.count(Episode.id)).where(
                Episode.novel_id == novel.id, Episode.status == "final"
            )
        )
        or 0
    )
    pubs = session.execute(
        select(Publication.status, func.count(Publication.id))
        .where(Publication.novel_id == novel.id)
        .group_by(Publication.status)
    ).all()
    project = (novel.extra or {}).get("project") or {}
    completion = (novel.extra or {}).get("completion") or {}
    return {
        "slug": novel.slug,
        "title": novel.title,
        "status": novel.status,
        "publishing_mode": novel.publishing_mode,
        "steps": project.get("steps") or [],
        "request": project.get("request") or {},
        "written": finals,
        "planned_episodes": novel.planned_episodes,
        "progress": round(finals / novel.planned_episodes, 4)
        if novel.planned_episodes
        else 0,
        "schedule": jobs.novel_status(session, novel),
        "completion": {
            k: completion.get(k) for k in ("verdict", "summary", "completed", "checked_at")
        }
        if completion
        else None,
        "publications": {str(status): int(count) for status, count in pubs},
    }
