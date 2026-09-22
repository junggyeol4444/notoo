"""파서 공통 인터페이스."""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(slots=True)
class RawChapter:
    """파일 포맷 자체가 알려준 챕터.

    EPUB의 spine 항목이나 DOCX의 제목 스타일처럼 '포맷이 이미 구분해 둔'
    단위를 말한다. 회차 분리기(structure)는 이 정보가 있으면 그대로 쓰고,
    없으면 본문에서 마커를 찾는다.
    """

    title: str
    text: str
    order: int


@dataclass(slots=True)
class ParsedDocument:
    """파서 출력."""

    text: str
    source_format: str
    encoding: str = "utf-8"
    title: str | None = None
    author: str | None = None
    raw_chapters: list[RawChapter] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def has_native_chapters(self) -> bool:
        # 챕터가 하나뿐이면 포맷이 나눠준 게 아니라 통짜 파일이다.
        return len(self.raw_chapters) > 1


class BaseParser(abc.ABC):
    """모든 파서의 기반."""

    #: 이 파서가 담당하는 확장자 (소문자, 점 포함)
    extensions: tuple[str, ...] = ()
    #: 포맷 이름
    format_name: str = ""

    @abc.abstractmethod
    def parse(self, path: Path) -> ParsedDocument:
        """파일 경로를 받아 텍스트를 뽑는다."""

    def supports(self, path: Path) -> bool:
        return path.suffix.lower() in self.extensions
