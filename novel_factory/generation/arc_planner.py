"""전체 Story Arc 설계 (기획안 28번).

두 단계로 나눈다.

  1. 골격 (결정적)  Arc 경계와 대형 사건 회차. 참고작의 사건 간격 범위로 정한다.
                    기획안: "참고 작품들의 평균 대형 사건 간격 약 25화
                            → 새 작품 약 20~30화마다 대형 사건 배치"
  2. 내용 (LLM)     Arc마다 이름·목표·갈등·해소·감정 목표·대형 사건 내용.

LLM이 없어도 1단계만으로 Arc를 저장할 수 있다. 이름은 "ARC 1"처럼 붙는다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from novel_factory.config import Settings, get_settings
from novel_factory.database.models import Arc, Novel
from novel_factory.database.repositories import ArcRepository
from novel_factory.generation.guidance import EventSchedule, GenreGuidance
from novel_factory.generation.prompts import ARC_SYSTEM, build_arc_prompt
from novel_factory.generation.schemas import ArcPlanOut
from novel_factory.generation.structured import request_structured
from novel_factory.llm.base import LLMProvider, Message

#: Arc 하나의 목표 길이(회차). 기획안 28번 예시(250화 → 6개 Arc)에 가깝게.
TARGET_ARC_LENGTH = 40
#: Arc가 이보다 짧아지면 경계를 대형 사건에 맞추지 않고 이상적인 위치를 쓴다.
MIN_ARC_LENGTH = 8
#: Arc 설계 JSON 출력 상한. 없으면 로컬 모델이 끝없이 쓰는 경우가 있다.
ARC_OUTPUT_TOKENS = 4000


@dataclass(slots=True)
class ArcSkeleton:
    order: int
    start_episode: int
    end_episode: int
    major_event_episodes: list[int] = field(default_factory=list)
    is_final: bool = False

    def as_dict(self) -> dict[str, object]:
        return {
            "order": self.order,
            "start_episode": self.start_episode,
            "end_episode": self.end_episode,
            "major_event_episodes": self.major_event_episodes,
            "is_final": self.is_final,
        }


def build_arc_skeleton(
    schedule: EventSchedule, *, target_length: int = TARGET_ARC_LENGTH
) -> list[ArcSkeleton]:
    """Arc 경계를 정한다. 각 Arc는 대형 사건에서 끝난다.

    Arc의 절정이 곧 그 Arc의 마지막 대형 사건이 되도록, 이상적인 경계 위치에서
    가장 가까운 대형 사건 회차로 경계를 옮긴다.
    """
    total = schedule.total_episodes
    if total <= 0:
        return []
    count = max(1, round(total / target_length))
    majors = sorted(set(schedule.major))

    ends: list[int] = []
    previous_end = 0
    for i in range(1, count):
        ideal = round(total * i / count)
        candidates = [
            m
            for m in majors
            if m - previous_end >= MIN_ARC_LENGTH and total - m >= MIN_ARC_LENGTH
        ]
        end = min(candidates, key=lambda m: abs(m - ideal)) if candidates else ideal
        # 이상적인 위치에서 너무 멀면 대형 사건에 맞추지 않는다.
        if abs(end - ideal) > target_length // 2:
            end = ideal
        if end <= previous_end or total - end < MIN_ARC_LENGTH:
            continue
        ends.append(end)
        previous_end = end
    ends.append(total)

    skeleton: list[ArcSkeleton] = []
    start = 1
    for order, end in enumerate(ends, start=1):
        skeleton.append(
            ArcSkeleton(
                order=order,
                start_episode=start,
                end_episode=end,
                major_event_episodes=[m for m in majors if start <= m <= end],
                is_final=end == total,
            )
        )
        start = end + 1
    return skeleton


def bible_block(novel: Novel) -> str:
    return "\n".join(
        [
            "# 작품 기준 (Novel Bible)",
            f"제목: {novel.title}",
            f"장르: {novel.genre}",
            f"로그라인: {novel.logline}",
            f"핵심 소재: {novel.premise}",
            f"분위기: {novel.mood}",
            f"시점: {novel.pov}",
            f"핵심 갈등: {novel.main_conflict}",
            f"최종 결말: {novel.ending or '미정'}",
            f"총 회차: {novel.planned_episodes}화",
        ]
    )


@dataclass(slots=True)
class ArcPlanResult:
    skeleton: list[ArcSkeleton]
    arcs: list[Arc]
    used_llm: bool
    attempts: int = 0
    warnings: list[str] = field(default_factory=list)


def plan_arcs(
    session: Session,
    novel: Novel,
    schedule: EventSchedule,
    guidance: GenreGuidance,
    *,
    provider: LLMProvider | None = None,
    replace: bool = False,
    settings: Settings | None = None,
) -> ArcPlanResult:
    """Arc를 설계해 DB에 저장한다.

    이미 Arc가 있으면 replace=True일 때만 갈아엎는다. 연재 중에 Arc를 바꾸면
    이미 쓴 회차와 어긋날 수 있어서 기본은 보존이다.
    """
    cfg = settings or get_settings()
    repo = ArcRepository(session)
    existing = repo.for_novel(novel.id)
    if existing and not replace:
        return ArcPlanResult(
            [], existing, used_llm=False, warnings=["이미 Arc가 있어 그대로 둡니다."]
        )

    skeleton = build_arc_skeleton(schedule)
    warnings: list[str] = []
    contents: dict[int, dict[str, object]] = {}
    attempts = 0
    used_llm = False

    if provider is not None and provider.available:
        prompt = build_arc_prompt(
            bible_block(novel),
            [s.as_dict() for s in skeleton],
            guidance.pattern_instructions,
        )
        plan, result = request_structured(
            provider,
            [Message("system", ARC_SYSTEM), Message("user", prompt)],
            ArcPlanOut,
            retries=cfg.llm_structured_retries,
            temperature=cfg.llm_structured_temperature,
            max_tokens=ARC_OUTPUT_TOKENS,
            json_mode=True,
        )
        attempts = result.attempts
        used_llm = True
        for arc in plan.arcs:
            contents[arc.order] = arc.model_dump()
        missing = [s.order for s in skeleton if s.order not in contents]
        if missing:
            warnings.append(
                f"LLM이 Arc {missing}의 내용을 주지 않아 기본 이름을 붙였습니다."
            )

    for arc in existing:
        session.delete(arc)
    session.flush()

    saved: list[Arc] = []
    for s in skeleton:
        content = contents.get(s.order, {})
        major_texts = list(content.get("major_events") or [])  # type: ignore[arg-type]
        goal = str(content.get("goal") or "")
        if major_texts:
            pairs = zip(s.major_event_episodes, major_texts, strict=False)
            goal = (goal + "\n" if goal else "") + "\n".join(
                f"[{ep}화 대형 사건] {text}" for ep, text in pairs
            )
        saved.append(
            repo.add(
                Arc(
                    novel_id=novel.id,
                    order=s.order,
                    name=str(
                        content.get("name")
                        or ("FINAL ARC" if s.is_final else f"ARC {s.order}")
                    ),
                    start_episode=s.start_episode,
                    end_episode=s.end_episode,
                    goal=goal,
                    conflict=str(content.get("conflict") or ""),
                    resolution=str(content.get("resolution") or ""),
                    emotion_target=str(content.get("emotion_target") or ""),
                )
            )
        )

    extra = dict(novel.extra or {})
    extra["event_schedule"] = schedule.as_dict()
    novel.extra = extra
    session.flush()
    return ArcPlanResult(
        skeleton, saved, used_llm=used_llm, attempts=attempts, warnings=warnings
    )
