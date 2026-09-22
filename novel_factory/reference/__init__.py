"""참고소설 분석 (기획안 5~20번).

    from novel_factory.reference import analyze_file
    result = analyze_file("novel.epub", reference_id="REF001")
    print(result.profile.report())
"""

from novel_factory.reference.pipeline import (
    AnalysisResult,
    analyze_document,
    analyze_file,
    analyze_text,
)

__all__ = ["AnalysisResult", "analyze_file", "analyze_document", "analyze_text"]
