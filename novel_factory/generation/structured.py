"""구조화 응답 (JSON) 받기.

계획·장면 설계·사실 추출은 정해진 형식의 답이 필요하다. 로컬 모델은 형식을
자주 어긴다. 실제로 흔한 모양은 이렇다.

  - 추론 모델이 답 앞에 <think> ... </think>를 붙인다 (Qwen3, DeepSeek-R1 계열)
  - ```json ... ``` 코드펜스로 감싼다
  - "다음은 계획입니다:" 같은 말을 앞뒤에 붙인다
  - 마지막 항목 뒤에 쉼표를 남긴다

이 모듈은 그런 겉포장을 걷어내고, pydantic으로 내용을 검증하고, 틀리면 무엇이
틀렸는지 알려 주며 다시 요청한다. 서버의 JSON 모드(response_format)는 켜져
있으면 쓰지만 거기에 기대지 않는다.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from novel_factory.errors import LLMError
from novel_factory.llm.base import LLMProvider, Message

ModelT = TypeVar("ModelT", bound=BaseModel)

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
# 닫히지 않은 <think> (출력이 잘린 경우)
_OPEN_THINK_RE = re.compile(r"<think>.*\Z", re.DOTALL | re.IGNORECASE)
_FENCE_RE = re.compile(r"```(?:json|JSON)?\s*(.*?)```", re.DOTALL)
_TRAILING_COMMA_RE = re.compile(r",(\s*[}\]])")


class StructuredOutputError(LLMError):
    """재시도를 다 써도 형식에 맞는 답을 받지 못했다."""

    def __init__(self, message: str, attempts: list[str]) -> None:
        super().__init__(message)
        self.attempts = attempts


def strip_reasoning(text: str) -> str:
    """추론 모델의 <think> 블록을 지운다."""
    text = _THINK_RE.sub("", text)
    return _OPEN_THINK_RE.sub("", text)


def _balanced_span(text: str, start: int) -> int | None:
    """text[start]의 여는 괄호에 짝이 맞는 닫는 괄호 위치.

    문자열 안의 괄호와 이스케이프를 건너뛴다. 대사에 {, [ 가 들어 있어도
    잘리지 않게 하려면 이 처리가 필요하다.
    """
    opener = text[start]
    closer = "}" if opener == "{" else "]"
    depth = 0
    in_string = False
    escaped = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == opener:
            depth += 1
        elif ch == closer:
            depth -= 1
            if depth == 0:
                return i
    return None


def extract_json(text: str) -> Any:
    """모델 응답에서 JSON 값 하나를 꺼낸다. 실패하면 ValueError."""
    if not text or not text.strip():
        raise ValueError("응답이 비어 있습니다.")
    cleaned = strip_reasoning(text).strip()

    candidates: list[str] = []
    fenced = _FENCE_RE.findall(cleaned)
    candidates.extend(f.strip() for f in fenced)
    candidates.append(cleaned)

    last_error: Exception | None = None
    for candidate in candidates:
        for i, ch in enumerate(candidate):
            if ch not in "{[":
                continue
            end = _balanced_span(candidate, i)
            if end is None:
                continue
            chunk = candidate[i : end + 1]
            for attempt in (chunk, _TRAILING_COMMA_RE.sub(r"\1", chunk)):
                try:
                    return json.loads(attempt)
                except json.JSONDecodeError as exc:
                    last_error = exc
            # 첫 번째로 균형이 맞는 덩어리가 JSON이 아니면 다음 여는 괄호를 본다.
    raise ValueError(f"JSON을 찾지 못했습니다: {last_error or '여는 괄호 없음'}")


def _describe_validation_error(exc: ValidationError) -> str:
    lines = []
    for err in exc.errors()[:8]:
        loc = ".".join(str(p) for p in err.get("loc", ())) or "(최상위)"
        lines.append(f"- {loc}: {err.get('msg')}")
    return "\n".join(lines)


@dataclass(slots=True)
class StructuredResult:
    value: BaseModel
    attempts: int
    raw: list[str] = field(default_factory=list)


def request_structured(
    provider: LLMProvider,
    messages: list[Message],
    schema: type[ModelT],
    *,
    retries: int = 2,
    temperature: float | None = 0.3,
    max_tokens: int | None = None,
    json_mode: bool = True,
) -> tuple[ModelT, StructuredResult]:
    """형식에 맞는 답을 받을 때까지 (retries번까지) 다시 요청한다.

    재요청할 때는 모델이 낸 답과 무엇이 틀렸는지를 대화에 붙인다. 같은
    요청을 그대로 반복하면 로컬 모델은 같은 실수를 되풀이하는 경우가 많다.
    """
    conversation = list(messages)
    raw_outputs: list[str] = []
    last_problem = ""

    for attempt in range(retries + 1):
        completion = provider.complete(
            conversation,
            temperature=temperature,
            max_tokens=max_tokens,
            json_mode=json_mode,
        )
        raw = completion.text
        raw_outputs.append(raw)

        try:
            data = extract_json(raw)
            value = schema.model_validate(data)
        except ValueError as exc:
            if isinstance(exc, ValidationError):
                last_problem = "형식은 JSON이지만 내용이 규격과 다릅니다.\n" + (
                    _describe_validation_error(exc)
                )
            else:
                last_problem = f"JSON으로 읽을 수 없습니다: {exc}"
            if completion.truncated:
                last_problem += "\n응답이 길이 제한에 걸려 잘렸습니다. 더 짧게 답하세요."
        else:
            return value, StructuredResult(
                value=value, attempts=attempt + 1, raw=raw_outputs
            )

        if attempt < retries:
            conversation = [
                *conversation,
                Message("assistant", raw),
                Message(
                    "user",
                    "앞의 답을 쓸 수 없습니다.\n"
                    f"{last_problem}\n"
                    "설명 없이 규격에 맞는 JSON 하나만 다시 출력하세요.",
                ),
            ]

    raise StructuredOutputError(
        f"{retries + 1}번 요청했지만 형식에 맞는 답을 받지 못했습니다. "
        f"마지막 문제: {last_problem}",
        raw_outputs,
    )
