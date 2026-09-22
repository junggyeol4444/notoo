"""파일 포맷별 파서 레지스트리.

기획안 5.1 지원 형식: TXT / EPUB / DOCX / PDF / Markdown
"""

from __future__ import annotations

from pathlib import Path

from novel_factory.errors import UnsupportedFormatError
from novel_factory.reference.parser.base import BaseParser, ParsedDocument, RawChapter
from novel_factory.reference.parser.docx import DocxParser
from novel_factory.reference.parser.epub import EpubParser
from novel_factory.reference.parser.pdf import PdfParser
from novel_factory.reference.parser.plain import MarkdownParser, TxtParser

PARSERS: tuple[BaseParser, ...] = (
    TxtParser(),
    MarkdownParser(),
    EpubParser(),
    DocxParser(),
    PdfParser(),
)

SUPPORTED_EXTENSIONS: frozenset[str] = frozenset(
    ext for p in PARSERS for ext in p.extensions
)


def get_parser(path: Path) -> BaseParser:
    for parser in PARSERS:
        if parser.supports(path):
            return parser
    raise UnsupportedFormatError(
        f"지원하지 않는 형식입니다: {path.suffix or '(확장자 없음)'} "
        f"(지원: {', '.join(sorted(SUPPORTED_EXTENSIONS))})"
    )


def parse_file(path: Path) -> ParsedDocument:
    """경로에 맞는 파서를 골라 텍스트를 뽑는다."""
    return get_parser(path).parse(path)


__all__ = [
    "BaseParser",
    "ParsedDocument",
    "RawChapter",
    "PARSERS",
    "SUPPORTED_EXTENSIONS",
    "get_parser",
    "parse_file",
]
