"""TXT / Markdown 파서."""

from __future__ import annotations

import re
from pathlib import Path

from novel_factory.reference.parser.base import BaseParser, ParsedDocument
from novel_factory.text.encoding import decode_bytes

# Markdown에서 본문 통계를 왜곡하는 표기만 걷어낸다.
_MD_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_MD_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_MD_CODEFENCE_RE = re.compile(r"^```.*?^```", re.MULTILINE | re.DOTALL)
_MD_EMPHASIS_RE = re.compile(r"(\*\*|__|\*|_|~~)(.+?)\1", re.DOTALL)
_MD_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$", re.MULTILINE)
_MD_HR_RE = re.compile(r"^\s*(?:[-*_]\s*){3,}$", re.MULTILINE)
_MD_QUOTE_RE = re.compile(r"^>\s?", re.MULTILINE)


class TxtParser(BaseParser):
    extensions = (".txt",)
    format_name = "txt"

    def parse(self, path: Path) -> ParsedDocument:
        text, encoding = decode_bytes(path.read_bytes())
        return ParsedDocument(
            text=text,
            source_format=self.format_name,
            encoding=encoding,
            title=path.stem,
        )


class MarkdownParser(BaseParser):
    extensions = (".md", ".markdown")
    format_name = "markdown"

    def parse(self, path: Path) -> ParsedDocument:
        raw, encoding = decode_bytes(path.read_bytes())
        title = None

        first_heading = _MD_HEADING_RE.search(raw)
        if first_heading:
            title = first_heading.group(2).strip()

        text = _MD_CODEFENCE_RE.sub("", raw)
        text = _MD_IMAGE_RE.sub("", text)
        text = _MD_LINK_RE.sub(r"\1", text)
        text = _MD_HR_RE.sub("", text)
        text = _MD_QUOTE_RE.sub("", text)
        # 제목 기호(#)는 지우되 제목 문구는 남긴다. 회차 분리기가 이걸 마커로 쓴다.
        text = _MD_HEADING_RE.sub(r"\2", text)
        text = _MD_EMPHASIS_RE.sub(r"\2", text)

        return ParsedDocument(
            text=text,
            source_format=self.format_name,
            encoding=encoding,
            title=title or path.stem,
        )
