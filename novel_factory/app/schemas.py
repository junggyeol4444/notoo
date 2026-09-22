"""API 요청/응답 스키마 (기획안 53·54번)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# 참고소설
# ---------------------------------------------------------------------------
class ReferenceCreate(BaseModel):
    """POST /references — 서버가 읽을 수 있는 경로로 등록."""

    path: str = Field(description="서버에서 접근 가능한 파일 경로")
    reference_id: str | None = Field(default=None, description="비우면 자동 부여")
    title: str = ""
    author: str = ""
    genre: str = ""


class ReferenceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    reference_id: str
    title: str
    author: str
    genre: str
    source_format: str
    encoding: str
    char_count: int
    episode_count: int
    status: str
    warnings: list[str] = Field(default_factory=list)


class ReferenceAnalyzeRequest(BaseModel):
    """POST /references/{reference_id}/analyze"""

    genre: str | None = None
    fallback_chars: int = Field(default=5000, ge=500, le=50000)
    major_quantile: float = Field(default=0.85, ge=0.5, le=0.99)
    store_fingerprints: bool = True


class ReferenceProfileOut(BaseModel):
    reference_id: str
    status: str
    profile: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


class AspectWeights(BaseModel):
    """기획안 16·50번 참고 강도. 0을 주면 그 항목은 집계에서 빠진다."""

    pacing: float | None = Field(default=None, ge=0.0, le=1.0)
    episode_shape: float | None = Field(default=None, ge=0.0, le=1.0)
    cliffhanger: float | None = Field(default=None, ge=0.0, le=1.0)
    foreshadowing: float | None = Field(default=None, ge=0.0, le=1.0)
    relationship: float | None = Field(default=None, ge=0.0, le=1.0)
    event_interval: float | None = Field(default=None, ge=0.0, le=1.0)
    emotion: float | None = Field(default=None, ge=0.0, le=1.0)
    dialogue: float | None = Field(default=None, ge=0.0, le=1.0)
    style: float | None = Field(default=None, ge=0.0, le=1.0)

    def as_mapping(self) -> dict[str, float]:
        return {k: v for k, v in self.model_dump().items() if v is not None}


class NovelReferenceLinkIn(BaseModel):
    """POST /novels/{slug}/references"""

    reference_id: str
    default_weight: float = Field(default=1.0, ge=0.0, le=1.0)
    weights: AspectWeights = Field(default_factory=AspectWeights)


# ---------------------------------------------------------------------------
# 작품
# ---------------------------------------------------------------------------
class NovelCreate(BaseModel):
    """POST /novels (기획안 54번)."""

    slug: str = Field(min_length=1, max_length=64)
    title: str
    genre: str = ""
    logline: str = ""
    premise: str = ""
    mood: str = ""
    pov: str = "3인칭 제한"
    target_reader: str = ""
    main_conflict: str = ""
    ending: str = ""
    episodes: int = Field(default=0, ge=0, le=5000, description="목표 회차 수")
    characters_per_episode: int = Field(
        default=5000, ge=500, le=50000, description="회차당 목표 글자 수"
    )
    publishing_mode: str = "manual"
    reference_ids: list[str] = Field(default_factory=list)


class NovelOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    slug: str
    title: str
    genre: str
    logline: str
    mood: str
    pov: str
    main_conflict: str
    planned_episodes: int
    target_chars_per_episode: int
    status: str


class ArcIn(BaseModel):
    order: int = Field(ge=1)
    name: str
    start_episode: int = Field(ge=1)
    end_episode: int = Field(ge=1)
    goal: str = ""
    conflict: str = ""
    resolution: str = ""
    emotion_target: str = ""


class ArcOut(ArcIn):
    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------------------
# 인물 / 설정
# ---------------------------------------------------------------------------
class CharacterIn(BaseModel):
    name: str
    code: str | None = None
    role: str = "조연"
    age: int | None = None
    gender: str = ""
    job: str = ""
    appearance: str = ""
    personality: list[str] = Field(default_factory=list)
    speech_style: str = ""
    goals: list[str] = Field(default_factory=list)
    secrets: list[str] = Field(default_factory=list)
    first_episode: int | None = None


class CharacterOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    code: str
    name: str
    role: str
    age: int | None
    job: str
    personality: list[str]
    speech_style: str
    first_episode: int | None
    is_alive: bool


class KnowledgeIn(BaseModel):
    fact_key: str
    fact: str
    knows: bool = True
    learned_episode: int | None = None
    source: str = ""


class RelationshipIn(BaseModel):
    source_code: str
    target_code: str
    from_episode: int = Field(ge=1)
    state: str
    intensity: float = Field(default=0.0, ge=-1.0, le=1.0)
    note: str = ""
    symmetric: bool = True


class WorldEntryIn(BaseModel):
    category: str
    name: str
    description: str = ""
    rules: list[str] = Field(default_factory=list)
    attributes: dict[str, Any] = Field(default_factory=dict)
    first_episode: int | None = None


class TimelineEventIn(BaseModel):
    title: str
    occurred_at: str = ""
    description: str = ""
    episode_number: int | None = None
    sort_key: float | None = None
    participants: list[str] = Field(default_factory=list)
    location: str = ""
    importance: int = Field(default=1, ge=1, le=5)


class ForeshadowingIn(BaseModel):
    description: str
    setup_episode: int = Field(ge=1)
    planned_payoff: int | None = None
    code: str | None = None
    importance: int = Field(default=3, ge=1, le=5)
    note: str = ""


class ForeshadowingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    code: str
    description: str
    setup_episode: int
    planned_payoff: int | None
    actual_payoff: int | None
    status: str
    mentions: list[int]


class StyleBibleIn(BaseModel):
    target_sentence_chars: float = Field(default=20.0, gt=0)
    target_paragraph_chars: float = Field(default=90.0, gt=0)
    target_dialogue_ratio: float = Field(default=0.40, ge=0.0, le=1.0)
    target_narration_ratio: float = Field(default=0.45, ge=0.0, le=1.0)
    target_inner_ratio: float = Field(default=0.15, ge=0.0, le=1.0)
    pov: str = "3인칭 제한"
    tense: str = "과거"
    forbidden: list[str] = Field(default_factory=list)
    preferred: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# 집계 / 컨텍스트 / 유사도
# ---------------------------------------------------------------------------
class GenreProfileOut(BaseModel):
    genre: str
    reference_count: int
    profile: dict[str, Any] = Field(default_factory=dict)
    patterns: list[dict[str, Any]] = Field(default_factory=list)


class SimilarityCheckIn(BaseModel):
    text: str = Field(min_length=1)
    reference_ids: list[str] = Field(default_factory=list)
    top_k: int = Field(default=5, ge=1, le=50)


class HealthOut(BaseModel):
    status: str
    database: str
    llm_configured: bool
    llm_available: bool | None = None
    supported_formats: list[str]
