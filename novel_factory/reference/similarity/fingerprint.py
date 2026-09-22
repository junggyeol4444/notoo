"""참고작 지문과 유사도 검사 (기획안 20·36번).

참고작 원문을 보관하지 않고 유사도를 재려면 지문이 필요하다.
여기서 쓰는 지문은 문자 n-gram을 64비트로 해싱한 집합이다.

  - 해시에서 원문을 복원할 수 없다. 저작권 측면에서 원문 보관보다 안전하다.
  - 띄어쓰기와 문장부호를 지우고 자르기 때문에 공백만 바꾼 베끼기도 잡는다.
  - 뜻이 같고 표현이 다른 문장은 못 잡는다. 그건 의미 유사도의 영역이고
    임베딩이 필요하다. 이 모듈은 '표현 유사도'만 담당한다.

판정은 두 값을 함께 본다.
  jaccard    두 글이 서로 얼마나 겹치는가
  containment 새 원고가 참고작 안에 얼마나 들어 있는가
짧은 새 원고가 긴 참고작의 한 대목을 통째로 베낀 경우, jaccard는 낮게
나오지만 containment는 1에 가깝다. 표절 판정에는 containment가 핵심이다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from novel_factory.text.tokens import jaccard, shingle_hashes

#: 지문에 쓸 문자 n-gram 길이. 한국어 8글자는 대략 2~3어절이다.
SHINGLE_SIZE = 8
#: 회차 하나당 보관할 최대 해시 수
EPISODE_FINGERPRINT_LIMIT = 2048

#: containment가 이 값을 넘으면 재작성 대상이다.
CONTAINMENT_FAIL = 0.35
#: 경고만 띄우는 구간
CONTAINMENT_WARN = 0.20


@dataclass(slots=True)
class FingerprintIndex:
    """참고작들의 회차 지문 모음."""

    # (reference_id, episode_seq) -> 해시 집합
    entries: dict[tuple[str, int], set[int]] = field(default_factory=dict)

    def add(self, reference_id: str, episode_seq: int, hashes: set[int]) -> None:
        if hashes:
            self.entries[(reference_id, episode_seq)] = hashes

    def add_from_metrics(self, reference_id: str, metrics: list) -> int:
        """AnalysisResult.metrics에서 지문을 옮겨 담는다."""
        added = 0
        for m in metrics:
            fp = getattr(m, "fingerprint", None)
            if fp:
                self.add(reference_id, m.seq, set(fp))
                added += 1
        return added

    def __len__(self) -> int:
        return len(self.entries)


@dataclass(slots=True)
class SimilarityHit:
    reference_id: str
    episode_seq: int
    jaccard: float
    containment: float

    @property
    def verdict(self) -> str:
        if self.containment >= CONTAINMENT_FAIL:
            return "FAIL"
        if self.containment >= CONTAINMENT_WARN:
            return "WARN"
        return "PASS"

    def as_dict(self) -> dict[str, object]:
        return {
            "reference_id": self.reference_id,
            "episode_seq": self.episode_seq,
            "jaccard": round(self.jaccard, 4),
            "containment": round(self.containment, 4),
            "verdict": self.verdict,
        }


@dataclass(slots=True)
class SimilarityReport:
    """기획안 40번 similarity_report.json의 내용."""

    max_containment: float
    max_jaccard: float
    verdict: str
    hits: list[SimilarityHit] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.verdict != "FAIL"

    def as_dict(self) -> dict[str, object]:
        return {
            "verdict": self.verdict,
            "max_containment": round(self.max_containment, 4),
            "max_jaccard": round(self.max_jaccard, 4),
            "hits": [h.as_dict() for h in self.hits],
        }


def containment(candidate: set[int], reference: set[int]) -> float:
    """새 원고가 참고작 안에 얼마나 들어 있는가 (0~1)."""
    if not candidate:
        return 0.0
    return len(candidate & reference) / len(candidate)


def check_text(
    text: str,
    index: FingerprintIndex,
    *,
    shingle_size: int = SHINGLE_SIZE,
    top_k: int = 5,
) -> SimilarityReport:
    """새로 쓴 원고를 지문 색인과 대조한다."""
    candidate = shingle_hashes(text, shingle_size)
    if not candidate or not index.entries:
        return SimilarityReport(0.0, 0.0, "PASS", [])

    hits: list[SimilarityHit] = []
    for (ref_id, seq), reference in index.entries.items():
        cont = containment(candidate, reference)
        if cont <= 0.0:
            continue
        hits.append(
            SimilarityHit(
                reference_id=ref_id,
                episode_seq=seq,
                jaccard=jaccard(candidate, reference),
                containment=cont,
            )
        )

    hits.sort(key=lambda h: -h.containment)
    top = hits[:top_k]
    max_cont = top[0].containment if top else 0.0
    max_jac = max((h.jaccard for h in top), default=0.0)

    verdict = "PASS"
    if max_cont >= CONTAINMENT_FAIL:
        verdict = "FAIL"
    elif max_cont >= CONTAINMENT_WARN:
        verdict = "WARN"

    return SimilarityReport(max_cont, max_jac, verdict, top)
