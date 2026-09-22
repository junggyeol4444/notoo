"""집필 엔진 연결.

로컬 추론 서버만 바라본다. 주소와 모델 이름만 주면 붙는다.

    NF_LLM_BASE_URL=http://localhost:11434/v1
    NF_LLM_MODEL=<올려 둔 모델 이름>

설정이 없으면 NullProvider가 나오고, 호출하면 예외를 던진다.
조용히 빈 문자열을 돌려주는 쪽이 더 위험하다.
"""

from novel_factory.config import Settings, get_settings
from novel_factory.llm.base import Completion, LLMProvider, Message
from novel_factory.llm.null import EchoProvider, NullProvider
from novel_factory.llm.openai_compat import OpenAICompatProvider


def get_provider(settings: Settings | None = None) -> LLMProvider:
    cfg = settings or get_settings()
    if not cfg.llm_enabled:
        return NullProvider()
    return OpenAICompatProvider.from_settings(cfg)


__all__ = [
    "Completion",
    "EchoProvider",
    "LLMProvider",
    "Message",
    "NullProvider",
    "OpenAICompatProvider",
    "get_provider",
]
