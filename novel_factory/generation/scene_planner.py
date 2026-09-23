"""장면 설계 (기획안 30번).

    Scene 1: 목적, 등장인물, 장소, 갈등, 감정
    Scene 2: ...

장면 수와 장면별 목표 분량, 장면의 역할(도입/전개/갈등/보상/클리프행어)은 코드가
정한다. 역할은 참고작의 평균 회차 구조(Reference Profile의 episode_shape)를 따른다.
예를 들어 도입 10% / 전개 45% / 갈등 20% / 보상 15% / 클리프행어 10%이고 장면이
4개면, 각 장면 중간 지점이 어느 구간에 들어가는지로 역할을 붙인다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import model_validator
from sqlalchemy.orm import Session

from novel_factory.config import Settings, get_settings
from novel_factory.database.models import Episode, Novel
from novel_factory.errors import NovelFactoryError
from novel_factory.generation.context import prepare_context
from novel_factory.generation.guidance import GenreGuidance, load_guidance
from novel_factory.generation.prompts import SCENE_SYSTEM, build_scene_prompt
from novel_factory.generation.schemas import ScenePlanOut
from novel_factory.generation.structured import request_structured
from novel_factory.llm.base import LLMProvider, Message

ROLE_DESCRIPTIONS: dict[str, str] = {
    "도입": "도입 — 상황과 인물을 세운다",
    "전개": "전개 — 사건을 밀고 나간다",
    "갈등": "갈등 — 충돌이 드러나고 긴장이 오른다",
    "보상": "보상 — 독자가 기다린 성취나 해소를 준다",
    "반전": "반전 — 예상을 뒤집는다",
    "클리프행어": "클리프행어 — 다음 화를 누르게 만드는 지점에서 끊는다",
    "마무리": "마무리 — 여운을 남기며 닫는다",
}
SCENE_OUTPUT_TOKENS = 3000
MIN_SCENES = 2
MAX_SCENES = 12


@dataclass(slots=True)
class SceneSlot:
    role: str
    target_chars: int


def allocate_scenes(
    target_chars: int,
    shape: dict[str, float],
    *,
    min_chars: int,
    max_chars: int,
    hook_required: bool,
) -> list[SceneSlot]:
    """장면 수·역할·분량을 정한다."""
    middle = (min_chars + max_chars) / 2
    count = round(target_chars / middle) if middle else MIN_SCENES
    count = min(max(count, MIN_SCENES), MAX_SCENES)
    # 장면 하나가 범위를 벗어나면 수를 조정한다.
    while count < MAX_SCENES and target_chars / count > max_chars:
        count += 1
    while count > MIN_SCENES and target_chars / count < min_chars:
        count -= 1

    roles = _assign_roles(count, shape, hook_required=hook_required)
    base, remainder = divmod(target_chars, count)
    return [
        SceneSlot(role, base + (1 if i < remainder else 0)) for i, role in enumerate(roles)
    ]


_MIDDLE_PHASES = ("전개", "갈등", "보상", "반전")


def _assign_roles(count: int, shape: dict[str, float], *, hook_required: bool) -> list[str]:
    """장면 역할.

    첫 장면은 도입, 마지막 장면은 클리프행어(또는 마무리)로 고정한다. 장면 중간점이
    어느 구간에 드는지로 정하면 도입(보통 10%)처럼 작은 구간이 장면을 하나도 못 받는다.
    가운데 장면은 전개·갈등·보상·반전 비율대로 최대 잔여 방식으로 나누고, 구간 순서를
    지킨다.
    """
    last = "클리프행어" if hook_required else "마무리"
    if count == 1:
        return [last]
    head = ["도입"] if count >= 3 and shape.get("도입", 0.0) > 0 else []
    seats = count - len(head) - 1

    weights = [(p, shape.get(p, 0.0)) for p in _MIDDLE_PHASES if shape.get(p, 0.0) > 0]
    if not weights:
        weights = [("전개", 1.0)]
    total = sum(w for _, w in weights)
    quotas = [(p, seats * w / total) for p, w in weights]
    given = {p: int(q) for p, q in quotas}
    leftover = seats - sum(given.values())
    by_remainder = sorted(quotas, key=lambda pq: -(pq[1] - int(pq[1])))
    for p, _ in by_remainder[:leftover]:
        given[p] += 1

    middle = [p for p, _ in weights for _ in range(given[p])]
    return [*head, *middle, last]


def _fixed_count_schema(count: int) -> type[ScenePlanOut]:
    class FixedCount(ScenePlanOut):
        @model_validator(mode="after")
        def _check_count(self) -> FixedCount:
            if len(self.scenes) != count:
                raise ValueError(
                    f"scenes는 정확히 {count}개여야 합니다 (받은 개수 {len(self.scenes)})."
                )
            return self

    return FixedCount


@dataclass(slots=True)
class ScenePlanResult:
    episode: Episode
    scenes: list[dict[str, object]]
    attempts: int
    warnings: list[str] = field(default_factory=list)


def plan_scenes(
    session: Session,
    novel: Novel,
    episode: Episode,
    provider: LLMProvider,
    *,
    guidance: GenreGuidance | None = None,
    settings: Settings | None = None,
) -> ScenePlanResult:
    cfg = settings or get_settings()
    plan = (episode.outline or {}).get("plan")
    directives = (episode.outline or {}).get("directives") or {}
    if not plan:
        raise NovelFactoryError(
            f"{episode.number}화에 회차 계획이 없습니다. 먼저 계획하세요."
        )

    guide = guidance or load_guidance(session, novel)
    hook_required = bool((directives.get("hook") or {}).get("required"))
    slots = allocate_scenes(
        int(directives.get("target_chars") or novel.target_chars_per_episode),
        guide.episode_shape,
        min_chars=cfg.scene_min_chars,
        max_chars=cfg.scene_max_chars,
        hook_required=hook_required,
    )

    cast_codes = list(plan.get("characters") or [])
    _ctx, block, _report = prepare_context(
        session,
        novel,
        episode.number,
        cfg,
        output_tokens=SCENE_OUTPUT_TOKENS,
        extra_prompt_chars=len(str(plan)),
        character_codes=cast_codes or None,
    )
    prompt = build_scene_prompt(
        block,
        plan,
        scene_count=len(slots),
        scene_roles=[
            f"{ROLE_DESCRIPTIONS.get(s.role, s.role)} (약 {s.target_chars:,}자)"
            for s in slots
        ],
    )
    out, result = request_structured(
        provider,
        [Message("system", SCENE_SYSTEM), Message("user", prompt)],
        _fixed_count_schema(len(slots)),
        retries=cfg.llm_structured_retries,
        temperature=cfg.llm_structured_temperature,
        max_tokens=SCENE_OUTPUT_TOKENS,
    )

    known = set(directives.get("cast_codes") or [])
    warnings: list[str] = []
    scenes: list[dict[str, object]] = []
    for i, (scene, slot) in enumerate(zip(out.scenes, slots, strict=True)):
        data = scene.model_dump()
        unknown = [c for c in scene.characters if c not in known]
        if unknown:
            warnings.append(f"장면 {i + 1}: 없는 인물 코드를 뺐습니다: {unknown}")
        data["characters"] = [c for c in scene.characters if c in known]
        data["index"] = i
        data["role"] = slot.role
        data["target_chars"] = slot.target_chars
        scenes.append(data)

    episode.scenes = scenes
    outline = dict(episode.outline or {})
    outline["scene_warnings"] = warnings
    episode.outline = outline
    session.flush()
    return ScenePlanResult(episode, scenes, result.attempts, warnings)
