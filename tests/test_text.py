"""한국어 텍스트 처리 테스트."""

from __future__ import annotations

import pytest

from novel_factory.text.dialogue import SegmentKind, segment_text, speech_stats
from novel_factory.text.encoding import decode_bytes, detect_encoding
from novel_factory.text.lexicon import (
    DEFAULT_LEXICON,
    count_hits,
    count_onomatopoeia,
    count_similes,
    find_hits,
    iter_hits,
)
from novel_factory.text.morph import noun_stems, strip_particle
from novel_factory.text.normalize import normalize_text
from novel_factory.text.sentence import split_paragraphs, split_sentences
from novel_factory.text.tokens import jaccard, shingle_hashes, syllables


class TestEncoding:
    @pytest.mark.parametrize(
        "encoding",
        ["utf-8", "cp949", "euc-kr", "utf-16"],
    )
    def test_roundtrip(self, encoding: str) -> None:
        original = "김도윤은 창밖을 내려다보았다. 도시는 그대로였다."
        text, detected = decode_bytes(original.encode(encoding))
        assert text == original, f"{encoding} 복호화 실패 (감지: {detected})"

    def test_bom_is_reported_only_when_present(self) -> None:
        plain = "안녕하세요".encode()
        assert decode_bytes(plain)[1] == "utf-8"
        assert decode_bytes(b"\xef\xbb\xbf" + plain)[1] == "utf-8-sig"

    def test_empty(self) -> None:
        assert decode_bytes(b"") == ("", "utf-8")
        assert detect_encoding(b"") == "utf-8"


class TestSentenceSplit:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ('"뭐?" 그가 물었다.', ['"뭐?"', "그가 물었다."]),
            ("그는… 말이 없었다.", ["그는… 말이 없었다."]),
            ("가격은 3.5억이었다. 비쌌다.", ["가격은 3.5억이었다.", "비쌌다."]),
            ("2026. 3. 1.에 벌어진 일이다.", ["2026. 3. 1.에 벌어진 일이다."]),
            # normalize_text가 말줄임표를 한 글자로 모은다.
            ('"안 돼……!" 그녀가 소리쳤다.', ['"안 돼…!"', "그녀가 소리쳤다."]),
        ],
    )
    def test_cases(self, text: str, expected: list[str]) -> None:
        assert split_sentences(normalize_text(text)) == expected

    def test_closing_quote_stays_with_sentence(self) -> None:
        """닫는 따옴표가 따로 떨어져 나오면 1글자짜리 가짜 문장이 생긴다."""
        for sentence in split_sentences(normalize_text('"괜찮아. 정말로."')):
            assert len(sentence) > 1

    def test_newline_is_a_boundary(self) -> None:
        assert len(split_sentences("첫 줄이다\n둘째 줄이다")) == 2

    def test_empty(self) -> None:
        assert split_sentences("") == []
        assert split_sentences("   \n  ") == []

    def test_paragraphs(self) -> None:
        assert len(split_paragraphs("가\n\n나\n\n다")) == 3


class TestDialogue:
    def test_segments(self) -> None:
        text = normalize_text(
            "\"정말 괜찮겠어?\"\n서연이 물었다.\n'이 여자, 뭔가 알고 있다.'\n그는 잔을 내려놓았다."
        )
        kinds = [s.kind for s in segment_text(text)]
        assert SegmentKind.DIALOGUE in kinds
        assert SegmentKind.INNER in kinds
        assert SegmentKind.NARRATION in kinds

    def test_ratios_sum_to_one(self) -> None:
        text = normalize_text("\"안녕.\"\n그가 말했다.\n'이상하다.'")
        stats = speech_stats(text)
        total = stats.dialogue_ratio + stats.inner_ratio + stats.narration_ratio
        assert total == pytest.approx(1.0)

    def test_unclosed_quote_does_not_swallow_rest(self) -> None:
        """따옴표 짝이 안 맞으면 그 줄 끝에서 닫는다.

        안 그러면 원고 하나의 오타가 작품 전체 대사 비율을 뒤집는다.
        """
        text = '"열린 따옴표\n다음 줄은 서술이다. 여기까지 대사가 되면 안 된다.'
        segments = segment_text(text)
        narration = " ".join(s.text for s in segments if s.kind is SegmentKind.NARRATION)
        assert "다음 줄은 서술이다" in narration

    def test_english_apostrophe_is_not_inner_monologue(self) -> None:
        stats = speech_stats(normalize_text("그는 don't이라고 적었다."))
        assert stats.inner_ratio == 0.0


class TestLexicon:
    @pytest.mark.parametrize(
        ("text", "should_hit"),
        [
            ("커피를 한 모금 마셨다.", False),  # '피'가 '커피'에 걸리면 안 된다
            ("바닥에 피가 흥건했다.", True),
            ("이번 분기 실적이 좋았다.", False),  # '적'이 '실적'에 걸리면 안 된다
            ("적대 세력이 움직였다.", True),
            ("서울에서 만났다.", False),  # '울'이 '서울'에 걸리면 안 된다
            ("그녀가 울었다.", True),
        ],
    )
    def test_no_substring_false_positives(self, text: str, should_hit: bool) -> None:
        assert bool(count_hits(text, DEFAULT_LEXICON.negative)) is should_hit

    def test_find_hits_counts_each_position_once(self) -> None:
        hits = find_hits("배신. 배신. 배신.", DEFAULT_LEXICON.negative)
        assert hits["배신"] == 3

    def test_iter_hits_gives_positions(self) -> None:
        positions = [
            i for i, _ in iter_hits("조용했다. 배신이었다.", DEFAULT_LEXICON.negative)
        ]
        assert positions and positions[0] > 0

    def test_simile_and_onomatopoeia(self) -> None:
        text = "마치 얼음처럼 차가웠다. 쿵쿵. 두근두근."
        assert count_similes(text) == 2
        assert count_onomatopoeia(text) >= 2


class TestMorph:
    @pytest.mark.parametrize(
        ("eojeol", "expected_stem"),
        [
            ("도윤은", "도윤"),
            ("서연이가", "서연"),
            ("민석에게", "민석"),
            ("이번에는", "이번"),
            ("도윤에서", "도윤"),
            ("회장께서", "회장"),
            ("서연보다", "서연"),
            ("민석까지", "민석"),
            ("도윤이라고", "도윤"),
        ],
    )
    def test_strip_particle(self, eojeol: str, expected_stem: str) -> None:
        hit = strip_particle(eojeol)
        assert hit is not None and hit[0] == expected_stem

    @pytest.mark.parametrize("eojeol", ["있었다", "않았다", "생각했다", "조용했다"])
    def test_predicates_are_rejected(self, eojeol: str) -> None:
        assert strip_particle(eojeol) is None

    def test_noun_stems_skips_predicates(self) -> None:
        stems = noun_stems("도윤은 서류를 넘겼다. 숫자는 정직했다.")
        assert "도윤" in stems
        assert "넘겼다" not in stems


class TestTokens:
    def test_syllables_ignores_whitespace(self) -> None:
        assert syllables("가 나  다\n라") == 4

    def test_fingerprint_is_whitespace_insensitive(self) -> None:
        text = "도윤은 서류를 한 장씩 넘겼다. 숫자는 정직했다."
        a = shingle_hashes(text)
        b = shingle_hashes(text.replace(" ", ""))
        assert jaccard(a, b) > 0.9

    def test_fingerprint_limit(self) -> None:
        long_text = "가나다라마바사아자차" * 500
        assert len(shingle_hashes(long_text, limit=100)) <= 100

    def test_unrelated_texts_do_not_match(self) -> None:
        a = shingle_hashes("도윤은 계약서를 내려놓았다. 회의실은 조용했다.")
        b = shingle_hashes("소년은 언덕을 올랐다. 멀리서 종소리가 들렸다.")
        assert jaccard(a, b) < 0.05
