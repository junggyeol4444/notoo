"""완결 검사 (기획안 47번).

마지막 화 이후 전체 작품을 검사한다.

    미회수 복선 / 사라진 등장인물 / 미해결 갈등 / Timeline 오류 / 설정 충돌 / 결말 불일치

모든 문제(FAIL)가 해결되어야 완결 처리한다 (novel.status = "completed").
WARN은 사람이 볼 것이고 완결을 막지 않는다.

    Episodes       규칙  FAIL 1화~목표 회차 중 확정 안 된 회차
    Foreshadowing  규칙  FAIL 회수되지 않은 복선 (OPEN/DEVELOPING)
    Characters     규칙  FAIL 주요 인물이 퇴장 처리 없이 끝부분에서 사라짐
                         WARN 조연이 그렇게 사라짐
    Timeline       규칙  FAIL 퇴장한 인물이 그 뒤 사건에 참여
                         WARN 날짜가 앞 회차보다 앞섬 (회상일 수 있음)
    Settings       규칙  FAIL 회차 품질 보고에 Continuity·Logic FAIL이 남아 있음
                   LLM   WARN LLM이 찾은 설정 어긋남
    Conflict       LLM   FAIL 해소되지 않은 갈등 (high), WARN (low)
    Ending         LLM   FAIL 계획한 결말과 다르게 끝남
                         WARN 계획한 결말이 없는데 어긋난다고 봄

LLM 판단만으로 완결을 막는 경우가 있다 (Conflict high, Ending). 판단이 틀렸다고
보면 사람이 force로 완결 처리한다. 그 사실은 기록에 남는다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from novel_factory.config import Settings, get_settings
from novel_factory.database.models import Episode, Novel
from novel_factory.database.repositories import (
    ArcRepository,
    CharacterRepository,
    ForeshadowingRepository,
    TimelineRepository,
)
from novel_factory.generation.prompts import COMPLETION_SYSTEM, build_completion_prompt
from novel_factory.generation.schemas import CompletionOut
from novel_factory.generation.structured import StructuredOutputError, request_structured
from novel_factory.llm.base import LLMProvider, Message
from novel_factory.quality.continuity import name_forms, name_pattern, parse_dates
from novel_factory.quality.report import CheckResult, Issue, QualityReport, Severity

MAJOR_ROLES = ("주인공", "주요조연", "적대자")
#: 끝에서 이만큼(회차 수 비율, 최소 회차) 동안 안 나오면 사라진 것으로 본다
DISAPPEAR_RATIO = 0.2
DISAPPEAR_MIN = 10
COMPLETION_OUTPUT_TOKENS = 2000
#: 줄거리가 예산을 넘으면 앞에서 이만큼은 남기고 가운데를 줄인다
KEEP_HEAD_SUMMARIES = 10


def _issue(checker: str, code: str, severity: Severity, message: str, /, **ev) -> Issue:
    return Issue(checker, code, severity, message, evidence=dict(ev))


def _finals(session: Session, novel: Novel) -> list[Episode]:
    return list(
        session.scalars(
            select(Episode)
            .where(Episode.novel_id == novel.id, Episode.status == "final")
            .order_by(Episode.number)
        )
    )


def check_episodes(novel: Novel, finals: list[Episode]) -> CheckResult:
    result = CheckResult("Episodes")
    have = {e.number for e in finals}
    missing = [n for n in range(1, novel.planned_episodes + 1) if n not in have]
    if missing:
        result.issues.append(
            _issue(
                "Episodes",
                "missing_episodes",
                Severity.FAIL,
                f"확정되지 않은 회차 {len(missing)}개: {missing[:20]}",
                missing=missing[:200],
            )
        )
    result.metrics = {"planned": novel.planned_episodes, "final": len(have)}
    return result


def check_foreshadowing(session: Session, novel: Novel) -> CheckResult:
    result = CheckResult("Foreshadowing")
    items = ForeshadowingRepository(session).for_novel(novel.id)
    for f in items:
        if f.status in ("OPEN", "DEVELOPING"):
            result.issues.append(
                _issue(
                    "Foreshadowing",
                    "unresolved_foreshadowing",
                    Severity.FAIL,
                    f"[{f.code}] {f.description} ({f.setup_episode}화 설치) 미회수",
                    code=f.code,
                    setup_episode=f.setup_episode,
                )
            )
    result.metrics = {
        "total": len(items),
        "resolved": sum(1 for f in items if f.status == "RESOLVED"),
        "abandoned": sum(1 for f in items if f.status == "ABANDONED"),
    }
    return result


def last_appearances(
    session: Session, novel: Novel, finals: list[Episode]
) -> dict[str, int]:
    """인물 코드 → 마지막으로 이름이 나온 회차 (없으면 0)."""
    out: dict[str, int] = {}
    for c in CharacterRepository(session).for_novel(novel.id):
        pattern = name_pattern(name_forms(c))
        out[c.code] = max(
            (e.number for e in finals if pattern.search(e.final_text or "")), default=0
        )
    return out


def check_characters(session: Session, novel: Novel, finals: list[Episode]) -> CheckResult:
    result = CheckResult("Characters")
    if not finals:
        return result
    last = finals[-1].number
    window = max(DISAPPEAR_MIN, int(last * DISAPPEAR_RATIO))
    seen = last_appearances(session, novel, finals)
    for c in CharacterRepository(session).for_novel(novel.id):
        if not c.is_alive or c.exit_episode is not None:
            continue  # 퇴장 처리된 인물
        appeared = seen.get(c.code, 0)
        if appeared == 0 or appeared >= last - window:
            continue
        major = c.role in MAJOR_ROLES
        result.issues.append(
            _issue(
                "Characters",
                "disappeared_character",
                Severity.FAIL if major else Severity.WARN,
                f"{c.name}({c.role})이(가) {appeared}화 이후 나오지 않는데 "
                "퇴장 처리가 없다.",
                character=c.code,
                last_seen=appeared,
            )
        )
    result.metrics = {"window": window}
    return result


def check_timeline(session: Session, novel: Novel) -> CheckResult:
    result = CheckResult("Timeline")
    events = sorted(
        TimelineRepository(session).for_novel(novel.id, limit=100_000),
        key=lambda e: (e.episode_number or 0, e.sort_key, e.id),
    )
    chars = {c.code: c for c in CharacterRepository(session).for_novel(novel.id)}
    latest = None
    latest_ep = 0
    for e in events:
        for code in e.participants or []:
            c = chars.get(code)
            if (
                c is not None
                and c.exit_episode is not None
                and e.episode_number
                and e.episode_number > c.exit_episode
            ):
                result.issues.append(
                    _issue(
                        "Timeline",
                        "dead_participant",
                        Severity.FAIL,
                        f"{c.exit_episode}화에 퇴장한 {c.name}이(가) {e.episode_number}화 "
                        f"사건 '{e.title}'에 참여한다.",
                        event=e.id,
                        character=code,
                    )
                )
        dates = parse_dates(e.occurred_at or "")
        if not dates:
            continue
        when = min(dates)
        if latest is not None and when < latest and (e.episode_number or 0) > latest_ep:
            result.issues.append(
                _issue(
                    "Timeline",
                    "date_regression",
                    Severity.WARN,
                    f"{e.episode_number}화 사건 '{e.title}'({when.isoformat()})이 "
                    f"{latest_ep}화까지의 날짜({latest.isoformat()})보다 앞선다. "
                    "회상인지 확인.",
                    event=e.id,
                )
            )
        if latest is None or when > latest:
            latest, latest_ep = when, e.episode_number or 0
    result.metrics = {"events": len(events)}
    return result


def check_episode_reports(finals: list[Episode]) -> CheckResult:
    """회차마다 남은 품질 FAIL. 설정 충돌(Continuity·Logic)은 완결을 막는다."""
    result = CheckResult("Settings")
    for e in finals:
        quality = (e.quality_reports or {}).get("quality") or {}
        for name, r in (quality.get("results") or {}).items():
            if r.get("verdict") != "FAIL":
                continue
            blocking = name in ("Continuity", "Logic")
            result.issues.append(
                _issue(
                    "Settings",
                    "episode_fail_left",
                    Severity.FAIL if blocking else Severity.WARN,
                    f"{e.number}화 품질 검사 {name}: FAIL이 남아 있다.",
                    episode=e.number,
                    checker=name,
                )
            )
    return result


def _summaries_within(finals: list[Episode], budget_chars: int) -> list[str]:
    lines = [f"{e.number}화: {e.summary}" for e in finals if e.summary]
    if sum(len(s) for s in lines) <= budget_chars:
        return lines
    head = lines[:KEEP_HEAD_SUMMARIES]
    tail: list[str] = []
    size = sum(len(s) for s in head)
    for line in reversed(lines[KEEP_HEAD_SUMMARIES:]):
        if size + len(line) > budget_chars:
            break
        tail.insert(0, line)
        size += len(line)
    skipped = len(lines) - len(head) - len(tail)
    return [*head, f"... (중간 {skipped}화 줄거리 생략)", *tail]


def check_with_llm(
    session: Session,
    novel: Novel,
    finals: list[Episode],
    provider: LLMProvider | None,
    settings: Settings,
) -> list[CheckResult]:
    conflict, ending, llm_settings = (
        CheckResult("Conflict"),
        CheckResult("Ending"),
        CheckResult("SettingsLLM"),
    )
    if provider is None or not provider.available:
        for r in (conflict, ending):
            r.skipped = "LLM이 설정되지 않았다."
        return [conflict, ending]
    if not finals:
        for r in (conflict, ending):
            r.skipped = "확정된 회차가 없다."
        return [conflict, ending]

    final_text = finals[-1].final_text or ""
    budget_chars = int(
        (settings.llm_context_tokens - COMPLETION_OUTPUT_TOKENS - 3000)
        / settings.llm_tokens_per_char
    ) - len(final_text)
    bible = {
        "title": novel.title,
        "genre": novel.genre,
        "logline": novel.logline,
        "premise": novel.premise,
        "main_conflict": novel.main_conflict,
        "planned_ending": novel.ending,
    }
    arcs = [
        {
            "name": a.name,
            "episodes": f"{a.start_episode}~{a.end_episode}",
            "goal": a.goal,
            "conflict": a.conflict,
            "resolution": a.resolution,
        }
        for a in ArcRepository(session).for_novel(novel.id)
    ]
    prompt = build_completion_prompt(
        bible=bible,
        arcs=arcs,
        summaries=_summaries_within(finals, max(budget_chars, 2000)),
        final_episode=final_text,
    )
    try:
        out, _ = request_structured(
            provider,
            [Message("system", COMPLETION_SYSTEM), Message("user", prompt)],
            CompletionOut,
            retries=settings.llm_structured_retries,
            temperature=settings.llm_structured_temperature,
            max_tokens=COMPLETION_OUTPUT_TOKENS,
        )
    except StructuredOutputError:
        for r in (conflict, ending):
            r.skipped = "형식에 맞는 답을 받지 못했다."
        return [conflict, ending]

    for u in out.unresolved_conflicts:
        high = u.severity.strip().lower() == "high"
        conflict.issues.append(
            _issue(
                "Conflict",
                "unresolved_conflict",
                Severity.FAIL if high else Severity.WARN,
                f"미해결 갈등: {u.conflict} — {u.reason}".rstrip(" —"),
            )
        )
    last = finals[-1].number
    for sc in out.setting_conflicts:
        episodes = [n for n in sc.episodes if 1 <= n <= last]
        if not episodes:
            continue  # 없는 회차를 가리키면 근거가 없다
        llm_settings.issues.append(
            _issue(
                "SettingsLLM",
                "setting_conflict",
                Severity.WARN,
                f"설정 어긋남 ({', '.join(f'{n}화' for n in episodes)}): {sc.description}",
                episodes=episodes,
            )
        )
    if not out.ending_consistent:
        planned = bool(novel.ending.strip())
        ending.issues.append(
            _issue(
                "Ending",
                "ending_mismatch",
                Severity.FAIL if planned else Severity.WARN,
                "결말 불일치: " + ("; ".join(out.ending_issues) or "이유 없음"),
                planned_ending=novel.ending,
            )
        )
    return [conflict, ending, llm_settings]


@dataclass(slots=True)
class CompletionResult:
    report: QualityReport
    completed: bool
    forced: bool = False

    def as_dict(self) -> dict[str, object]:
        return {**self.report.as_dict(), "completed": self.completed, "forced": self.forced}


def check_completion(
    session: Session,
    novel: Novel,
    provider: LLMProvider | None = None,
    *,
    settings: Settings | None = None,
    force: bool = False,
    mark: bool = True,
) -> CompletionResult:
    """완결 검사. FAIL이 없으면(또는 force) mark=True일 때 작품을 completed로 바꾼다."""
    cfg = settings or get_settings()
    finals = _finals(session, novel)
    report = QualityReport()
    report.add(check_episodes(novel, finals))
    report.add(check_foreshadowing(session, novel))
    report.add(check_characters(session, novel, finals))
    report.add(check_timeline(session, novel))
    settings_result = check_episode_reports(finals)
    for r in check_with_llm(session, novel, finals, provider, cfg):
        if r.checker == "SettingsLLM":
            settings_result.issues.extend(r.issues)
        else:
            report.add(r)
    report.add(settings_result)

    ok = report.verdict != "FAIL"
    completed = mark and (ok or force)
    if completed:
        novel.status = "completed"
    result = CompletionResult(report, completed, forced=completed and not ok)
    novel.extra = {
        **(novel.extra or {}),
        "completion": {
            **result.as_dict(),
            "checked_at": datetime.now(UTC).isoformat(timespec="seconds"),
        },
    }
    session.flush()
    return result
