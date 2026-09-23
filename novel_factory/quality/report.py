"""품질 검사 결과 (기획안 38번).

    Continuity: PASS
    Logic: PASS
    Similarity: FAIL
    Hook: PASS

이면 회차 전체가 아니라 문제 장면만 다시 쓴다. 그러려면 문제마다 '어느 장면인가'가
있어야 한다. 검사기는 원고에서 문제 구절(quote)을 뽑아 오고, locate_scene()이 그
구절이 들어 있는 장면을 찾는다.

심각도
  FAIL  자동 수정 대상. 확실한 위반만 여기 둔다.
  WARN  사람이 볼 것. 규칙 기반 추정이라 틀릴 수 있는 것은 전부 여기다.
  INFO  참고용 수치.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum


class Severity(StrEnum):
    INFO = "INFO"
    WARN = "WARN"
    FAIL = "FAIL"


VERDICT_PASS = "PASS"

_WS_RE = re.compile(r"\s+")


@dataclass(slots=True)
class Issue:
    checker: str
    code: str
    severity: Severity
    message: str
    quote: str = ""
    scene_index: int | None = None
    evidence: dict[str, object] = field(default_factory=dict)

    def as_dict(self) -> dict[str, object]:
        return {
            "checker": self.checker,
            "code": self.code,
            "severity": str(self.severity),
            "message": self.message,
            "quote": self.quote,
            "scene_index": self.scene_index,
            "evidence": self.evidence,
        }


@dataclass(slots=True)
class CheckResult:
    checker: str
    issues: list[Issue] = field(default_factory=list)
    skipped: str = ""  # 검사를 못 한 이유 (LLM 없음 등)
    metrics: dict[str, object] = field(default_factory=dict)

    @property
    def verdict(self) -> str:
        if self.skipped:
            return "SKIPPED"
        if any(i.severity is Severity.FAIL for i in self.issues):
            return "FAIL"
        if any(i.severity is Severity.WARN for i in self.issues):
            return "WARN"
        return VERDICT_PASS

    def as_dict(self) -> dict[str, object]:
        return {
            "checker": self.checker,
            "verdict": self.verdict,
            "skipped": self.skipped,
            "metrics": self.metrics,
            "issues": [i.as_dict() for i in self.issues],
        }


@dataclass(slots=True)
class QualityReport:
    results: dict[str, CheckResult] = field(default_factory=dict)
    round: int = 0

    def add(self, result: CheckResult) -> None:
        self.results[result.checker] = result

    @property
    def issues(self) -> list[Issue]:
        return [i for r in self.results.values() for i in r.issues]

    @property
    def verdict(self) -> str:
        verdicts = {r.verdict for r in self.results.values()}
        if "FAIL" in verdicts:
            return "FAIL"
        if "WARN" in verdicts:
            return "WARN"
        return VERDICT_PASS

    def failing_scenes(self) -> dict[int, list[Issue]]:
        """자동 수정할 장면과 그 이유. 장면을 못 찾은 FAIL은 여기 없다."""
        out: dict[int, list[Issue]] = {}
        for issue in self.issues:
            if issue.severity is Severity.FAIL and issue.scene_index is not None:
                out.setdefault(issue.scene_index, []).append(issue)
        return out

    def summary(self) -> str:
        """기획안 38번 표기."""
        return "\n".join(f"{name}: {r.verdict}" for name, r in self.results.items())

    def as_dict(self) -> dict[str, object]:
        return {
            "verdict": self.verdict,
            "round": self.round,
            "summary": self.summary(),
            "results": {k: v.as_dict() for k, v in self.results.items()},
        }


def _squash(text: str) -> str:
    return _WS_RE.sub("", text)


def locate_scene(quote: str, scene_texts: list[str]) -> int | None:
    """구절이 들어 있는 장면 번호. 공백 차이는 무시한다."""
    needle = _squash(quote)
    if not needle:
        return None
    for i, text in enumerate(scene_texts):
        if needle in _squash(text):
            return i
    return None


def quote_exists(quote: str, text: str, *, min_chars: int = 4) -> bool:
    """LLM이 인용했다는 구절이 원고에 실제로 있는가.

    LLM 검사기는 원고에 없는 문장을 지어내서 지적하기도 한다. 인용이 원고에
    없으면 그 지적은 버린다. 너무 짧은 인용(몇 글자)은 어디에나 있어서 근거가 못 된다.
    """
    needle = _squash(quote)
    return len(needle) >= min_chars and needle in _squash(text)
