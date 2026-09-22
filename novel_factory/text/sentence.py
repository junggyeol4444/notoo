"""한국어 문장/문단 분리.

웹소설 본문은 한 줄이 곧 한 문장인 경우가 많고, 대사는 따옴표로 감싸며,
말줄임표로 끝나는 문장이 흔하다. 이 세 가지를 전제로 한 규칙 기반 분리다.
형태소 분석기를 쓰지 않으므로 완벽하지 않다. 목적은 '문장 길이 통계가
작품끼리 비교 가능할 정도로 일관되게 나오는 것'이지 언어학적 정확성이 아니다.
"""

from __future__ import annotations

import re

_TERMINATORS = frozenset(".!?…")
# 종결 부호 뒤에 붙어 나올 수 있는 닫는 기호들. 여기까지 한 문장에 포함시킨다.
_CLOSERS = frozenset("\"')」』】》〉]}")
_OPENERS = frozenset("\"'(「『【《〈[{—―-")

_BLANK_SPLIT_RE = re.compile(r"\n\s*\n")
# "Mr." "vs." 같은 약어 뒤 마침표를 문장 끝으로 오인하지 않기 위한 가드.
_ABBREV_RE = re.compile(r"(?:^|\s)[A-Za-z]{1,3}\.$")
# "2026. 3. 1." 처럼 숫자 사이에 낀 마침표는 날짜 표기다.
_NUMERIC_TAIL_RE = re.compile(r"\d\s*$")


def split_paragraphs(text: str) -> list[str]:
    """빈 줄 기준으로 문단을 나눈다."""
    return [p.strip() for p in _BLANK_SPLIT_RE.split(text) if p.strip()]


def split_sentences(text: str) -> list[str]:
    """문장 목록. 빈 문자열은 포함하지 않는다.

    줄바꿈은 무조건 문장 경계로 본다. 웹소설에서 줄바꿈은 문장을 끊는
    표현 수단이지 단순한 줄 넘김이 아니기 때문이다.
    """
    if not text or not text.strip():
        return []

    out: list[str] = []
    for line in text.split("\n"):
        out.extend(_split_line(line.strip()))
    return out


def _split_line(line: str) -> list[str]:
    if not line:
        return []

    out: list[str] = []
    length = len(line)
    start = 0
    i = 0

    while i < length:
        if line[i] not in _TERMINATORS:
            i += 1
            continue

        # 연속된 종결 부호를 한 덩어리로 흡수한다: "뭐?!", "안 돼……!"
        j = i + 1
        while j < length and line[j] in _TERMINATORS:
            j += 1
        last_term = line[j - 1]

        # 종결 부호에 이어 붙은 닫는 기호까지 문장에 포함시킨다: '뭐?"'
        term_end = j
        while j < length and line[j] in _CLOSERS:
            j += 1
        had_closer = j > term_end

        if _is_boundary(line, start, i, j, last_term, had_closer):
            candidate = line[start:j].strip()
            if candidate:
                out.append(candidate)
            start = j
            while start < length and line[start].isspace():
                start += 1
            i = start
        else:
            i = j

    tail = line[start:].strip()
    if tail:
        # 종결 부호 없이 끝난 줄도 하나의 문장으로 센다.
        # 웹소설 대사에서 흔한 형태다.
        out.append(tail)
    return out


def _is_boundary(
    line: str,
    start: int,
    term_start: int,
    end: int,
    last_term: str,
    had_closer: bool,
) -> bool:
    """line[start:end]가 완결된 한 문장인지 판단."""
    # 말줄임표는 문장 중간에도 자주 쓰인다("그는… 말이 없었다").
    # 닫는 기호가 따라붙거나 줄이 끝날 때만 종결로 인정한다.
    if last_term == "…" and not had_closer and end < len(line):
        return False

    if _ABBREV_RE.search(line[start:end]):
        return False

    if end >= len(line):
        return True

    # 날짜 표기("2026. 3. 1.")를 문장 세 개로 쪼개지 않는다.
    if last_term == "." and not had_closer:
        rest = line[end:].lstrip()
        if rest[:1].isdigit() and _NUMERIC_TAIL_RE.search(line[start:term_start]):
            return False

    head = line[end]
    return head.isspace() or head in _OPENERS
