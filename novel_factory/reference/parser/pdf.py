"""PDF 파서.

pypdf가 있으면 쓰고, 없으면 무엇을 설치해야 하는지 알려주고 멈춘다.
PDF는 원래 레이아웃 포맷이라 소설 본문 추출 품질이 다른 포맷보다 나쁘다.
줄바꿈이 문장 중간에 들어가는 경우가 많아서 그 부분을 보정한다.
"""

from __future__ import annotations

import re
from pathlib import Path

from novel_factory.errors import MissingDependencyError, ParseError
from novel_factory.reference.parser.base import BaseParser, ParsedDocument, RawChapter

# 페이지 하단 쪽번호만 있는 줄
_PAGE_NUMBER_RE = re.compile(r"^\s*[-–—\[(]?\s*\d{1,4}\s*[-–—\])]?\s*$")
# 한글 문장 중간에 들어간 줄바꿈: 종결 부호 없이 끝난 줄 + 한글로 시작하는 다음 줄
_SOFT_WRAP_RE = re.compile(r"([가-힣,、])\n([가-힣])")
# 영문 하이픈 줄바꿈
_HYPHEN_WRAP_RE = re.compile(r"(\w)-\n(\w)")


class PdfParser(BaseParser):
    extensions = (".pdf",)
    format_name = "pdf"

    def parse(self, path: Path) -> ParsedDocument:
        try:
            from pypdf import PdfReader
        except ImportError as exc:  # pragma: no cover - 환경 의존
            raise MissingDependencyError(
                "PDF를 읽으려면 pypdf가 필요합니다: pip install 'novel-factory[pdf]' "
                "또는 pip install pypdf"
            ) from exc

        warnings: list[str] = []
        try:
            reader = PdfReader(str(path))
            if reader.is_encrypted:
                # 빈 암호로 열리는 PDF가 많다.
                try:
                    reader.decrypt("")
                except Exception as exc:
                    raise ParseError(f"암호가 걸린 PDF: {path.name}") from exc
            pages = [(page.extract_text() or "") for page in reader.pages]
            meta = reader.metadata or {}
        except ParseError:
            raise
        except Exception as exc:
            raise ParseError(f"PDF 파싱 실패: {path.name} ({exc})") from exc

        cleaned = [self._clean_page(p) for p in pages]
        chapters = [
            RawChapter(title=f"p.{i + 1}", text=t, order=i)
            for i, t in enumerate(cleaned)
            if t.strip()
        ]
        text = "\n\n".join(c.text for c in chapters)
        if not text.strip():
            raise ParseError(
                f"PDF에서 텍스트를 뽑지 못함(스캔 이미지 PDF일 수 있음): {path.name}"
            )
        if len(text) < 200 * len(pages):
            warnings.append(
                "페이지당 추출 글자 수가 매우 적습니다. 스캔 PDF라면 OCR이 필요합니다."
            )

        return ParsedDocument(
            text=text,
            source_format=self.format_name,
            encoding="utf-8",
            title=(meta.get("/Title") or "").strip() or path.stem,
            author=(meta.get("/Author") or "").strip() or None,
            # 페이지는 회차가 아니다. 회차 분리는 structure 모듈에 맡긴다.
            raw_chapters=[],
            warnings=warnings,
        )

    @staticmethod
    def _clean_page(text: str) -> str:
        lines = [ln for ln in text.split("\n") if not _PAGE_NUMBER_RE.match(ln)]
        joined = "\n".join(lines)
        joined = _HYPHEN_WRAP_RE.sub(r"\1\2", joined)
        joined = _SOFT_WRAP_RE.sub(r"\1\2", joined)
        return joined.strip()
