"""장기 생성 평가 (기획안 55번 Phase 5·6).

    Phase 5  30화 연속 생성 — 설정 오류와 Reference Pattern 적용을 평가한다.
    Phase 6  100화 이상 장기 테스트 — 장기 기억과 복선 회수 성능을 검증한다.

이미 쓴 회차들의 기록(품질 보고, 기억 갱신 결과, 복선 상태)을 모아 수치로 낸다.
합격·불합격 선은 긋지 않는다. 기획안에 기준이 없고, 기준을 지어내면 그 숫자가
근거처럼 쓰이기 때문이다. 목표값이 있는 항목은 목표와의 차이를 같이 낸다.

  episodes          구간 안 회차 수, 확정·보류(held)·빠진 회차
  setting_errors    설정 오류: 회차별 품질 검사의 Continuity·Logic 지적 (고친 뒤 남은 것,
                    처음 검사에서 FAIL이었던 회차 수), 기억 갱신에서 버린 항목 수
  pattern           Reference Pattern 적용: 클리프행어 사용률·유형 분포, 분량, 대사 비율을
                    참고작에서 온 목표와 비교
  memory            장기 기억: 인물이 모르는 정보 사용, 사망·미등장 인물 등장, 나이 불일치,
                    계획한 인물 누락, 과거 장면 검색이 쓰인 회차 수
  foreshadowing     복선 회수: 심은 수, 회수 수, 예정 안에 회수한 수, 늦은 회수, 예정이
                    지났는데 열려 있는 수, 평균 회수 간격
"""

from __future__ import annotations

from collections import Counter

from sqlalchemy import select
from sqlalchemy.orm import Session

from novel_factory.database.models import Episode, Foreshadowing, Novel
from novel_factory.generation.guidance import load_guidance
from novel_factory.reference.analyzer.cliffhanger import NO_CLIFFHANGER

SETTING_CHECKERS = ("Continuity", "Logic")
MEMORY_CODES = (
    "knowledge_leak",
    "dead_character",
    "future_character",
    "age_mismatch",
    "planned_absent",
    "contradiction",
)


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 4) if values else None


def _results(episode: Episode) -> dict[str, dict]:
    return ((episode.quality_reports or {}).get("quality") or {}).get("results") or {}


def _first_round_fail(episode: Episode, checker: str) -> bool:
    history = ((episode.quality_reports or {}).get("quality") or {}).get("history") or []
    return bool(history) and f"{checker}: FAIL" in str(history[0].get("summary") or "")


def evaluate_novel(
    session: Session, novel: Novel, *, start: int = 1, end: int | None = None
) -> dict[str, object]:
    last = end or novel.planned_episodes or 0
    episodes = list(
        session.scalars(
            select(Episode)
            .where(
                Episode.novel_id == novel.id,
                Episode.number >= start,
                Episode.number <= (last or 10**9),
            )
            .order_by(Episode.number)
        )
    )
    finals = [e for e in episodes if e.status == "final"]
    numbers = {e.number for e in episodes}
    last = last or max(numbers, default=0)

    # --- 회차 --------------------------------------------------------------------
    report: dict[str, object] = {
        "range": [start, last],
        "episodes": {
            "in_range": last - start + 1 if last >= start else 0,
            "final": len(finals),
            "held": [e.number for e in episodes if e.status == "held"],
            "missing": [n for n in range(start, last + 1) if n not in numbers][:200],
        },
    }

    # --- 설정 오류 ---------------------------------------------------------------
    remaining: Counter[str] = Counter()
    first_fail: Counter[str] = Counter()
    final_fail: Counter[str] = Counter()
    rejected = 0
    rejected_kinds: Counter[str] = Counter()
    for e in finals:
        results = _results(e)
        for checker in SETTING_CHECKERS:
            r = results.get(checker) or {}
            for issue in r.get("issues") or []:
                remaining[f"{checker}/{issue.get('code')}/{issue.get('severity')}"] += 1
            if r.get("verdict") == "FAIL":
                final_fail[checker] += 1
            if _first_round_fail(e, checker):
                first_fail[checker] += 1
        for line in ((e.memory_delta or {}).get("applied") or {}).get("rejected") or []:
            rejected += 1
            rejected_kinds[str(line).split(" ", 1)[0].split("'", 1)[0]] += 1
    report["setting_errors"] = {
        "issues_after_fix": dict(remaining.most_common()),
        "episodes_fail_before_fix": dict(first_fail),
        "episodes_fail_after_fix": dict(final_fail),
        "memory_rejected": rejected,
        "memory_rejected_by_kind": dict(rejected_kinds.most_common()),
    }

    # --- Reference Pattern 적용 ------------------------------------------------------
    guide = load_guidance(session, novel)
    hooks = [e.hook_type or NO_CLIFFHANGER for e in finals]
    used = [h for h in hooks if h not in ("", NO_CLIFFHANGER)]
    achieved_dist = {k: v / len(used) for k, v in Counter(used).items()} if used else {}
    kinds = set(achieved_dist) | set(guide.cliffhanger_distribution)
    tv = (
        round(
            0.5
            * sum(
                abs(achieved_dist.get(k, 0.0) - guide.cliffhanger_distribution.get(k, 0.0))
                for k in kinds
            ),
            4,
        )
        if used
        else None
    )
    target_chars = novel.target_chars_per_episode or 0
    length_ratios = [e.char_count / target_chars for e in finals if target_chars]
    dialogue = [
        float((_results(e).get("Style") or {}).get("metrics", {}).get("dialogue_ratio"))
        for e in finals
        if (_results(e).get("Style") or {}).get("metrics", {}).get("dialogue_ratio")
        is not None
    ]
    events = Counter(
        str(((e.outline or {}).get("directives") or {}).get("event_kind") or "")
        for e in finals
    )
    rate = len(used) / len(finals) if finals else None
    planned_hooks = [
        bool(
            (((e.outline or {}).get("directives") or {}).get("hook") or {}).get("required")
        )
        for e in finals
    ]
    planned_rate = sum(planned_hooks) / len(planned_hooks) if planned_hooks else None
    report["pattern"] = {
        "source": guide.source,
        "cliffhanger_rate": {
            # planned는 계획(지시)한 비율, achieved는 원고 말미를 판정한 비율
            "planned": round(planned_rate, 4) if planned_rate is not None else None,
            "achieved": round(rate, 4) if rate is not None else None,
            "target": round(guide.cliffhanger_rate, 4),
            "diff": round(rate - guide.cliffhanger_rate, 4) if rate is not None else None,
        },
        "cliffhanger_distribution": {
            "achieved": {k: round(v, 4) for k, v in achieved_dist.items()},
            "target": {k: round(v, 4) for k, v in guide.cliffhanger_distribution.items()},
            # 두 분포가 얼마나 다른가 (0 = 같음, 1 = 전혀 겹치지 않음)
            "total_variation": tv,
        },
        "length": {
            "target_chars": target_chars,
            "mean_ratio": _mean(length_ratios),
            "within_30pct": sum(1 for r in length_ratios if 0.7 <= r <= 1.3),
        },
        "dialogue_ratio": {
            "achieved": _mean(dialogue),
            "target": round(guide.dialogue_ratio, 4),
        },
        "event_kinds": dict(events),
    }

    # --- 장기 기억 -----------------------------------------------------------------
    memory_codes: Counter[str] = Counter()
    for e in finals:
        for r in _results(e).values():
            for issue in r.get("issues") or []:
                if issue.get("code") in MEMORY_CODES:
                    memory_codes[str(issue.get("code"))] += 1
    searched = [
        e
        for e in finals
        if ((e.reference_usage or {}).get("memory_search") or {}).get("hits")
    ]
    methods = Counter(
        str(((e.reference_usage or {}).get("memory_search") or {}).get("method") or "none")
        for e in finals
    )
    report["memory"] = {
        "issues": dict(memory_codes.most_common()),
        "episodes_with_recalled_scenes": len(searched),
        "search_methods": dict(methods),
    }

    # --- 복선 회수 -----------------------------------------------------------------
    items = list(
        session.scalars(
            select(Foreshadowing).where(
                Foreshadowing.novel_id == novel.id,
                Foreshadowing.setup_episode >= start,
                Foreshadowing.setup_episode <= last,
            )
        )
    )
    resolved = [f for f in items if f.status == "RESOLVED"]
    on_time = [
        f
        for f in resolved
        if f.actual_payoff is not None
        and f.planned_payoff is not None
        and f.actual_payoff <= f.planned_payoff
    ]
    late = [
        f
        for f in resolved
        if f.actual_payoff is not None
        and f.planned_payoff is not None
        and f.actual_payoff > f.planned_payoff
    ]
    written_to = max((e.number for e in finals), default=0)
    overdue = [
        f
        for f in items
        if f.status in ("OPEN", "DEVELOPING")
        and f.planned_payoff is not None
        and f.planned_payoff < written_to
    ]
    spans = [
        f.actual_payoff - f.setup_episode for f in resolved if f.actual_payoff is not None
    ]
    report["foreshadowing"] = {
        "planted": len(items),
        "resolved": len(resolved),
        "resolved_on_time": len(on_time),
        "resolved_late": len(late),
        "open": sum(1 for f in items if f.status in ("OPEN", "DEVELOPING")),
        "overdue_open": [f.code for f in overdue],
        "abandoned": sum(1 for f in items if f.status == "ABANDONED"),
        "mean_span": _mean([float(s) for s in spans]),
        "target_span": list(guide.foreshadow_span),
    }
    return report
