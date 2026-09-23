"""HWP 5.0 파서 (stdlib만 사용).

한글과컴퓨터 '글' 2002 이후의 .hwp 파일을 읽는다. 구조는 다음과 같다.

  OLE 복합 문서 (cfb.py)
    FileHeader               서명, 버전, 속성 비트(압축/암호/배포용)
    \\x05HwpSummaryInformation 제목·작성자 (OLE 속성 집합)
    BodyText/Section0..N     본문. 압축 비트가 켜져 있으면 raw deflate.

  본문 스트림은 레코드의 연속이다.
    레코드 헤더 4바이트: TagID(10비트) | Level(10비트) | Size(12비트)
    Size가 0xFFF면 뒤따르는 4바이트가 실제 크기다.

  HWPTAG_PARA_HEADER(66)이 문단의 시작이고, 바로 아래 레벨의
  HWPTAG_PARA_TEXT(67)에 UTF-16LE 본문이 들어 있다. 본문 안의 0~31 코드는
  제어 문자다. 일부는 1글자, 일부는 8글자(16바이트)를 차지한다.

본문 최상위 문단(레벨 0)만 읽는다. 머리말·꼬리말·각주·표·글상자 안의
문단은 레벨이 더 깊어서 빠진다. 소설 통계에 머리말("제3장 | 작가명")이나
쪽 번호가 섞이면 회차 분리와 문장 통계가 틀어지기 때문이다.

읽지 않는 것
  암호가 걸린 문서      본문이 암호화돼 있다.
  배포용 문서           복사·편집을 막으려고 본문을 암호화한 문서다.
                        그 보호를 푸는 것은 기술적 보호조치 우회라서 하지 않는다.
  글 97 이하(.hwp 3.x)  전혀 다른 바이너리 형식이다.
"""

from __future__ import annotations

import re
import struct
import zlib
from array import array
from pathlib import Path

from novel_factory.errors import ParseError
from novel_factory.reference.parser.base import BaseParser, ParsedDocument
from novel_factory.reference.parser.cfb import SIGNATURE as CFB_SIGNATURE
from novel_factory.reference.parser.cfb import CompoundFile

HWP_SIGNATURE = b"HWP Document File"
HWP3_SIGNATURE = b"HWP Document File V3"

FLAG_COMPRESSED = 1 << 0
FLAG_PASSWORD = 1 << 1
FLAG_DISTRIBUTION = 1 << 2

HWPTAG_BEGIN = 0x010
HWPTAG_PARA_HEADER = HWPTAG_BEGIN + 50
HWPTAG_PARA_TEXT = HWPTAG_BEGIN + 51

# PARA_TEXT 안의 제어 문자 중 1글자(2바이트)만 차지하는 것.
# 나머지 0~31 코드(인라인·확장 컨트롤)는 8글자(16바이트)를 차지한다.
# 한컴 공개 소스 OWPMLUtil/HncCtrlChDef.h의 HWPCH_MASK_CHAR와 같은 집합이다.
_CHAR_CONTROLS = frozenset({0, 10, 13, 24, 25, 26, 27, 28, 29, 30, 31})
_CONTROL_SPAN = 8

# 제어 문자를 본문 문자로 바꾸는 규칙. 여기 없는 제어 문자는 버린다.
_CONTROL_REPLACEMENT = {
    9: "\t",  # 탭 (인라인 컨트롤)
    10: "\n",  # 강제 줄 나눔
    24: "-",  # 하이픈
    29: "\t",  # 편집용 탭 (HNCCH_EDITTAB)
    30: " ",  # 묶음 빈칸
    31: " ",  # 고정폭 빈칸
}

_SECTION_RE = re.compile(r"^BodyText/Section(\d+)$")

SUMMARY_STREAM = "\x05HwpSummaryInformation"
PIDSI_TITLE = 0x02
PIDSI_AUTHOR = 0x04
VT_LPSTR = 0x1E
VT_LPWSTR = 0x1F


def _decompress(raw: bytes) -> bytes:
    """HWP 본문 스트림은 zlib 헤더 없는 raw deflate다."""
    try:
        return zlib.decompress(raw, -15)
    except zlib.error as exc:
        raise ParseError(f"HWP 본문 압축을 풀지 못했습니다: {exc}") from exc


def iter_records(data: bytes):
    """(tag_id, level, payload)를 차례로 돌려준다."""
    pos = 0
    end = len(data)
    while pos + 4 <= end:
        header = struct.unpack_from("<I", data, pos)[0]
        pos += 4
        tag = header & 0x3FF
        level = (header >> 10) & 0x3FF
        size = (header >> 20) & 0xFFF
        if size == 0xFFF:
            if pos + 4 > end:
                break
            size = struct.unpack_from("<I", data, pos)[0]
            pos += 4
        if pos + size > end:
            # 끝이 잘린 레코드. 있는 만큼만 돌려주고 멈춘다.
            yield tag, level, data[pos:end]
            break
        yield tag, level, data[pos : pos + size]
        pos += size


def decode_para_text(payload: bytes) -> str:
    """PARA_TEXT 레코드를 문자열로 바꾼다.

    UTF-16 코드 단위를 모아 한 번에 디코딩한다. 한 글자씩 chr()로 바꾸면
    보조 평면 문자(대리 쌍)가 깨진다.
    """
    units = array("H")
    units.frombytes(payload[: len(payload) // 2 * 2])
    if units.itemsize != 2:  # pragma: no cover - 모든 주요 플랫폼에서 2바이트
        raise ParseError("이 플랫폼에서는 UTF-16 코드 단위를 읽을 수 없습니다.")
    if struct.pack("=H", 1) != struct.pack("<H", 1):  # pragma: no cover - 빅엔디언
        units.byteswap()

    out = array("H")
    i = 0
    n = len(units)
    while i < n:
        code = units[i]
        if code >= 32:
            out.append(code)
            i += 1
            continue
        if code == 13:  # 문단 끝
            break
        replacement = _CONTROL_REPLACEMENT.get(code)
        if replacement is not None:
            out.extend(array("H", replacement.encode("utf-16-le")))
        i += 1 if code in _CHAR_CONTROLS else _CONTROL_SPAN
    return out.tobytes().decode("utf-16-le", errors="replace")


def read_file_header(cfb: CompoundFile) -> tuple[tuple[int, int, int, int], int]:
    """(버전, 속성 비트)."""
    if not cfb.exists("FileHeader"):
        raise ParseError("HWP의 FileHeader 스트림이 없습니다.")
    header = cfb.read("FileHeader")
    if not header.startswith(HWP_SIGNATURE):
        raise ParseError("HWP 서명이 아닙니다.")
    version_raw, flags = struct.unpack_from("<II", header, 32)
    version = (
        (version_raw >> 24) & 0xFF,
        (version_raw >> 16) & 0xFF,
        (version_raw >> 8) & 0xFF,
        version_raw & 0xFF,
    )
    return version, flags


def read_summary(cfb: CompoundFile) -> tuple[str | None, str | None]:
    """요약 정보에서 (제목, 작성자). 읽지 못하면 (None, None).

    OLE 속성 집합 형식이다. 머리 28바이트 뒤에 (FMTID, 오프셋) 목록이 오고,
    각 구역은 (크기, 개수, [(ID, 오프셋)...]) 다음에 값들이 이어진다.
    """
    if not cfb.exists(SUMMARY_STREAM):
        return None, None
    try:
        data = cfb.read(SUMMARY_STREAM)
        num_sets = struct.unpack_from("<I", data, 24)[0]
        if num_sets < 1:
            return None, None
        section_offset = struct.unpack_from("<I", data, 28 + 16)[0]
        _size, count = struct.unpack_from("<II", data, section_offset)
        values: dict[int, str] = {}
        for k in range(count):
            pid, offset = struct.unpack_from("<II", data, section_offset + 8 + k * 8)
            if pid not in (PIDSI_TITLE, PIDSI_AUTHOR):
                continue
            pos = section_offset + offset
            vtype = struct.unpack_from("<H", data, pos)[0]
            length = struct.unpack_from("<I", data, pos + 4)[0]
            if vtype == VT_LPWSTR:
                raw = data[pos + 8 : pos + 8 + length * 2]
                text = raw.decode("utf-16-le", errors="replace")
            elif vtype == VT_LPSTR:
                raw = data[pos + 8 : pos + 8 + length]
                text = raw.decode("cp949", errors="replace")
            else:
                continue
            text = text.split("\x00", 1)[0].strip()
            if text:
                values[pid] = text
        return values.get(PIDSI_TITLE), values.get(PIDSI_AUTHOR)
    except (struct.error, KeyError, IndexError):
        return None, None


def extract_body(cfb: CompoundFile, *, compressed: bool) -> list[str]:
    """본문 최상위 문단 목록. 빈 문단은 빈 문자열로 남긴다.

    빈 문단은 문단 경계 정보다. DOCX 파서와 같은 방식으로 살려 둬야
    포맷 간 문단 통계가 맞는다.
    """
    sections = sorted(
        (int(m.group(1)), path)
        for path in cfb.list_streams()
        if (m := _SECTION_RE.match(path))
    )
    if not sections:
        raise ParseError("HWP에 BodyText 구역이 없습니다.")

    paragraphs: list[str] = []
    for _index, path in sections:
        raw = cfb.read(path)
        data = _decompress(raw) if compressed else raw

        current: str | None = None
        in_top_paragraph = False
        for tag, level, payload in iter_records(data):
            if tag == HWPTAG_PARA_HEADER:
                if level == 0:
                    if current is not None:
                        paragraphs.append(current)
                    current = ""
                    in_top_paragraph = True
                else:
                    # 머리말·각주·표 안의 문단. 다음 최상위 문단까지 무시한다.
                    in_top_paragraph = False
            elif tag == HWPTAG_PARA_TEXT and in_top_paragraph and level == 1:
                current = (current or "") + decode_para_text(payload)
        if current is not None:
            paragraphs.append(current)
    return paragraphs


class HwpParser(BaseParser):
    extensions = (".hwp",)
    format_name = "hwp"

    def parse(self, path: Path) -> ParsedDocument:
        data = path.read_bytes()
        if data.startswith(HWP3_SIGNATURE):
            raise ParseError(
                f"글 97 이하(HWP 3.x) 형식은 지원하지 않습니다: {path.name}. "
                "한글에서 열어 HWP 5.0 이상이나 HWPX로 다시 저장해 주세요."
            )
        if not data.startswith(CFB_SIGNATURE):
            raise ParseError(f"HWP 5.0 파일이 아닙니다: {path.name}")

        cfb = CompoundFile(data)
        version, flags = read_file_header(cfb)

        if flags & FLAG_PASSWORD:
            raise ParseError(
                f"암호가 걸린 HWP 문서입니다: {path.name}. 암호를 풀고 저장해 주세요."
            )
        if flags & FLAG_DISTRIBUTION:
            raise ParseError(
                f"배포용 HWP 문서입니다: {path.name}. 배포용 문서는 복사를 막기 위해 "
                "본문이 암호화돼 있어 읽지 않습니다. 원본 문서를 사용해 주세요."
            )

        paragraphs = extract_body(cfb, compressed=bool(flags & FLAG_COMPRESSED))
        text = "\n".join(paragraphs).strip()
        if not text:
            raise ParseError(f"HWP에서 본문 텍스트를 찾지 못했습니다: {path.name}")

        title, author = read_summary(cfb)
        warnings: list[str] = []
        if version[0] != 5:
            warnings.append(
                f"HWP 버전 {'.'.join(map(str, version))}은 검증되지 않았습니다."
            )

        return ParsedDocument(
            text=text,
            source_format=self.format_name,
            encoding="utf-16-le",
            title=title or path.stem,
            author=author,
            warnings=warnings,
        )
