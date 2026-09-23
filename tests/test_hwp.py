"""HWP 5.0 / HWPX 파서 테스트.

픽스처는 tests/fixtures/make_fixtures.py가 만든다. 실제 한컴 샘플은 배포
라이선스 때문에 저장소에 넣지 않았다. 대신 다음 방식으로 검증했다.

  CFB 리더   : pyhwp 저장소의 실제 한컴 작성 HWP 36개에서 olefile과 모든
               스트림이 바이트 단위로 일치
  HWP 본문   : 픽스처를 pyhwp 레코드 모델 파서로 읽은 결과 = TXT 원본 = 이 파서
  HWP 제어문자: 한컴 공개 소스 OWPMLUtil/HncCtrlChDef.h의 마스크와 대조
  HWPX       : python-hwpx에 들어 있는 한컴 빈 문서 골격과 배치 대조,
               픽스처를 python-hwpx로 읽은 결과 = TXT 원본 = 이 파서
"""

from __future__ import annotations

import io
import struct
import zipfile
from pathlib import Path

import pytest

from novel_factory.errors import ParseError
from novel_factory.reference.parser import SUPPORTED_EXTENSIONS, parse_file
from novel_factory.reference.parser.cfb import CompoundFile
from novel_factory.reference.parser.hwp import (
    FLAG_COMPRESSED,
    FLAG_DISTRIBUTION,
    FLAG_PASSWORD,
    HwpParser,
    decode_para_text,
    iter_records,
)
from novel_factory.reference.parser.hwpx import HwpxParser, paragraph_text
from novel_factory.text.normalize import normalize_text

FIXTURES = Path(__file__).resolve().parent / "fixtures"

import make_fixtures as mf  # noqa: E402  conftest가 경로를 넣는다
from cfb_writer import build_cfb  # noqa: E402


def _source_text() -> str:
    return normalize_text((FIXTURES / "sample_novel.txt").read_text(encoding="utf-8"))


def _hwp_with(
    body_records: bytes, *, flags: int = FLAG_COMPRESSED, signature: bytes | None = None
) -> bytes:
    """본문 레코드와 속성 비트를 직접 지정한 HWP."""
    header = bytearray(256)
    header[:17] = b"HWP Document File"
    struct.pack_into("<II", header, 32, 0x05000300, flags)
    if signature is not None:
        header[: len(signature)] = signature
    body = mf._deflate(body_records) if flags & FLAG_COMPRESSED else body_records
    return build_cfb({"FileHeader": bytes(header), "BodyText/Section0": body})


def _write(tmp_path: Path, name: str, data: bytes) -> Path:
    path = tmp_path / name
    path.write_bytes(data)
    return path


class TestRegistration:
    def test_extensions_are_supported(self) -> None:
        assert {".hwp", ".hwpx"} <= SUPPORTED_EXTENSIONS


class TestCompoundFile:
    def test_lists_fixture_streams(self) -> None:
        cfb = CompoundFile((FIXTURES / "sample_novel.hwp").read_bytes())
        streams = cfb.list_streams()
        assert "FileHeader" in streams
        assert "BodyText/Section0" in streams
        assert "BodyText/Section1" in streams

    def test_reads_small_and_large_streams(self) -> None:
        """4,096바이트 미만은 미니 스트림, 이상은 일반 섹터에 있다."""
        small = b"a" * 100
        large = bytes(range(256)) * 40  # 10,240바이트
        cfb = CompoundFile(build_cfb({"small": small, "dir/large": large}))
        assert cfb.read("small") == small
        assert cfb.read("dir/large") == large

    def test_missing_stream(self) -> None:
        cfb = CompoundFile(build_cfb({"a": b"x"}))
        with pytest.raises(KeyError):
            cfb.read("없음")

    def test_rejects_non_cfb(self) -> None:
        with pytest.raises(ParseError):
            CompoundFile(b"not an ole file" * 100)

    def test_matches_olefile(self) -> None:
        """독립 구현(olefile)과 스트림 내용이 같아야 한다."""
        olefile = pytest.importorskip("olefile")
        path = FIXTURES / "sample_novel.hwp"
        ole = olefile.OleFileIO(str(path))
        mine = CompoundFile(path.read_bytes())
        reference = {"/".join(p): ole.openstream(p).read() for p in ole.listdir()}
        assert set(reference) == set(mine.list_streams())
        for name, data in reference.items():
            assert mine.read(name) == data, name


class TestHwpRecords:
    def test_record_header_fields(self) -> None:
        data = mf._hwp_record(67, 1, b"\x41\x00")
        assert list(iter_records(data)) == [(67, 1, b"\x41\x00")]

    def test_extended_size_record(self) -> None:
        """크기가 0xFFF 이상이면 헤더 뒤 4바이트가 실제 크기다."""
        payload = b"\x00" * 5000
        records = list(iter_records(mf._hwp_record(67, 1, payload)))
        assert records == [(67, 1, payload)]

    def test_truncated_record_does_not_crash(self) -> None:
        data = mf._hwp_record(67, 1, b"abcdef")[:-3]
        assert len(list(iter_records(data))) == 1


class TestParaText:
    def _units(self, *codes: int) -> bytes:
        return struct.pack(f"<{len(codes)}H", *codes)

    def test_plain_text_stops_at_paragraph_break(self) -> None:
        payload = "본문".encode("utf-16-le") + self._units(13) + "무시".encode("utf-16-le")
        assert decode_para_text(payload) == "본문"

    def test_tab_is_an_eight_unit_control(self) -> None:
        payload = (
            "가".encode("utf-16-le")
            + self._units(9, 0, 0, 0, 0, 0, 0, 9)
            + "나".encode("utf-16-le")
            + self._units(13)
        )
        assert decode_para_text(payload) == "가\t나"

    def test_extended_control_is_skipped(self) -> None:
        """표·그림 같은 확장 컨트롤(11)은 8칸을 차지하고 글자는 없다."""
        payload = (
            "앞".encode("utf-16-le")
            + self._units(11, 0x6274, 0x6C, 0, 0, 0, 0, 11)
            + "뒤".encode("utf-16-le")
            + self._units(13)
        )
        assert decode_para_text(payload) == "앞뒤"

    @pytest.mark.parametrize(
        ("code", "expected"),
        [(10, "\n"), (24, "-"), (30, " "), (31, " ")],
    )
    def test_single_unit_controls(self, code: int, expected: str) -> None:
        payload = "가".encode("utf-16-le") + self._units(code) + "나".encode("utf-16-le")
        assert decode_para_text(payload) == f"가{expected}나"

    def test_surrogate_pair(self) -> None:
        """보조 평면 문자(대리 쌍)가 깨지면 안 된다."""
        text = "𠀀한자"
        assert decode_para_text(text.encode("utf-16-le") + self._units(13)) == text


class TestHwpParser:
    def test_fixture_matches_txt_source(self) -> None:
        doc = parse_file(FIXTURES / "sample_novel.hwp")
        assert doc.source_format == "hwp"
        assert normalize_text(doc.text) == _source_text()

    def test_summary_info(self) -> None:
        doc = parse_file(FIXTURES / "sample_novel.hwp")
        assert doc.title == "회귀한 인수합병가"
        assert doc.author == "테스트 작가"

    def test_sections_are_read_in_numeric_order(self, tmp_path: Path) -> None:
        """Section10이 Section2보다 뒤에 와야 한다. 문자열 정렬이면 틀린다."""
        streams = {"FileHeader": _hwp_with(b"")}  # 자리만
        header = CompoundFile(streams["FileHeader"]).read("FileHeader")
        streams = {"FileHeader": header}
        for i in (0, 2, 10):
            streams[f"BodyText/Section{i}"] = mf._deflate(mf._hwp_paragraph(f"구역{i}"))
        doc = HwpParser().parse(_write(tmp_path, "s.hwp", build_cfb(streams)))
        assert doc.text.split("\n") == ["구역0", "구역2", "구역10"]

    def test_nested_paragraphs_are_excluded(self, tmp_path: Path) -> None:
        """머리말·각주·표 안의 문단(레벨 1 이상)은 본문에 넣지 않는다."""
        records = mf._hwp_paragraph("본문 첫 문단")
        nested_header = struct.pack("<IIHBBHHHI", 5, 0, 0, 0, 0, 1, 0, 0, 0)
        records += mf._hwp_record(66, 2, nested_header)
        records += mf._hwp_record(67, 3, "머리말".encode("utf-16-le") + b"\x0d\x00")
        records += mf._hwp_paragraph("본문 둘째 문단")
        doc = HwpParser().parse(_write(tmp_path, "n.hwp", _hwp_with(records)))
        assert doc.text == "본문 첫 문단\n본문 둘째 문단"

    def test_uncompressed_body(self, tmp_path: Path) -> None:
        records = mf._hwp_paragraph("압축 안 함")
        doc = HwpParser().parse(_write(tmp_path, "u.hwp", _hwp_with(records, flags=0)))
        assert doc.text == "압축 안 함"

    def test_password_protected_is_refused(self, tmp_path: Path) -> None:
        data = _hwp_with(mf._hwp_paragraph("x"), flags=FLAG_COMPRESSED | FLAG_PASSWORD)
        with pytest.raises(ParseError, match="암호"):
            HwpParser().parse(_write(tmp_path, "p.hwp", data))

    def test_distribution_document_is_refused(self, tmp_path: Path) -> None:
        """배포용 문서의 복사 방지를 풀지 않는다."""
        data = _hwp_with(mf._hwp_paragraph("x"), flags=FLAG_COMPRESSED | FLAG_DISTRIBUTION)
        with pytest.raises(ParseError, match="배포용"):
            HwpParser().parse(_write(tmp_path, "d.hwp", data))

    def test_hwp3_is_refused_with_guidance(self, tmp_path: Path) -> None:
        path = _write(
            tmp_path, "old.hwp", b"HWP Document File V3.00 \x1a\x01\x02" + b"\x00" * 200
        )
        with pytest.raises(ParseError, match="HWP 3"):
            HwpParser().parse(path)

    def test_not_an_hwp(self, tmp_path: Path) -> None:
        with pytest.raises(ParseError):
            HwpParser().parse(_write(tmp_path, "x.hwp", b"plain text" * 100))

    def test_empty_body(self, tmp_path: Path) -> None:
        data = _hwp_with(mf._hwp_paragraph(""))
        with pytest.raises(ParseError, match="본문"):
            HwpParser().parse(_write(tmp_path, "e.hwp", data))


def _hwpx(
    sections: dict[str, str], hpf: str | None, *, container: str | None = None
) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(zipfile.ZipInfo("mimetype"), "application/hwp+zip", zipfile.ZIP_STORED)
        if container is not None:
            zf.writestr("META-INF/container.xml", container)
        if hpf is not None:
            zf.writestr("Contents/content.hpf", hpf)
        for name, xml in sections.items():
            zf.writestr(name, xml)
    return buf.getvalue()


def _sec(*paragraphs: str) -> str:
    body = "".join(mf._hwpx_paragraph(p, i) for i, p in enumerate(paragraphs))
    return f"<hs:sec {mf._HWPX_NS}>{body}</hs:sec>"


class TestHwpxParser:
    def test_fixture_matches_txt_source(self) -> None:
        doc = parse_file(FIXTURES / "sample_novel.hwpx")
        assert doc.source_format == "hwpx"
        assert normalize_text(doc.text) == _source_text()

    def test_metadata(self) -> None:
        doc = parse_file(FIXTURES / "sample_novel.hwpx")
        assert doc.title == "회귀한 인수합병가"
        assert doc.author == "테스트 작가"

    def test_header_control_is_not_body(self) -> None:
        """픽스처 첫 구역에는 머리말 컨트롤이 들어 있다."""
        assert "머리말" not in parse_file(FIXTURES / "sample_novel.hwpx").text

    def test_spine_order_wins_over_file_names(self, tmp_path: Path) -> None:
        hpf = (
            '<opf:package xmlns:opf="http://www.idpf.org/2007/opf/"><opf:manifest>'
            '<opf:item id="a" href="Contents/section0.xml"/>'
            '<opf:item id="b" href="Contents/section1.xml"/>'
            '</opf:manifest><opf:spine><opf:itemref idref="b"/><opf:itemref idref="a"/>'
            "</opf:spine></opf:package>"
        )
        data = _hwpx(
            {"Contents/section0.xml": _sec("영"), "Contents/section1.xml": _sec("일")}, hpf
        )
        doc = HwpxParser().parse(_write(tmp_path, "o.hwpx", data))
        assert doc.text == "일\n영"

    def test_numeric_fallback_without_package(self, tmp_path: Path) -> None:
        sections = {f"Contents/section{i}.xml": _sec(str(i)) for i in (10, 2, 0)}
        doc = HwpxParser().parse(_write(tmp_path, "f.hwpx", _hwpx(sections, None)))
        assert doc.text == "0\n2\n10"

    def test_container_points_to_package(self, tmp_path: Path) -> None:
        """content.hpf 위치는 META-INF/container.xml이 알려 준다."""
        container = (
            '<ocf:container xmlns:ocf="urn:oasis:names:tc:opendocument:xmlns:container">'
            '<ocf:rootfiles><ocf:rootfile full-path="Other/pkg.hpf" '
            'media-type="application/hwpml-package+xml"/></ocf:rootfiles></ocf:container>'
        )
        hpf = (
            '<opf:package xmlns:opf="http://www.idpf.org/2007/opf/"><opf:metadata>'
            "<opf:title>다른 위치</opf:title></opf:metadata></opf:package>"
        )
        buf = io.BytesIO(
            _hwpx({"Contents/section0.xml": _sec("본문")}, None, container=container)
        )
        with zipfile.ZipFile(buf, "a") as zf:
            zf.writestr("Other/pkg.hpf", hpf)
        doc = HwpxParser().parse(_write(tmp_path, "c.hwpx", buf.getvalue()))
        assert doc.title == "다른 위치"

    def test_inline_elements(self) -> None:
        from xml.etree import ElementTree as ET

        ns = "http://www.hancom.co.kr/hwpml/2016/paragraph"  # 2016 네임스페이스도 읽는다
        p = ET.fromstring(
            f'<hp:p xmlns:hp="{ns}"><hp:run><hp:t>가<hp:tab/>나<hp:lineBreak/>다'
            "<hp:nbSpace/>라<hp:hyphen/>마<hp:markpenBegin/>바<hp:markpenEnd/></hp:t></hp:run></hp:p>"
        )
        assert paragraph_text(p) == "가\t나\n다 라-마바"

    def test_table_text_is_excluded(self) -> None:
        from xml.etree import ElementTree as ET

        p = ET.fromstring(
            '<hp:p xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph"><hp:run>'
            "<hp:tbl><hp:tr><hp:tc><hp:subList><hp:p><hp:run><hp:t>셀</hp:t></hp:run></hp:p>"
            "</hp:subList></hp:tc></hp:tr></hp:tbl><hp:t>본문</hp:t></hp:run></hp:p>"
        )
        assert paragraph_text(p) == "본문"

    def test_not_a_zip(self, tmp_path: Path) -> None:
        with pytest.raises(ParseError):
            HwpxParser().parse(_write(tmp_path, "b.hwpx", b"not a zip"))

    def test_no_sections(self, tmp_path: Path) -> None:
        with pytest.raises(ParseError, match="구역"):
            HwpxParser().parse(_write(tmp_path, "n.hwpx", _hwpx({}, None)))
