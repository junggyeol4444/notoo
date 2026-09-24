"""EPUB 3 만들기 (기획안 44번). 표준 라이브러리만 쓴다.

    book.epub
      mimetype                     (압축하지 않고 맨 앞에)
      META-INF/container.xml
      OEBPS/content.opf            패키지 문서 (메타데이터, 목록, 읽는 순서)
      OEBPS/nav.xhtml              EPUB 3 목차
      OEBPS/toc.ncx                EPUB 2 목차 (옛 리더기용)
      OEBPS/styles/book.css
      OEBPS/images/cover.jpg       (있으면)
      OEBPS/text/cover.xhtml       (있으면)
      OEBPS/text/title.xhtml       속표지
      OEBPS/text/ep0001.xhtml ...  회차마다 한 파일

회차 본문은 문단마다 <p>로 싼다. 빈 줄이 문단 경계다. 원고의 글자를 XML로
이스케이프하고, XML에 쓸 수 없는 제어 문자는 버린다.
"""

from __future__ import annotations

import re
import uuid
import zipfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from xml.sax.saxutils import escape

# XML 1.0에 쓸 수 없는 문자 (탭·줄바꿈 제외 제어 문자)
_INVALID_XML_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]")
_BLANK_LINE_RE = re.compile(r"\n\s*\n")

CSS = """\
body { margin: 0 5%; line-height: 1.8; word-break: keep-all; }
h1 { font-size: 1.3em; margin: 2em 0 1.5em; text-align: center; }
p { margin: 0 0 0.9em; text-indent: 0; }
.title-page { text-align: center; margin-top: 30%; }
.title-page .title { font-size: 1.8em; font-weight: bold; }
.title-page .author { margin-top: 2em; }
.cover { margin: 0; padding: 0; text-align: center; }
.cover img { max-width: 100%; max-height: 100%; }
"""


@dataclass(slots=True)
class Chapter:
    number: int
    title: str
    text: str

    @property
    def file_name(self) -> str:
        return f"ep{self.number:04d}.xhtml"

    @property
    def heading(self) -> str:
        return f"{self.number}화 {self.title}".strip()


@dataclass(slots=True)
class BookMeta:
    title: str
    identifier: str  # urn:uuid:...
    language: str = "ko"
    author: str = ""
    publisher: str = ""
    description: str = ""
    subjects: list[str] = field(default_factory=list)
    modified: datetime = field(default_factory=lambda: datetime.now(UTC))


def book_identifier(*parts: str) -> str:
    """같은 작품·같은 권이면 늘 같은 식별자. 다시 만들어도 리더기가 같은 책으로 본다."""
    return f"urn:uuid:{uuid.uuid5(uuid.NAMESPACE_URL, 'novel-factory:' + ':'.join(parts))}"


def _x(text: str) -> str:
    return escape(_INVALID_XML_RE.sub("", text), {'"': "&quot;"})


def paragraphs(text: str) -> list[str]:
    """빈 줄로 문단을 나누고, 문단 안의 줄바꿈은 한 줄로 합친다."""
    out = []
    for block in _BLANK_LINE_RE.split(text.strip()):
        lines = [ln.strip() for ln in block.split("\n") if ln.strip()]
        if lines:
            out.append(" ".join(lines))
    return out


def _xhtml(title: str, body: str, *, lang: str, epub_type: str = "") -> str:
    ns = ' xmlns:epub="http://www.idpf.org/2007/ops"'
    section = f' epub:type="{epub_type}"' if epub_type else ""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml"{ns} xml:lang="{lang}" lang="{lang}">
<head>
<meta charset="UTF-8"/>
<title>{_x(title)}</title>
<link rel="stylesheet" type="text/css" href="../styles/book.css"/>
</head>
<body{section}>
{body}
</body>
</html>
"""


def chapter_xhtml(ch: Chapter, lang: str) -> str:
    body = [f'<section epub:type="chapter"><h1>{_x(ch.heading)}</h1>']
    body += [f"<p>{_x(p)}</p>" for p in paragraphs(ch.text)]
    body.append("</section>")
    return _xhtml(ch.heading, "\n".join(body), lang=lang, epub_type="bodymatter")


def _nav(meta: BookMeta, chapters: list[Chapter], has_cover: bool) -> str:
    items = "\n".join(
        f'<li><a href="text/{c.file_name}">{_x(c.heading)}</a></li>' for c in chapters
    )
    landmarks = (
        '<li><a epub:type="cover" href="text/cover.xhtml">표지</a></li>\n'
        if has_cover
        else ""
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="{meta.language}" lang="{meta.language}">
<head><meta charset="UTF-8"/><title>목차</title></head>
<body>
<nav epub:type="toc" id="toc"><h1>목차</h1>
<ol>
{items}
</ol>
</nav>
<nav epub:type="landmarks" hidden="hidden"><ol>
{landmarks}<li><a epub:type="bodymatter" href="text/{chapters[0].file_name}">본문</a></li>
</ol></nav>
</body>
</html>
"""


def _ncx(meta: BookMeta, chapters: list[Chapter]) -> str:
    points = "\n".join(
        f'<navPoint id="np{i}" playOrder="{i}"><navLabel><text>{_x(c.heading)}</text>'
        f'</navLabel><content src="text/{c.file_name}"/></navPoint>'
        for i, c in enumerate(chapters, start=1)
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1" xml:lang="{meta.language}">
<head>
<meta name="dtb:uid" content="{_x(meta.identifier)}"/>
<meta name="dtb:depth" content="1"/>
<meta name="dtb:totalPageCount" content="0"/>
<meta name="dtb:maxPageNumber" content="0"/>
</head>
<docTitle><text>{_x(meta.title)}</text></docTitle>
<navMap>
{points}
</navMap>
</ncx>
"""


def _opf(meta: BookMeta, chapters: list[Chapter], has_cover: bool) -> str:
    modified = meta.modified.strftime("%Y-%m-%dT%H:%M:%SZ")
    dc = [
        f'<dc:identifier id="bookid">{_x(meta.identifier)}</dc:identifier>',
        f"<dc:title>{_x(meta.title)}</dc:title>",
        f"<dc:language>{_x(meta.language)}</dc:language>",
        f'<meta property="dcterms:modified">{modified}</meta>',
    ]
    if meta.author:
        dc.append(f'<dc:creator id="creator">{_x(meta.author)}</dc:creator>')
    if meta.publisher:
        dc.append(f"<dc:publisher>{_x(meta.publisher)}</dc:publisher>")
    if meta.description:
        dc.append(f"<dc:description>{_x(meta.description)}</dc:description>")
    dc += [f"<dc:subject>{_x(s)}</dc:subject>" for s in meta.subjects if s]
    if has_cover:
        # EPUB 2 리더기용 표지 표시
        dc.append('<meta name="cover" content="cover-image"/>')

    manifest = [
        '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>',
        '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>',
        '<item id="css" href="styles/book.css" media-type="text/css"/>',
        '<item id="title" href="text/title.xhtml" media-type="application/xhtml+xml"/>',
    ]
    spine = []
    if has_cover:
        manifest += [
            '<item id="cover-image" href="images/cover.jpg" media-type="image/jpeg" '
            'properties="cover-image"/>',
            '<item id="cover" href="text/cover.xhtml" media-type="application/xhtml+xml"/>',
        ]
        spine.append('<itemref idref="cover" linear="no"/>')
    spine.append('<itemref idref="title"/>')
    for c in chapters:
        cid = c.file_name.removesuffix(".xhtml")
        manifest.append(
            f'<item id="{cid}" href="text/{c.file_name}" media-type="application/xhtml+xml"/>'
        )
        spine.append(f'<itemref idref="{cid}"/>')
    nl = "\n"
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid" xml:lang="{meta.language}">
<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
{nl.join(dc)}
</metadata>
<manifest>
{nl.join(manifest)}
</manifest>
<spine toc="ncx">
{nl.join(spine)}
</spine>
</package>
"""


CONTAINER = """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
<rootfiles>
<rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
</rootfiles>
</container>
"""


def write_epub(
    path: Path,
    meta: BookMeta,
    chapters: list[Chapter],
    *,
    cover_jpeg: bytes | None = None,
) -> Path:
    if not chapters:
        raise ValueError("EPUB에 넣을 회차가 없습니다.")
    path.parent.mkdir(parents=True, exist_ok=True)
    has_cover = cover_jpeg is not None
    lang = meta.language
    title_body = (
        '<div class="title-page">'
        f'<p class="title">{_x(meta.title)}</p>'
        + (f'<p class="author">{_x(meta.author)}</p>' if meta.author else "")
        + "</div>"
    )
    with zipfile.ZipFile(path, "w") as z:
        # mimetype은 첫 항목이고 압축하지 않는다 (EPUB OCF 규칙).
        z.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip", zipfile.ZIP_STORED)

        def put(name: str, data: str | bytes) -> None:
            z.writestr(name, data, zipfile.ZIP_DEFLATED)

        put("META-INF/container.xml", CONTAINER)
        put("OEBPS/content.opf", _opf(meta, chapters, has_cover))
        put("OEBPS/nav.xhtml", _nav(meta, chapters, has_cover))
        put("OEBPS/toc.ncx", _ncx(meta, chapters))
        put("OEBPS/styles/book.css", CSS)
        put("OEBPS/text/title.xhtml", _xhtml(meta.title, title_body, lang=lang))
        if cover_jpeg is not None:
            put("OEBPS/images/cover.jpg", cover_jpeg)
            put(
                "OEBPS/text/cover.xhtml",
                _xhtml(
                    "표지",
                    '<div class="cover"><img src="../images/cover.jpg" alt="표지"/></div>',
                    lang=lang,
                    epub_type="cover",
                ),
            )
        for ch in chapters:
            put(f"OEBPS/text/{ch.file_name}", chapter_xhtml(ch, lang))
    return path
