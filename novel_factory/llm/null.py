"""LLM이 설정되지 않았을 때 쓰는 제공자.

조용히 빈 문자열을 돌려주지 않는다. 그러면 LLM 없이 돌아가는 것처럼
보이다가 원고가 통째로 비는 사고가 난다. 호출되면 예외를 던지고,
호출 전에 available로 걸러내도록 한다.
"""

from __future__ import annotations

from novel_factory.errors import LLMNotConfiguredError
from novel_factory.llm.base import Completion, LLMProvider, Message


class NullProvider(LLMProvider):
    name = "null"

    @property
    def available(self) -> bool:
        return False

    def complete(
        self,
        messages: list[Message],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        stop: list[str] | None = None,
        json_mode: bool = False,
    ) -> Completion:
        raise LLMNotConfiguredError(
            "LLM이 설정되지 않았습니다. NF_LLM_BASE_URL과 NF_LLM_MODEL을 지정하세요. "
            "참고작 분석(Phase 1)과 장기기억 DB(Phase 2)는 LLM 없이 동작합니다."
        )


class EchoProvider(LLMProvider):
    """테스트용. 받은 프롬프트를 그대로 돌려준다."""

    name = "echo"

    def __init__(self, responses: list[str] | None = None) -> None:
        self.responses = list(responses or [])
        self.calls: list[list[Message]] = []

    def complete(
        self,
        messages: list[Message],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        stop: list[str] | None = None,
        json_mode: bool = False,
    ) -> Completion:
        self.calls.append(list(messages))
        if self.responses:
            return Completion(text=self.responses.pop(0), model="echo")
        return Completion(text=messages[-1].content if messages else "", model="echo")
