"""임베딩 클라이언트 (기획안 41번 의미 검색용).

로컬 서버의 OpenAI 호환 /v1/embeddings를 부른다.

    NF_EMBEDDING_MODEL=<서버에 올려 둔 임베딩 모델 이름>
    NF_EMBEDDING_BASE_URL=<비우면 NF_LLM_BASE_URL을 쓴다>

글쓰기 모델과 임베딩 모델은 보통 다르다. 그래서 모델 이름은 따로 받는다.
모델 이름이 없으면 임베더를 만들지 않고, 기억 검색은 어휘 검색으로 돈다.

벡터 차원은 설정으로 받지 않고 응답에서 읽는다. 모델을 바꾸면 차원도 바뀌므로,
조각마다 어느 모델로 만든 벡터인지 적어 두고 같은 모델끼리만 비교한다
(memory/retrieval.py).
"""

from __future__ import annotations

from typing import Any

import httpx

from novel_factory.config import Settings, get_settings
from novel_factory.errors import LLMError, LLMNotConfiguredError
from novel_factory.llm.openai_compat import post_json_with_retry


class OpenAICompatEmbedder:
    def __init__(
        self,
        base_url: str,
        model: str,
        *,
        api_key: str = "",
        timeout: float = 120.0,
        max_retries: int = 2,
        batch_size: int = 32,
        client: httpx.Client | None = None,
    ) -> None:
        if not base_url or not model:
            raise LLMNotConfiguredError(
                "임베딩을 쓰려면 NF_EMBEDDING_MODEL과 서버 주소"
                "(NF_EMBEDDING_BASE_URL 또는 NF_LLM_BASE_URL)가 필요합니다."
            )
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout = timeout
        self.max_retries = max_retries
        self.batch_size = max(1, batch_size)
        self._client = client
        self._owns_client = client is None

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> OpenAICompatEmbedder:
        cfg = settings or get_settings()
        return cls(
            base_url=cfg.embedding_base_url or cfg.llm_base_url,
            model=cfg.embedding_model,
            api_key=cfg.embedding_api_key or cfg.llm_api_key,
            timeout=cfg.llm_timeout_sec,
            max_retries=cfg.llm_max_retries,
            batch_size=cfg.embedding_batch_size,
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

    def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for start in range(0, len(texts), self.batch_size):
            batch = texts[start : start + self.batch_size]
            data = post_json_with_retry(
                self.client,
                "/embeddings",
                {"model": self.model, "input": batch},
                self.max_retries,
            )
            out.extend(self._parse(data, len(batch)))
        return out

    @staticmethod
    def _parse(data: dict[str, Any], expected: int) -> list[list[float]]:
        items = data.get("data")
        if not isinstance(items, list) or len(items) != expected:
            raise LLMError(
                f"임베딩 응답 개수가 맞지 않습니다 (요청 {expected}개): {str(data)[:200]}"
            )
        # 순서가 보장되지 않는 서버가 있어 index로 정렬한다.
        ordered = sorted(items, key=lambda d: int(d.get("index", 0)))
        vectors: list[list[float]] = []
        for item in ordered:
            vector = item.get("embedding")
            if not isinstance(vector, list) or not vector:
                raise LLMError(f"임베딩 응답에 벡터가 없습니다: {str(item)[:200]}")
            vectors.append([float(x) for x in vector])
        return vectors


def get_embedder(settings: Settings | None = None) -> OpenAICompatEmbedder | None:
    """임베딩 모델이 설정돼 있으면 임베더, 아니면 None."""
    cfg = settings or get_settings()
    if not cfg.embedding_model or not (cfg.embedding_base_url or cfg.llm_base_url):
        return None
    return OpenAICompatEmbedder.from_settings(cfg)
