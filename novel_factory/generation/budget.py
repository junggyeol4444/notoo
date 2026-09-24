"""컨텍스트 예산.

Writer Context가 모델의 컨텍스트 길이를 넘으면 뒤쪽이 잘리거나 서버가 요청을
거부한다. 설정(NF_LLM_CONTEXT_TOKENS) 안에 들어오도록, 덜 중요한 것부터 줄인다.

토큰 수는 추정이다. 토크나이저 없이 '한 글자당 토큰 수'(NF_LLM_TOKENS_PER_CHAR)를
곱해서 센다. 쓰는 모델의 토크나이저로 실측해서 이 값을 맞추면 정확해진다.

줄이는 순서 (먼저 버리는 것부터)
  0. 관련된 과거 장면 발췌    점수 높은 1개는 남긴다
  1. 오래된 회차 요약        최근 2개는 남긴다
  2. 중요도 낮은 시간선 사건  중요도 4 이상과 최근 5개는 남긴다
  3. 세계관 항목              이번 화 계획에 이름이 나오는 것은 남긴다
  4. 참고 패턴               마지막까지 3개는 남긴다
Novel Bible, 등장인물, 관계, 정보 제한, 살아 있는 복선, Style Bible은 줄이지 않는다.
이것들이 빠지면 설정 오류가 난다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace

from novel_factory.memory.context_builder import WriterContext


def estimate_tokens(text: str, tokens_per_char: float) -> int:
    return math.ceil(len(text) * tokens_per_char)


@dataclass(slots=True)
class BudgetReport:
    budget_tokens: int
    estimated_tokens: int
    trimmed: list[str] = field(default_factory=list)
    # 기억 검색 결과 요약 (prepare_context가 채운다)
    memory_search: dict[str, object] = field(default_factory=dict)

    @property
    def fits(self) -> bool:
        return self.estimated_tokens <= self.budget_tokens

    def as_dict(self) -> dict[str, object]:
        return {
            "budget_tokens": self.budget_tokens,
            "estimated_tokens": self.estimated_tokens,
            "fits": self.fits,
            "trimmed": self.trimmed,
            "memory_search": self.memory_search,
        }


def fit_context(
    ctx: WriterContext,
    budget_tokens: int,
    *,
    tokens_per_char: float,
    keep_names: set[str] | None = None,
) -> tuple[WriterContext, BudgetReport]:
    """예산 안에 들어오도록 줄인 사본과 무엇을 줄였는지."""
    keep = keep_names or set()
    current = replace(
        ctx,
        recent_summaries=list(ctx.recent_summaries),
        timeline=list(ctx.timeline),
        world=list(ctx.world),
        reference_patterns=list(ctx.reference_patterns),
        related_scenes=list(ctx.related_scenes),
    )
    trimmed: list[str] = []

    def size() -> int:
        return estimate_tokens(current.to_prompt(), tokens_per_char)

    while size() > budget_tokens and len(current.related_scenes) > 1:
        dropped = current.related_scenes.pop()  # 점수 순이라 끝이 가장 낮다
        trimmed.append(f"과거 장면 {dropped.get('episode_number')}화")

    while size() > budget_tokens and len(current.recent_summaries) > 2:
        dropped = current.recent_summaries.pop(0)
        trimmed.append(f"회차 요약 {dropped.get('number')}화")

    while size() > budget_tokens:
        removable = [
            i
            for i, t in enumerate(current.timeline[:-5])
            if int(t.get("importance") or 1) < 4  # type: ignore[call-overload]
        ]
        if not removable:
            break
        dropped = current.timeline.pop(removable[0])
        trimmed.append(f"시간선 '{dropped.get('title')}'")

    while size() > budget_tokens:
        removable = [
            i for i, w in enumerate(current.world) if str(w.get("name")) not in keep
        ]
        if not removable:
            break
        dropped = current.world.pop(removable[-1])
        trimmed.append(f"세계관 '{dropped.get('name')}'")

    while size() > budget_tokens and len(current.reference_patterns) > 3:
        dropped = current.reference_patterns.pop()
        trimmed.append(f"참고 패턴 {dropped.get('pattern_id')}")

    return current, BudgetReport(budget_tokens, size(), trimmed)
