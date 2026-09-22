"""도메인 예외."""

from __future__ import annotations


class NovelFactoryError(Exception):
    """이 프로젝트가 던지는 모든 예외의 뿌리."""


class UnsupportedFormatError(NovelFactoryError):
    """파서가 없는 파일 형식."""


class ParseError(NovelFactoryError):
    """파일은 읽었지만 텍스트를 뽑지 못한 경우."""


class MissingDependencyError(NovelFactoryError):
    """선택 의존성이 설치되지 않은 경우."""


class NotFoundError(NovelFactoryError):
    """DB에 없는 리소스."""


class LLMError(NovelFactoryError):
    """LLM 호출 실패."""


class LLMNotConfiguredError(LLMError):
    """LLM_BASE_URL / LLM_MODEL 미설정."""
