"""참고소설 수집 (기획안 5·49번).

업로드된 파일을 받아 저장하고, 포맷을 확인하고, 본문을 뽑아 둔다.
분석은 여기서 하지 않는다. 업로드와 분석을 나눠야 무거운 분석을
큐(Celery)로 넘길 수 있다.
"""

from __future__ import annotations

import hashlib
import shutil
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from novel_factory.config import Settings, get_settings
from novel_factory.errors import UnsupportedFormatError
from novel_factory.reference.parser import (
    SUPPORTED_EXTENSIONS,
    ParsedDocument,
    get_parser,
    parse_file,
)
from novel_factory.text.normalize import normalize_text
from novel_factory.text.tokens import syllables


@dataclass(slots=True)
class ImportedReference:
    """저장된 참고소설 하나."""

    reference_id: str
    original_name: str
    stored_path: Path
    extracted_path: Path
    source_format: str
    encoding: str
    content_hash: str
    char_count: int
    title: str | None = None
    author: str | None = None
    native_chapter_count: int = 0
    imported_at: str = field(
        default_factory=lambda: datetime.now(UTC).isoformat(timespec="seconds")
    )
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "reference_id": self.reference_id,
            "original_name": self.original_name,
            "stored_path": str(self.stored_path),
            "extracted_path": str(self.extracted_path),
            "source_format": self.source_format,
            "encoding": self.encoding,
            "content_hash": self.content_hash,
            "char_count": self.char_count,
            "title": self.title,
            "author": self.author,
            "native_chapter_count": self.native_chapter_count,
            "imported_at": self.imported_at,
            "warnings": self.warnings,
        }

    def read_text(self) -> str:
        return self.extracted_path.read_text(encoding="utf-8")


def check_supported(filename: str) -> str:
    """확장자를 확인하고 포맷 이름을 돌려준다."""
    suffix = Path(filename).suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise UnsupportedFormatError(
            f"지원하지 않는 형식입니다: {suffix or '(확장자 없음)'} "
            f"(지원: {', '.join(sorted(SUPPORTED_EXTENSIONS))})"
        )
    return get_parser(Path(filename)).format_name


def _content_hash(path: Path) -> str:
    digest = hashlib.blake2b(digest_size=16)
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def import_file(
    source: Path | str,
    *,
    reference_id: str,
    original_name: str | None = None,
    settings: Settings | None = None,
) -> ImportedReference:
    """파일을 저장소로 들여오고 본문을 뽑아 둔다."""
    cfg = settings or get_settings()
    cfg.ensure_dirs()

    src = Path(source)
    name = original_name or src.name
    check_supported(name)

    suffix = Path(name).suffix.lower()
    stored = cfg.uploads_dir / f"{reference_id}{suffix}"
    if src.resolve() != stored.resolve():
        shutil.copyfile(src, stored)

    document: ParsedDocument = parse_file(stored)
    text = normalize_text(document.text)

    extracted = cfg.extracted_dir / f"{reference_id}.txt"
    extracted.write_text(text, encoding="utf-8")

    return ImportedReference(
        reference_id=reference_id,
        original_name=name,
        stored_path=stored,
        extracted_path=extracted,
        source_format=document.source_format,
        encoding=document.encoding,
        content_hash=_content_hash(stored),
        char_count=syllables(text),
        title=document.title,
        author=document.author,
        native_chapter_count=len(document.raw_chapters),
        warnings=list(document.warnings),
    )


def import_bytes(
    data: bytes,
    filename: str,
    *,
    reference_id: str,
    settings: Settings | None = None,
) -> ImportedReference:
    """업로드 바이트를 그대로 받아 들여온다 (FastAPI UploadFile 용)."""
    cfg = settings or get_settings()
    cfg.ensure_dirs()
    check_supported(filename)

    suffix = Path(filename).suffix.lower()
    stored = cfg.uploads_dir / f"{reference_id}{suffix}"
    stored.write_bytes(data)
    return import_file(
        stored, reference_id=reference_id, original_name=filename, settings=cfg
    )


__all__ = [
    "SUPPORTED_EXTENSIONS",
    "ImportedReference",
    "check_supported",
    "import_bytes",
    "import_file",
]
