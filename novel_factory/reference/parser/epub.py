"""EPUB 파서 (stdlib만 사용).

ebooklib에 의존하지 않는다. EPUB은 결국 zip + OPF(XML) + XHTML이라
표준 라이브러리로 충분하고, 의존성이 하나 줄면 배포가 쉬워진다.

읽는 순서:
  META-INF/container.xml  ->  OPF 경로
  OPF의 <manifest>        ->  id별 파일 경로
  OPF의 <spine>           ->  실제 읽기 순서
"""

from __future__ import annotations

import posixpath
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from novel_factory.errors import ParseError
from novel_factory.reference.parser.base import BaseParser, ParsedDocument, RawChapter
from novel_factory.reference.parser.html_text import html_to_text
from novel_factory.text.encoding import decode_bytes

_CONTAINER = "META-INF/container.xml"
_NS = {
    "c": "urn:oasis:names:tc:opendocument:xmlns:container",
    "opf": "http://www.idpf.org/2007/opf",
    "dc": "http://purl.org/dc/elements/1.1/",
}
_DOC_MEDIA_TYPES = {
    "application/xhtml+xml",
    "text/html",
    "application/x-dtbncx+xml",
}
# 본문이 아닌 부속물. 통계에 섞이면 회차 평균 길이가 내려간다.
_SKIP_TITLE_HINTS = ("cover", "nav", "toc", "표지", "목차", "판권", "copyright")


class EpubParser(BaseParser):
    extensions = (".epub",)
    format_name = "epub"

    def parse(self, path: Path) -> ParsedDocument:
        warnings: list[str] = []
        try:
            with zipfile.ZipFile(path) as zf:
                opf_path = self._find_opf(zf)
                opf_root = ET.fromstring(zf.read(opf_path))
                base = posixpath.dirname(opf_path)

                title, author = self._read_metadata(opf_root)
                manifest = self._read_manifest(opf_root, base)
                spine = self._read_spine(opf_root)

                chapters: list[RawChapter] = []
                order = 0
                for idref in spine:
                    entry = manifest.get(idref)
                    if entry is None:
                        warnings.append(f"spine 항목 '{idref}'에 대응하는 manifest 없음")
                        continue
                    href, media_type = entry
                    if media_type not in _DOC_MEDIA_TYPES:
                        continue
                    if any(h in href.lower() for h in _SKIP_TITLE_HINTS):
                        continue
                    try:
                        raw = zf.read(href)
                    except KeyError:
                        warnings.append(f"파일 없음: {href}")
                        continue
                    html, _ = decode_bytes(raw)
                    body, headings = html_to_text(html)
                    if not body.strip():
                        continue
                    chapters.append(
                        RawChapter(
                            title=headings[0] if headings else posixpath.basename(href),
                            text=body,
                            order=order,
                        )
                    )
                    order += 1
        except zipfile.BadZipFile as exc:
            raise ParseError(f"EPUB이 손상되었거나 zip이 아님: {path.name}") from exc
        except ET.ParseError as exc:
            raise ParseError(f"EPUB 내부 XML 파싱 실패: {path.name} ({exc})") from exc

        if not chapters:
            raise ParseError(f"EPUB에서 본문을 찾지 못함: {path.name}")

        return ParsedDocument(
            text="\n\n".join(c.text for c in chapters),
            source_format=self.format_name,
            encoding="utf-8",
            title=title or path.stem,
            author=author,
            raw_chapters=chapters,
            warnings=warnings,
        )

    @staticmethod
    def _find_opf(zf: zipfile.ZipFile) -> str:
        try:
            container = ET.fromstring(zf.read(_CONTAINER))
        except KeyError as exc:
            raise ParseError("EPUB에 META-INF/container.xml이 없음") from exc
        rootfile = container.find(".//c:rootfile", _NS)
        if rootfile is None or not rootfile.get("full-path"):
            # 네임스페이스 없이 쓴 EPUB도 있다.
            rootfile = container.find(".//rootfile")
        if rootfile is None or not rootfile.get("full-path"):
            raise ParseError("container.xml에서 OPF 경로를 찾지 못함")
        return rootfile.attrib["full-path"]

    @staticmethod
    def _read_metadata(root: ET.Element) -> tuple[str | None, str | None]:
        def _text(tag: str) -> str | None:
            el = root.find(f".//dc:{tag}", _NS)
            return el.text.strip() if el is not None and el.text else None

        return _text("title"), _text("creator")

    @staticmethod
    def _read_manifest(root: ET.Element, base: str) -> dict[str, tuple[str, str]]:
        out: dict[str, tuple[str, str]] = {}
        for item in root.iter():
            if not item.tag.endswith("item"):
                continue
            item_id = item.get("id")
            href = item.get("href")
            if not item_id or not href:
                continue
            full = posixpath.normpath(posixpath.join(base, href)) if base else href
            out[item_id] = (full, item.get("media-type", ""))
        return out

    @staticmethod
    def _read_spine(root: ET.Element) -> list[str]:
        out: list[str] = []
        for itemref in root.iter():
            if itemref.tag.endswith("itemref"):
                idref = itemref.get("idref")
                if idref:
                    out.append(idref)
        return out
