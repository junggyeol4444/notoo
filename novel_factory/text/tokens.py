"""토큰/음절 단위 유틸과 지문(shingle) 생성.

형태소 분석기를 쓰지 않는다. 웹소설 통계에서 필요한 단위는
음절 수, 어절 수, 그리고 표절 검사용 문자 n-gram 정도다.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterator

_HANGUL_SYLLABLE_RE = re.compile(r"[가-힣]")
_WS_RE = re.compile(r"\s+")
# 지문용: 공백과 문장부호를 지우고 글자만 남긴다.
_NON_WORD_RE = re.compile(r"[^\w가-힣]+", re.UNICODE)


def syllables(text: str) -> int:
    """공백을 제외한 글자 수. 웹소설의 '자수'는 보통 이 기준이다."""
    return len(_WS_RE.sub("", text))


def count_hangul(text: str) -> int:
    """한글 음절 수만 센다."""
    return len(_HANGUL_SYLLABLE_RE.findall(text))


def eojeols(text: str) -> list[str]:
    """어절(공백 단위) 목록."""
    return [t for t in _WS_RE.split(text) if t]


def char_ngrams(text: str, n: int = 8) -> Iterator[str]:
    """공백/문장부호를 제거한 뒤의 문자 n-gram.

    참고작과의 문장 유사도를 볼 때 띄어쓰기만 바꾼 베끼기를 잡기 위해
    공백을 지우고 비교한다.
    """
    flat = _NON_WORD_RE.sub("", text)
    if len(flat) < n:
        if flat:
            yield flat
        return
    for i in range(len(flat) - n + 1):
        yield flat[i : i + n]


def shingle_hashes(text: str, n: int = 8, *, limit: int | None = None) -> set[int]:
    """문자 n-gram을 64비트 정수로 해싱한 집합.

    참고소설 원문을 보관하지 않고도 유사도를 비교하기 위한 지문이다.
    해시에서 원문을 복원할 수 없으므로 저작권 측면에서도 원문 보관보다 낫다.
    limit을 주면 해시값이 작은 쪽부터 그만큼만 남기는 min-hash 방식으로
    긴 작품도 고정 크기로 저장한다.
    """
    hashes = {
        int.from_bytes(hashlib.blake2b(g.encode("utf-8"), digest_size=8).digest(), "big")
        for g in char_ngrams(text, n)
    }
    if limit is not None and len(hashes) > limit:
        return set(sorted(hashes)[:limit])
    return hashes


def jaccard(a: set[int], b: set[int]) -> float:
    """두 지문 집합의 자카드 유사도."""
    if not a or not b:
        return 0.0
    inter = len(a & b)
    if inter == 0:
        return 0.0
    return inter / len(a | b)
