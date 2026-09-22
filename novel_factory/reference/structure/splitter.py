"""회차 분리.

한국 웹소설/전자책에서 실제로 쓰이는 회차 마커를 인식한다.
마커를 찾지 못하면 목표 글자 수 기준으로 문단 경계에서 자른다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from itertools import pairwise

from novel_factory.reference.parser.base import ParsedDocument
from novel_factory.text.normalize import normalize_text
from novel_factory.text.tokens import syllables

# 마커 줄은 제목 줄이다. 본문 문장 안에 "3화"가 섞여 있는 경우와 구분하려면
# 줄 전체가 짧아야 한다는 조건이 필요하다.
MAX_MARKER_LINE_CHARS = 60

_NUM = r"(?P<num>\d{1,4})"
MARKER_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    # 제12화 / 제 12 화 / 12화 / 012화. / 12화 - 제목
    (
        "화",
        re.compile(rf"^\s*(?:제\s*)?{_NUM}\s*화(?![가-힣])\s*[.:\-–—]?\s*(?P<title>.*)$"),
    ),
    # 제3장 / 3장
    (
        "장",
        re.compile(rf"^\s*(?:제\s*)?{_NUM}\s*장(?![가-힣])\s*[.:\-–—]?\s*(?P<title>.*)$"),
    ),
    # 제3권은 회차가 아니라 상위 단위지만, 마커가 이것뿐이면 회차로 쓴다.
    (
        "권",
        re.compile(rf"^\s*(?:제\s*)?{_NUM}\s*권(?![가-힣])\s*[.:\-–—]?\s*(?P<title>.*)$"),
    ),
    (
        "회",
        re.compile(rf"^\s*(?:제\s*)?{_NUM}\s*회(?![가-힣])\s*[.:\-–—]?\s*(?P<title>.*)$"),
    ),
    (
        "chapter",
        re.compile(
            rf"^\s*(?:chapter|chap\.?|ch\.?)\s*{_NUM}\s*[.:\-–—]?\s*(?P<title>.*)$", re.I
        ),
    ),
    (
        "episode",
        re.compile(rf"^\s*(?:episode|ep)\.?\s*{_NUM}\s*[.:\-–—]?\s*(?P<title>.*)$", re.I),
    ),
    ("hash", re.compile(rf"^\s*#\s*{_NUM}\s*[.:\-–—]?\s*(?P<title>.*)$")),
    # 숫자만 있는 줄 (신뢰도 낮음. 다른 마커가 하나도 없을 때만 쓴다.)
    ("bare", re.compile(rf"^\s*{_NUM}\s*[.:]?\s*$")),
]

# 번호가 없는 특수 회차
SPECIAL_MARKERS: list[tuple[str, re.Pattern[str]]] = [
    (
        "프롤로그",
        re.compile(
            r"^\s*(?:prologue|프롤로그|서장|서막)\s*[.:\-–—]?\s*(?P<title>.*)$", re.I
        ),
    ),
    (
        "에필로그",
        re.compile(r"^\s*(?:epilogue|에필로그|종장)\s*[.:\-–—]?\s*(?P<title>.*)$", re.I),
    ),
    (
        "외전",
        re.compile(
            r"^\s*(?:외전|번외|번외편|side\s*story)\s*[.:\-–—]?\s*(?P<title>.*)$", re.I
        ),
    ),
    (
        "작가의말",
        re.compile(r"^\s*(?:작가의\s*말|후기|작가\s*후기)\s*[.:\-–—]?\s*(?P<title>.*)$"),
    ),
]

# 본문이 아니라 부가 정보인 회차. 통계에서 제외한다.
NON_STORY_KINDS = frozenset({"작가의말"})


@dataclass(slots=True)
class Episode:
    """분리된 한 회차."""

    index: int  # 0부터 시작하는 순서
    number: int | None  # 마커에서 읽은 회차 번호 (없으면 None)
    title: str
    text: str
    kind: str = "화"  # 어떤 마커로 잡혔는지
    is_story: bool = True  # 작가의 말 등은 False
    seq: int = 0  # 스토리 회차 중 몇 번째인지 (1부터). 전개 분석은 이 값을 쓴다.

    @property
    def char_count(self) -> int:
        """공백 제외 글자 수. 웹소설 '자수' 기준."""
        return syllables(self.text)

    def as_dict(self) -> dict[str, object]:
        return {
            "index": self.index,
            "number": self.number,
            "title": self.title,
            "kind": self.kind,
            "is_story": self.is_story,
            "seq": self.seq,
            "char_count": self.char_count,
        }


def assign_sequence(episodes: list[Episode]) -> list[Episode]:
    """스토리 회차에 1부터 순번을 매긴다.

    파일이 알려준 `number`는 원본 그대로 두고, 전개 속도·사건 간격 계산은
    항상 `seq`를 쓴다. 원본 번호는 빠지거나 중복되거나 외전 때문에 흔들린다.
    """
    seq = 0
    for ep in episodes:
        if ep.is_story:
            seq += 1
            ep.seq = seq
        else:
            ep.seq = 0
    return episodes


@dataclass(slots=True)
class SplitResult:
    episodes: list[Episode]
    method: str  # "native" | "marker" | "fallback"
    marker_kind: str | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def story_episodes(self) -> list[Episode]:
        return [e for e in self.episodes if e.is_story]


def _match_line(line: str) -> tuple[str, int | None, str] | None:
    """(kind, number, title) 또는 None."""
    stripped = line.strip()
    if not stripped or len(stripped) > MAX_MARKER_LINE_CHARS:
        return None

    for kind, pattern in SPECIAL_MARKERS:
        m = pattern.match(stripped)
        if m:
            return kind, None, (m.group("title") or "").strip() or kind

    for kind, pattern in MARKER_PATTERNS:
        m = pattern.match(stripped)
        if m:
            num = int(m.group("num"))
            title = (m.groupdict().get("title") or "").strip()
            return kind, num, title or f"{num}{kind if kind in '화장권회' else ''}"
    return None


def _scan_markers(text: str) -> dict[str, list[tuple[int, int, int | None, str]]]:
    """마커 종류별로 (줄번호, 문자오프셋, 번호, 제목) 목록을 모은다."""
    found: dict[str, list[tuple[int, int, int | None, str]]] = {}
    offset = 0
    for line_no, line in enumerate(text.split("\n")):
        hit = _match_line(line)
        if hit is not None:
            kind, num, title = hit
            found.setdefault(kind, []).append((line_no, offset, num, title))
        offset += len(line) + 1
    return found


def _pick_marker_kind(
    found: dict[str, list[tuple[int, int, int | None, str]]],
) -> str | None:
    """가장 믿을 만한 마커 종류를 고른다.

    개수가 많고, 번호가 단조 증가하는 쪽이 진짜 회차 마커다.
    "bare"(숫자만 있는 줄)는 다른 후보가 전혀 없을 때만 쓴다.
    """
    candidates = {
        k: v
        for k, v in found.items()
        if k not in ("bare",) and k not in {n for n, _ in SPECIAL_MARKERS}
    }
    if not candidates:
        candidates = {k: v for k, v in found.items() if k == "bare"}
    if not candidates:
        return None

    def score(kind: str) -> tuple[float, int]:
        hits = candidates[kind]
        nums = [n for _, _, n, _ in hits if n is not None]
        if len(nums) < 2:
            return (0.0, len(hits))
        increasing = sum(1 for a, b in pairwise(nums) if b > a)
        monotonic = increasing / (len(nums) - 1)
        return (monotonic * len(hits), len(hits))

    best = max(candidates, key=score)
    if len(candidates[best]) < 2:
        return None
    return best


def split_by_markers(text: str) -> SplitResult | None:
    """마커 기반 분리. 마커를 못 찾으면 None."""
    found = _scan_markers(text)
    kind = _pick_marker_kind(found)
    if kind is None:
        return None

    # 고른 마커 종류 + 특수 회차(프롤로그/에필로그/외전)를 모두 경계로 쓴다.
    boundaries: list[tuple[int, int | None, str, str]] = [
        (off, num, title, kind) for _, off, num, title in found[kind]
    ]
    for special, _ in SPECIAL_MARKERS:
        for _, off, num, title in found.get(special, []):
            boundaries.append((off, num, title, special))
    boundaries.sort(key=lambda b: b[0])

    episodes: list[Episode] = []
    warnings: list[str] = []

    # 첫 마커 앞의 글은 제목/서문이다. 500자를 넘으면 버리지 않고 회차로 만든다.
    head = text[: boundaries[0][0]].strip()
    if syllables(head) > 500:
        episodes.append(Episode(0, None, "머리말", head, kind="머리말", is_story=False))
        warnings.append("첫 회차 마커 앞에 긴 본문이 있어 '머리말'로 분리했습니다.")

    for i, (offset, num, title, mkind) in enumerate(boundaries):
        end = boundaries[i + 1][0] if i + 1 < len(boundaries) else len(text)
        chunk = text[offset:end]
        # 마커 줄 자체는 본문에서 뺀다. 제목이 통계에 섞이면 안 된다.
        nl = chunk.find("\n")
        body = chunk[nl + 1 :].strip() if nl != -1 else ""
        if not body:
            continue
        episodes.append(
            Episode(
                index=len(episodes),
                number=num,
                title=title,
                text=body,
                kind=mkind,
                is_story=mkind not in NON_STORY_KINDS,
            )
        )

    if not episodes:
        return None
    return SplitResult(
        episodes=assign_sequence(episodes),
        method="marker",
        marker_kind=kind,
        warnings=warnings,
    )


def split_by_length(text: str, target_chars: int) -> SplitResult:
    """마커가 없을 때의 폴백. 문단 경계에서 목표 길이에 맞춰 자른다."""
    paragraphs = [p for p in text.split("\n\n") if p.strip()]
    episodes: list[Episode] = []
    buf: list[str] = []
    buf_len = 0

    for para in paragraphs:
        plen = syllables(para)
        if buf and buf_len + plen > target_chars:
            episodes.append(
                Episode(
                    index=len(episodes),
                    number=len(episodes) + 1,
                    title=f"{len(episodes) + 1}화(추정)",
                    text="\n\n".join(buf).strip(),
                    kind="length",
                )
            )
            buf, buf_len = [], 0
        buf.append(para)
        buf_len += plen

    if buf:
        episodes.append(
            Episode(
                index=len(episodes),
                number=len(episodes) + 1,
                title=f"{len(episodes) + 1}화(추정)",
                text="\n\n".join(buf).strip(),
                kind="length",
            )
        )

    return SplitResult(
        episodes=assign_sequence(episodes),
        method="fallback",
        warnings=[
            f"회차 마커를 찾지 못해 {target_chars}자 기준으로 나눴습니다. "
            "전개 속도·회차 구조 분석의 신뢰도가 낮습니다."
        ],
    )


def split_document(doc: ParsedDocument, *, fallback_chars: int = 5000) -> SplitResult:
    """ParsedDocument를 회차 단위로 나눈다.

    우선순위:
      1. 포맷이 이미 나눠 준 챕터(EPUB spine, DOCX 제목 스타일)
      2. 본문의 회차 마커
      3. 길이 기준 폴백
    """
    if doc.has_native_chapters:
        episodes: list[Episode] = []
        doc_title = (doc.title or "").strip()
        for raw in doc.raw_chapters:
            body = normalize_text(raw.text)
            if not body.strip():
                continue
            # 표지/속표지: 본문이 책 제목뿐인 챕터는 회차가 아니다.
            if doc_title and body.strip() == doc_title:
                continue
            hit = _match_line(raw.title)
            kind, num, title = hit if hit else ("native", None, raw.title.strip())
            # 챕터 제목이 본문 첫 줄에 그대로 또 들어 있으면 뺀다.
            first_nl = body.find("\n")
            if first_nl != -1 and body[:first_nl].strip() == raw.title.strip():
                body = body[first_nl + 1 :].strip()
            episodes.append(
                Episode(
                    index=len(episodes),
                    number=num,
                    title=title or raw.title,
                    text=body,
                    kind=kind,
                    is_story=kind not in NON_STORY_KINDS,
                )
            )
        if len(episodes) > 1:
            return SplitResult(
                episodes=assign_sequence(episodes), method="native", marker_kind=None
            )

    text = normalize_text(doc.text)
    by_marker = split_by_markers(text)
    if by_marker is not None and len(by_marker.story_episodes) > 1:
        return by_marker
    return split_by_length(text, fallback_chars)
