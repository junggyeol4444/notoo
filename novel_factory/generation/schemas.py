"""LLM이 돌려줘야 하는 구조화 응답의 규격.

모델이 채우는 부분만 여기 있다. 회차 범위, 사건 일정, 장면별 목표 글자 수처럼
수치로 정하는 값은 모델에게 맡기지 않고 코드가 정한 뒤 붙인다.

필드마다 기본값을 둔 이유: 로컬 모델은 선택 항목을 자주 빼먹는다. 필수가 아닌
항목이 빠졌다고 계획 전체를 버리면 재시도만 늘어난다.
"""

from __future__ import annotations

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
