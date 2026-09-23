"""Writer Engine (기획안 31번).

장면 하나씩 쓴다. 한 번에 회차 전체(5,000자)를 쓰게 하면 로컬 모델은 뒤로 갈수록
계획을 잊고 분량을 못 채운다. 장면 단위로 쓰고, 다음 장면에는 직전 원고의
끝부분을 붙여 이어지게 한다.

장면마다 하는 일
  1. 장면 계획 + Writer Context + 직전 원고 끝으로 초고를 받는다.
  2. 목표 분량의 일정 비율보다 짧거나 길이 제한에 걸려 잘렸으면 이어 쓰게 한다.
  3. 참고작 지문과 대조한다(기획안 20번). FAIL이면 그 장면만 다시 쓴다(기획안 38번).
     몇 번 다시 써도 FAIL이면 가장 덜 비슷한 판본을 쓰고 경고를 남긴다.

전체 참고소설 원문은 Writer에게 주지 않는다 (기획안 31번). 참고작에서 넘어가는 것은
패턴 지시문뿐이다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from novel_factory.config import Settings, get_settings
from novel_factory.database.models import Episode, Novel
from novel_factory.errors import NovelFactoryError
from novel_factory.generation.context import output_tokens_for, prepare_context
from novel_factory.generation.guidance import GenreGuidance, load_guidance
from novel_factory.generation.prompts import (
    WRITER_SYSTEM,
    build_continue_prompt,
    build_rewrite_prompt,
    build_scene_writing_prompt,
)
from novel_factory.generation.structured import strip_reasoning
from novel_factory.llm.base import LLMProvider, Message
from novel_factory.reference.similarity import FingerprintIndex, check_text
from novel_factory.text.normalize import normalize_text
from novel_factory.text.tokens import syllables

#: 한 장면에서 이어 쓰기를 요청하는 최대 횟수
MAX_CONTINUATIONS = 2

_FENCE_RE = re.compile(r"^```[a-zA-Z]*\s*$", re.MULTILINE)
# 모델이 본문 앞에 붙이는 제목 줄: "## 장면 2", "장면 2:", "[장면 2]", "Scene 2"
_HEADING_RE = re.compile(
    r"^\s*(?:#{1,6}\s*.*|\[?\s*(?:장면|씬|scene)\s*\d+\s*\]?\s*[:：.\-–—]?.*)$",
    re.IGNORECASE,
)


def clean_prose(text: str) -> str:
    """모델 출력에서 본문이 아닌 것을 걷어낸다."""
    text = strip_reasoning(text)
    text = _FENCE_RE.sub("", text)
    lines = text.split("\n")
    # 제목 줄은 맨 앞 몇 줄에만 붙는다. 본문 중간의 '#'은 건드리지 않는다.
    while lines and (not lines[0].strip() or _HEADING_RE.match(lines[0])):
        lines.pop(0)
    return normalize_text("\n".join(lines))


@dataclass(slots=True)
class SceneDraft:
    index: int
    text: str
    target_chars: int
    continuations: int = 0
    rewrites: int = 0
    similarity: dict[str, object] | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def chars(self) -> int:
        return syllables(self.text)

    def as_dict(self) -> dict[str, object]:
        return {
            "index": self.index,
            "target_chars": self.target_chars,
            "chars": self.chars,
            "continuations": self.continuations,
            "rewrites": self.rewrites,
            "similarity": self.similarity,
            "warnings": self.warnings,
        }


@dataclass(slots=True)
class DraftResult:
    episode: Episode
    scenes: list[SceneDraft]
    text: str
    similarity: dict[str, object]
    warnings: list[str] = field(default_factory=list)

    @property
    def chars(self) -> int:
        return syllables(self.text)


def _write_once(
    provider: LLMProvider,
    messages: list[Message],
    target_chars: int,
    settings: Settings,
) -> tuple[str, int, list[str]]:
    """장면 하나. 짧거나 잘렸으면 이어 쓴다. (본문, 이어쓰기 횟수, 경고)."""
    max_tokens = output_tokens_for(target_chars, settings)
    completion = provider.complete(
        messages, temperature=settings.llm_temperature, max_tokens=max_tokens
    )
    text = clean_prose(completion.text)
    truncated = completion.truncated
    warnings: list[str] = []
    continuations = 0

    while continuations < MAX_CONTINUATIONS:
        missing = target_chars - syllables(text)
        short = syllables(text) < target_chars * settings.writer_min_length_ratio
        if not (short or truncated):
            break
        continuations += 1
        follow = provider.complete(
            [
                *messages,
                Message("assistant", text),
                Message(
                    "user",
                    build_continue_prompt(
                        text[-settings.writer_tail_chars :], missing_chars=max(missing, 200)
                    ),
                ),
            ],
            temperature=settings.llm_temperature,
            max_tokens=output_tokens_for(max(missing, 200), settings),
        )
        addition = clean_prose(follow.text)
        if not addition:
            warnings.append("이어 쓰기 요청에 빈 응답이 왔습니다.")
            break
        # 잘린 경우는 문장 중간이라 그대로 잇고, 아니면 문단을 나눈다.
        text = text + ("" if truncated else "\n\n") + addition
        truncated = follow.truncated

    if syllables(text) < target_chars * settings.writer_min_length_ratio:
        warnings.append(f"목표 {target_chars:,}자 중 {syllables(text):,}자만 썼습니다.")
    if truncated:
        warnings.append("마지막 응답이 길이 제한에 걸려 잘렸습니다.")
    return text, continuations, warnings


def write_episode(
    session: Session,
    novel: Novel,
    episode: Episode,
    provider: LLMProvider,
    *,
    guidance: GenreGuidance | None = None,
    settings: Settings | None = None,
    similarity_index: FingerprintIndex | None = None,
) -> DraftResult:
    cfg = settings or get_settings()
    outline = episode.outline or {}
    plan = outline.get("plan")
    directives = outline.get("directives") or {}
    scenes = list(episode.scenes or [])
    if not plan or not scenes:
        raise NovelFactoryError(
            f"{episode.number}화에 회차 계획이나 장면 설계가 없습니다. 먼저 계획하세요."
        )

    guide = guidance or load_guidance(session, novel)
    index = similarity_index or FingerprintIndex()
    avoid = list(directives.get("avoid_phrases") or [])
    hook = plan.get("hook") if (directives.get("hook") or {}).get("required") else None

    biggest_scene = max(int(s.get("target_chars") or 0) for s in scenes)
    _ctx, block, _report = prepare_context(
        session,
        novel,
        episode.number,
        cfg,
        output_tokens=output_tokens_for(biggest_scene, cfg),
        extra_prompt_chars=len(str(plan)) + len(str(scenes[0])) + cfg.writer_tail_chars,
        character_codes=list(plan.get("characters") or []) or None,
    )

    drafts: list[SceneDraft] = []
    body = ""
    for i, scene in enumerate(scenes):
        target = int(scene.get("target_chars") or 1000)
        prompt = build_scene_writing_prompt(
            block,
            plan,
            {k: v for k, v in scene.items() if k not in ("index",)},
            scene_index=i,
            scene_count=len(scenes),
            target_chars=target,
            previous_tail=body[-cfg.writer_tail_chars :],
            hook=hook,
            avoid_phrases=avoid,
            dialogue_ratio=guide.dialogue_ratio,
        )
        messages = [Message("system", WRITER_SYSTEM), Message("user", prompt)]
        text, continuations, warnings = _write_once(provider, messages, target, cfg)
        draft = SceneDraft(i, text, target, continuations=continuations, warnings=warnings)

        if index.entries and text:
            _check_and_rewrite(provider, messages, draft, index, cfg)

        drafts.append(draft)
        body = f"{body}\n\n{draft.text}" if body else draft.text

    text = normalize_text(body)
    report = (
        check_text(text, index).as_dict()
        if index.entries
        else {
            "verdict": "SKIPPED",
            "note": "연결된 참고작 지문이 없습니다.",
        }
    )

    warnings = [f"장면 {d.index + 1}: {w}" for d in drafts for w in d.warnings]
    episode.draft = text
    episode.final_text = text
    episode.char_count = syllables(text)
    episode.status = "drafted"
    episode.scenes = [
        {**scene, "draft": draft.as_dict()}
        for scene, draft in zip(scenes, drafts, strict=True)
    ]
    # JSON 컬럼은 안쪽 값이 바뀐 것을 스스로 알아채지 못한다.
    flag_modified(episode, "scenes")
    reports = dict(episode.quality_reports or {})
    reports["similarity"] = report
    reports["writer_warnings"] = warnings
    episode.quality_reports = reports
    session.flush()
    return DraftResult(episode, drafts, text, report, warnings)


def _check_and_rewrite(
    provider: LLMProvider,
    messages: list[Message],
    draft: SceneDraft,
    index: FingerprintIndex,
    settings: Settings,
) -> None:
    """참고작과 너무 비슷한 장면만 다시 쓴다 (기획안 20·38번)."""
    report = check_text(draft.text, index)
    best_text, best = draft.text, report
    attempts = 0
    while report.verdict == "FAIL" and attempts < settings.similarity_rewrite_attempts:
        attempts += 1
        top = report.hits[0] if report.hits else None
        reason = (
            f"참고작 {top.reference_id}({top.scope})와 표현이 "
            f"{top.containment:.0%} 겹칩니다"
            if top
            else "참고작과 표현이 너무 겹칩니다"
        )
        completion = provider.complete(
            [
                *messages,
                Message("assistant", best_text),
                Message("user", build_rewrite_prompt(best_text, reason=reason)),
            ],
            temperature=min(settings.llm_temperature + 0.1, 1.2),
            max_tokens=output_tokens_for(draft.target_chars, settings),
        )
        candidate = clean_prose(completion.text)
        if not candidate:
            continue
        report = check_text(candidate, index)
        if report.max_containment < best.max_containment:
            best_text, best = candidate, report

    draft.text = best_text
    draft.rewrites = attempts
    draft.similarity = best.as_dict()
    if best.verdict == "FAIL":
        draft.warnings.append(
            f"다시 써도 참고작과 겹칩니다 (containment {best.max_containment:.0%})."
        )
