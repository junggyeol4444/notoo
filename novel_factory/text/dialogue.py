"""대사 / 속마음 / 서술 분리.

한국 웹소설 표기 관습:
  "……"  큰따옴표 = 발화(대사)
  '……'  작은따옴표 = 속마음(내적 독백)
  「……」 = 발화 (일부 작품)
  『……』 = 인용/강조

작은따옴표는 영어 축약형(don't)이나 소유격과 충돌한다. 그래서 앞뒤 문맥이
따옴표다울 때만 여는/닫는 기호로 인정한다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from novel_factory.text.tokens import syllables


class SegmentKind(str, Enum):
    DIALOGUE = "dialogue"     # 입 밖으로 낸 말
    INNER = "inner"           # 속마음
    NARRATION = "narration"   # 서술


@dataclass(frozen=True, slots=True)
class Segment:
    kind: SegmentKind
    text: str
    start: int
    end: int

    @property
    def length(self) -> int:
        """공백 제외 글자 수."""
        return syllables(self.text)


# 여는 기호 -> (닫는 기호, 종류)
_PAIRS: dict[str, tuple[str, SegmentKind]] = {
    '"': ('"', SegmentKind.DIALOGUE),
    "「": ("」", SegmentKind.DIALOGUE),   # 「」
    "'": ("'", SegmentKind.INNER),
    "『": ("』", SegmentKind.INNER),      # 『』
}

_BEFORE_OPEN = frozenset(" \t\n([{-—―─")
_AFTER_CLOSE = frozenset(" \t\n.,!?)]}…—―")


def _can_open_apostrophe(text: str, i: int) -> bool:
    """i 위치의 작은따옴표가 '여는 따옴표'로 볼 만한지."""
    if i == 0:
        return True
    if text[i - 1] not in _BEFORE_OPEN:
        return False
    # 뒤가 공백이면 따옴표가 아니라 그냥 부호일 가능성이 높다.
    return i + 1 < len(text) and not text[i + 1].isspace()


def _can_close_apostrophe(text: str, i: int) -> bool:
    if i + 1 >= len(text):
        return True
    return text[i + 1] in _AFTER_CLOSE


def segment_text(text: str) -> list[Segment]:
    """본문을 대사/속마음/서술 구간으로 나눈다.

    닫히지 않은 따옴표는 해당 줄 끝에서 강제로 닫는다. 실제 원고에는
    따옴표 짝이 안 맞는 줄이 생각보다 많고, 한 번 어긋나면 작품 전체의
    대사 비율이 뒤집히기 때문이다.
    """
    segments: list[Segment] = []
    if not text:
        return segments

    buf_start = 0
    i = 0
    n = len(text)

    while i < n:
        ch = text[i]
        pair = _PAIRS.get(ch)
        if pair is None:
            i += 1
            continue
        closer, kind = pair
        if ch == "'" and not _can_open_apostrophe(text, i):
            i += 1
            continue

        # 닫는 짝 찾기. 같은 줄 안에서만 찾는다.
        line_end = text.find("\n", i + 1)
        limit = n if line_end == -1 else line_end
        j = i + 1
        found = -1
        while j < limit:
            if text[j] == closer:
                if closer == "'" and not _can_close_apostrophe(text, j):
                    j += 1
                    continue
                found = j
                break
            j += 1

        end = (found + 1) if found != -1 else limit
        if buf_start < i:
            segments.append(
                Segment(SegmentKind.NARRATION, text[buf_start:i], buf_start, i)
            )
        segments.append(Segment(kind, text[i:end], i, end))
        buf_start = end
        i = end

    if buf_start < n:
        segments.append(Segment(SegmentKind.NARRATION, text[buf_start:n], buf_start, n))

    # 공백만 있는 서술 조각은 버린다.
    return [s for s in segments if s.text.strip()]


@dataclass(frozen=True, slots=True)
class SpeechStats:
    total_chars: int
    dialogue_chars: int
    inner_chars: int
    narration_chars: int
    dialogue_lines: int

    @property
    def dialogue_ratio(self) -> float:
        return self.dialogue_chars / self.total_chars if self.total_chars else 0.0

    @property
    def inner_ratio(self) -> float:
        return self.inner_chars / self.total_chars if self.total_chars else 0.0

    @property
    def narration_ratio(self) -> float:
        return self.narration_chars / self.total_chars if self.total_chars else 0.0

    def as_dict(self) -> dict[str, float | int]:
        return {
            "total_chars": self.total_chars,
            "dialogue_chars": self.dialogue_chars,
            "inner_chars": self.inner_chars,
            "narration_chars": self.narration_chars,
            "dialogue_lines": self.dialogue_lines,
            "dialogue_ratio": round(self.dialogue_ratio, 4),
            "inner_ratio": round(self.inner_ratio, 4),
            "narration_ratio": round(self.narration_ratio, 4),
        }


def speech_stats(text: str, segments: list[Segment] | None = None) -> SpeechStats:
    """대사/속마음/서술 비율 (공백 제외 글자 수 기준)."""
    segs = segments if segments is not None else segment_text(text)
    dialogue = sum(s.length for s in segs if s.kind is SegmentKind.DIALOGUE)
    inner = sum(s.length for s in segs if s.kind is SegmentKind.INNER)
    narration = sum(s.length for s in segs if s.kind is SegmentKind.NARRATION)
    total = dialogue + inner + narration
    lines = sum(1 for s in segs if s.kind is SegmentKind.DIALOGUE)
    return SpeechStats(total, dialogue, inner, narration, lines)
