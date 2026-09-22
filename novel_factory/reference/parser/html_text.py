"""HTML/XHTML에서 본문 텍스트를 뽑는다.

EPUB 챕터는 XHTML이다. 정규식으로 태그를 지우면 <p> 경계가 사라져서
문단 통계가 망가진다. 블록 요소에서 줄바꿈을 넣어 주는 파서가 필요하다.
"""

from __future__ import annotations

from html.parser import HTMLParser

_BLOCK_TAGS = frozenset(
    {
        "p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6",
        "blockquote", "section", "article", "header", "footer", "pre", "hr",
    }
)
_SKIP_TAGS = frozenset({"script", "style", "head", "title", "meta", "link"})
# 챕터 제목으로 볼 태그
_HEADING_TAGS = frozenset({"h1", "h2", "h3", "h4"})


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.headings: list[str] = []
        self._skip_depth = 0
        self._heading_depth = 0
        self._heading_buf: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
            return
        if tag in _BLOCK_TAGS:
            self.parts.append("\n")
        if tag in _HEADING_TAGS:
            self._heading_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if tag in _HEADING_TAGS and self._heading_depth:
            self._heading_depth -= 1
            if self._heading_depth == 0:
                heading = "".join(self._heading_buf).strip()
                if heading:
                    self.headings.append(heading)
                self._heading_buf.clear()
        if tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        self.parts.append(data)
        if self._heading_depth:
            self._heading_buf.append(data)


def html_to_text(html: str) -> tuple[str, list[str]]:
    """(본문 텍스트, 발견한 제목 목록)."""
    extractor = _TextExtractor()
    try:
        extractor.feed(html)
        extractor.close()
    except Exception:
        # 깨진 XHTML이 섞여 있어도 거기까지 모은 텍스트는 살린다.
        pass
    text = "".join(extractor.parts)
    lines = [ln.strip() for ln in text.split("\n")]
    return "\n".join(ln for ln in lines if ln), extractor.headings
