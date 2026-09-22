"""한국어 웹소설 텍스트를 다루는 공용 유틸.

이 패키지는 LLM 없이 동작한다. 형태소 분석기(konlpy 등) 의존도 없다.
한국어 웹소설의 표기 관습(따옴표 대사, `~다.` 종결, 짧은 문단)을
전제로 한 규칙 기반 처리다.
"""

from novel_factory.text.dialogue import Segment, SegmentKind, segment_text, speech_stats
from novel_factory.text.encoding import decode_bytes, detect_encoding
from novel_factory.text.normalize import normalize_text
from novel_factory.text.sentence import split_paragraphs, split_sentences
from novel_factory.text.tokens import (
    char_ngrams,
    count_hangul,
    eojeols,
    shingle_hashes,
    syllables,
)

__all__ = [
    "Segment",
    "SegmentKind",
    "char_ngrams",
    "count_hangul",
    "decode_bytes",
    "detect_encoding",
    "eojeols",
    "normalize_text",
    "segment_text",
    "shingle_hashes",
    "speech_stats",
    "split_paragraphs",
    "split_sentences",
    "syllables",
]

from novel_factory.text.lexicon import (
    DEFAULT_LEXICON,
    LexiconBundle,
    count_hits,
    count_onomatopoeia,
    count_similes,
    weighted_hits,
)
from novel_factory.text.tokens import jaccard

__all__ += [
    "DEFAULT_LEXICON",
    "LexiconBundle",
    "count_hits",
    "count_onomatopoeia",
    "count_similes",
    "jaccard",
    "weighted_hits",
]
