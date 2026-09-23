"""반복 표현 감지 (기획안 33번).

AI가 쓴 소설에서 가장 먼저 눈에 띄는 것은 같은 동작 묘사의 반복이다.

  피식 웃었다. / 눈빛이 가라앉았다. / 입꼬리가 올라갔다. / 말없이 바라보았다.

최근 N화의 서술부에서 '문장을 끝맺는 두세 어절'을 세고, 기준을 넘은 표현을
다음 회차에서 쓰지 말라고 Writer에게 넘긴다. 대사는 세지 않는다. 인물의 말버릇은
반복돼도 되는 개성이기 때문이다.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

from novel_factory.text.dialogue import SegmentKind, segment_text
from novel_factory.text.sentence import split_sentences

# 기획안 33번에 예시로 적힌 표현. 기준 횟수와 무관하게 항상 센다.
KNOWN_CLICHES: tuple[str, ...] = (
    "피식 웃었다",
    "눈빛이 가라앉았다",
    "입꼬리가 올라갔다",
    "말없이 바라보았다",
)

_EOJEOL_RE = re.compile(r"\S+")
_TRAILING_PUNCT_RE = re.compile(r"[.!?…\"'”’)\]]+$")
# 서술 문장의 끝으로 볼 어미. 명사로 끝나는 조각("그의 얼굴.")은 관용구가 아니다.
_PREDICATE_END_RE = re.compile(r"(?:다|요|죠)$")

#: 최근 몇 화를 볼지
DEFAULT_WINDOW = 5
#: 이 횟수 이상 나오면 과다 사용
DEFAULT_THRESHOLD = 4


@dataclass(slots=True)
class RepeatedPhrase:
    phrase: str
    count: int
    episodes: int  # 몇 개 회차에 걸쳐 나왔는가
    known_cliche: bool = False

    def as_dict(self) -> dict[str, object]:
        return {
            "phrase": self.phrase,
            "count": self.count,
            "episodes": self.episodes,
            "known_cliche": self.known_cliche,
        }


def _narration(text: str) -> str:
    return "\n".join(s.text for s in segment_text(text) if s.kind is SegmentKind.NARRATION)


def sentence_endings(text: str, *, sizes: tuple[int, ...] = (2, 3)) -> list[str]:
    """서술 문장마다 끝의 2~3어절."""
    out: list[str] = []
    for sentence in split_sentences(_narration(text)):
        words = _EOJEOL_RE.findall(sentence)
        if not words:
            continue
        words[-1] = _TRAILING_PUNCT_RE.sub("", words[-1])
        if not words[-1] or not _PREDICATE_END_RE.search(words[-1]):
            continue
        for n in sizes:
            if len(words) >= n:
                out.append(" ".join(words[-n:]))
    return out


def find_repetitions(
    texts: list[str],
    *,
    threshold: int = DEFAULT_THRESHOLD,
    min_episodes: int = 2,
    limit: int = 20,
) -> list[RepeatedPhrase]:
    """여러 회차에 걸쳐 반복된 문장 끝 표현.

    한 회차 안에서만 몰려 나온 표현은 그 장면의 의도일 수 있어서 빼고,
    min_episodes개 이상의 회차에 걸쳐 나온 것만 잡는다.
    """
    counts: Counter[str] = Counter()
    spread: Counter[str] = Counter()
    for text in texts:
        endings = sentence_endings(text)
        counts.update(endings)
        spread.update(set(endings))

    narration_all = "\n".join(_narration(t) for t in texts)
    result: list[RepeatedPhrase] = []
    seen: set[str] = set()

    for phrase in KNOWN_CLICHES:
        occurrences = narration_all.count(phrase)
        if occurrences >= 2:
            episodes = sum(1 for t in texts if phrase in _narration(t))
            result.append(RepeatedPhrase(phrase, occurrences, episodes, known_cliche=True))
            seen.add(phrase)

    for phrase, count in counts.most_common():
        if count < threshold:
            break
        if phrase in seen or spread[phrase] < min_episodes:
            continue
        # 3어절 표현이 잡혔으면 그 안의 2어절 꼬리는 따로 올리지 않는다.
        if any(other.endswith(phrase) and other != phrase for other in seen):
            continue
        seen.add(phrase)
        result.append(RepeatedPhrase(phrase, count, spread[phrase]))

    result.sort(key=lambda r: (-r.known_cliche, -r.count))
    return result[:limit]
