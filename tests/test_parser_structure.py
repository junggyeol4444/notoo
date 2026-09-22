"""파서와 회차 분리 테스트."""

from __future__ import annotations

from pathlib import Path

import pytest

from novel_factory.errors import UnsupportedFormatError
from novel_factory.reference.parser import SUPPORTED_EXTENSIONS, parse_file
from novel_factory.reference.structure import (
    analyze_episode_shape,
    split_by_markers,
    split_document,
)
from novel_factory.reference.structure.episode_shape import PHASES
from novel_factory.text.normalize import normalize_text

FIXTURES = Path(__file__).resolve().parent / "fixtures"


class TestParsers:
    @pytest.mark.parametrize(
        ("filename", "expected_format"),
        [
            ("sample_novel.txt", "txt"),
            ("sample_novel_cp949.txt", "txt"),
            ("sample_novel.md", "markdown"),
            ("sample_novel.epub", "epub"),
            ("sample_novel.docx", "docx"),
            ("sample_novel.pdf", "pdf"),
        ],
    )
    def test_every_format_parses(self, filename: str, expected_format: str) -> None:
        doc = parse_file(FIXTURES / filename)
        assert doc.source_format == expected_format
        assert doc.text.strip()

    def test_cp949_matches_utf8(self) -> None:
        """같은 원고를 다른 인코딩으로 저장해도 같은 본문이 나와야 한다."""
        utf8 = parse_file(FIXTURES / "sample_novel.txt")
        cp949 = parse_file(FIXTURES / "sample_novel_cp949.txt")
        assert cp949.encoding == "cp949"
        assert utf8.text == cp949.text

    def test_epub_reads_metadata_and_spine(self) -> None:
        doc = parse_file(FIXTURES / "sample_novel.epub")
        assert doc.title == "회귀한 인수합병가"
        assert doc.author == "테스트 작가"
        assert doc.has_native_chapters

    def test_docx_reads_headings(self) -> None:
        doc = parse_file(FIXTURES / "sample_novel.docx")
        assert doc.title == "회귀한 인수합병가"
        assert doc.has_native_chapters

    def test_unsupported_extension(self, tmp_path: Path) -> None:
        bad = tmp_path / "novel.hwp"
        bad.write_bytes(b"x")
        with pytest.raises(UnsupportedFormatError):
            parse_file(bad)

    def test_supported_set(self) -> None:
        assert {".txt", ".md", ".epub", ".docx", ".pdf"} <= SUPPORTED_EXTENSIONS


class TestSplitter:
    def test_formats_agree_on_episode_count(self) -> None:
        """포맷이 달라도 같은 원고면 같은 회차 수가 나와야 한다."""
        counts = {
            name: len(split_document(parse_file(FIXTURES / name)).story_episodes)
            for name in ("sample_novel.txt", "sample_novel.md", "sample_novel.epub")
        }
        assert len(set(counts.values())) == 1, counts

    def test_marker_split_reads_numbers(self) -> None:
        result = split_document(parse_file(FIXTURES / "sample_novel.txt"))
        assert result.method == "marker"
        numbered = [e for e in result.story_episodes if e.number is not None]
        assert [e.number for e in numbered] == sorted(e.number for e in numbered)

    def test_sequence_is_always_contiguous(self) -> None:
        """원본 번호가 빠지거나 없어도 seq는 1부터 연속이어야 한다."""
        episodes = split_document(parse_file(FIXTURES / "sample_novel.txt")).story_episodes
        assert [e.seq for e in episodes] == list(range(1, len(episodes) + 1))

    def test_marker_line_must_be_short(self) -> None:
        """본문 안의 '3화'라는 말에 잘리면 안 된다."""
        text = normalize_text(
            "제1화 시작\n\n" + "그는 3화 정도는 더 버틸 수 있다고 생각했다. " * 6 + "\n\n"
            "제2화 다음\n\n둘째 회차 본문이다.\n"
        )
        result = split_by_markers(text)
        assert result is not None
        assert len(result.story_episodes) == 2

    def test_fallback_when_no_markers(self) -> None:
        text = normalize_text("\n\n".join(["문단이다. " * 40 for _ in range(12)]))
        result = split_document(_doc(text), fallback_chars=500)
        assert result.method == "fallback"
        assert len(result.story_episodes) > 1
        assert result.warnings

    def test_prologue_and_epilogue_detected(self) -> None:
        text = normalize_text(
            "프롤로그\n\n시작 전의 이야기다.\n\n제1화\n\n본문이다.\n\n"
            "제2화\n\n본문이다.\n\n에필로그\n\n끝난 뒤의 이야기다.\n"
        )
        result = split_by_markers(text)
        assert result is not None
        kinds = {e.kind for e in result.episodes}
        assert "프롤로그" in kinds and "에필로그" in kinds

    def test_author_note_is_not_story(self) -> None:
        text = normalize_text(
            "제1화\n\n본문.\n\n제2화\n\n본문.\n\n작가의 말\n\n읽어 주셔서 감사합니다.\n"
        )
        result = split_by_markers(text)
        assert result is not None
        assert len(result.story_episodes) == 2
        assert len(result.episodes) == 3


class TestEpisodeShape:
    def test_ratios_sum_to_one(self, sample_episodes) -> None:
        for episode in sample_episodes[:10]:
            shape = analyze_episode_shape(episode.text)
            assert sum(shape.ratios.values()) == pytest.approx(1.0, abs=1e-6)

    def test_all_phases_present_in_keys(self, sample_episodes) -> None:
        shape = analyze_episode_shape(sample_episodes[3].text)
        assert set(shape.as_dict()) == set(PHASES)

    def test_cliffhanger_never_dominates_whole_episode(self, sample_episodes) -> None:
        """꼬리 구간이 회차의 1/4을 크게 넘으면 구간 배분이 무의미해진다."""
        for episode in sample_episodes[:10]:
            shape = analyze_episode_shape(episode.text, tail_chars=400)
            assert shape.ratios["클리프행어"] <= 0.5

    def test_empty_text(self) -> None:
        shape = analyze_episode_shape("")
        assert shape.total_chars == 0
        assert sum(shape.ratios.values()) == 0.0


def _doc(text: str):
    from novel_factory.reference.parser.base import ParsedDocument

    return ParsedDocument(text=text, source_format="txt")


class TestCrossFormatConsistency:
    """같은 원고를 다른 포맷으로 읽어도 같은 통계가 나와야 한다.

    포맷마다 다른 값이 나오면 참고작 비교 자체가 무의미해진다.
    실제로 EPUB의 <p>를 줄바꿈 하나로만 바꾸던 시절에는 평균 문단 길이가
    TXT 22자 / EPUB 191자로 갈렸다.
    """

    FORMATS = ("sample_novel.txt", "sample_novel.md", "sample_novel.epub")

    @pytest.fixture(scope="class")
    def profiles(self):
        from novel_factory.reference import analyze_file

        return {
            name: analyze_file(
                FIXTURES / name, reference_id=name, with_fingerprint=False
            ).profile
            for name in self.FORMATS
        }

    def test_same_episode_count(self, profiles) -> None:
        counts = {n: p.basic.episode_count for n, p in profiles.items()}
        assert len(set(counts.values())) == 1, counts

    def test_same_sentence_length(self, profiles) -> None:
        values = [p.basic.avg_sentence_chars for p in profiles.values()]
        assert max(values) - min(values) < 1.0, values

    def test_paragraph_length_is_comparable(self, profiles) -> None:
        values = [p.basic.avg_paragraph_chars for p in profiles.values()]
        assert max(values) / min(values) < 1.5, values

    def test_same_dialogue_ratio(self, profiles) -> None:
        values = [p.basic.dialogue_ratio for p in profiles.values()]
        assert max(values) - min(values) < 0.02, values

    def test_same_cliffhanger_rate(self, profiles) -> None:
        values = [p.cliffhanger.rate for p in profiles.values()]
        assert max(values) - min(values) < 0.05, values
