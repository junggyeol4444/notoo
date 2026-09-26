"""LLM이 돌려줘야 하는 구조화 응답의 규격.

모델이 채우는 부분만 여기 있다. 회차 범위, 사건 일정, 장면별 목표 글자 수처럼
수치로 정하는 값은 모델에게 맡기지 않고 코드가 정한 뒤 붙인다.

필드마다 기본값을 둔 이유: 로컬 모델은 선택 항목을 자주 빼먹는다. 필수가 아닌
항목이 빠졌다고 계획 전체를 버리면 재시도만 늘어난다.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field, field_validator


class _Lenient(BaseModel):
    """모르는 필드가 섞여 와도 버리고 넘어간다."""

    model_config = ConfigDict(extra="ignore")


def _as_list(value: object) -> object:
    """문자열 하나로 온 목록 항목을 목록으로 감싼다. 로컬 모델이 흔히 이렇게 준다."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    return value


# ---------------------------------------------------------------------------
# Arc (기획안 28번)
# ---------------------------------------------------------------------------
class ArcContent(_Lenient):
    order: int
    name: str = Field(min_length=1)
    goal: str = ""
    conflict: str = ""
    resolution: str = ""
    emotion_target: str = ""
    major_events: list[str] = Field(default_factory=list)

    _list = field_validator("major_events", mode="before")(_as_list)


class ArcPlanOut(_Lenient):
    arcs: list[ArcContent] = Field(min_length=1)


# ---------------------------------------------------------------------------
# Episode (기획안 29번)
# ---------------------------------------------------------------------------
class NewForeshadowing(_Lenient):
    description: str = Field(min_length=1)
    planned_payoff: int | None = None


class HookOut(_Lenient):
    type: str = ""
    content: str = ""


class EpisodePlanOut(_Lenient):
    title: str = Field(min_length=1)
    purpose: str = Field(min_length=1)
    required_events: list[str] = Field(min_length=1)
    characters: list[str] = Field(default_factory=list)  # 인물 코드 (C001 ...)
    emotion_flow: list[str] = Field(default_factory=list)
    foreshadowings_used: list[str] = Field(default_factory=list)  # 복선 코드 (F001 ...)
    new_foreshadowings: list[NewForeshadowing] = Field(default_factory=list)
    reward: str = ""
    conflict: str = ""
    hook: HookOut = Field(default_factory=HookOut)

    _lists = field_validator(
        "required_events",
        "characters",
        "emotion_flow",
        "foreshadowings_used",
        mode="before",
    )(_as_list)


# ---------------------------------------------------------------------------
# Scene (기획안 30번)
# ---------------------------------------------------------------------------
class SceneOut(_Lenient):
    purpose: str = Field(min_length=1)
    characters: list[str] = Field(default_factory=list)
    location: str = ""
    conflict: str = ""
    emotion: str = ""
    beats: list[str] = Field(default_factory=list)

    _lists = field_validator("characters", "beats", mode="before")(_as_list)


class ScenePlanOut(_Lenient):
    scenes: list[SceneOut] = Field(min_length=1)


# ---------------------------------------------------------------------------
# Memory Update (기획안 39번)
# ---------------------------------------------------------------------------
class NewCharacter(_Lenient):
    name: str = Field(min_length=1)
    role: str = "조연"
    job: str = ""
    personality: list[str] = Field(default_factory=list)
    speech_style: str = ""

    _list = field_validator("personality", mode="before")(_as_list)


class WorldFact(_Lenient):
    category: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str = ""


class TimelineFact(_Lenient):
    title: str = Field(min_length=1)
    occurred_at: str = ""
    participants: list[str] = Field(default_factory=list)
    importance: int = Field(default=2, ge=1, le=5)

    _list = field_validator("participants", mode="before")(_as_list)


class RelationshipChange(_Lenient):
    source: str = Field(min_length=1)  # 인물 코드 또는 이름
    target: str = Field(min_length=1)
    state: str = Field(min_length=1)
    intensity: float = Field(default=0.0, ge=-1.0, le=1.0)
    note: str = ""


class KnowledgeChange(_Lenient):
    character: str = Field(min_length=1)
    fact_key: str = Field(min_length=1)
    fact: str = Field(min_length=1)
    knows: bool = True


class ItemChange(_Lenient):
    character: str = Field(min_length=1)
    item: str = Field(min_length=1)
    action: str = "gained"  # gained | lost


class Death(_Lenient):
    character: str = Field(min_length=1)
    cause: str = ""


class MemoryDeltaOut(_Lenient):
    summary: str = Field(min_length=1)
    new_characters: list[NewCharacter] = Field(default_factory=list)
    world: list[WorldFact] = Field(default_factory=list)
    timeline: list[TimelineFact] = Field(default_factory=list)
    relationships: list[RelationshipChange] = Field(default_factory=list)
    knowledge: list[KnowledgeChange] = Field(default_factory=list)
    items: list[ItemChange] = Field(default_factory=list)
    deaths: list[Death] = Field(default_factory=list)
    new_foreshadowings: list[NewForeshadowing] = Field(default_factory=list)
    advanced_foreshadowings: list[str] = Field(default_factory=list)
    resolved_foreshadowings: list[str] = Field(default_factory=list)
    hook_type: str = ""

    _lists = field_validator(
        "advanced_foreshadowings", "resolved_foreshadowings", mode="before"
    )(_as_list)


# ---------------------------------------------------------------------------
# Logic 검사 (기획안 35번)
# ---------------------------------------------------------------------------
class LogicIssueOut(_Lenient):
    kind: str = Field(min_length=1)
    quote: str = ""  # 원고에서 그대로 옮긴 문제 구절
    explanation: str = ""
    severity: str = "low"  # high | low


class LogicOut(_Lenient):
    issues: list[LogicIssueOut] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Reader Simulation (기획안 37번)
# ---------------------------------------------------------------------------
_NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?")


def _score(value: object) -> object:
    """'7점', '7/10', 7.5 같은 답을 0~10 사이 숫자로."""
    if isinstance(value, str):
        m = _NUMBER_RE.search(value)
        if not m:
            return value  # 검증 오류로 넘겨 재요청하게 한다
        value = float(m.group())
    if isinstance(value, int | float):
        return min(max(float(value), 0.0), 10.0)
    return value


class ReaderScores(_Lenient):
    immersion: float = 0.0  # 몰입도
    pacing: float = 0.0  # 전개속도
    character_appeal: float = 0.0  # 캐릭터 매력
    conflict: float = 0.0  # 갈등
    reward: float = 0.0  # 보상
    cliffhanger: float = 0.0  # 클리프행어

    _scores = field_validator(
        "immersion",
        "pacing",
        "character_appeal",
        "conflict",
        "reward",
        "cliffhanger",
        mode="before",
    )(_score)


class BoringPart(_Lenient):
    quote: str = ""
    reason: str = ""


class ReaderOut(_Lenient):
    scores: ReaderScores
    boring_parts: list[BoringPart] = Field(default_factory=list)  # 지루한 구간
    comment: str = ""


# ---------------------------------------------------------------------------
# 표지 (기획안 43번)
# ---------------------------------------------------------------------------
class CoverPromptOut(_Lenient):
    prompt: str = Field(min_length=10)
    negative_prompt: str = ""


# ---------------------------------------------------------------------------
# 전자책 작품 소개 (기획안 46번)
# ---------------------------------------------------------------------------
class BlurbOut(_Lenient):
    description: str = Field(min_length=10)
    keywords: list[str] = Field(default_factory=list)
    categories: list[str] = Field(default_factory=list)

    _lists = field_validator("keywords", "categories", mode="before")(_as_list)


# ---------------------------------------------------------------------------
# 완결 검사 (기획안 47번)
# ---------------------------------------------------------------------------
class UnresolvedConflict(_Lenient):
    conflict: str = Field(min_length=1)
    reason: str = ""
    severity: str = "low"


class SettingConflict(_Lenient):
    description: str = Field(min_length=1)
    episodes: list[int] = Field(default_factory=list)
    severity: str = "low"


class CompletionOut(_Lenient):
    unresolved_conflicts: list[UnresolvedConflict] = Field(default_factory=list)
    setting_conflicts: list[SettingConflict] = Field(default_factory=list)
    ending_consistent: bool = True
    ending_issues: list[str] = Field(default_factory=list)

    _lists = field_validator("ending_issues", mode="before")(_as_list)


# ---------------------------------------------------------------------------
# 작품 설계 (기획안 57번)
# ---------------------------------------------------------------------------
class GenesisCharacter(_Lenient):
    name: str = Field(min_length=1)
    role: str = "조연"
    gender: str = ""
    age: int | None = None
    job: str = ""
    appearance: str = ""
    personality: list[str] = Field(default_factory=list)
    speech_style: str = ""
    goals: list[str] = Field(default_factory=list)
    secrets: list[str] = Field(default_factory=list)
    abilities: list[str] = Field(default_factory=list)
    first_episode: int | None = None

    _lists = field_validator("personality", "goals", "secrets", "abilities", mode="before")(
        _as_list
    )

    @field_validator("age", mode="before")
    @classmethod
    def _age(cls, value: object) -> object:
        if isinstance(value, str):
            m = _NUMBER_RE.search(value)
            return int(float(m.group())) if m else None
        return value


class GenesisForeshadowing(_Lenient):
    description: str = Field(min_length=1)
    setup_episode: int = 1
    planned_payoff: int | None = None


class GenesisStyle(_Lenient):
    forbidden: list[str] = Field(default_factory=list)
    preferred: list[str] = Field(default_factory=list)

    _lists = field_validator("forbidden", "preferred", mode="before")(_as_list)


class GenesisOut(_Lenient):
    title: str = ""
    logline: str = Field(min_length=5)
    premise: str = ""
    mood: str = ""
    main_conflict: str = Field(min_length=5)
    ending: str = ""
    target_reader: str = ""
    characters: list[GenesisCharacter] = Field(min_length=2)
    world: list[WorldFact] = Field(default_factory=list)
    foreshadowings: list[GenesisForeshadowing] = Field(default_factory=list)
    style: GenesisStyle = Field(default_factory=GenesisStyle)


# ---------------------------------------------------------------------------
# 사용자 요청 해석 (기획안 57번)
# ---------------------------------------------------------------------------
class RequestReference(_Lenient):
    name: str = Field(min_length=1)
    aspects: list[str] = Field(default_factory=list)

    _list = field_validator("aspects", mode="before")(_as_list)


class RequestOut(_Lenient):
    genre: str = ""
    title: str = ""
    episodes: int | None = None
    chars_per_episode: int | None = None
    references: list[RequestReference] = Field(default_factory=list)
    notes: str = ""
