"""Logic 검사 (기획안 35번) — LLM이 원고를 읽고 논리 문제를 찾는다.

    행동에 동기가 있는가 / 우연에 과도하게 의존하는가 / 갑작스러운 능력이 등장하는가 /
    인물이 모르는 정보를 사용하는가 / 이전 설정과 충돌하는가 / 사건 해결이 지나치게 편리한가

뜻을 읽어야 하는 검사라 규칙으로는 못 한다. 대신 LLM의 지적을 그대로 믿지 않는다.
  - quote가 원고에 실제로 없으면 버린다. LLM은 원고에 없는 문장을 지어내서 지적하기도 한다.
  - 정해 둔 종류(kind)가 아니면 버린다.
  - high만 FAIL(자동 수정 대상)이고 나머지는 WARN이다.
버린 지적은 개수를 metrics에 남긴다.

Continuity 검사(규칙 기반)가 못 보는 성별·직업·외모·능력·장소·관계·보유 아이템·
지식 범위 모순은 여기 contradiction과 knowledge_leak으로 들어온다.
"""

from __future__ import annotations

import re

from sqlalchemy.orm import Session

from novel_factory.config import Settings, get_settings
from novel_factory.database.models import Episode, Novel
from novel_factory.generation.context import prepare_context
from novel_factory.generation.prompts import LOGIC_SYSTEM, build_logic_prompt
from novel_factory.generation.schemas import LogicOut
from novel_factory.generation.structured import StructuredOutputError, request_structured
from novel_factory.llm.base import LLMProvider, Message
from novel_factory.quality.report import (
    CheckResult,
    Issue,
    Severity,
    locate_scene,
    quote_exists,
)
from novel_factory.text.normalize import normalize_text

CHECKER = "Logic"
LOGIC_OUTPUT_TOKENS = 2000

LOGIC_KINDS: dict[str, str] = {
    "unmotivated": "행동에 동기가 없다",
    "coincidence": "우연에 과도하게 의존한다",
    "sudden_ability": "앞에서 보여 주지 않은 능력이 갑자기 나온다",
    "knowledge_leak": "인물이 모르는 정보를 말하거나 행동의 근거로 쓴다 ('정보 제한' 참고)",
    "contradiction": (
        "이전 설정(성별, 직업, 외모, 능력, 장소, 관계, 보유 아이템, 시간선)과 충돌한다"
    ),
    "convenient": "사건 해결이 지나치게 편리하다",
}

# LLM이 인용을 줄이려고 넣는 말줄임표. 앞뒤 조각이 각각 원고에 있으면 인정한다.
_ELLIPSIS_RE = re.compile(r"…|\.{3,}")


def verified_quote(quote: str, text: str) -> str:
    """원고에서 확인된 인용이면 그 인용(정규화한 것), 아니면 빈 문자열."""
    quote = normalize_text(quote or "").strip().strip("\"'“”‘’")
    if not quote:
        return ""
    pieces = [p.strip() for p in _ELLIPSIS_RE.split(quote) if p.strip()]
    if not pieces:
        return ""
    if all(quote_exists(p, text) for p in pieces):
        return max(pieces, key=len)
    return ""


def check_logic(
    session: Session,
    novel: Novel,
    episode: Episode,
    provider: LLMProvider | None,
    *,
    settings: Settings | None = None,
) -> CheckResult:
    cfg = settings or get_settings()
    result = CheckResult(CHECKER)
    text = episode.final_text or ""
    if not text.strip():
        result.skipped = "원고가 없다."
        return result
    if provider is None or not provider.available:
        result.skipped = "LLM이 설정되지 않았다."
        return result

    plan = dict((episode.outline or {}).get("plan") or {})
    _ctx, block, _report = prepare_context(
        session,
        novel,
        episode.number,
        cfg,
        output_tokens=LOGIC_OUTPUT_TOKENS,
        extra_prompt_chars=len(text) + len(str(plan)),
    )
    prompt = build_logic_prompt(
        block,
        episode_number=episode.number,
        episode_plan=plan,
        text=text,
        kinds=LOGIC_KINDS,
    )
    try:
        out, structured = request_structured(
            provider,
            [Message("system", LOGIC_SYSTEM), Message("user", prompt)],
            LogicOut,
            retries=cfg.llm_structured_retries,
            temperature=cfg.llm_structured_temperature,
            max_tokens=LOGIC_OUTPUT_TOKENS,
        )
    except StructuredOutputError as exc:
        result.skipped = f"형식에 맞는 답을 받지 못했다 ({len(exc.attempts)}회 시도)."
        return result

    scene_texts = [str(s.get("text") or "") for s in (episode.scenes or [])]
    unknown_kind = unverified = 0
    for raw in out.issues:
        kind = raw.kind.strip()
        if kind not in LOGIC_KINDS:
            unknown_kind += 1
            continue
        quote = verified_quote(raw.quote, text)
        if not quote:
            unverified += 1
            continue
        severity = (
            Severity.FAIL if raw.severity.strip().lower() == "high" else Severity.WARN
        )
        result.issues.append(
            Issue(
                CHECKER,
                kind,
                severity,
                f"{LOGIC_KINDS[kind]}: {raw.explanation.strip() or '(설명 없음)'}",
                quote,
                locate_scene(quote, scene_texts),
                {"llm_severity": raw.severity},
            )
        )

    result.metrics = {
        "reported": len(out.issues),
        "kept": len(result.issues),
        "dropped_unverified_quote": unverified,
        "dropped_unknown_kind": unknown_kind,
        "attempts": structured.attempts,
    }
    return result
