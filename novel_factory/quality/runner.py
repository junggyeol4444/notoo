"""품질 검사 실행과 자동 수정 (기획안 38·42번).

검사 순서 (기획안 42번)

    Continuity → Logic → Similarity → Style → Hook  (→ Reader, 선택)

자동 수정 (기획안 38번)

    Continuity: PASS
    Logic: PASS
    Similarity: FAIL
    Hook: PASS

이면 회차 전체가 아니라 문제 장면만 다시 쓴다. 문제 장면은 FAIL 지적의 인용
구절이 들어 있는 장면이다(report.locate_scene). 고친 뒤 전체를 다시 검사하고,
quality_fix_rounds번까지 되풀이한다.

고쳐 쓴 판본이 더 나쁠 수도 있다(한 문제를 고치다 다른 문제를 만든다). 그래서
라운드마다 원고와 결과를 남겨 두고, 마지막에 FAIL이 가장 적은 판본(같으면 WARN이
적은 판본, 그것도 같으면 먼저 것)을 고른다.

Reader Simulation은 점수만 매기는 검사라 고칠 대상을 정하지 않는다. 고르기가
끝난 원고에 한 번만 돌린다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from novel_factory.config import Settings, get_settings
from novel_factory.database.models import Episode, Novel
from novel_factory.generation.context import output_tokens_for, prepare_context
from novel_factory.generation.guidance import GenreGuidance, load_guidance
from novel_factory.generation.prompts import WRITER_SYSTEM, build_fix_prompt
from novel_factory.generation.writer import assemble_text, write_scene
from novel_factory.llm.base import LLMProvider, Message
from novel_factory.memory.retrieval import plan_query
from novel_factory.quality.continuity import check_continuity
from novel_factory.quality.logic import check_logic
from novel_factory.quality.reader import simulate_readers
from novel_factory.quality.report import CheckResult, Issue, QualityReport, Severity
from novel_factory.quality.style_check import check_hook, check_style
from novel_factory.reference.service import linked_name_hashes
from novel_factory.reference.similarity import FingerprintIndex, check_text
from novel_factory.reference.similarity.names import find_reused_names, name_salt
from novel_factory.text.sentence import split_sentences
from novel_factory.text.tokens import syllables

SIMILARITY = "Similarity"
#: 고칠 장면 다음 장면의 앞부분을 이만큼 보여 준다 (이어지게 쓰도록)
NEXT_HEAD_CHARS = 600


def check_reference_names(
    episode: Episode, name_hashes: dict[str, str], salt: bytes, result: CheckResult
) -> None:
    """참고작 인물 이름(전체 이름)이 원고에 나오는가 (기획안 36번 고유 설정 유사).

    참고작의 주요 인물(주인공·주요조연·적대자) 이름이면 FAIL, 나머지 인물이면 WARN.
    """
    scenes = [str(s.get("text") or "") for s in (episode.scenes or [])] or [
        episode.final_text or ""
    ]
    reported: set[str] = set()
    for i, text in enumerate(scenes):
        for word, level in find_reused_names(text, name_hashes, salt):
            if word in reported:
                continue
            reported.add(word)
            quote = next((s for s in split_sentences(text) if word in s), word)
            result.issues.append(
                Issue(
                    SIMILARITY,
                    "reference_name",
                    Severity.FAIL if level == "main" else Severity.WARN,
                    f"참고작 {'주요 ' if level == 'main' else ''}인물과 같은 이름 "
                    f"'{word}'이(가) 나온다. 다른 이름으로 바꾼다.",
                    quote,
                    i if episode.scenes else None,
                    {"name": word, "level": level},
                )
            )


def check_similarity(
    episode: Episode,
    index: FingerprintIndex,
    *,
    name_hashes: dict[str, str] | None = None,
    salt: bytes = b"",
) -> CheckResult:
    """장면별로 참고작 지문과 대조하고, 참고작 인물 이름 재사용을 본다 (기획안 36번)."""
    result = CheckResult(SIMILARITY)
    if name_hashes:
        check_reference_names(episode, name_hashes, salt, result)
    if not index.entries:
        if not name_hashes:
            result.skipped = "연결된 참고작 지문이 없다."
        return result
    scenes = episode.scenes or []
    worst = 0.0
    for i, scene in enumerate(scenes):
        report = check_text(str(scene.get("text") or ""), index)
        worst = max(worst, report.max_containment)
        if report.verdict == "FAIL":
            top = report.hits[0] if report.hits else None
            where = f"참고작 {top.reference_id}({top.scope})" if top else "참고작"
            result.issues.append(
                Issue(
                    SIMILARITY,
                    "reference_overlap",
                    Severity.FAIL,
                    f"{where}와 표현이 {report.max_containment:.0%} 겹친다.",
                    scene_index=i,
                    evidence=report.as_dict(),
                )
            )
    whole = check_text(episode.final_text or "", index)
    if whole.verdict == "FAIL" and not result.issues:
        # 장면 하나하나는 괜찮은데 회차 전체로 보면 겹친다. 어느 장면인지 모른다.
        result.issues.append(
            Issue(
                SIMILARITY,
                "reference_overlap",
                Severity.FAIL,
                f"회차 전체가 참고작과 {whole.max_containment:.0%} 겹친다.",
                evidence=whole.as_dict(),
            )
        )
    elif whole.verdict == "WARN":
        result.issues.append(
            Issue(
                SIMILARITY,
                "reference_overlap",
                Severity.WARN,
                f"회차 전체가 참고작과 {whole.max_containment:.0%} 겹친다.",
                evidence=whole.as_dict(),
            )
        )
    result.metrics = {
        "max_scene_containment": round(worst, 4),
        "episode_containment": round(whole.max_containment, 4),
    }
    return result


def run_checks(
    session: Session,
    novel: Novel,
    episode: Episode,
    provider: LLMProvider | None,
    *,
    settings: Settings | None = None,
    similarity_index: FingerprintIndex | None = None,
    guidance: GenreGuidance | None = None,
    logic: bool = True,
) -> QualityReport:
    cfg = settings or get_settings()
    guide = guidance or load_guidance(session, novel)
    report = QualityReport()
    report.add(check_continuity(session, novel, episode))
    if logic:
        report.add(check_logic(session, novel, episode, provider, settings=cfg))
    report.add(
        check_similarity(
            episode,
            similarity_index or FingerprintIndex(),
            name_hashes=linked_name_hashes(session, novel),
            salt=name_salt(cfg),
        )
    )
    report.add(
        check_style(session, novel, episode, default_dialogue_ratio=guide.dialogue_ratio)
    )
    report.add(check_hook(episode))
    return report


@dataclass(slots=True)
class QualityOutcome:
    report: QualityReport
    history: list[dict[str, object]] = field(default_factory=list)
    chosen_round: int = 0
    fixed_scenes: list[int] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            **self.report.as_dict(),
            "chosen_round": self.chosen_round,
            "fixed_scenes": self.fixed_scenes,
            "history": self.history,
            "warnings": self.warnings,
        }


def _score(report: QualityReport) -> tuple[int, int]:
    issues = report.issues
    return (
        sum(1 for i in issues if i.severity is Severity.FAIL),
        sum(1 for i in issues if i.severity is Severity.WARN),
    )


def _scene_texts(episode: Episode) -> list[str]:
    return [str(s.get("text") or "") for s in (episode.scenes or [])]


def _apply_texts(episode: Episode, texts: list[str]) -> None:
    scenes = list(episode.scenes or [])
    episode.scenes = [{**s, "text": t} for s, t in zip(scenes, texts, strict=True)]
    flag_modified(episode, "scenes")
    episode.final_text = assemble_text(texts)
    episode.char_count = syllables(episode.final_text)


def _problem_lines(issues: list[Issue]) -> list[str]:
    lines = []
    for i in issues:
        line = f"[{i.checker}] {i.message}"
        if i.quote:
            line += f' 해당 구절: "{i.quote}"'
        lines.append(line)
    return lines


def check_and_fix(
    session: Session,
    novel: Novel,
    episode: Episode,
    provider: LLMProvider | None,
    *,
    settings: Settings | None = None,
    similarity_index: FingerprintIndex | None = None,
    guidance: GenreGuidance | None = None,
    fix: bool = True,
    logic: bool | None = None,
    reader: bool | None = None,
) -> QualityOutcome:
    """검사하고, FAIL 장면을 고치고, 결과를 episode.quality_reports["quality"]에 남긴다.

    fix=False면 원고를 건드리지 않고 검사만 한다.
    """
    cfg = settings or get_settings()
    guide = guidance or load_guidance(session, novel)
    index = similarity_index or FingerprintIndex()
    use_logic = cfg.quality_logic if logic is None else logic
    use_reader = cfg.quality_reader if reader is None else reader
    can_write = provider is not None and provider.available

    def check() -> QualityReport:
        return run_checks(
            session,
            novel,
            episode,
            provider,
            settings=cfg,
            similarity_index=index,
            guidance=guide,
            logic=use_logic,
        )

    report = check()
    outcome = QualityOutcome(report)
    outcome.history.append(
        {"round": 0, "verdict": report.verdict, "summary": report.summary()}
    )
    candidates = [(_score(report), 0, _scene_texts(episode), report)]

    rounds = cfg.quality_fix_rounds if fix else 0
    plan = dict((episode.outline or {}).get("plan") or {})
    directives = (episode.outline or {}).get("directives") or {}
    hook = plan.get("hook") if (directives.get("hook") or {}).get("required") else None
    block = ""

    for n in range(1, rounds + 1):
        failing = report.failing_scenes()
        if not failing:
            break
        if not can_write:
            outcome.warnings.append("LLM이 없어서 FAIL 장면을 고치지 못했다.")
            break
        scenes = list(episode.scenes or [])
        if not block:
            biggest = max(int(s.get("target_chars") or 0) for s in scenes)
            _ctx, block, _budget = prepare_context(
                session,
                novel,
                episode.number,
                cfg,
                output_tokens=output_tokens_for(biggest, cfg),
                extra_prompt_chars=len(str(plan))
                + 2 * biggest
                + cfg.writer_tail_chars
                + NEXT_HEAD_CHARS,
                character_codes=list(plan.get("characters") or []) or None,
                query=plan_query(plan),
            )

        texts = _scene_texts(episode)
        for idx in sorted(failing):
            scene = scenes[idx]
            target = int(scene.get("target_chars") or 1000)
            prompt = build_fix_prompt(
                block,
                plan,
                {k: v for k, v in scene.items() if k not in ("index", "text", "draft")},
                scene_index=idx,
                scene_count=len(scenes),
                target_chars=target,
                scene_text=texts[idx],
                problems=_problem_lines(failing[idx]),
                previous_tail=assemble_text(texts[:idx])[-cfg.writer_tail_chars :],
                next_head=texts[idx + 1][:NEXT_HEAD_CHARS] if idx + 1 < len(texts) else "",
                hook=hook,
            )
            new_text, _cont, warnings = write_scene(
                provider,  # type: ignore[arg-type]
                [Message("system", WRITER_SYSTEM), Message("user", prompt)],
                target,
                cfg,
            )
            outcome.warnings.extend(f"{n}차 수정 장면 {idx + 1}: {w}" for w in warnings)
            if new_text:
                texts[idx] = new_text

        _apply_texts(episode, texts)
        report = check()
        report.round = n
        outcome.history.append(
            {
                "round": n,
                "fixed_scenes": sorted(failing),
                "verdict": report.verdict,
                "summary": report.summary(),
            }
        )
        candidates.append((_score(report), n, texts, report))

    # 가장 나은 판본. 점수가 같으면 라운드가 이른 것 (덜 고친 원고).
    _best_score, best_round, best_texts, best_report = min(
        candidates, key=lambda c: (c[0], c[1])
    )
    if best_round != candidates[-1][1]:
        _apply_texts(episode, best_texts)
        outcome.warnings.append(
            f"{candidates[-1][1]}차까지 고쳐도 나아지지 않아서 {best_round}차 원고를 쓴다."
        )
    outcome.report = best_report
    outcome.chosen_round = best_round
    outcome.fixed_scenes = sorted(
        {i for h in outcome.history[1 : best_round + 1] for i in h["fixed_scenes"]}  # type: ignore[attr-defined]
    )

    if use_reader:
        best_report.add(simulate_readers(session, novel, episode, provider, settings=cfg))

    reports = dict(episode.quality_reports or {})
    reports["quality"] = outcome.as_dict()
    if outcome.chosen_round and index.entries:
        # 원고가 바뀌었으니 similarity_report.json도 바뀐 원고 기준으로.
        reports["similarity"] = check_text(episode.final_text or "", index).as_dict()
    episode.quality_reports = reports
    session.flush()
    return outcome
