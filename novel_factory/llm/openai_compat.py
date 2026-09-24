"""OpenAI 호환 엔드포인트 클라이언트.

로컬에서 돌리는 추론 서버는 대부분 OpenAI의 /v1/chat/completions 형식을
따른다. 그래서 주소와 모델 이름만 받으면 어디든 붙는다.

  Ollama      NF_LLM_BASE_URL=http://localhost:11434/v1
  LM Studio   NF_LLM_BASE_URL=http://localhost:1234/v1
  vLLM        NF_LLM_BASE_URL=http://localhost:8000/v1
  llama.cpp   NF_LLM_BASE_URL=http://localhost:8080/v1
  text-gen-webui  NF_LLM_BASE_URL=http://localhost:5000/v1

API 키는 선택이다. 로컬 서버는 대개 키를 요구하지 않는다.
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from novel_factory.config import Settings, get_settings
from novel_factory.errors import LLMError, LLMNotConfiguredError
from novel_factory.llm.base import Completion, LLMProvider, Message

#: 재시도할 HTTP 상태. 429는 큐가 찼다는 뜻이고 5xx는 서버 쪽 일시 오류다.
_RETRY_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504})


class OpenAICompatProvider(LLMProvider):
    name = "openai-compat"

    def __init__(
        self,
        base_url: str,
        model: str,
        *,
        api_key: str = "",
        timeout: float = 300.0,
        max_retries: int = 2,
        default_temperature: float = 0.8,
        json_mode: bool = False,
        client: httpx.Client | None = None,
    ) -> None:
        if not base_url or not model:
            raise LLMNotConfiguredError(
                "LLM을 쓰려면 NF_LLM_BASE_URL과 NF_LLM_MODEL이 둘 다 필요합니다."
            )
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout = timeout
        self.max_retries = max_retries
        self.default_temperature = default_temperature
        # 서버가 response_format을 지원할 때만 켠다 (NF_LLM_JSON_MODE).
        self.json_mode = json_mode
        self._client = client
        self._owns_client = client is None

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> OpenAICompatProvider:
        cfg = settings or get_settings()
        return cls(
            base_url=cfg.llm_base_url,
            model=cfg.llm_model,
            api_key=cfg.llm_api_key,
            timeout=cfg.llm_timeout_sec,
            max_retries=cfg.llm_max_retries,
            default_temperature=cfg.llm_temperature,
            json_mode=cfg.llm_json_mode,
        )

    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            headers = {"Content-Type": "application/json"}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"
            self._client = httpx.Client(
                base_url=self.base_url, headers=headers, timeout=self.timeout
            )
        return self._client

    def close(self) -> None:
        if self._client is not None and self._owns_client:
            self._client.close()
            self._client = None

    def __enter__(self) -> OpenAICompatProvider:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @property
    def available(self) -> bool:
        """서버가 떠 있고 모델이 올라와 있는지 확인한다."""
        try:
            response = self.client.get("/models", timeout=5.0)
        except httpx.HTTPError:
            return False
        return response.status_code < 400

    def complete(
        self,
        messages: list[Message],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        stop: list[str] | None = None,
        json_mode: bool = False,
    ) -> Completion:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [m.as_dict() for m in messages],
            "temperature": (
                self.default_temperature if temperature is None else temperature
            ),
            "stream": False,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if stop:
            payload["stop"] = stop
        if json_mode and self.json_mode:
            payload["response_format"] = {"type": "json_object"}

        data = self._post_with_retry("/chat/completions", payload)
        return self._parse(data)

    def _post_with_retry(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        return post_json_with_retry(self.client, path, payload, self.max_retries)

    @staticmethod
    def _parse(data: dict[str, Any]) -> Completion:
        choices = data.get("choices") or []
        if not choices:
            raise LLMError(f"LLM 응답에 choices가 없습니다: {str(data)[:200]}")
        choice = choices[0]
        message = choice.get("message") or {}
        text = message.get("content")
        if text is None:
            # 일부 서버는 완성형 API처럼 text를 준다.
            text = choice.get("text", "")
        usage = data.get("usage") or {}
        return Completion(
            text=text or "",
            model=data.get("model", ""),
            prompt_tokens=int(usage.get("prompt_tokens") or 0),
            completion_tokens=int(usage.get("completion_tokens") or 0),
            finish_reason=choice.get("finish_reason") or "",
            raw=data,
        )


def post_json_with_retry(
    client: httpx.Client, path: str, payload: dict[str, Any], max_retries: int
) -> dict[str, Any]:
    """POST하고 JSON을 받는다. 연결 오류와 일시 오류(_RETRY_STATUS)는 다시 시도한다.

    채팅과 임베딩 클라이언트가 같이 쓴다.
    """
    last_error: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            response = client.post(path, json=payload)
        except httpx.HTTPError as exc:
            last_error = exc
        else:
            if response.status_code < 400:
                try:
                    return response.json()
                except ValueError as exc:
                    raise LLMError(
                        f"LLM 응답이 JSON이 아닙니다: {response.text[:200]}"
                    ) from exc
            if response.status_code not in _RETRY_STATUS:
                raise LLMError(
                    f"LLM 요청 실패 [{response.status_code}]: {response.text[:300]}"
                )
            last_error = LLMError(
                f"LLM 요청 실패 [{response.status_code}]: {response.text[:200]}"
            )

        if attempt < max_retries:
            # 로컬 서버가 다른 요청을 처리 중일 때가 많다. 지수 백오프.
            time.sleep(2**attempt)

    raise LLMError(
        f"LLM 호출이 {max_retries + 1}번 모두 실패했습니다: {last_error}"
    ) from last_error
