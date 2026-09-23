"""Reader Simulation (기획안 37번) — 가상 독자가 회차를 읽고 점수를 매긴다.

    페르소나: 웹소설 독자 / 판타지 독자 / 로맨스 독자 / 까다로운 독자 (설정으로 바꾼다)
    평가:     몰입도 / 전개속도 / 캐릭터 매력 / 갈등 / 보상 / 클리프행어 / 지루한 구간

점수는 참고용이다. LLM이 매긴 점수로 원고를 자동으로 고치지 않는다(FAIL 없음).
지루한 구간은 인용이 원고에 실제로 있을 때만 WARN으로 남기고, 없으면 버린다.

페르소나마다 원고 전체를 따로 읽힌다. 한 번에 여러 독자를 흉내 내게 하면 로컬
모델은 점수를 서로 비슷하게 맞추는 경향이 있다. 그 대신 호출 수가 페르소나 수만큼
늘어나므로 기본 설정에서는 꺼 둔다 (NF_QUALITY_READER).
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from novel_factory.config import Settings, get_settings
from novel_factory.database.models import Episode, Novel
from novel_factory.generation.prompts import READER_SYSTEM, build_reader_prompt
from novel_factory.generation.schemas import ReaderOut, ReaderScores
from novel_factory.generation.structured import StructuredOutputError, request_structured
from novel_factory.llm.base import LLMProvider, Message
from novel_factory.quality.logic import verified_quote
from novel_factory.quality.report import CheckResult, Issue, Severity, locate_scene

CHECKER = "Reader"
READER_OUTPUT_TOKENS = 1500
SCORE_FIELDS: tuple[str, ...] = tuple(ReaderScores.model_fields)


def simulate_readers(
    session: Session,
    novel: Novel,
    episode: Episode,
    provider: LLMProvider | None,
    *,
    personas: list[str] | None = None,
    settings: Settings | None = None,
) -> CheckResult:
    cfg = settings or get_settings()
    result = CheckResult(CHECKER)
    text = episode.final_text or ""
    who = [
        p for p in (personas if personas is not None else cfg.quality_reader_personas) if p
    ]
    if not text.strip():
        result.skipped = "원고가 없다."
        return result
    if provider is None or not provider.available:
        result.skipped = "LLM이 설정되지 않았다."
        return result
    if not who:
        result.skipped = "페르소나가 없다."
        return result

    scene_texts = [str(s.get("text") or "") for s in (episode.scenes or [])]
    per_persona: dict[str, object] = {}
    failed: list[str] = []
    for persona in who:
        prompt = build_reader_prompt(
            persona, episode_number=episode.number, genre=novel.genre, text=text
        )
        try:
            out, _ = request_structured(
                provider,
                [Message("system", READER_SYSTEM), Message("user", prompt)],
                ReaderOut,
                retries=cfg.llm_structured_retries,
                temperature=cfg.llm_structured_temperature,
                max_tokens=READER_OUTPUT_TOKENS,
            )
        except StructuredOutputError:
            failed.append(persona)
            continue

        boring: list[dict[str, object]] = []
        for part in out.boring_parts:
            quote = verified_quote(part.quote, text)
            if not quote:
                continue
            scene = locate_scene(quote, scene_texts)
            boring.append({"quote": quote, "reason": part.reason, "scene_index": scene})
            result.issues.append(
                Issue(
                    CHECKER,
                    "boring_part",
                    Severity.WARN,
                    f"{persona}: 지루한 구간 — {part.reason.strip() or '(이유 없음)'}",
                    quote,
                    scene,
                    {"persona": persona},
                )
            )
        per_persona[persona] = {
            "scores": out.scores.model_dump(),
            "boring_parts": boring,
            "comment": out.comment,
        }

    if not per_persona:
        result.skipped = "어느 페르소나에서도 형식에 맞는 답을 받지 못했다."
        return result

    scored = [p["scores"] for p in per_persona.values()]  # type: ignore[index]
    result.metrics = {
        "personas": per_persona,
        "average": {
            f: round(sum(s[f] for s in scored) / len(scored), 2) for f in SCORE_FIELDS
        },
        "failed_personas": failed,
    }
    return result
