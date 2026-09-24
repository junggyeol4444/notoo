"""Continuity 검사 (기획안 34번) — 장기기억 DB와 원고를 대조한다. LLM 없이.

    이름 / 나이 / 사망 여부 / 등장 시점 / 날짜 / 계획한 등장인물

잡는 것
  dead_character       이미 죽은 인물이 등장한다. 그 인물이 말까지 하고 회차
                       계획에도 그 인물이 없으면 FAIL, 이름만 나오면 WARN
                       (회상·언급일 수 있다).
  future_character     아직 등장하지 않은 인물의 이름이 나온다. Writer에게 그 인물을
                       보여 주지 않았는데 이름이 나왔다면 우연이거나 누설이다. WARN.
  age_mismatch         "도윤은 서른네 살"처럼 나이가 적혔는데 DB와 다르다. WARN
                       (작중 시간이 흘렀을 수 있다).
  time_backwards       본문의 날짜가 이미 지나간 시간선보다 앞선다. WARN (회상일 수 있다).
  planned_absent       회차 계획의 등장인물이 본문에 한 번도 안 나온다. WARN.

못 잡는 것
  인물이 모르는 정보를 말하는지, 설정과 모순되는 서술인지는 뜻을 읽어야 한다.
  그건 Logic 검사(LLM)가 맡는다.
"""

from __future__ import annotations

import re
from datetime import date

from sqlalchemy.orm import Session

from novel_factory.database.models import Character, Episode, Novel
from novel_factory.database.repositories import CharacterRepository, TimelineRepository
from novel_factory.quality.report import CheckResult, Issue, Severity, locate_scene
from novel_factory.text.sentence import split_sentences

CHECKER = "Continuity"

_HANGUL = r"가-힣"
_DATE_PATTERNS = (
    re.compile(r"(\d{4})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일"),
    re.compile(r"(\d{4})[-./](\d{1,2})[-./](\d{1,2})"),
)
_NATIVE_AGES = {
    "스물": 20,
    "서른": 30,
    "마흔": 40,
    "쉰": 50,
    "예순": 60,
    "일흔": 70,
    "여든": 80,
    "아흔": 90,
}
_NATIVE_UNITS = {
    "한": 1,
    "두": 2,
    "세": 3,
    "네": 4,
    "다섯": 5,
    "여섯": 6,
    "일곱": 7,
    "여덟": 8,
    "아홉": 9,
}
_NATIVE_AGE_RE = re.compile(
    r"(스물|서른|마흔|쉰|예순|일흔|여든|아흔)\s*(한|두|세|네|다섯|여섯|일곱|여덟|아홉)?\s*살"
)
_DIGIT_AGE_RE = re.compile(r"(\d{1,3})\s*(?:살|세)(?![" + _HANGUL + r"])")
# 발화 동사. 인물 추출기의 ATTRIBUTION_VERBS는 "이었다"처럼 흔한 어미까지 넣어
# 넓게 잡는데, 여기서는 FAIL을 내는 근거라서 말을 했다는 것이 분명한 것만 쓴다.
SPEECH_VERBS: tuple[str, ...] = (
    "말했",
    "물었",
    "대답했",
    "답했",
    "외쳤",
    "소리쳤",
    "속삭",
    "중얼",
    "되물었",
    "입을 열",
    "말을 이",
    "내뱉",
)
#: 이름 뒤 이 글자 수 안에 나이가 나와야 그 인물의 나이로 본다
AGE_WINDOW = 20


def name_forms(c: Character) -> list[str]:
    forms = [c.name]
    if len(c.name) == 3:
        forms.append(c.name[1:])
    return forms


def name_pattern(forms: list[str]) -> re.Pattern[str]:
    # 앞 글자가 한글이면 다른 단어의 일부다 ("연구소서연" 같은 우연을 막는다).
    alts = "|".join(re.escape(f) for f in sorted(forms, key=len, reverse=True))
    return re.compile(rf"(?<![{_HANGUL}])(?:{alts})")


def _sentences_with(pattern: re.Pattern[str], text: str) -> list[str]:
    return [s for s in split_sentences(text) if pattern.search(s)]


def _is_speaking(pattern: re.Pattern[str], sentence: str) -> bool:
    return any(v in sentence for v in SPEECH_VERBS) and bool(pattern.search(sentence))


def parse_dates(text: str) -> list[date]:
    out: list[date] = []
    for pattern in _DATE_PATTERNS:
        for m in pattern.finditer(text):
            try:
                out.append(date(int(m.group(1)), int(m.group(2)), int(m.group(3))))
            except ValueError:
                continue
    return out


def _ages_near(pattern: re.Pattern[str], text: str) -> list[tuple[int, str]]:
    """이름 바로 뒤에 나오는 나이 표현 (나이, 해당 문장)."""
    found: list[tuple[int, str]] = []
    for sentence in split_sentences(text):
        for m in pattern.finditer(sentence):
            window = sentence[m.end() : m.end() + AGE_WINDOW]
            digit = _DIGIT_AGE_RE.search(window)
            if digit:
                found.append((int(digit.group(1)), sentence))
                continue
            native = _NATIVE_AGE_RE.search(window)
            if native:
                age = _NATIVE_AGES[native.group(1)] + _NATIVE_UNITS.get(
                    native.group(2) or "", 0
                )
                found.append((age, sentence))
    return found


def check_continuity(session: Session, novel: Novel, episode: Episode) -> CheckResult:
    n = episode.number
    text = episode.final_text or ""
    scene_texts = [str(s.get("text") or "") for s in (episode.scenes or [])]
    result = CheckResult(CHECKER)
    plan = (episode.outline or {}).get("plan") or {}
    plan_blob = str(plan)
    planned_codes = set(plan.get("characters") or [])

    def issue(
        code: str, severity: Severity, message: str, quote: str, **evidence: object
    ) -> None:
        result.issues.append(
            Issue(
                CHECKER,
                code,
                severity,
                message,
                quote,
                locate_scene(quote, scene_texts),
                dict(evidence),
            )
        )

    characters = CharacterRepository(session).for_novel(novel.id)
    for c in characters:
        pattern = name_pattern(name_forms(c))
        hits = _sentences_with(pattern, text)

        dead = not c.is_alive and c.exit_episode is not None and c.exit_episode < n
        if dead and hits:
            speaking = [s for s in hits if _is_speaking(pattern, s)]
            in_plan = c.code in planned_codes or c.name in plan_blob
            if speaking and not in_plan:
                issue(
                    "dead_character",
                    Severity.FAIL,
                    f"{c.exit_episode}화에 퇴장한 {c.name}이(가) 말하거나 행동한다. "
                    "회차 계획에도 이 인물이 없다.",
                    speaking[0],
                    character=c.code,
                    exit_episode=c.exit_episode,
                )
            else:
                issue(
                    "dead_character",
                    Severity.WARN,
                    f"{c.exit_episode}화에 퇴장한 {c.name}이(가) 언급된다. 회상인지 확인.",
                    hits[0],
                    character=c.code,
                    exit_episode=c.exit_episode,
                )

        if c.first_episode is not None and c.first_episode > n and hits:
            issue(
                "future_character",
                Severity.WARN,
                f"{c.first_episode}화에 처음 나와야 할 {c.name}의 이름이 나온다.",
                hits[0],
                character=c.code,
                first_episode=c.first_episode,
            )

        if c.age:
            for age, sentence in _ages_near(pattern, text):
                if age != c.age:
                    issue(
                        "age_mismatch",
                        Severity.WARN,
                        f"{c.name}의 나이가 설정은 {c.age}세인데 본문은 {age}세다.",
                        sentence,
                        character=c.code,
                        expected=c.age,
                        found=age,
                    )
                    break

        if c.code in planned_codes and not hits:
            result.issues.append(
                Issue(
                    CHECKER,
                    "planned_absent",
                    Severity.WARN,
                    f"회차 계획의 등장인물 {c.name}이(가) 본문에 나오지 않는다.",
                    evidence={"character": c.code},
                )
            )

    past = [
        d
        for e in TimelineRepository(session).up_to_episode(novel.id, n - 1)
        for d in parse_dates(e.occurred_at or "")
    ]
    if past:
        latest = max(past)
        for sentence in split_sentences(text):
            earlier = [d for d in parse_dates(sentence) if d < latest]
            if earlier:
                issue(
                    "time_backwards",
                    Severity.WARN,
                    f"본문 날짜 {earlier[0].isoformat()}가 이미 지난 시간선 "
                    f"{latest.isoformat()}보다 앞선다. 회상인지 확인.",
                    sentence,
                    found=earlier[0].isoformat(),
                    latest=latest.isoformat(),
                )
                break

    result.metrics = {"characters_checked": len(characters), "timeline_dates": len(past)}
    return result
