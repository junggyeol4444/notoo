"""Style 검사와 Hook 검사 — LLM 없이.

Style (기획안 32·33번)
  forbidden_phrase     Style Bible의 금지 표현이 글자 그대로 나온다. FAIL.
                       "과도한 비유"처럼 추상적인 규칙은 글자로 찾을 수 없어서
                       이 검사로는 못 잡는다.
  avoided_phrase       이번 화에서 피하라고 한 반복 표현이 서술부에 또 나온다. WARN.
  sentence_length      평균 문장 길이가 목표에서 50% 넘게 벗어난다. WARN.
  paragraph_length     평균 문단 길이가 목표의 2배를 넘거나 절반에 못 미친다. WARN.
  dialogue_ratio       대사 비율이 목표에서 20%p 넘게 벗어난다. WARN.
  pov                  Style Bible의 시점과 서술부 시점 판정이 다르다. WARN.
  length               분량이 목표의 70% 미만이거나 150% 초과. WARN.

Hook (기획안 8·29번)
  hook_missing         계획은 클리프행어로 끊으라고 했는데 마지막 장면 말미에
                       훅 근거가 없다 (훅 어휘가 없고 구조 신호도 하나 이하). FAIL.
  hook_weak            근거는 있지만 판정 기준 점수에 못 미친다. WARN.
  hook_type            훅은 있는데 계획한 유형과 다르게 판정된다. WARN
                       (유형 판정은 규칙 기반 추정이라 확정하지 않는다).
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from novel_factory.database.models import Episode, Novel
from novel_factory.database.repositories import StyleBibleRepository
from novel_factory.quality.report import CheckResult, Issue, Severity, locate_scene
from novel_factory.reference.analyzer.cliffhanger import (
    NO_CLIFFHANGER,
    classify_cliffhanger,
)
from novel_factory.reference.analyzer.episode_metrics import compute_metrics
from novel_factory.reference.analyzer.style import detect_pov
from novel_factory.reference.structure.splitter import Episode as SplitEpisode
from novel_factory.text.dialogue import SegmentKind, segment_text
from novel_factory.text.sentence import split_sentences

STYLE = "Style"
HOOK = "Hook"

SENTENCE_TOLERANCE = 0.5
DIALOGUE_TOLERANCE = 0.20
LENGTH_RANGE = (0.7, 1.5)
HOOK_TAIL_CHARS = 400


def _narration(text: str) -> str:
    return "\n".join(s.text for s in segment_text(text) if s.kind is SegmentKind.NARRATION)


def _first_sentence_with(phrase: str, text: str) -> str:
    return next((s for s in split_sentences(text) if phrase in s), phrase)


def check_style(
    session: Session,
    novel: Novel,
    episode: Episode,
    *,
    default_dialogue_ratio: float | None = None,
) -> CheckResult:
    text = episode.final_text or ""
    scene_texts = [str(s.get("text") or "") for s in (episode.scenes or [])]
    result = CheckResult(STYLE)
    if not text.strip():
        result.skipped = "원고가 없다."
        return result

    split = SplitEpisode(
        index=0, number=episode.number, title=episode.title, text=text, seq=1
    )
    m = compute_metrics(split, with_fingerprint=False)
    bible = StyleBibleRepository(session).for_novel(novel.id)
    directives = (episode.outline or {}).get("directives") or {}

    def issue(
        code: str, severity: Severity, message: str, quote: str = "", **ev: object
    ) -> None:
        scene = locate_scene(quote, scene_texts) if quote else None
        result.issues.append(Issue(STYLE, code, severity, message, quote, scene, dict(ev)))

    if bible is not None:
        for phrase in bible.forbidden or []:
            if phrase and phrase in text:
                issue(
                    "forbidden_phrase",
                    Severity.FAIL,
                    f"금지 표현 '{phrase}'이(가) 쓰였다.",
                    _first_sentence_with(phrase, text),
                    phrase=phrase,
                )

    narration = _narration(text)
    for phrase in directives.get("avoid_phrases") or []:
        count = narration.count(phrase)
        if count:
            issue(
                "avoided_phrase",
                Severity.WARN,
                f"피하라고 한 표현 '{phrase}'이(가) {count}번 나온다.",
                _first_sentence_with(phrase, text),
                phrase=phrase,
                count=count,
            )

    target_sentence = bible.target_sentence_chars if bible else None
    if (
        target_sentence
        and m.avg_sentence_chars
        and abs(m.avg_sentence_chars - target_sentence) / target_sentence
        > SENTENCE_TOLERANCE
    ):
        issue(
            "sentence_length",
            Severity.WARN,
            f"평균 문장 {m.avg_sentence_chars:.0f}자 (목표 {target_sentence:.0f}자).",
            actual=round(m.avg_sentence_chars, 1),
            target=target_sentence,
        )

    target_paragraph = bible.target_paragraph_chars if bible else None
    if target_paragraph and m.avg_paragraph_chars:
        ratio = m.avg_paragraph_chars / target_paragraph
        if ratio > 2 or ratio < 0.5:
            issue(
                "paragraph_length",
                Severity.WARN,
                f"평균 문단 {m.avg_paragraph_chars:.0f}자 (목표 {target_paragraph:.0f}자).",
                actual=round(m.avg_paragraph_chars, 1),
                target=target_paragraph,
            )

    target_dialogue = bible.target_dialogue_ratio if bible else default_dialogue_ratio
    if (
        target_dialogue is not None
        and abs(m.dialogue_ratio - target_dialogue) > DIALOGUE_TOLERANCE
    ):
        issue(
            "dialogue_ratio",
            Severity.WARN,
            f"대사 비율 {m.dialogue_ratio:.0%} (목표 {target_dialogue:.0%}).",
            actual=round(m.dialogue_ratio, 3),
            target=target_dialogue,
        )

    if bible is not None and bible.pov:
        pov, first, third = detect_pov([split])
        wanted = (
            "1인칭" if "1인칭" in bible.pov else ("3인칭" if "3인칭" in bible.pov else "")
        )
        if wanted and pov in ("1인칭", "3인칭") and pov != wanted:
            issue(
                "pov",
                Severity.WARN,
                f"Style Bible은 {bible.pov}인데 서술부는 {pov}로 판정된다.",
                first_person=first,
                third_person=third,
            )

    target_chars = int(
        directives.get("target_chars") or novel.target_chars_per_episode or 0
    )
    if target_chars:
        ratio = m.char_count / target_chars
        low, high = LENGTH_RANGE
        if ratio < low or ratio > high:
            issue(
                "length",
                Severity.WARN,
                f"분량 {m.char_count:,}자 (목표 {target_chars:,}자).",
                actual=m.char_count,
                target=target_chars,
            )

    result.metrics = {
        "char_count": m.char_count,
        "avg_sentence_chars": round(m.avg_sentence_chars, 1),
        "avg_paragraph_chars": round(m.avg_paragraph_chars, 1),
        "dialogue_ratio": round(m.dialogue_ratio, 3),
        "inner_ratio": round(m.inner_ratio, 3),
    }
    return result


def check_hook(episode: Episode) -> CheckResult:
    result = CheckResult(HOOK)
    outline = episode.outline or {}
    required = bool(((outline.get("directives") or {}).get("hook") or {}).get("required"))
    planned = str(((outline.get("plan") or {}).get("hook") or {}).get("type") or "")
    scenes = episode.scenes or []
    # 장면 원문이 없는 옛 회차는 회차 본문 끝으로 본다.
    last_text = str(scenes[-1].get("text") or "") if scenes else ""
    last_text = last_text or episode.final_text or ""
    if not last_text.strip():
        result.skipped = "원고가 없다."
        return result

    verdict = classify_cliffhanger(last_text[-HOOK_TAIL_CHARS:])
    result.metrics = {
        "planned": planned,
        "detected": verdict.kind,
        "score": round(verdict.score, 2),
    }
    last_index = len(scenes) - 1 if scenes else None
    if not required:
        return result

    # 근거 기준은 판정기와 같다. 짧은 마지막 문단 하나만으로는 훅이 아니다.
    if verdict.kind == NO_CLIFFHANGER and not verdict.grounded:
        result.issues.append(
            Issue(
                HOOK,
                "hook_missing",
                Severity.FAIL,
                f"'{planned}' 클리프행어로 끊어야 하는데 "
                "마지막 장면 말미에 훅 근거가 없다.",
                scene_index=last_index,
                evidence={"planned": planned},
            )
        )
    elif verdict.kind == NO_CLIFFHANGER:
        result.issues.append(
            Issue(
                HOOK,
                "hook_weak",
                Severity.WARN,
                f"'{planned}' 클리프행어가 약하다 (판정 점수 {verdict.score:.1f}).",
                scene_index=last_index,
                evidence={"planned": planned, "score": verdict.score},
            )
        )
    elif planned and verdict.kind != planned:
        result.issues.append(
            Issue(
                HOOK,
                "hook_type",
                Severity.WARN,
                f"계획한 유형은 '{planned}'인데 '{verdict.kind}'로 판정된다.",
                scene_index=last_index,
                evidence={"planned": planned, "detected": verdict.kind},
            )
        )
    return result
