"""전자책 패키지 (기획안 44·46번).

    완결 또는 지정 분량마다:
      book.epub / cover.jpg / metadata.xml
    전자책 자료:
      EPUB / Cover / Metadata / 가격정보 / 작품설명 / 키워드 / 카테고리

권 나누기 (PublishingInfo.volume_size)
  0   완결(novel.status == "completed") 뒤에 전체를 한 권으로 만든다.
  N   1화부터 N화씩 끊어 권을 만든다. 연재 중에는 N화가 다 찬 권만 만들고,
      완결이면 마지막 남은 회차도 한 권으로 만든다.
  확정(final)된 회차만 넣는다. 1화부터 이어진 구간만 쓰고, 중간에 빠진 회차가
  있으면 그 앞까지만 쓴다.

작품설명·키워드·카테고리가 비어 있으면 LLM이 작품 기준과 앞 회차 줄거리로 쓴다.
LLM이 없으면 로그라인·장르로 채운다. 한 번 채운 값은 저장해서 다음 권에도 쓴다.
사람이 고친 값은 덮어쓰지 않는다.

같은 권을 다시 만들지 않는다 (force=True면 다시 만든다).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from xml.sax.saxutils import escape

from sqlalchemy import select
from sqlalchemy.orm import Session

from novel_factory.config import Settings, get_settings
from novel_factory.database.models import Episode, Novel
from novel_factory.errors import NovelFactoryError
from novel_factory.generation.prompts import BLURB_SYSTEM, build_blurb_prompt
from novel_factory.generation.schemas import BlurbOut
from novel_factory.generation.structured import StructuredOutputError, request_structured
from novel_factory.llm.base import LLMProvider, Message
from novel_factory.publishing.cover import cover_dir, make_cover
from novel_factory.publishing.epub import BookMeta, Chapter, book_identifier, write_epub
from novel_factory.publishing.info import PublishingInfo, get_info, save_info

#: 작품설명을 쓸 때 LLM에게 보여 줄 앞 회차 줄거리 수
BLURB_SUMMARIES = 10


def contiguous_final(session: Session, novel: Novel) -> list[Episode]:
    episodes = session.scalars(
        select(Episode)
        .where(Episode.novel_id == novel.id, Episode.status == "final")
        .order_by(Episode.number)
    ).all()
    out: list[Episode] = []
    for expected, ep in enumerate(episodes, start=1):
        if ep.number != expected:
            break
        out.append(ep)
    return out


def plan_volumes(
    last_episode: int, volume_size: int, complete: bool
) -> list[tuple[int, int]]:
    """[(시작 회차, 끝 회차)]. 권 번호는 목록 순서 + 1."""
    if last_episode <= 0:
        return []
    if volume_size <= 0:
        return [(1, last_episode)] if complete else []
    volumes = []
    start = 1
    while start + volume_size - 1 <= last_episode:
        volumes.append((start, start + volume_size - 1))
        start += volume_size
    if complete and start <= last_episode:
        volumes.append((start, last_episode))
    return volumes


def ensure_blurb(
    session: Session,
    novel: Novel,
    provider: LLMProvider | None,
    settings: Settings,
) -> tuple[PublishingInfo, list[str]]:
    info = get_info(novel)
    warnings: list[str] = []
    if info.description and info.keywords and info.categories:
        return info, warnings

    summaries = [
        f"{e.number}화: {e.summary}"
        for e in contiguous_final(session, novel)[:BLURB_SUMMARIES]
        if e.summary
    ]
    blurb: BlurbOut | None = None
    if provider is not None and provider.available:
        try:
            blurb, _ = request_structured(
                provider,
                [
                    Message("system", BLURB_SYSTEM),
                    Message(
                        "user",
                        build_blurb_prompt(
                            title=novel.title,
                            genre=novel.genre,
                            logline=novel.logline,
                            premise=novel.premise,
                            summaries=summaries,
                        ),
                    ),
                ],
                BlurbOut,
                retries=settings.llm_structured_retries,
                temperature=settings.llm_structured_temperature,
                max_tokens=1200,
            )
        except StructuredOutputError:
            warnings.append("작품설명을 LLM에게서 받지 못해 로그라인으로 채웠습니다.")

    if not info.description:
        info.description = (
            blurb.description
            if blurb
            else " ".join(x for x in (novel.logline, novel.premise) if x)
        )
    if not info.keywords:
        info.keywords = [
            k.strip() for k in (blurb.keywords if blurb else []) if k.strip()
        ] or [x for x in (novel.genre,) if x]
    if not info.categories:
        info.categories = [
            c.strip() for c in (blurb.categories if blurb else []) if c.strip()
        ] or [x for x in (novel.genre or "웹소설",) if x]
    save_info(novel, info)
    return info, warnings


def metadata_xml(
    *,
    novel: Novel,
    info: PublishingInfo,
    volume: int,
    total_volumes: int | None,
    start: int,
    end: int,
    identifier: str,
    title: str,
) -> str:
    def el(tag: str, value: object) -> str:
        return f"  <{tag}>{escape(str(value))}</{tag}>"

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<ebook xmlns="urn:novel-factory:ebook:1">',
        el("title", title),
        el("series", novel.title),
        el("volume", volume),
        f'  <episodes start="{start}" end="{end}" count="{end - start + 1}"/>',
        el("identifier", identifier),
        el("language", info.language),
        el("genre", novel.genre),
    ]
    if info.author:
        lines.append(el("author", info.author))
    if info.publisher:
        lines.append(el("publisher", info.publisher))
    lines.append(el("description", info.description))
    lines.append("  <keywords>")
    lines += [f"    <keyword>{escape(k)}</keyword>" for k in info.keywords]
    lines.append("  </keywords>")
    lines.append("  <categories>")
    lines += [f"    <category>{escape(c)}</category>" for c in info.categories]
    lines.append("  </categories>")
    if info.price > 0:
        lines.append(f'  <price currency="{escape(info.currency)}">{info.price}</price>')
    lines.append(f"  <status>{'completed' if total_volumes else 'serializing'}</status>")
    lines.append('  <files epub="book.epub" cover="cover.jpg"/>')
    lines.append(el("generated", datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")))
    lines.append("</ebook>")
    return "\n".join(lines) + "\n"


@dataclass(slots=True)
class VolumeResult:
    volume: int
    start: int
    end: int
    folder: Path
    built: bool  # False면 이미 있어서 건너뜀

    def as_dict(self) -> dict[str, object]:
        return {
            "volume": self.volume,
            "start": self.start,
            "end": self.end,
            "folder": str(self.folder),
            "built": self.built,
        }


@dataclass(slots=True)
class EbookReport:
    volumes: list[VolumeResult] = field(default_factory=list)
    info: dict[str, object] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "volumes": [v.as_dict() for v in self.volumes],
            "info": self.info,
            "warnings": self.warnings,
        }


def ebook_dir(novel: Novel, settings: Settings | None = None) -> Path:
    cfg = settings or get_settings()
    return cfg.novels_dir / novel.slug / "ebook"


def build_ebooks(
    session: Session,
    novel: Novel,
    provider: LLMProvider | None = None,
    *,
    settings: Settings | None = None,
    force: bool = False,
    episode_range: tuple[int, int] | None = None,
) -> EbookReport:
    """권 계획대로 전자책을 만든다. episode_range를 주면 그 구간만 한 권으로 만든다."""
    cfg = settings or get_settings()
    report = EbookReport()
    finals = contiguous_final(session, novel)
    by_number = {e.number: e for e in finals}
    last = finals[-1].number if finals else 0
    info = get_info(novel)
    complete = novel.status == "completed"

    if episode_range is not None:
        start, end = episode_range
        missing = [n for n in range(start, end + 1) if n not in by_number]
        if start < 1 or end < start or missing:
            raise NovelFactoryError(
                f"{start}~{end}화 중 확정되지 않은 회차가 있습니다: {missing[:10]}"
            )
        volumes = [(start, end)]
        numbered = [(0, start, end)]  # 0권 = 따로 지정한 구간
    else:
        volumes = plan_volumes(last, info.volume_size, complete)
        numbered = [(i, s, e) for i, (s, e) in enumerate(volumes, start=1)]
    if not volumes:
        report.warnings.append(
            "만들 권이 없습니다. volume_size가 0이면 완결 뒤에 만들고, N이면 N화가 다 차야 "
            "한 권이 됩니다."
        )
        return report

    info, warnings = ensure_blurb(session, novel, provider, cfg)
    report.warnings.extend(warnings)
    report.info = info.as_dict()

    cover_path = cover_dir(novel, cfg) / "cover.jpg"
    if not cover_path.exists():
        result = make_cover(session, novel, provider, settings=cfg)
        report.warnings.extend(result.warnings)
    cover = cover_path.read_bytes()

    multi = len(volumes) > 1 or info.volume_size > 0
    for vol, start, end in numbered:
        name = f"vol{vol:02d}" if vol else f"ep{start:04d}-{end:04d}"
        folder = ebook_dir(novel, cfg) / name
        epub_path = folder / "book.epub"
        if epub_path.exists() and not force:
            report.volumes.append(VolumeResult(vol, start, end, folder, built=False))
            continue
        title = f"{novel.title} {vol}권" if (multi and vol) else novel.title
        if not vol:
            title = f"{novel.title} ({start}~{end}화)"
        identifier = book_identifier(novel.slug, name)
        meta = BookMeta(
            title=title,
            identifier=identifier,
            language=info.language,
            author=info.author,
            publisher=info.publisher,
            description=info.description,
            subjects=[*info.categories, *info.keywords],
        )
        chapters = [
            Chapter(n, by_number[n].title, by_number[n].final_text or "")
            for n in range(start, end + 1)
        ]
        write_epub(epub_path, meta, chapters, cover_jpeg=cover)
        (folder / "cover.jpg").write_bytes(cover)
        (folder / "metadata.xml").write_text(
            metadata_xml(
                novel=novel,
                info=info,
                volume=vol,
                total_volumes=len(volumes) if complete else None,
                start=start,
                end=end,
                identifier=identifier,
                title=title,
            ),
            encoding="utf-8",
        )
        (folder / "metadata.json").write_text(
            json.dumps(
                {
                    "title": title,
                    "volume": vol,
                    "start": start,
                    "end": end,
                    "identifier": identifier,
                    **info.as_dict(),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        report.volumes.append(VolumeResult(vol, start, end, folder, built=True))
    return report
