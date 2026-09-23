"""계획·집필 단계가 공통으로 쓰는 컨텍스트 준비."""

from __future__ import annotations

from sqlalchemy.orm import Session

from novel_factory.config import Settings
from novel_factory.database.models import Novel
from novel_factory.generation.budget import BudgetReport, fit_context
from novel_factory.memory.context_builder import WriterContext, build_context

#: 시스템 프롬프트, 지시문, 출력 형식 예시에 쓰는 몫 (추정)
PROMPT_OVERHEAD_TOKENS = 2500


def prepare_context(
    session: Session,
    novel: Novel,
    episode_number: int,
    settings: Settings,
    *,
    output_tokens: int,
    extra_prompt_chars: int = 0,
    character_codes: list[str] | None = None,
    keep_names: set[str] | None = None,
) -> tuple[WriterContext, str, BudgetReport]:
    """(줄인 컨텍스트, 프롬프트 블록, 예산 보고).

    extra_prompt_chars는 컨텍스트 말고 같은 요청에 들어갈 글(회차 계획, 직전 원고
    등)의 길이다. 그만큼 예산에서 뺀다.
    """
    ctx = build_context(session, novel, episode_number, character_codes=character_codes)
    budget = (
        settings.llm_context_tokens
        - output_tokens
        - PROMPT_OVERHEAD_TOKENS
        - int(extra_prompt_chars * settings.llm_tokens_per_char)
    )
    fitted, report = fit_context(
        ctx,
        max(budget, 1000),
        tokens_per_char=settings.llm_tokens_per_char,
        keep_names=keep_names,
    )
    return fitted, fitted.to_prompt(), report


def output_tokens_for(chars: int, settings: Settings, *, margin: float = 1.5) -> int:
    """글자 수 목표를 max_tokens로. 모자라서 잘리는 것보다 넉넉한 편이 낫다."""
    return int(chars * settings.llm_tokens_per_char * margin) + 256
