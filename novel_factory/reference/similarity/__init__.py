"""유사성 방지 (기획안 20·36번)."""

from novel_factory.reference.similarity.fingerprint import (
    CONTAINMENT_FAIL,
    CONTAINMENT_WARN,
    SHINGLE_SIZE,
    WHOLE_WORK,
    FingerprintIndex,
    SimilarityHit,
    SimilarityReport,
    check_text,
    containment,
)

__all__ = [
    "CONTAINMENT_FAIL",
    "CONTAINMENT_WARN",
    "SHINGLE_SIZE",
    "WHOLE_WORK",
    "FingerprintIndex",
    "SimilarityHit",
    "SimilarityReport",
    "check_text",
    "containment",
]
