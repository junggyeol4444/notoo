"""장기기억 DB 스키마 (기획안 21~28, 41번).

이 스키마가 Phase 2의 전부다. 장편소설을 한 프롬프트에 넣지 않고
쓰려면, 작품의 모든 사실이 검색 가능한 레코드로 쪼개져 있어야 한다.

  Novel            작품 자체와 Novel Bible
  Arc              전체 스토리 구조
  Episode          회차. 원고와 요약
  Character        등장인물
  CharacterKnowledge  누가 무엇을 아는가 (기획안 23번)
  Relationship     인물 관계. 시점별로 여러 줄이 쌓인다 (기획안 24번)
  WorldEntry       세계관 항목 (기획안 25번)
  TimelineEvent    사건과 날짜 (기획안 26번)
  Foreshadowing    복선과 상태 (기획안 27번)
  StyleBible       작품 문체 (기획안 32번)
  ReferenceNovel   참고작과 분석 결과
  ReferenceLink    작품-참고작 연결과 참고 강도 (기획안 16번)
  ReferencePatternRow  재사용 패턴 (기획안 19번)
  MemoryChunk      의미 검색용 조각 (기획안 41번)

JSON 컬럼을 쓰는 곳이 있다. 분석 결과처럼 스키마가 자주 바뀌고
질의 대상이 아닌 값만 JSON으로 둔다. 검색·정렬에 쓰는 값은 전부 컬럼이다.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from novel_factory.database.base import Base


def _utcnow() -> datetime:
    return datetime.now(UTC)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow
    )


# ---------------------------------------------------------------------------
# 작품
# ---------------------------------------------------------------------------
class Novel(TimestampMixin, Base):
    """작품 하나와 그 Novel Bible (기획안 21번).

    Novel Bible은 작품의 절대 기준이다. Reference Profile이 이걸 이기지
    못한다(기획안 21번). 그래서 참고작 관련 값과 같은 테이블에 두지 않고
    별도 컬럼으로 분명히 분리했다.
    """

    __tablename__ = "novels"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True, index=True)

    # --- Novel Bible ---
    title: Mapped[str] = mapped_column(String(200))
    genre: Mapped[str] = mapped_column(String(50), index=True, default="")
    logline: Mapped[str] = mapped_column(Text, default="")
    premise: Mapped[str] = mapped_column(Text, default="")  # 핵심 소재
    mood: Mapped[str] = mapped_column(String(200), default="")  # 전체 분위기
    pov: Mapped[str] = mapped_column(String(30), default="3인칭 제한")
    target_reader: Mapped[str] = mapped_column(String(200), default="")
    main_conflict: Mapped[str] = mapped_column(Text, default="")
    ending: Mapped[str] = mapped_column(Text, default="")  # 최종 결말

    planned_episodes: Mapped[int] = mapped_column(Integer, default=0)
    target_chars_per_episode: Mapped[int] = mapped_column(Integer, default=5000)

    status: Mapped[str] = mapped_column(String(20), default="planning", index=True)
    publishing_mode: Mapped[str] = mapped_column(String(20), default="manual")

    extra: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    arcs: Mapped[list[Arc]] = relationship(
        back_populates="novel", cascade="all, delete-orphan", order_by="Arc.order"
    )
    episodes: Mapped[list[Episode]] = relationship(
        back_populates="novel", cascade="all, delete-orphan", order_by="Episode.number"
    )
    characters: Mapped[list[Character]] = relationship(
        back_populates="novel", cascade="all, delete-orphan"
    )
    world_entries: Mapped[list[WorldEntry]] = relationship(
        back_populates="novel", cascade="all, delete-orphan"
    )
    timeline: Mapped[list[TimelineEvent]] = relationship(
        back_populates="novel", cascade="all, delete-orphan"
    )
    foreshadowings: Mapped[list[Foreshadowing]] = relationship(
        back_populates="novel", cascade="all, delete-orphan"
    )
    style_bible: Mapped[StyleBible | None] = relationship(
        back_populates="novel", cascade="all, delete-orphan", uselist=False
    )
    reference_links: Mapped[list[ReferenceLink]] = relationship(
        back_populates="novel", cascade="all, delete-orphan"
    )


class Arc(TimestampMixin, Base):
    """전체 스토리 구조의 한 단위 (기획안 28번)."""

    __tablename__ = "arcs"
    __table_args__ = (UniqueConstraint("novel_id", "order", name="uq_arc_order"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    novel_id: Mapped[int] = mapped_column(
        ForeignKey("novels.id", ondelete="CASCADE"), index=True
    )
    order: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(200))
    start_episode: Mapped[int] = mapped_column(Integer)
    end_episode: Mapped[int] = mapped_column(Integer)
    goal: Mapped[str] = mapped_column(Text, default="")
    conflict: Mapped[str] = mapped_column(Text, default="")
    resolution: Mapped[str] = mapped_column(Text, default="")
    emotion_target: Mapped[str] = mapped_column(String(100), default="")

    novel: Mapped[Novel] = relationship(back_populates="arcs")


class Episode(TimestampMixin, Base):
    """회차 하나 (기획안 40번 저장 구조)."""

    __tablename__ = "episodes"
    __table_args__ = (
        UniqueConstraint("novel_id", "number", name="uq_episode_number"),
        Index("ix_episode_novel_status", "novel_id", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    novel_id: Mapped[int] = mapped_column(
        ForeignKey("novels.id", ondelete="CASCADE"), index=True
    )
    arc_id: Mapped[int | None] = mapped_column(
        ForeignKey("arcs.id", ondelete="SET NULL"), nullable=True
    )
    number: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(200), default="")

    status: Mapped[str] = mapped_column(String(20), default="planned")
    # planned -> outlined -> drafted -> checked -> final

    outline: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    scenes: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    draft: Mapped[str] = mapped_column(Text, default="")
    final_text: Mapped[str] = mapped_column(Text, default="")
    summary: Mapped[str] = mapped_column(Text, default="")

    char_count: Mapped[int] = mapped_column(Integer, default=0)
    hook_type: Mapped[str] = mapped_column(String(30), default="")
    quality_reports: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    memory_delta: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    reference_usage: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    novel: Mapped[Novel] = relationship(back_populates="episodes")


# ---------------------------------------------------------------------------
# 인물
# ---------------------------------------------------------------------------
class Character(TimestampMixin, Base):
    """등장인물 (기획안 22번)."""

    __tablename__ = "characters"
    __table_args__ = (UniqueConstraint("novel_id", "code", name="uq_character_code"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    novel_id: Mapped[int] = mapped_column(
        ForeignKey("novels.id", ondelete="CASCADE"), index=True
    )
    code: Mapped[str] = mapped_column(String(20))  # C001
    name: Mapped[str] = mapped_column(String(100), index=True)
    role: Mapped[str] = mapped_column(String(30), default="조연")

    age: Mapped[int | None] = mapped_column(Integer, nullable=True)
    gender: Mapped[str] = mapped_column(String(20), default="")
    job: Mapped[str] = mapped_column(String(100), default="")
    appearance: Mapped[str] = mapped_column(Text, default="")
    personality: Mapped[list[str]] = mapped_column(JSON, default=list)
    speech_style: Mapped[str] = mapped_column(Text, default="")
    goals: Mapped[list[str]] = mapped_column(JSON, default=list)
    secrets: Mapped[list[str]] = mapped_column(JSON, default=list)
    abilities: Mapped[list[str]] = mapped_column(JSON, default=list)
    items: Mapped[list[str]] = mapped_column(JSON, default=list)

    first_episode: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_episode: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_alive: Mapped[bool] = mapped_column(Boolean, default=True)
    exit_episode: Mapped[int | None] = mapped_column(Integer, nullable=True)
    exit_reason: Mapped[str] = mapped_column(String(200), default="")

    novel: Mapped[Novel] = relationship(back_populates="characters")
    knowledge: Mapped[list[CharacterKnowledge]] = relationship(
        back_populates="character", cascade="all, delete-orphan"
    )


class CharacterKnowledge(TimestampMixin, Base):
    """누가 무엇을 언제부터 아는가 (기획안 23번).

    이 테이블이 "등장인물이 알 수 없는 정보를 말하는 오류"를 막는다.
    Logic Checker는 대사에 나온 사실마다 화자가 그걸 아는지 여기서 확인한다.
    """

    __tablename__ = "character_knowledge"
    __table_args__ = (Index("ix_knowledge_char_fact", "character_id", "fact_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    character_id: Mapped[int] = mapped_column(
        ForeignKey("characters.id", ondelete="CASCADE"), index=True
    )
    fact_key: Mapped[str] = mapped_column(String(120), index=True)
    fact: Mapped[str] = mapped_column(Text)
    knows: Mapped[bool] = mapped_column(Boolean, default=True)
    learned_episode: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source: Mapped[str] = mapped_column(String(200), default="")
    note: Mapped[str] = mapped_column(Text, default="")

    character: Mapped[Character] = relationship(back_populates="knowledge")


class Relationship(TimestampMixin, Base):
    """인물 관계의 한 시점 (기획안 24번).

    관계는 변한다. 한 쌍에 여러 줄이 쌓이고, 각 줄이 "몇 화 시점에
    어떤 관계였는가"를 기록한다. 최신 상태는 from_episode가 가장 큰 줄이다.
    """

    __tablename__ = "relationships"
    __table_args__ = (
        Index("ix_relationship_pair", "novel_id", "source_id", "target_id", "from_episode"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    novel_id: Mapped[int] = mapped_column(
        ForeignKey("novels.id", ondelete="CASCADE"), index=True
    )
    source_id: Mapped[int] = mapped_column(
        ForeignKey("characters.id", ondelete="CASCADE"), index=True
    )
    target_id: Mapped[int] = mapped_column(
        ForeignKey("characters.id", ondelete="CASCADE"), index=True
    )
    from_episode: Mapped[int] = mapped_column(Integer, default=1)
    state: Mapped[str] = mapped_column(String(50))  # 경계 / 협력 / 신뢰 / 적대
    intensity: Mapped[float] = mapped_column(Float, default=0.0)  # -1 ~ +1
    note: Mapped[str] = mapped_column(Text, default="")


# ---------------------------------------------------------------------------
# 세계관 / 시간선 / 복선
# ---------------------------------------------------------------------------
class WorldEntry(TimestampMixin, Base):
    """세계관 항목 (기획안 25번)."""

    __tablename__ = "world_entries"
    __table_args__ = (
        UniqueConstraint("novel_id", "category", "name", name="uq_world_entry"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    novel_id: Mapped[int] = mapped_column(
        ForeignKey("novels.id", ondelete="CASCADE"), index=True
    )
    category: Mapped[str] = mapped_column(String(40), index=True)
    # 회사 / 학교 / 국가 / 길드 / 마법 / 경제 / 화폐 / 기술 / 지역 / 아이템 / 능력 / 조직
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    rules: Mapped[list[str]] = mapped_column(JSON, default=list)
    attributes: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    first_episode: Mapped[int | None] = mapped_column(Integer, nullable=True)

    novel: Mapped[Novel] = relationship(back_populates="world_entries")


class TimelineEvent(TimestampMixin, Base):
    """사건 하나와 그 시점 (기획안 26번).

    날짜를 문자열로 둔다. 현대물은 실제 날짜를, 판타지는 "제국력 312년"
    같은 표기를 쓴다. 정렬은 sort_key(상대 시간)로 한다.
    """

    __tablename__ = "timeline_events"
    __table_args__ = (Index("ix_timeline_sort", "novel_id", "sort_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    novel_id: Mapped[int] = mapped_column(
        ForeignKey("novels.id", ondelete="CASCADE"), index=True
    )
    episode_number: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    occurred_at: Mapped[str] = mapped_column(String(100), default="")
    sort_key: Mapped[float] = mapped_column(Float, default=0.0)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    participants: Mapped[list[str]] = mapped_column(JSON, default=list)
    location: Mapped[str] = mapped_column(String(200), default="")
    importance: Mapped[int] = mapped_column(Integer, default=1)  # 1~5

    novel: Mapped[Novel] = relationship(back_populates="timeline")


class Foreshadowing(TimestampMixin, Base):
    """복선 하나 (기획안 27번).

    status: OPEN / DEVELOPING / RESOLVED / ABANDONED
    완결 검사(기획안 47번)는 OPEN과 DEVELOPING이 남아 있으면 통과시키지 않는다.
    """

    __tablename__ = "foreshadowings"
    __table_args__ = (
        UniqueConstraint("novel_id", "code", name="uq_foreshadow_code"),
        Index("ix_foreshadow_status", "novel_id", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    novel_id: Mapped[int] = mapped_column(
        ForeignKey("novels.id", ondelete="CASCADE"), index=True
    )
    code: Mapped[str] = mapped_column(String(20))  # F013
    description: Mapped[str] = mapped_column(Text)
    setup_episode: Mapped[int] = mapped_column(Integer)
    planned_payoff: Mapped[int | None] = mapped_column(Integer, nullable=True)
    actual_payoff: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="OPEN")
    mentions: Mapped[list[int]] = mapped_column(JSON, default=list)
    importance: Mapped[int] = mapped_column(Integer, default=3)
    note: Mapped[str] = mapped_column(Text, default="")

    novel: Mapped[Novel] = relationship(back_populates="foreshadowings")


class StyleBible(TimestampMixin, Base):
    """작품 문체 (기획안 32번).

    참고작 문체 통계를 그대로 복사하는 자리가 아니다. 참고작에서는 수치만
    가져오고, 금지/선호 항목은 이 작품 고유의 규칙이다.
    """

    __tablename__ = "style_bibles"

    id: Mapped[int] = mapped_column(primary_key=True)
    novel_id: Mapped[int] = mapped_column(
        ForeignKey("novels.id", ondelete="CASCADE"), unique=True, index=True
    )

    target_sentence_chars: Mapped[float] = mapped_column(Float, default=20.0)
    target_paragraph_chars: Mapped[float] = mapped_column(Float, default=90.0)
    target_dialogue_ratio: Mapped[float] = mapped_column(Float, default=0.40)
    target_narration_ratio: Mapped[float] = mapped_column(Float, default=0.45)
    target_inner_ratio: Mapped[float] = mapped_column(Float, default=0.15)
    pov: Mapped[str] = mapped_column(String(30), default="3인칭 제한")
    tense: Mapped[str] = mapped_column(String(20), default="과거")

    forbidden: Mapped[list[str]] = mapped_column(JSON, default=list)
    preferred: Mapped[list[str]] = mapped_column(JSON, default=list)
    # 최근 N화에서 반복되면 제한할 표현 (기획안 33번)
    throttled_phrases: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    novel: Mapped[Novel] = relationship(back_populates="style_bible")


# ---------------------------------------------------------------------------
# 참고작
# ---------------------------------------------------------------------------
class ReferenceNovel(TimestampMixin, Base):
    """참고소설과 그 분석 결과.

    원문은 이 테이블에 없다. 파일은 디스크에, 분석 결과만 여기 JSON으로
    들어온다 (기획안 56번 원칙).
    """

    __tablename__ = "reference_novels"

    id: Mapped[int] = mapped_column(primary_key=True)
    reference_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(300), default="")
    author: Mapped[str] = mapped_column(String(200), default="")
    genre: Mapped[str] = mapped_column(String(50), index=True, default="")

    source_format: Mapped[str] = mapped_column(String(20), default="")
    encoding: Mapped[str] = mapped_column(String(30), default="")
    content_hash: Mapped[str] = mapped_column(String(64), default="", index=True)
    stored_path: Mapped[str] = mapped_column(Text, default="")
    extracted_path: Mapped[str] = mapped_column(Text, default="")
    char_count: Mapped[int] = mapped_column(Integer, default=0)
    episode_count: Mapped[int] = mapped_column(Integer, default=0)

    status: Mapped[str] = mapped_column(String(20), default="imported", index=True)
    # imported -> analyzing -> analyzed -> failed
    analyzed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    profile: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    warnings: Mapped[list[str]] = mapped_column(JSON, default=list)

    fingerprints: Mapped[list[ReferenceFingerprint]] = relationship(
        back_populates="reference", cascade="all, delete-orphan"
    )


class ReferenceFingerprint(Base):
    """참고작 회차 하나의 해시 지문 (기획안 20번).

    원문을 복원할 수 없다. Similarity Checker 전용이다.
    """

    __tablename__ = "reference_fingerprints"
    __table_args__ = (
        UniqueConstraint("reference_id", "episode_seq", name="uq_fingerprint_episode"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    reference_id: Mapped[int] = mapped_column(
        ForeignKey("reference_novels.id", ondelete="CASCADE"), index=True
    )
    episode_seq: Mapped[int] = mapped_column(Integer)
    shingle_size: Mapped[int] = mapped_column(Integer, default=8)
    # 64비트 해시를 JSON 배열로. PostgreSQL로 가면 BIGINT[]로 바꾸는 게 낫다.
    hashes: Mapped[list[int]] = mapped_column(JSON, default=list)

    reference: Mapped[ReferenceNovel] = relationship(back_populates="fingerprints")


class ReferenceLink(TimestampMixin, Base):
    """작품과 참고작의 연결, 그리고 참고 강도 (기획안 16·50번)."""

    __tablename__ = "reference_links"
    __table_args__ = (
        UniqueConstraint("novel_id", "reference_id", name="uq_reference_link"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    novel_id: Mapped[int] = mapped_column(
        ForeignKey("novels.id", ondelete="CASCADE"), index=True
    )
    reference_id: Mapped[int] = mapped_column(
        ForeignKey("reference_novels.id", ondelete="CASCADE"), index=True
    )
    # 항목별 0~1 가중치. {"pacing": 0.8, "style": 0.0}
    weights: Mapped[dict[str, float]] = mapped_column(JSON, default=dict)
    default_weight: Mapped[float] = mapped_column(Float, default=1.0)

    novel: Mapped[Novel] = relationship(back_populates="reference_links")


class ReferencePatternRow(TimestampMixin, Base):
    """재사용 패턴 (기획안 19번)."""

    __tablename__ = "reference_patterns"
    __table_args__ = (Index("ix_pattern_genre_aspect", "genre", "aspect"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    pattern_id: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    genre: Mapped[str] = mapped_column(String(50), default="")
    aspect: Mapped[str] = mapped_column(String(40), default="")
    instruction: Mapped[str] = mapped_column(Text)
    effect: Mapped[str] = mapped_column(Text, default="")
    placement: Mapped[str] = mapped_column(String(100), default="")
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    source: Mapped[str] = mapped_column(String(20), default="derived")
    evidence: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


# ---------------------------------------------------------------------------
# 의미 검색
# ---------------------------------------------------------------------------
class MemoryChunk(TimestampMixin, Base):
    """과거 장면을 의미로 찾기 위한 조각 (기획안 41번).

    임베딩을 JSON 배열로 둔다. pgvector로 옮길 때 이 컬럼만 vector 타입으로
    바꾸면 된다. 그때까지는 파이썬에서 코사인 유사도를 계산한다.
    """

    __tablename__ = "memory_chunks"
    __table_args__ = (Index("ix_memory_novel_kind", "novel_id", "kind"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    novel_id: Mapped[int] = mapped_column(
        ForeignKey("novels.id", ondelete="CASCADE"), index=True
    )
    episode_number: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String(30), default="scene")
    # scene / summary / character / world / foreshadow
    content: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float] | None] = mapped_column(JSON, nullable=True)
    meta: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
