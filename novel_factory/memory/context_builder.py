"""Writer Context 조립 (기획안 31번).

Phase 2의 결론이다. 250화짜리 작품의 모든 설정을 프롬프트에 넣을 수는
없으니, 이번 화에 필요한 것만 골라서 넘긴다.

무엇을 넣지 않는지가 더 중요하다.

  - 참고소설 원문은 넣지 않는다 (기획안 31·56번).
    참고작에서 넘어가는 것은 Reference Pattern의 지시문뿐이다.
  - 이번 화 이후의 사건은 넣지 않는다.
    미래 정보를 주면 인물이 아직 모르는 일을 말한다.
  - 이번 화에 등장하지 않는 인물의 상세 설정은 넣지 않는다.
  - 이미 회수된 복선은 넣지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from novel_factory.database.models import Character, Episode, Novel
from novel_factory.database.repositories import (
    ArcRepository,
    CharacterRepository,
    EpisodeRepository,
    ForeshadowingRepository,
    KnowledgeRepository,
    PatternRepository,
    RelationshipRepository,
    StyleBibleRepository,
    TimelineRepository,
    WorldRepository,
)

#: Writer에게 넘길 최근 회차 요약 개수
RECENT_EPISODE_COUNT = 5
#: 이번 화에 쓸 수 있는 Reference Pattern 개수 상한
MAX_PATTERNS = 8
#: 임박한 복선으로 볼 여유 회차. 회수 예정이 이 안에 들면 챙긴다.
FORESHADOW_LOOKAHEAD = 20


@dataclass(slots=True)
class WriterContext:
    """한 회차를 쓰기 위해 필요한 것 전부."""

    novel_bible: dict[str, object] = field(default_factory=dict)
    arc: dict[str, object] = field(default_factory=dict)
    episode_number: int = 0
    characters: list[dict[str, object]] = field(default_factory=list)
    relationships: list[dict[str, object]] = field(default_factory=list)
    knowledge_limits: list[dict[str, object]] = field(default_factory=list)
    world: list[dict[str, object]] = field(default_factory=list)
    timeline: list[dict[str, object]] = field(default_factory=list)
    open_foreshadowings: list[dict[str, object]] = field(default_factory=list)
    recent_summaries: list[dict[str, object]] = field(default_factory=list)
    style_bible: dict[str, object] = field(default_factory=dict)
    reference_patterns: list[dict[str, object]] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "novel_bible": self.novel_bible,
            "arc": self.arc,
            "episode_number": self.episode_number,
            "characters": self.characters,
            "relationships": self.relationships,
            "knowledge_limits": self.knowledge_limits,
            "world": self.world,
            "timeline": self.timeline,
            "open_foreshadowings": self.open_foreshadowings,
            "recent_summaries": self.recent_summaries,
            "style_bible": self.style_bible,
            "reference_patterns": self.reference_patterns,
        }

    def to_prompt(self) -> str:
        """LLM에 넘길 한국어 프롬프트 블록.

        Novel Bible을 맨 위에 둔다. 기획안 21번에 따라 Novel Bible이
        Reference Pattern을 이긴다는 것을 순서로도 분명히 한다.
        """
        nb = self.novel_bible
        lines: list[str] = [
            "# 작품 기준 (Novel Bible)",
            f"제목: {nb.get('title', '')}",
            f"장르: {nb.get('genre', '')}",
            f"로그라인: {nb.get('logline', '')}",
            f"분위기: {nb.get('mood', '')}",
            f"시점: {nb.get('pov', '')}",
            f"핵심 갈등: {nb.get('main_conflict', '')}",
            "",
        ]

        if self.arc:
            lines += [
                "# 현재 Arc",
                f"{self.arc.get('name', '')} "
                f"({self.arc.get('start_episode')}~{self.arc.get('end_episode')}화)",
                f"목표: {self.arc.get('goal', '')}",
                f"갈등: {self.arc.get('conflict', '')}",
                "",
            ]

        lines += [f"# 이번 화: {self.episode_number}화", ""]

        if self.characters:
            lines.append("# 등장인물")
            for c in self.characters:
                traits = ", ".join(c.get("personality") or [])  # type: ignore[arg-type]
                lines.append(
                    f"- {c.get('name')} ({c.get('role')}, {c.get('job') or '직업 미상'}): "
                    f"{traits or '성격 미설정'} / 말투: {c.get('speech_style') or '미설정'}"
                )
            lines.append("")

        if self.relationships:
            lines.append("# 인물 관계 (이번 화 시점)")
            for r in self.relationships:
                lines.append(
                    f"- {r.get('source')} → {r.get('target')}: {r.get('state')}"
                )
            lines.append("")

        if self.knowledge_limits:
            lines.append("# 정보 제한 (이 인물은 아래를 모른다. 말하게 하지 말 것)")
            for k in self.knowledge_limits:
                lines.append(f"- {k.get('character')}: {k.get('fact')}")
            lines.append("")

        if self.world:
            lines.append("# 세계관")
            for w in self.world:
                lines.append(
                    f"- [{w.get('category')}] {w.get('name')}: {w.get('description')}"
                )
            lines.append("")

        if self.timeline:
            lines.append("# 지금까지의 시간선")
            for t in self.timeline:
                lines.append(f"- {t.get('occurred_at')} {t.get('title')}")
            lines.append("")

        if self.open_foreshadowings:
            lines.append("# 살아 있는 복선")
            for f in self.open_foreshadowings:
                due = f.get("planned_payoff")
                lines.append(
                    f"- [{f.get('code')}] {f.get('description')} "
                    f"(설치 {f.get('setup_episode')}화"
                    + (f", 회수 예정 {due}화" if due else "")
                    + ")"
                )
            lines.append("")

        if self.recent_summaries:
            lines.append("# 최근 회차 줄거리")
            for e in self.recent_summaries:
                lines.append(f"- {e.get('number')}화: {e.get('summary')}")
            lines.append("")

        if self.style_bible:
            sb = self.style_bible
            lines += [
                "# 문체 기준 (Style Bible)",
                f"평균 문장 {sb.get('target_sentence_chars')}자 / "
                f"문단 {sb.get('target_paragraph_chars')}자",
                f"대사 {float(sb.get('target_dialogue_ratio') or 0) * 100:.0f}% / "
                f"서술 {float(sb.get('target_narration_ratio') or 0) * 100:.0f}% / "
                f"속마음 {float(sb.get('target_inner_ratio') or 0) * 100:.0f}%",
            ]
            forbidden = sb.get("forbidden") or []
            if forbidden:
                lines.append(f"금지: {', '.join(forbidden)}")  # type: ignore[arg-type]
            throttled = sb.get("throttled_phrases") or {}
            if throttled:
                lines.append(
                    f"최근 과다 사용 (이번 화에서 피할 것): {', '.join(throttled)}"  # type: ignore[arg-type]
                )
            lines.append("")

        if self.reference_patterns:
            lines.append(
                "# 참고 패턴 (구조 지침이다. Novel Bible과 충돌하면 Novel Bible을 따른다)"
            )
            for p in self.reference_patterns:
                lines.append(f"- {p.get('instruction')}")
            lines.append("")

        return "\n".join(lines).rstrip()


def _character_dict(c: Character) -> dict[str, object]:
    return {
        "code": c.code,
        "name": c.name,
        "role": c.role,
        "age": c.age,
        "job": c.job,
        "personality": list(c.personality or []),
        "speech_style": c.speech_style,
        "goals": list(c.goals or []),
        "secrets": list(c.secrets or []),
    }


def build_context(
    session: Session,
    novel: Novel,
    episode_number: int,
    *,
    character_codes: list[str] | None = None,
    recent_count: int = RECENT_EPISODE_COUNT,
    max_patterns: int = MAX_PATTERNS,
) -> WriterContext:
    """이번 화를 쓰는 데 필요한 것만 모은다."""
    ctx = WriterContext(episode_number=episode_number)

    ctx.novel_bible = {
        "title": novel.title,
        "genre": novel.genre,
        "logline": novel.logline,
        "premise": novel.premise,
        "mood": novel.mood,
        "pov": novel.pov,
        "target_reader": novel.target_reader,
        "main_conflict": novel.main_conflict,
        "planned_episodes": novel.planned_episodes,
        "target_chars_per_episode": novel.target_chars_per_episode,
    }

    arc = ArcRepository(session).containing_episode(novel.id, episode_number)
    if arc is not None:
        ctx.arc = {
            "name": arc.name,
            "order": arc.order,
            "start_episode": arc.start_episode,
            "end_episode": arc.end_episode,
            "goal": arc.goal,
            "conflict": arc.conflict,
            "emotion_target": arc.emotion_target,
        }

    chars_repo = CharacterRepository(session)
    if character_codes:
        cast = [
            c
            for c in (chars_repo.get_by_code(novel.id, code) for code in character_codes)
            if c is not None
        ]
    else:
        cast = chars_repo.alive_in(novel.id, episode_number)
    ctx.characters = [_character_dict(c) for c in cast]

    # 관계는 이번 화 시점의 상태만.
    rel_repo = RelationshipRepository(session)
    by_id = {c.id: c for c in cast}
    seen: set[tuple[int, int]] = set()
    for a in cast:
        for b in cast:
            if a.id == b.id or (a.id, b.id) in seen:
                continue
            seen.add((a.id, b.id))
            state = rel_repo.state_at(novel.id, a.id, b.id, episode_number)
            if state is not None:
                ctx.relationships.append(
                    {
                        "source": a.name,
                        "target": by_id[b.id].name,
                        "state": state.state,
                        "intensity": state.intensity,
                        "since_episode": state.from_episode,
                    }
                )

    # 정보 제한: 이번 화 등장인물이 '모르는' 사실만 추린다.
    know_repo = KnowledgeRepository(session)
    for c in cast:
        for row in know_repo.for_character(c.id):
            unknown = not row.knows or (
                row.learned_episode is not None and row.learned_episode > episode_number
            )
            if unknown:
                ctx.knowledge_limits.append({"character": c.name, "fact": row.fact})

    ctx.world = [
        {"category": w.category, "name": w.name, "description": w.description}
        for w in WorldRepository(session).for_novel(novel.id)
        if w.first_episode is None or w.first_episode <= episode_number
    ]

    ctx.timeline = [
        {
            "occurred_at": t.occurred_at,
            "title": t.title,
            "episode_number": t.episode_number,
        }
        for t in TimelineRepository(session).up_to_episode(novel.id, episode_number)
    ]

    fs_repo = ForeshadowingRepository(session)
    for f in fs_repo.open_items(novel.id):
        if f.setup_episode > episode_number:
            continue  # 아직 설치되지 않은 복선
        due = f.planned_payoff
        if due is not None and due > episode_number + FORESHADOW_LOOKAHEAD:
            continue  # 한참 뒤에 회수할 복선은 이번 화 프롬프트를 채울 이유가 없다
        ctx.open_foreshadowings.append(
            {
                "code": f.code,
                "description": f.description,
                "setup_episode": f.setup_episode,
                "planned_payoff": due,
                "status": f.status,
                "mentions": list(f.mentions or []),
            }
        )

    ctx.recent_summaries = [
        {"number": e.number, "title": e.title, "summary": e.summary}
        for e in EpisodeRepository(session).recent(
            novel.id, recent_count, before=episode_number
        )
        if e.summary
    ]

    bible = StyleBibleRepository(session).for_novel(novel.id)
    if bible is not None:
        ctx.style_bible = {
            "target_sentence_chars": bible.target_sentence_chars,
            "target_paragraph_chars": bible.target_paragraph_chars,
            "target_dialogue_ratio": bible.target_dialogue_ratio,
            "target_narration_ratio": bible.target_narration_ratio,
            "target_inner_ratio": bible.target_inner_ratio,
            "pov": bible.pov,
            "tense": bible.tense,
            "forbidden": list(bible.forbidden or []),
            "preferred": list(bible.preferred or []),
            "throttled_phrases": list((bible.throttled_phrases or {}).keys()),
        }

    ctx.reference_patterns = [
        {
            "pattern_id": p.pattern_id,
            "aspect": p.aspect,
            "instruction": p.instruction,
            "confidence": p.confidence,
        }
        for p in PatternRepository(session).select_patterns(
            genre=novel.genre, limit=max_patterns
        )
    ]

    return ctx
