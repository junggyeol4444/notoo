"""본문 정규화.

여기서 하는 일은 '통계를 왜곡하는 표기 흔들림'만 제거하는 것이다.
문체 분석 대상인 특징(문장 길이, 말줄임표 사용, 문단 나눔)은 건드리지 않는다.
"""

from __future__ import annotations

import re
import unicodedata

# 전각 따옴표 변형들을 대표 문자로 모은다. 대사 추출이 따옴표에 의존하기 때문에
# 이 통일이 없으면 같은 작품 안에서도 대사 비율이 튄다.
_QUOTE_MAP = {
    "“": '"',
    "”": '"',
    "〝": '"',
    "〞": '"',
    "＂": '"',
    "«": '"',
    "»": '"',
    "‘": "'",
    "’": "'",
    "‚": "'",
    "‛": "'",
    "＇": "'",
}
_QUOTE_RE = re.compile("|".join(map(re.escape, _QUOTE_MAP)))

# 말줄임표: ..., ···, ⋯, … 를 한 형태로. 웹소설에서 매우 흔하다.
_ELLIPSIS_RE = re.compile(r"(?:\.{2,}|·{2,}|…+|⋯+)")

_ZERO_WIDTH_RE = re.compile(r"[​-‏  ﻿­]")
_SPACE_RE = re.compile(r"[ \t 　 - ]+")
# 빈 줄 3개 이상은 2개로. 문단 경계 판정을 단순하게 유지한다.
_BLANKS_RE = re.compile(r"\n{3,}")
_TRAILING_WS_RE = re.compile(r"[ \t]+\n")


def normalize_text(text: str, *, unify_quotes: bool = True) -> str:
    """분석에 넣기 전 본문을 정규화한다."""
    if not text:
        return ""

    # NFC: 자모 분리 저장된 한글(맥/일부 EPUB)을 완성형으로 되돌린다.
    text = unicodedata.normalize("NFC", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _ZERO_WIDTH_RE.sub("", text)

    if unify_quotes:
        text = _QUOTE_RE.sub(lambda m: _QUOTE_MAP[m.group(0)], text)

    text = _ELLIPSIS_RE.sub("…", text)
    text = _SPACE_RE.sub(" ", text)
    text = _TRAILING_WS_RE.sub("\n", text)
    text = _BLANKS_RE.sub("\n\n", text)
    return text.strip()
