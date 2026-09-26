"""Memory Update (기획안 39번).

회차가 확정되면 원고에서 새로 생긴 사실을 뽑아 장기기억 DB에 넣는다.

    새 캐릭터 / 새 장소 / 새 사건 / 관계 변화 / 새로운 정보 /
    획득 아이템 / 새 복선 / 회수된 복선

두 단계로 나눈다.

  extract_memory()  LLM이 원고를 읽고 MemoryDelta를 낸다.
  apply_memory()    코드가 MemoryDelta를 검증해서 DB에 반영한다. LLM 없이 돈다.

적용 단계가 LLM 출력을 그대로 믿지 않는 이유: 장기기억에 틀린 사실이 한 번
들어가면 이후 모든 회차의 Writer Context로 퍼진다. 그래서
  - 모르는 인물을 가리키는 관계·지식·아이템 변경은 버린다
  - 이미 있는 이름을 '새 인물'로 넣으려 하면 새로 만들지 않는다
  - 살아 있지 않은 복선 코드를 회수·진전시키려 하면 버린다
그리고 버린 것은 전부 경고로 남긴다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import delete
from sqlalchemy.orm import Session

from novel_factory.config import Settings, get_settings
from novel_factory.database.models import (
    Character,
    Episode,
    Foreshadowing,
    MemoryChunk,
    Novel,
    TimelineEvent,
)
from novel_factory.database.repositories import (
    CharacterRepository,
    ForeshadowingRepository,
    KnowledgeRepository,
    RelationshipRepository,
    TimelineRepository,
    WorldRepository,
)
from novel_factory.database.vector import get_vector_index
from novel_factory.errors import NovelFactoryError
from novel_factory.generation.guidance import CLIFFHANGER_KINDS
from novel_factory.generation.prompts import MEMORY_SYSTEM, build_memory_prompt
from novel_factory.generation.schemas import MemoryDeltaOut
from novel_factory.generation.structured import request_structured
from novel_factory.llm.base import LLMProvider, Message
from novel_factory.reference.analyzer.cliffhanger import (
    NO_CLIFFHANGER,
    classify_cliffhanger,
)

MEMORY_OUTPUT_TOKENS = 4000


@dataclass(slots=True)
class ApplyReport:
    created_characters: list[str] = field(default_factory=list)
    world_entries: list[str] = field(default_factory=list)
    timeline_events: list[str] = field(default_factory=list)
    relationships: list[str] = field(default_factory=list)
    knowledge: list[str] = field(default_factory=list)
    items: list[str] = field(default_factory=list)
    deaths: list[str] = field(default_factory=list)
    new_foreshadowings: list[str] = field(default_factory=list)
    advanced_foreshadowings: list[str] = field(default_factory=list)
    resolved_foreshadowings: list[str] = field(default_factory=list)
    hook_type: str = ""
    rejected: list[str] = field(default_factory=list)
    # 되돌리기 기록. 새로 만든 행의 id와, 바꾼 행의 이전 값.
    # 회차를 다시 만들 때 pipeline.rollback_episode_memory()가 이것만 정확히 되돌린다.
    # 회차 번호로 지우면 사용자가 직접 넣은 같은 회차의 설정까지 지워진다.
    undo: dict[str, list] = field(
        default_factory=lambda: {
            "characters": [],
            "world": [],
            "world_descriptions": [],
            "timeline": [],
            "relationships": [],
            "knowledge_created": [],
            "knowledge_updated": [],
            "items": [],
            "deaths": [],
            "foreshadowings": [],
            "foreshadowing_states": [],
        }
    )

    def as_dict(self) -> dict[str, object]:
        return {
            "created_characters": self.created_characters,
            "world_entries": self.world_entries,
            "timeline_events": self.timeline_events,
            "relationships": self.relationships,
            "knowledge": self.knowledge,
            "items": self.items,
            "deaths": self.deaths,
            "new_foreshadowings": self.new_foreshadowings,
            "advanced_foreshadowings": self.advanced_foreshadowings,
            "resolved_foreshadowings": self.resolved_foreshadowings,
            "hook_type": self.hook_type,
            "rejected": self.rejected,
            "undo": self.undo,
        }


class _CharacterResolver:
    """코드나 이름으로 인물을 찾는다. '김도윤'과 '도윤'도 같은 인물로 본다."""

    def __init__(self, characters: list[Character]) -> None:
        self.by_code = {c.code: c for c in characters}
        self.by_name: dict[str, Character] = {}
        for c in characters:
            self.add(c)

    def add(self, c: Character) -> None:
        self.by_code[c.code] = c
        self.by_name[c.name] = c
        # 성을 뗀 이름. 한 글자 성 + 두 글자 이름인 흔한 경우만.
        if len(c.name) == 3:
            self.by_name.setdefault(c.name[1:], c)

    def find(self, ref: str) -> Character | None:
        ref = ref.strip()
        return self.by_code.get(ref) or self.by_name.get(ref)


def extract_memory(
    session: Session,
    novel: Novel,
    episode: Episode,
    provider: LLMProvider,
    *,
    settings: Settings | None = None,
) -> MemoryDeltaOut:
    cfg = settings or get_settings()
    if not episode.final_text:
        raise NovelFactoryError(f"{episode.number}화에 원고가 없습니다.")
    cast = [
        {"code": c.code, "name": c.name, "role": c.role}
        for c in CharacterRepository(session).for_novel(novel.id)
    ]
    open_fs = [
        {"code": f.code, "description": f.description, "setup_episode": f.setup_episode}
        for f in ForeshadowingRepository(session).open_items(novel.id)
    ]
    planned_hook = str(
        ((episode.outline or {}).get("plan") or {}).get("hook", {}).get("type") or ""
    )
    prompt = build_memory_prompt(
        episode_number=episode.number,
        text=episode.final_text,
        cast=cast,
        open_foreshadowings=open_fs,
        planned_hook_type=planned_hook or None,
    )
    delta, _ = request_structured(
        provider,
        [Message("system", MEMORY_SYSTEM), Message("user", prompt)],
        MemoryDeltaOut,
        retries=cfg.llm_structured_retries,
        temperature=cfg.llm_structured_temperature,
        max_tokens=MEMORY_OUTPUT_TOKENS,
    )
    return delta


def apply_memory(
    session: Session,
    novel: Novel,
    episode: Episode,
    delta: MemoryDeltaOut,
) -> ApplyReport:
    """검증한 뒤 DB에 반영한다. LLM 없이 돈다."""
    n = episode.number
    report = ApplyReport()
    chars_repo = CharacterRepository(session)
    resolver = _CharacterResolver(chars_repo.for_novel(novel.id))

    # --- 새 인물 ------------------------------------------------------------
    for new in delta.new_characters:
        if resolver.find(new.name) is not None:
            report.rejected.append(
                f"'{new.name}'은 이미 있는 인물이라 새로 만들지 않았습니다."
            )
            continue
        character = chars_repo.add(
            Character(
                novel_id=novel.id,
                code=chars_repo.next_code(novel.id),
                name=new.name,
                role=new.role or "조연",
                job=new.job,
                personality=list(new.personality),
                speech_style=new.speech_style,
                first_episode=n,
            )
        )
        resolver.add(character)
        report.undo["characters"].append(character.id)
        report.created_characters.append(f"{character.code} {character.name}")

    # --- 세계관 --------------------------------------------------------------
    world = WorldRepository(session)
    for fact in delta.world:
        existing = world.get_by_name(novel.id, fact.category, fact.name)
        if existing is None:
            entry = world.upsert(
                novel.id,
                fact.category,
                fact.name,
                description=fact.description,
                first_episode=n,
            )
            report.undo["world"].append(entry.id)
            report.world_entries.append(f"[{fact.category}] {fact.name}")
        elif fact.description and not existing.description:
            report.undo["world_descriptions"].append({"id": existing.id, "old": ""})
            existing.description = fact.description
            report.world_entries.append(f"[{fact.category}] {fact.name} (설명 보강)")

    # --- 시간선 --------------------------------------------------------------
    timeline = TimelineRepository(session)
    for event in delta.timeline:
        participants = []
        for ref in event.participants:
            found = resolver.find(ref)
            participants.append(found.code if found else ref)
        event_row = timeline.add(
            TimelineEvent(
                novel_id=novel.id,
                episode_number=n,
                occurred_at=event.occurred_at,
                sort_key=timeline.next_sort_key(novel.id),
                title=event.title,
                participants=participants,
                importance=event.importance,
            )
        )
        report.undo["timeline"].append(event_row.id)
        report.timeline_events.append(event.title)

    # --- 관계 ----------------------------------------------------------------
    relations = RelationshipRepository(session)
    for change in delta.relationships:
        a, b = resolver.find(change.source), resolver.find(change.target)
        if a is None or b is None or a.id == b.id:
            report.rejected.append(
                f"관계 '{change.source} → {change.target}': 인물을 찾지 못했습니다."
            )
            continue
        rows = relations.record(
            novel.id,
            a.id,
            b.id,
            from_episode=n,
            state=change.state,
            intensity=change.intensity,
            note=change.note,
        )
        report.undo["relationships"].extend(r.id for r in rows)
        report.relationships.append(f"{a.name} → {b.name}: {change.state}")

    # --- 지식 ----------------------------------------------------------------
    knowledge = KnowledgeRepository(session)
    for change in delta.knowledge:
        who = resolver.find(change.character)
        if who is None:
            report.rejected.append(
                f"지식 '{change.fact_key}': 인물 '{change.character}'이 없습니다."
            )
            continue
        before = next(
            (k for k in knowledge.for_character(who.id) if k.fact_key == change.fact_key),
            None,
        )
        old = (
            {
                "fact": before.fact,
                "knows": before.knows,
                "learned_episode": before.learned_episode,
                "source": before.source,
            }
            if before is not None
            else None
        )
        row = knowledge.set_knowledge(
            who.id,
            change.fact_key,
            change.fact,
            knows=change.knows,
            learned_episode=n,
            source=f"{n}화",
        )
        if old is None:
            report.undo["knowledge_created"].append(row.id)
        else:
            report.undo["knowledge_updated"].append({"id": row.id, "old": old})
        report.knowledge.append(f"{who.name}: {change.fact}")

    # --- 아이템 --------------------------------------------------------------
    for change in delta.items:
        who = resolver.find(change.character)
        if who is None:
            report.rejected.append(
                f"아이템 '{change.item}': 인물 '{change.character}'이 없습니다."
            )
            continue
        items = list(who.items or [])
        report.undo["items"].append({"character_id": who.id, "old": list(items)})
        if change.action == "lost":
            if change.item in items:
                items.remove(change.item)
        elif change.item not in items:
            items.append(change.item)
        who.items = items
        report.items.append(f"{who.name} {change.action} {change.item}")

    # --- 사망 ----------------------------------------------------------------
    for death in delta.deaths:
        who = resolver.find(death.character)
        if who is None:
            report.rejected.append(f"사망 처리: 인물 '{death.character}'이 없습니다.")
            continue
        report.undo["deaths"].append(
            {
                "character_id": who.id,
                "old": {
                    "is_alive": who.is_alive,
                    "exit_episode": who.exit_episode,
                    "exit_reason": who.exit_reason,
                },
            }
        )
        who.is_alive = False
        who.exit_episode = n
        who.exit_reason = death.cause or "사망"
        report.deaths.append(who.name)

    # --- 복선 ----------------------------------------------------------------
    fs = ForeshadowingRepository(session)
    open_by_code = {f.code: f for f in fs.open_items(novel.id)}
    plan = (episode.outline or {}).get("plan") or {}

    planned_new = list(plan.get("new_foreshadowings") or [])
    seen_descriptions = {f.description for f in fs.for_novel(novel.id)}
    last = novel.planned_episodes or 0
    for item in [*planned_new, *(x.model_dump() for x in delta.new_foreshadowings)]:
        description = str(item.get("description") or "").strip()
        if not description or description in seen_descriptions:
            continue
        if last and n >= last:
            # 마지막 화 뒤에는 회수할 회차가 없다 (완결 검사가 막힌다).
            report.rejected.append(
                f"새 복선 '{description}': 마지막 화라 회수할 자리가 없습니다."
            )
            continue
        seen_descriptions.add(description)
        payoff = item.get("planned_payoff")
        created = fs.add(
            Foreshadowing(
                novel_id=novel.id,
                code=fs.next_code(novel.id),
                description=description,
                setup_episode=n,
                planned_payoff=(min(payoff, last) if last else payoff)
                if isinstance(payoff, int) and payoff > n
                else None,
            )
        )
        report.undo["foreshadowings"].append(created.id)
        report.new_foreshadowings.append(f"{created.code} {description}")

    def remember_state(item: Foreshadowing) -> None:
        if any(x["id"] == item.id for x in report.undo["foreshadowing_states"]):
            return
        report.undo["foreshadowing_states"].append(
            {
                "id": item.id,
                "old": {
                    "status": item.status,
                    "mentions": list(item.mentions or []),
                    "actual_payoff": item.actual_payoff,
                },
            }
        )

    for code in delta.resolved_foreshadowings:
        item = open_by_code.get(code)
        if item is None:
            report.rejected.append(f"복선 회수 '{code}': 살아 있는 복선이 아닙니다.")
            continue
        remember_state(item)
        fs.resolve(item, n)
        report.resolved_foreshadowings.append(code)

    resolved = set(report.resolved_foreshadowings)
    for code in [*delta.advanced_foreshadowings, *(plan.get("foreshadowings_used") or [])]:
        item = open_by_code.get(code)
        if item is None or code in resolved or code in report.advanced_foreshadowings:
            if item is None and code not in resolved:
                report.rejected.append(f"복선 진전 '{code}': 살아 있는 복선이 아닙니다.")
            continue
        remember_state(item)
        fs.mark_mention(item, n)
        report.advanced_foreshadowings.append(code)

    # --- 클리프행어 유형 ------------------------------------------------------
    # 모델이 적은 유형을 우선하되, 목록 밖의 값이면 원고 말미를 규칙으로 판정한다.
    hook = delta.hook_type.strip()
    if hook not in (*CLIFFHANGER_KINDS, NO_CLIFFHANGER):
        tail = episode.final_text[-400:]
        hook = classify_cliffhanger(tail).kind
    report.hook_type = hook

    # --- 회차 확정 ------------------------------------------------------------
    episode.summary = delta.summary
    episode.hook_type = hook
    episode.memory_delta = {"delta": delta.model_dump(), "applied": report.as_dict()}
    episode.status = "final"

    # 같은 회차를 다시 생성했으면 예전 요약 조각을 지운다.
    session.execute(
        delete(MemoryChunk).where(
            MemoryChunk.novel_id == novel.id,
            MemoryChunk.episode_number == n,
            MemoryChunk.kind == "summary",
        )
    )
    get_vector_index().add(
        session,
        novel.id,
        delta.summary,
        kind="summary",
        episode_number=n,
        meta={"title": episode.title},
    )
    session.flush()
    return report
