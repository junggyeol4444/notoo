"""DOCX 파서 (stdlib만 사용).

python-docx 없이 word/document.xml을 직접 읽는다. 필요한 건
문단(w:p), 텍스트 런(w:t), 줄바꿈(w:br), 탭(w:tab), 그리고
제목 스타일(w:pStyle)뿐이다.
"""

from __future__ import annotations

import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from novel_factory.errors import ParseError
from novel_factory.reference.parser.base import BaseParser, ParsedDocument, RawChapter

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_CP = "{http://schemas.openxmlformats.org/package/2006/metadata/core-properties}"
_DC = "{http://purl.org/dc/elements/1.1/}"

_DOCUMENT = "word/document.xml"
_CORE_PROPS = "docProps/core.xml"

# 제목 스타일 이름. 한글 워드는 "제목 1"로 저장되기도 한다.
_HEADING_PREFIXES = ("heading", "title", "제목")


def _paragraph_text(p: ET.Element) -> str:
    parts: list[str] = []
    for node in p.iter():
        tag = node.tag
        if tag == f"{_W}t":
            parts.append(node.text or "")
        elif tag == f"{_W}tab":
            parts.append("\t")
        elif tag in (f"{_W}br", f"{_W}cr"):
            parts.append("\n")
    return "".join(parts)


def _heading_level(p: ET.Element) -> int | None:
    style = p.find(f"{_W}pPr/{_W}pStyle")
    if style is None:
        return None
    val = (style.get(f"{_W}val") or "").strip().lower()
    for prefix in _HEADING_PREFIXES:
        if val.startswith(prefix):
            digits = "".join(ch for ch in val if ch.isdigit())
            return int(digits) if digits else 1
    return None


class DocxParser(BaseParser):
    extensions = (".docx",)
    format_name = "docx"

    def parse(self, path: Path) -> ParsedDocument:
        try:
            with zipfile.ZipFile(path) as zf:
                try:
                    document = ET.fromstring(zf.read(_DOCUMENT))
                except KeyError as exc:
                    raise ParseError(
                        f"DOCX에 {_DOCUMENT}가 없음 (구형 .doc 파일일 수 있음): {path.name}"
                    ) from exc
                title, author = self._read_core_props(zf)
        except zipfile.BadZipFile as exc:
            raise ParseError(
                f"DOCX가 손상되었거나 zip이 아님 (구형 .doc?): {path.name}"
            ) from exc
        except ET.ParseError as exc:
            raise ParseError(f"DOCX 내부 XML 파싱 실패: {path.name} ({exc})") from exc

        body = document.find(f"{_W}body")
        if body is None:
            raise ParseError(f"DOCX 본문이 비어 있음: {path.name}")

        lines: list[str] = []
        chapters: list[RawChapter] = []
        current_title = ""
        current_lines: list[str] = []

        def flush() -> None:
            nonlocal current_lines, current_title
            text = "\n".join(current_lines).strip()
            if text:
                chapters.append(
                    RawChapter(
                        title=current_title or f"section {len(chapters) + 1}",
                        text=text,
                        order=len(chapters),
                    )
                )
            current_lines = []

        for p in body.iter(f"{_W}p"):
            text = _paragraph_text(p).strip()
            level = _heading_level(p)
            if level is not None and text:
                flush()
                current_title = text
                lines.append(text)
                current_lines.append(text)
                continue
            if text:
                lines.append(text)
                current_lines.append(text)
            else:
                # 빈 문단은 문단 경계다. 통계에 필요하니 살려 둔다.
                lines.append("")
                current_lines.append("")
        flush()

        full_text = "\n".join(lines).strip()
        if not full_text:
            raise ParseError(f"DOCX에서 텍스트를 찾지 못함: {path.name}")

        return ParsedDocument(
            text=full_text,
            source_format=self.format_name,
            encoding="utf-8",
            title=title or path.stem,
            author=author,
            raw_chapters=chapters if len(chapters) > 1 else [],
        )

    @staticmethod
    def _read_core_props(zf: zipfile.ZipFile) -> tuple[str | None, str | None]:
        try:
            props = ET.fromstring(zf.read(_CORE_PROPS))
        except (KeyError, ET.ParseError):
            return None, None
        title_el = props.find(f"{_DC}title")
        creator_el = props.find(f"{_DC}creator")
        title = title_el.text.strip() if title_el is not None and title_el.text else None
        author = (
            creator_el.text.strip() if creator_el is not None and creator_el.text else None
        )
        return title, author
