"""사용자 요청 해석 (기획안 57번).

    현대판타지 소설을 만들어줘.

    참고소설:
    A
    B
    C

    A에서는 빠른 전개만 참고.
    B에서는 복선 구조 참고.
    C에서는 캐릭터 관계 변화 참고.

    250화.
    회차당 약 5,000자.

위 같은 글을 규칙으로 먼저 읽는다. 장르·회차 수를 못 찾았거나 참고 문장을 못 읽었으면
LLM에게 같은 글을 JSON으로 옮기게 하고, 규칙으로 읽은 값이 없는 칸만 채운다.

참고 항목 해석
  "X에서는 ... 참고"는 X에서 참고할 항목을 정하는 문장이다. 적힌 항목만 1.0, 나머지는
  0으로 둔다(그 참고작에서 다른 것은 가져오지 않는다). 참고 문장이 없는 참고작은
  모든 항목을 1.0으로 참고한다.
  낱말 → 항목 대응은 ASPECT_KEYWORDS에 있다. 긴 낱말을 먼저 본다
  ("캐릭터 관계 변화"는 relationship이고 "캐릭터 구조"가 아니다).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from novel_factory.generation.prompts import REQUEST_SYSTEM, build_request_prompt
from novel_factory.generation.schemas import RequestOut
from novel_factory.generation.structured import StructuredOutputError, request_structured
from novel_factory.llm.base import LLMProvider, Message
from novel_factory.reference.pattern.aggregator import ASPECTS

#: 요청 글의 낱말 → 참고 항목. 앞에 있는 것부터(긴 것부터) 찾는다.
ASPECT_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("캐릭터 관계 변화", ("relationship",)),
    ("인물 관계 변화", ("relationship",)),
    ("관계 변화", ("relationship",)),
    ("캐릭터 관계", ("relationship",)),
    ("인물 관계", ("relationship",)),
    ("캐릭터 구조", ("character_structure",)),
    ("인물 구조", ("character_structure",)),
    ("인물 구성", ("character_structure",)),
    ("캐릭터 구성", ("character_structure",)),
    ("빠른 전개", ("pacing",)),
    ("전개 속도", ("pacing",)),
    ("회차 구조", ("episode_shape",)),
    ("회차 구성", ("episode_shape",)),
    ("사건 주기", ("event_interval",)),
    ("사건 간격", ("event_interval",)),
    ("감정 곡선", ("emotion",)),
    ("감정곡선", ("emotion",)),
    ("감정선", ("emotion",)),
    ("대사 비율", ("dialogue",)),
    ("클리프행어", ("cliffhanger",)),
    ("절단신공", ("cliffhanger",)),
    ("복선", ("foreshadowing",)),
    ("문체", ("style",)),
    ("문장", ("style",)),
    ("전개", ("pacing",)),
    ("대사", ("dialogue",)),
    ("감정", ("emotion",)),
    ("관계", ("relationship",)),
)

KNOWN_GENRES: tuple[str, ...] = (
    "로맨스판타지",
    "현대판타지",
    "라이트노벨",
    "판타지",
    "무협",
    "로맨스",
    "SF",
    "미스터리",
    "스릴러",
    "공포",
    "드라마",
    "스포츠",
    "역사",
)

_GENRE_RE = re.compile(r"([가-힣A-Za-z]+)\s*소설(?:을|를)?\s*(?:만들|써|쓰|작성)")
_EPISODES_RE = re.compile(r"(?<![\d,])(\d[\d,]*)\s*화(?!\s*(?:마다|씩|째))")
_CHARS_RE = re.compile(r"회차\s*(?:당|마다)\s*(?:약|대략)?\s*([\d,]+)\s*자")
_REF_HEAD_RE = re.compile(r"(?:^|[\s.])참고\s*(?:소설|작품|작)?\s*[:：]\s*(.*)$")
_ASPECT_RE = re.compile(r"(.+?)\s*에서(?:는|은)?\s+(.+?)\s*(?:만\s*)?참고")
_TITLE_RE = re.compile(r"제목\s*[:：]?\s*[\"'「『]?([^\"'」』\n]+)[\"'」』]?")


@dataclass(slots=True)
class ReferenceSpec:
    name: str
    aspects: list[str] | None = None  # None = 전부 참고

    def weights(self) -> dict[str, float]:
        if self.aspects is None:
            return {}
        return {a: (1.0 if a in self.aspects else 0.0) for a in ASPECTS}


@dataclass(slots=True)
class ProjectRequest:
    text: str = ""
    genre: str = ""
    title: str = ""
    episodes: int | None = None
    chars_per_episode: int | None = None
    references: list[ReferenceSpec] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    used_llm: bool = False

    def as_dict(self) -> dict[str, object]:
        return {
            "genre": self.genre,
            "title": self.title,
            "episodes": self.episodes,
            "chars_per_episode": self.chars_per_episode,
            "references": [
                {"name": r.name, "aspects": r.aspects, "weights": r.weights()}
                for r in self.references
            ],
            "warnings": self.warnings,
            "used_llm": self.used_llm,
        }


def aspects_in(phrase: str) -> list[str]:
    found: list[str] = []
    rest = phrase
    for word, aspects in ASPECT_KEYWORDS:
        if word in rest:
            for a in aspects:
                if a not in found:
                    found.append(a)
            rest = rest.replace(word, " ")  # 짧은 낱말이 같은 자리를 다시 잡지 않게
    return found


def _number(text: str) -> int:
    return int(text.replace(",", ""))


def parse_rules(text: str) -> ProjectRequest:
    req = ProjectRequest(text=text)
    m = _GENRE_RE.search(text)
    if m:
        req.genre = m.group(1)
    else:
        req.genre = next((g for g in KNOWN_GENRES if g in text), "")
    m = _TITLE_RE.search(text)
    if m:
        req.title = m.group(1).strip()

    chars = _CHARS_RE.search(text)
    if chars:
        req.chars_per_episode = _number(chars.group(1))
    for m in _EPISODES_RE.finditer(text):
        # "회차당 약 5,000자" 안의 숫자와 헷갈리지 않게, 화 앞의 숫자만 본다.
        req.episodes = _number(m.group(1))
        break

    lines = text.splitlines()
    names: list[str] = []
    for i, line in enumerate(lines):
        head = _REF_HEAD_RE.search(line)
        if not head:
            continue
        inline = head.group(1).strip()
        if inline:
            names += [n.strip() for n in re.split(r"[,，/]", inline) if n.strip()]
        for follow in lines[i + 1 :]:
            item = follow.strip().lstrip("-*·•").strip()
            if not item:
                if names:
                    break
                continue
            if _ASPECT_RE.search(item) or _EPISODES_RE.search(item):
                break
            names.append(item)
        break

    specs: dict[str, ReferenceSpec] = {n: ReferenceSpec(n) for n in names}
    for sentence in re.split(r"[.\n。]", text):
        m = _ASPECT_RE.search(sentence.strip())
        if not m:
            continue
        name = m.group(1).strip().strip("\"'「」『』")
        aspects = aspects_in(m.group(2))
        if not aspects:
            req.warnings.append(
                f"'{sentence.strip()}'에서 참고 항목을 알아보지 못했습니다."
            )
            continue
        spec = specs.setdefault(name, ReferenceSpec(name))
        spec.aspects = sorted({*(spec.aspects or []), *aspects}, key=ASPECTS.index)
    req.references = list(specs.values())
    return req


def _needs_llm(req: ProjectRequest) -> bool:
    return not req.genre or req.episodes is None or bool(req.warnings)


def parse_request(text: str, provider: LLMProvider | None = None) -> ProjectRequest:
    """규칙으로 읽고, 모자라면 LLM으로 빈칸을 채운다."""
    req = parse_rules(text)
    if not _needs_llm(req) or provider is None or not provider.available:
        if not req.genre:
            req.warnings.append("장르를 찾지 못했습니다.")
        if req.episodes is None:
            req.warnings.append("회차 수를 찾지 못했습니다.")
        return req
    try:
        out, _ = request_structured(
            provider,
            [
                Message("system", REQUEST_SYSTEM),
                Message("user", build_request_prompt(text)),
            ],
            RequestOut,
            max_tokens=1500,
        )
    except StructuredOutputError:
        req.warnings.append("LLM으로도 요청을 읽지 못했습니다. 규칙으로 읽은 값만 씁니다.")
        return req
    req.used_llm = True
    req.genre = req.genre or out.genre.strip()
    req.title = req.title or out.title.strip()
    req.episodes = req.episodes if req.episodes is not None else out.episodes
    req.chars_per_episode = req.chars_per_episode or out.chars_per_episode
    known = {r.name: r for r in req.references}
    for r in out.references:
        aspects = [a for a in r.aspects if a in ASPECTS]
        dropped = [a for a in r.aspects if a not in ASPECTS]
        if dropped:
            req.warnings.append(f"'{r.name}'의 알 수 없는 참고 항목을 뺐습니다: {dropped}")
        spec = known.get(r.name)
        if spec is None:
            spec = known[r.name] = ReferenceSpec(r.name)
            req.references.append(spec)
        if aspects and spec.aspects is None:
            spec.aspects = aspects
    return req
