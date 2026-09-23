"""HWPX 파서 (stdlib만 사용).

HWPX는 한글 2010 이후의 개방형 형식(OWPML, KS X 6101)이다. EPUB처럼
zip 안에 XML이 들어 있다.

  mimetype                 application/hwp+zip
  Contents/content.hpf     목록(manifest)과 읽기 순서(spine), 제목·작성자
  Contents/section0.xml    본문 구역. section1.xml, section2.xml ... 로 이어진다

본문 구조
  <hs:sec>                     구역
    <hp:p>                     문단
      <hp:run>                 같은 글자 모양이 이어지는 구간
        <hp:t>본문<hp:tab/>본문<hp:lineBreak/>본문</hp:t>
        <hp:ctrl>...</hp:ctrl> 머리말·각주 같은 조판 부호
        <hp:tbl>...</hp:tbl>   표. 셀 안에 다시 <hp:p>가 들어 있다

구역의 '직계' 문단만 읽는다. 표 셀, 글상자, 머리말·꼬리말, 각주 안의 문단은
<hp:run> 아래로 더 깊이 들어가 있어서 자연히 빠진다. HWP 파서와 같은 기준이다.

네임스페이스는 로컬 이름으로만 본다. 한컴 공개 소스(hwpx-owpml-model)를 보면
paragraph 네임스페이스가 hwpml/2011과 hwpml/2016 두 가지로 쓰인다.
<t> 안의 요소 이름도 같은 소스에서 확인했다: tab, lineBreak, fwSpace, nbSpace,
hyphen(소스 클래스 이름은 hypen), markpen/titleMark/변경추적 표시.
"""

from __future__ import annotations

import posixpath
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from novel_factory.errors import ParseError
from novel_factory.reference.parser.base import BaseParser, ParsedDocument

MIMETYPE = "application/hwp+zip"
CONTAINER = "META-INF/container.xml"
CONTENT_HPF = "Contents/content.hpf"
PACKAGE_MEDIA_TYPE = "application/hwpml-package+xml"
_SECTION_RE = re.compile(r"^Contents/section(\d+)\.xml$", re.IGNORECASE)

# <t> 안의 빈 요소를 본문 문자로 바꾸는 규칙. 나머지(markpen, titleMark,
# insertBegin 등)는 표시용 부호라 버린다.
_INLINE_REPLACEMENT = {
    "tab": "\t",
    "lineBreak": "\n",
    "fwSpace": " ",
    "nbSpace": " ",
    "hyphen": "-",
    "hypen": "-",
}


def _local(tag: str) -> str:
    """'{네임스페이스}이름'에서 이름만."""
    return tag.rsplit("}", 1)[-1]


def _text_of_t(t: ET.Element) -> str:
    parts: list[str] = [t.text or ""]
    for child in t:
        parts.append(_INLINE_REPLACEMENT.get(_local(child.tag), ""))
        parts.append(child.tail or "")
    return "".join(parts)


def paragraph_text(p: ET.Element) -> str:
    """문단 하나의 본문. 표·조판 부호 안의 글은 넣지 않는다."""
    parts: list[str] = []
    for run in p:
        if _local(run.tag) != "run":
            continue
        for item in run:
            if _local(item.tag) == "t":
                parts.append(_text_of_t(item))
    return "".join(parts)


def section_paragraphs(xml: bytes) -> list[str]:
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        raise ParseError(f"HWPX 구역 XML 파싱 실패: {exc}") from exc
    return [paragraph_text(p) for p in root if _local(p.tag) == "p"]


def _sections_by_number(names: set[str]) -> list[str]:
    """section0.xml, section1.xml, ... 을 번호 순서로. section10이 section2보다 뒤다."""
    numbered: list[tuple[int, str]] = []
    for name in names:
        m = _SECTION_RE.match(name)
        if m:
            numbered.append((int(m.group(1)), name))
    return [name for _, name in sorted(numbered)]


def _package_path(zf: zipfile.ZipFile, names: set[str]) -> str | None:
    """content.hpf 위치. META-INF/container.xml이 알려 주는 값을 우선한다."""
    if CONTAINER in names:
        try:
            container = ET.fromstring(zf.read(CONTAINER))
        except ET.ParseError:
            container = None
        if container is not None:
            for el in container.iter():
                if _local(el.tag) != "rootfile":
                    continue
                path = el.get("full-path") or ""
                if el.get("media-type") == PACKAGE_MEDIA_TYPE and path in names:
                    return path
    return CONTENT_HPF if CONTENT_HPF in names else None


def _read_package(zf: zipfile.ZipFile) -> tuple[list[str], str | None, str | None]:
    """(구역 파일 목록, 제목, 작성자).

    content.hpf의 spine 순서를 따른다. 목록이 없거나 깨졌으면 파일 이름의
    번호 순서로 대신한다.
    """
    names = set(zf.namelist())
    fallback = _sections_by_number(names)
    package_path = _package_path(zf, names)
    if package_path is None:
        return fallback, None, None

    try:
        root = ET.fromstring(zf.read(package_path))
    except ET.ParseError:
        return fallback, None, None

    title = author = None
    manifest: dict[str, str] = {}
    spine: list[str] = []
    base = posixpath.dirname(package_path)
    for el in root.iter():
        name = _local(el.tag)
        if name == "title" and el.text and el.text.strip():
            title = el.text.strip()
        elif name == "meta" and (el.get("name") or "").lower() == "creator":
            if el.text and el.text.strip():
                author = el.text.strip()
        elif name == "item" and el.get("id") and el.get("href"):
            href = el.get("href", "")
            # href가 패키지 루트 기준인 파일도, content.hpf 기준인 파일도 있다.
            candidates = (href, posixpath.normpath(posixpath.join(base, href)))
            resolved = next((c for c in candidates if c in names), href)
            manifest[el.get("id", "")] = resolved
        elif name == "itemref" and el.get("idref"):
            spine.append(el.get("idref", ""))

    ordered = [
        manifest[i]
        for i in spine
        if i in manifest and _SECTION_RE.match(manifest[i]) and manifest[i] in names
    ]
    return (ordered or fallback), title, author


class HwpxParser(BaseParser):
    extensions = (".hwpx",)
    format_name = "hwpx"

    def parse(self, path: Path) -> ParsedDocument:
        warnings: list[str] = []
        try:
            with zipfile.ZipFile(path) as zf:
                names = set(zf.namelist())
                if "mimetype" in names:
                    mimetype = zf.read("mimetype").decode("ascii", errors="replace").strip()
                    if mimetype != MIMETYPE:
                        warnings.append(
                            f"mimetype이 '{mimetype}'입니다 (기대값 {MIMETYPE})."
                        )
                sections, title, author = _read_package(zf)
                if not sections:
                    raise ParseError(
                        f"HWPX에 본문 구역(section*.xml)이 없습니다: {path.name}"
                    )
                paragraphs: list[str] = []
                for section in sections:
                    paragraphs.extend(section_paragraphs(zf.read(section)))
        except zipfile.BadZipFile as exc:
            raise ParseError(f"HWPX가 손상됐거나 zip이 아닙니다: {path.name}") from exc

        text = "\n".join(paragraphs).strip()
        if not text:
            raise ParseError(f"HWPX에서 본문 텍스트를 찾지 못했습니다: {path.name}")

        return ParsedDocument(
            text=text,
            source_format=self.format_name,
            encoding="utf-8",
            title=title or path.stem,
            author=author,
            warnings=warnings,
        )
