"""작품 설계 (기획안 57번: 새로운 세계관 → 새로운 캐릭터 → 새로운 전체 스토리).

    참고소설 분석 → 공통 패턴 추출 → [여기] Novel Bible · 인물 · 세계관 · 복선 · 문체
    → 전체 스토리(Arc, arc_planner.py) → 집필

LLM이 내용을 짓고, 코드가 숫자와 형식을 지킨다.

  코드가 정하는 것
    인물 수      참고작 캐릭터 구조(character_structure)가 있으면 그 수를 요구한다.
    등장 시점    참고작의 새 인물 등장 간격이 있으면, LLM이 등장 회차를 비워 둔
                 인물을 그 간격으로 배치한다. 주인공은 1화.
    복선 간격    회수 예정 회차를 참고작 복선 간격 범위로 맞춘다.
    코드         C001, F001 같은 코드는 코드가 붙인다.
    문체 수치    문장·문단 길이, 대사 비율은 참고작 수치로 Style Bible에 넣는다.
  LLM이 채우는 것
    제목, 로그라인, 핵심 소재, 분위기, 갈등, 결말, 인물 설정, 세계관, 복선 내용,
    금지/선호 표현.

사용자가 이미 정한 작품 기준(Novel Bible 필드)은 바꾸지 않는다. 기획안 21번
"Novel Bible이 최우선"을 사용자 입력에도 적용한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from novel_factory.config import Settings, get_settings
from novel_factory.database.models import Character, Foreshadowing, Novel, WorldEntry
from novel_factory.database.repositories import (
    CharacterRepository,
    ForeshadowingRepository,
    StyleBibleRepository,
    WorldRepository,
)
from novel_factory.errors import NovelFactoryError
from novel_factory.generation.guidance import GenreGuidance, load_guidance
from novel_factory.generation.prompts import GENESIS_SYSTEM, build_genesis_prompt
from novel_factory.generation.schemas import GenesisOut
from novel_factory.generation.structured import request_structured
from novel_factory.llm.base import LLMProvider, Message

GENESIS_OUTPUT_TOKENS = 6000
ROLES = ("주인공", "주요조연", "조연", "적대자")
#: 제목 없이 만든 작품의 임시 제목. 이 제목이면 작품 설계가 제목을 새로 짓는다.
UNTITLED = "제목 미정"
#: 참고작이 없을 때 LLM에게 주는 인물 수 범위. 요구가 아니라 상한·하한이다.
CAST_RANGE = (4, 12)
#: 첫 회에 같이 나오는 핵심 인물 수 (주인공 포함). 나머지를 등장 간격으로 배치한다.
CORE_CAST = 3

BIBLE_FIELDS = ("logline", "premise", "mood", "main_conflict", "ending", "target_reader")


@dataclass(slots=True)
class GenesisResult:
    novel: Novel
    characters: list[str] = field(default_factory=list)
    world: list[str] = field(default_factory=list)
    foreshadowings: list[str] = field(default_factory=list)
    attempts: int = 0
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        n = self.novel
        return {
            "slug": n.slug,
            "title": n.title,
            "bible": {f: getattr(n, f) for f in ("genre", *BIBLE_FIELDS)},
            "characters": self.characters,
            "world": self.world,
            "foreshadowings": self.foreshadowings,
            "attempts": self.attempts,
            "warnings": self.warnings,
        }


def _cast_rule(guide: GenreGuidance, planned: int) -> str:
    if guide.supporting_count is not None or guide.antagonist_count is not None:
        sup = guide.supporting_count if guide.supporting_count is not None else 3
        ant = guide.antagonist_count if guide.antagonist_count is not None else 1
        rule = f"주인공 1명, 주요조연·조연 합쳐 {sup}명, 적대자 {ant}명을 만든다."
    else:
        low, high = CAST_RANGE
        rule = (
            f"주인공 1명을 포함해 {low}~{high}명을 작품에 맞게 만든다. "
            "적대자를 1명 이상 둔다."
        )
    if guide.character_intro_interval:
        rule += (
            f" 새 인물은 대략 {guide.character_intro_interval:.0f}화마다 한 명씩 등장한다. "
            "처음부터 나오는 핵심 인물만 first_episode를 1로 둔다."
        )
    if planned > 0:
        rule += f" 모든 인물의 first_episode는 {planned} 이하."
    return rule


def _foreshadow_rule(guide: GenreGuidance, planned: int) -> str:
    low, high = guide.foreshadow_span
    count = max(3, min(20, planned // 15)) if planned else 5
    return (
        f"복선 {count}개 안팎. 설치 회차(setup_episode)와 "
        "회수 예정 회차(planned_payoff) 사이는 "
        f"{low:.0f}~{high:.0f}화. 마지막 회수는 {planned}화 이하."
    )


def _next_code(prefix: str, existing: list[str]) -> int:
    nums = [int(c[1:]) for c in existing if c.startswith(prefix) and c[1:].isdigit()]
    return max(nums, default=0) + 1


def apply_genesis(
    session: Session,
    novel: Novel,
    out: GenesisOut,
    guide: GenreGuidance,
) -> GenesisResult:
    """LLM 결과를 검증해 DB에 넣는다. LLM 없이 돈다."""
    result = GenesisResult(novel)
    planned = max(novel.planned_episodes, 1)

    # --- Novel Bible: 사용자가 정한 값은 두고 빈 칸만 채운다 --------------------
    if (not novel.title or novel.title == UNTITLED) and out.title.strip():
        novel.title = out.title.strip()
    for name in BIBLE_FIELDS:
        value = getattr(out, name).strip()
        if value and not getattr(novel, name):
            setattr(novel, name, value)

    # --- 인물 -------------------------------------------------------------------
    chars_repo = CharacterRepository(session)
    existing = chars_repo.for_novel(novel.id)
    taken = {c.name for c in existing}
    has_hero = any(c.role == "주인공" for c in existing)
    code_no = _next_code("C", [c.code for c in existing])
    accepted = []
    for c in out.characters:
        name = c.name.strip()
        if name in taken:
            result.warnings.append(f"이름이 겹치는 인물 '{name}'을 뺐습니다.")
            continue
        role = c.role.strip() if c.role.strip() in ROLES else "조연"
        if role != c.role.strip():
            result.warnings.append(f"'{name}'의 역할 '{c.role}'을 조연으로 바꿨습니다.")
        if role == "주인공" and has_hero:
            role = "주요조연"
            result.warnings.append(
                f"주인공이 이미 있어 '{name}'을 주요조연으로 바꿨습니다."
            )
        has_hero = has_hero or role == "주인공"
        taken.add(name)
        accepted.append((c, name, role))
    if not has_hero and accepted:
        c, name, _ = accepted[0]
        accepted[0] = (c, name, "주인공")
        result.warnings.append(f"주인공이 없어 첫 인물 '{name}'을 주인공으로 정했습니다.")

    # 등장 회차: LLM 값이 범위 안이면 쓰고, 없으면 핵심 인물은 1화, 나머지는 간격대로.
    interval = guide.character_intro_interval
    core = 0
    scheduled = 0
    for c, name, role in accepted:
        first = (
            c.first_episode if c.first_episode and 1 <= c.first_episode <= planned else None
        )
        if role == "주인공":
            first = 1
        if first is None:
            if core < CORE_CAST or not interval:
                first = 1
            else:
                scheduled += 1
                first = min(planned, 1 + round(interval * scheduled))
        if first == 1:
            core += 1
        chars_repo.add(
            Character(
                novel_id=novel.id,
                code=f"C{code_no:03d}",
                name=name,
                role=role,
                gender=c.gender,
                age=c.age,
                job=c.job,
                appearance=c.appearance,
                personality=list(c.personality),
                speech_style=c.speech_style,
                goals=list(c.goals),
                secrets=list(c.secrets),
                abilities=list(c.abilities),
                first_episode=first,
            )
        )
        result.characters.append(f"C{code_no:03d} {name} ({role}, {first}화)")
        code_no += 1

    # --- 세계관 -----------------------------------------------------------------
    world_repo = WorldRepository(session)
    for w in out.world:
        if world_repo.get_by_name(novel.id, w.category, w.name) is not None:
            continue
        session.add(
            WorldEntry(
                novel_id=novel.id,
                category=w.category,
                name=w.name,
                description=w.description,
                first_episode=1,
            )
        )
        result.world.append(f"[{w.category}] {w.name}")

    # --- 복선: 회수 예정을 참고작 간격 범위와 목표 회차 안으로 -------------------
    fs_repo = ForeshadowingRepository(session)
    code_no = _next_code("F", [f.code for f in fs_repo.for_novel(novel.id)])
    low, high = guide.foreshadow_span
    for f in out.foreshadowings:
        setup = min(max(f.setup_episode, 1), max(planned - 1, 1))
        payoff = f.planned_payoff if f.planned_payoff is not None else setup + round(low)
        fixed = min(max(payoff, setup + round(low)), setup + round(high), planned)
        if fixed <= setup:
            result.warnings.append(f"복선 '{f.description}'은 회수할 자리가 없어 뺐습니다.")
            continue
        if fixed != payoff:
            result.warnings.append(
                f"복선 '{f.description}'의 회수 예정을 "
                f"{payoff}화에서 {fixed}화로 맞췄습니다."
            )
        session.add(
            Foreshadowing(
                novel_id=novel.id,
                code=f"F{code_no:03d}",
                description=f.description,
                setup_episode=setup,
                planned_payoff=fixed,
                status="OPEN",
            )
        )
        result.foreshadowings.append(f"F{code_no:03d} {setup}→{fixed}화 {f.description}")
        code_no += 1

    # --- 문체 -------------------------------------------------------------------
    style: dict[str, object] = {
        "forbidden": [x for x in out.style.forbidden if x.strip()],
        "preferred": [x for x in out.style.preferred if x.strip()],
        "target_dialogue_ratio": guide.dialogue_ratio,
    }
    if guide.sentence_chars:
        style["target_sentence_chars"] = round(guide.sentence_chars, 1)
    if guide.paragraph_chars:
        style["target_paragraph_chars"] = round(guide.paragraph_chars, 1)
    bible = StyleBibleRepository(session).for_novel(novel.id)
    if bible is None:
        StyleBibleRepository(session).upsert(novel.id, pov=novel.pov, **style)
    else:
        # 이미 있는 Style Bible은 사용자가 만든 것이다. 금지·선호 표현만 보탠다.
        bible.forbidden = sorted({*(bible.forbidden or []), *style["forbidden"]})  # type: ignore[misc]
        bible.preferred = sorted({*(bible.preferred or []), *style["preferred"]})  # type: ignore[misc]

    session.flush()
    return result


def design_novel(
    session: Session,
    novel: Novel,
    provider: LLMProvider,
    *,
    request: str = "",
    settings: Settings | None = None,
    guidance: GenreGuidance | None = None,
) -> GenesisResult:
    """작품 설계. 인물이 이미 있으면 이미 설계된 것으로 보고 거부한다."""
    cfg = settings or get_settings()
    if not provider.available:
        raise NovelFactoryError(
            "작품 설계에는 LLM이 필요합니다. NF_LLM_BASE_URL/NF_LLM_MODEL."
        )
    if CharacterRepository(session).for_novel(novel.id):
        raise NovelFactoryError(
            f"'{novel.slug}'에는 이미 인물이 있습니다. 작품 설계는 빈 작품에만 합니다."
        )
    guide = guidance or load_guidance(session, novel)
    given = {
        "genre": novel.genre,
        "title": "" if novel.title == UNTITLED else novel.title,
        "pov": novel.pov,
        **{f: getattr(novel, f) for f in BIBLE_FIELDS if getattr(novel, f)},
    }
    prompt = build_genesis_prompt(
        request=request,
        given=given,
        planned_episodes=novel.planned_episodes,
        chars_per_episode=novel.target_chars_per_episode,
        cast_rule=_cast_rule(guide, novel.planned_episodes),
        foreshadow_rule=_foreshadow_rule(guide, novel.planned_episodes),
        pattern_instructions=guide.pattern_instructions,
    )
    out, structured = request_structured(
        provider,
        [Message("system", GENESIS_SYSTEM), Message("user", prompt)],
        GenesisOut,
        retries=cfg.llm_structured_retries,
        temperature=min(cfg.llm_temperature, 0.9),  # 짓는 일이라 계획보다 온도를 높게
        max_tokens=GENESIS_OUTPUT_TOKENS,
    )
    result = apply_genesis(session, novel, out, guide)
    result.attempts = structured.attempts
    return result
