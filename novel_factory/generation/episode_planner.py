"""회차 계획 (기획안 29번).

    Episode, 목적, 필수 사건, 등장인물, 감정 흐름, 사용할 복선, 새로운 복선,
    보상, 갈등, 마지막 Hook

지시(directive)는 코드가 계산하고, 내용은 LLM이 채운다.

  코드가 정하는 것   사건 규모(대형/중형/휴식), 클리프행어 여부와 유형,
                     회수가 임박한 복선, 목표 분량, 피해야 할 반복 표현
  LLM이 정하는 것    제목, 목적, 사건, 감정 흐름, 보상, 갈등, 훅의 내용

LLM이 낸 계획도 그대로 믿지 않는다. 없는 인물 코드, 없는 복선 코드는 지우고,
지시와 다른 클리프행어 유형은 지시대로 되돌린다. 무엇을 고쳤는지는 경고로 남긴다.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from novel_factory.config import Settings, get_settings
from novel_factory.database.models import Episode, Novel
from novel_factory.database.repositories import (
    ArcRepository,
    CharacterRepository,
    EpisodeRepository,
    ForeshadowingRepository,
    StyleBibleRepository,
)
from novel_factory.errors import NovelFactoryError
from novel_factory.generation.budget import BudgetReport
from novel_factory.generation.context import prepare_context
from novel_factory.generation.guidance import (
    EventSchedule,
    GenreGuidance,
    HookDirective,
    build_event_schedule,
    choose_hook,
    hook_history,
    load_guidance,
)
from novel_factory.generation.prompts import EPISODE_SYSTEM, build_episode_prompt
from novel_factory.generation.schemas import EpisodePlanOut
from novel_factory.generation.structured import request_structured
from novel_factory.llm.base import LLMProvider, Message
from novel_factory.quality.repetition import find_repetitions

#: 이 회차 수 안에 회수 예정인 복선을 '임박'으로 본다
DUE_WINDOW = 2
#: 반복 표현을 찾을 때 볼 최근 회차 수
REPETITION_WINDOW = 5
#: 계획 JSON 출력에 줄 토큰
PLAN_OUTPUT_TOKENS = 3000


@dataclass(slots=True)
class EpisodeDirectives:
    episode_number: int
    event_kind: str
    hook: HookDirective
    target_chars: int
    due_foreshadowings: list[dict[str, object]] = field(default_factory=list)
    cast_codes: list[str] = field(default_factory=list)
    open_foreshadowing_codes: list[str] = field(default_factory=list)
    avoid_phrases: list[str] = field(default_factory=list)
    is_last_episode: bool = False

    def as_dict(self) -> dict[str, object]:
        return {
            "episode_number": self.episode_number,
            "event_kind": self.event_kind,
            "hook": self.hook.as_dict(),
            "target_chars": self.target_chars,
            "due_foreshadowings": self.due_foreshadowings,
            "cast_codes": self.cast_codes,
            "open_foreshadowing_codes": self.open_foreshadowing_codes,
            "avoid_phrases": self.avoid_phrases,
            "is_last_episode": self.is_last_episode,
        }


@dataclass(slots=True)
class EpisodePlanResult:
    episode: Episode
    plan: dict[str, object]
    directives: EpisodeDirectives
    budget: BudgetReport
    attempts: int
    warnings: list[str] = field(default_factory=list)


def load_schedule(novel: Novel, guidance: GenreGuidance) -> EventSchedule:
    """Arc 설계 때 저장한 일정. 없으면 같은 규칙으로 다시 계산한다(결정적)."""
    stored = (novel.extra or {}).get("event_schedule")
    if stored:
        return EventSchedule(
            total_episodes=int(stored.get("total_episodes", novel.planned_episodes)),
            major=list(stored.get("major", [])),
            minor=list(stored.get("minor", [])),
        )
    return build_event_schedule(novel.planned_episodes or 1, guidance, seed=novel.slug)


def avoid_phrases_for(session: Session, novel: Novel, episode_number: int) -> list[str]:
    """Style Bible에 등록된 제한 표현 + 최근 회차에서 과하게 쓴 표현."""
    phrases: list[str] = []
    bible = StyleBibleRepository(session).for_novel(novel.id)
    if bible is not None:
        phrases.extend((bible.throttled_phrases or {}).keys())
    recent = EpisodeRepository(session).recent(
        novel.id, REPETITION_WINDOW, before=episode_number
    )
    texts = [e.final_text for e in recent if e.final_text]
    if texts:
        phrases.extend(r.phrase for r in find_repetitions(texts))
    seen: set[str] = set()
    return [p for p in phrases if not (p in seen or seen.add(p))]


def compute_directives(
    session: Session,
    novel: Novel,
    episode_number: int,
    guidance: GenreGuidance,
) -> EpisodeDirectives:
    schedule = load_schedule(novel, guidance)
    total = novel.planned_episodes or schedule.total_episodes or episode_number
    is_last = episode_number >= total

    hook = choose_hook(
        guidance, hook_history(session, novel, episode_number), is_last_episode=is_last
    )

    fs_repo = ForeshadowingRepository(session)
    open_items = [
        f for f in fs_repo.open_items(novel.id) if f.setup_episode < episode_number
    ]
    due = [
        {
            "code": f.code,
            "description": f.description,
            "planned_payoff": f.planned_payoff,
            "overdue": f.planned_payoff is not None and f.planned_payoff < episode_number,
        }
        for f in open_items
        if f.planned_payoff is not None and f.planned_payoff <= episode_number + DUE_WINDOW
    ]
    # 마지막 화에서는 남은 복선을 전부 회수 대상으로 올린다 (기획안 47번 완결 검사 대비).
    if is_last:
        due = [
            {
                "code": f.code,
                "description": f.description,
                "planned_payoff": f.planned_payoff,
                "overdue": False,
            }
            for f in open_items
        ]

    cast = CharacterRepository(session).alive_in(novel.id, episode_number)
    return EpisodeDirectives(
        episode_number=episode_number,
        event_kind="major" if is_last else schedule.kind_of(episode_number),
        hook=hook,
        target_chars=novel.target_chars_per_episode,
        due_foreshadowings=due,
        cast_codes=[c.code for c in cast],
        open_foreshadowing_codes=[f.code for f in open_items],
        avoid_phrases=avoid_phrases_for(session, novel, episode_number),
        is_last_episode=is_last,
    )


def sanitize_plan(
    plan: EpisodePlanOut,
    directives: EpisodeDirectives,
    guidance: GenreGuidance,
    *,
    total_episodes: int,
) -> tuple[dict[str, object], list[str]]:
    """LLM 계획을 지시와 DB에 맞게 고친다."""
    warnings: list[str] = []
    data = plan.model_dump()

    cast = set(directives.cast_codes)
    unknown = [c for c in plan.characters if c not in cast]
    if unknown:
        warnings.append(f"계획에 없는 인물 코드를 뺐습니다: {unknown}")
    data["characters"] = [c for c in plan.characters if c in cast]

    open_codes = set(directives.open_foreshadowing_codes)
    unknown_fs = [c for c in plan.foreshadowings_used if c not in open_codes]
    if unknown_fs:
        warnings.append(f"살아 있지 않은 복선 코드를 뺐습니다: {unknown_fs}")
    data["foreshadowings_used"] = [c for c in plan.foreshadowings_used if c in open_codes]

    hook = dict(data.get("hook") or {})
    if directives.hook.required:
        if hook.get("type") != directives.hook.kind:
            if hook.get("type"):
                warnings.append(
                    f"클리프행어 유형을 '{hook.get('type')}'에서 지시한 "
                    f"'{directives.hook.kind}'로 되돌렸습니다."
                )
            hook["type"] = directives.hook.kind
    else:
        hook["type"] = ""
    data["hook"] = hook

    # 새 복선의 회수 예정 회차가 없거나 과거면 참고작의 회수 간격으로 정한다.
    span = round(statistics.fmean(guidance.foreshadow_span))
    n = directives.episode_number
    fixed = []
    for item in data.get("new_foreshadowings") or []:
        payoff = item.get("planned_payoff")
        if not isinstance(payoff, int) or payoff <= n:
            payoff = n + span
        item["planned_payoff"] = min(payoff, total_episodes)
        fixed.append(item)
    data["new_foreshadowings"] = fixed
    return data, warnings


def plan_episode(
    session: Session,
    novel: Novel,
    episode_number: int,
    provider: LLMProvider,
    *,
    guidance: GenreGuidance | None = None,
    settings: Settings | None = None,
    replace: bool = False,
) -> EpisodePlanResult:
    cfg = settings or get_settings()
    if episode_number < 1:
        raise NovelFactoryError("회차 번호는 1 이상입니다.")
    episodes = EpisodeRepository(session)
    existing = episodes.get_by_number(novel.id, episode_number)
    if existing is not None and existing.status == "final" and not replace:
        raise NovelFactoryError(
            f"{episode_number}화는 이미 확정됐습니다. "
            "다시 계획하려면 replace=True를 주세요."
        )

    guide = guidance or load_guidance(session, novel)
    directives = compute_directives(session, novel, episode_number, guide)
    arc = ArcRepository(session).containing_episode(novel.id, episode_number)
    # 회수할 복선이 설치된 장면과 Arc 목표에 가까운 장면을 앞 회차에서 찾는다.
    query = " ".join(
        [str(d["description"]) for d in directives.due_foreshadowings]
        + ([arc.goal, arc.conflict] if arc is not None else [])
    )
    _ctx, block, report = prepare_context(
        session, novel, episode_number, cfg, output_tokens=PLAN_OUTPUT_TOKENS, query=query
    )

    prompt = build_episode_prompt(
        block,
        episode_number=episode_number,
        event_kind=directives.event_kind,
        hook_required=directives.hook.required,
        hook_kind=directives.hook.kind,
        hook_reason=directives.hook.reason,
        target_chars=directives.target_chars,
        due_foreshadowings=[
            f"{d['code']} {d['description']}"
            + (" (회수 예정 지남)" if d["overdue"] else "")
            for d in directives.due_foreshadowings
        ],
        cast_codes=directives.cast_codes,
        open_foreshadowing_codes=directives.open_foreshadowing_codes,
    )
    plan, result = request_structured(
        provider,
        [Message("system", EPISODE_SYSTEM), Message("user", prompt)],
        EpisodePlanOut,
        retries=cfg.llm_structured_retries,
        temperature=cfg.llm_structured_temperature,
        max_tokens=PLAN_OUTPUT_TOKENS,
    )
    data, warnings = sanitize_plan(
        plan, directives, guide, total_episodes=novel.planned_episodes or episode_number
    )
    if not report.fits:
        warnings.append(
            f"컨텍스트가 예산({report.budget_tokens:,}토큰 추정)을 넘습니다. "
            "NF_LLM_CONTEXT_TOKENS와 NF_LLM_TOKENS_PER_CHAR를 확인하세요."
        )

    episode = existing or episodes.add(Episode(novel_id=novel.id, number=episode_number))
    episode.title = str(data.get("title") or episode.title)
    episode.status = "outlined"
    episode.outline = {
        "plan": data,
        "directives": directives.as_dict(),
        "warnings": warnings,
    }
    episode.scenes = []
    episode.hook_type = str((data.get("hook") or {}).get("type") or "")  # type: ignore[union-attr]
    episode.reference_usage = {
        "guidance": guide.as_dict(),
        "pattern_instructions": guide.pattern_instructions,
        "context_trimmed": report.trimmed,
    }
    session.flush()
    return EpisodePlanResult(
        episode=episode,
        plan=data,
        directives=directives,
        budget=report,
        attempts=result.attempts,
        warnings=warnings,
    )
