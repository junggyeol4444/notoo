"""LLM 제공자 인터페이스.

특정 회사의 SDK에 묶지 않는다. 이 프로젝트가 LLM에 요구하는 것은
"프롬프트를 주면 텍스트를 돌려준다" 하나뿐이고, 그건 어느 엔진이든 된다.

Phase 1(참고작 분석)과 Phase 2(장기기억 DB)는 LLM 없이 완결된다.
LLM이 필요해지는 곳은 Phase 3 이후의 집필과 계획이다.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field


@dataclass(slots=True)
class Message:
    role: str  # system / user / assistant
    content: str

    def as_dict(self) -> dict[str, str]:
        return {"role": self.role, "content": self.content}


@dataclass(slots=True)
class Completion:
    text: str
    model: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    finish_reason: str = ""
    raw: dict = field(default_factory=dict)

    @property
    def truncated(self) -> bool:
        """길이 제한에 걸려 잘렸는가.

        5,000자짜리 회차를 뽑을 때 이게 True면 원고가 문장 중간에서
        끊긴 것이다. 조용히 넘어가면 안 된다.
        """
        return self.finish_reason in ("length", "max_tokens")


class LLMProvider(abc.ABC):
    """모든 LLM 클라이언트의 기반."""

    name: str = "base"

    @property
    def available(self) -> bool:
        return True

    @abc.abstractmethod
    def complete(
        self,
        messages: list[Message],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        stop: list[str] | None = None,
    ) -> Completion:
        """대화를 주고 이어질 텍스트를 받는다."""

    def ask(
        self,
        prompt: str,
        *,
        system: str = "",
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> str:
        """한 번의 질문에 한 번의 답. 편의 함수."""
        messages: list[Message] = []
        if system:
            messages.append(Message("system", system))
        messages.append(Message("user", prompt))
        return self.complete(messages, temperature=temperature, max_tokens=max_tokens).text
