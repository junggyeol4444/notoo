"""표지 · 전자책 · 출판 API (기획안 43~46번).

GET  /novels/{slug}/publishing              출판 정보 (작가명, 가격, 설명, 키워드 ...)
PUT  /novels/{slug}/publishing              고치기 (publishing_mode 포함)
POST /novels/{slug}/cover                   표지 만들기
GET  /novels/{slug}/cover.jpg               표지
GET  /novels/{slug}/thumbnail.jpg           썸네일
POST /novels/{slug}/ebooks                  전자책 만들기 (권 계획대로, 또는 start~end)
GET  /novels/{slug}/ebooks                  만든 전자책 목록
GET  /novels/{slug}/ebooks/{name}/{file}    book.epub / cover.jpg / metadata.xml 받기
POST /novels/{slug}/publications            출판 대기열에 넣기
GET  /novels/{slug}/publications            출판 기록
POST /publications/process                  대기열 처리
GET  /publish-targets                       설정된 출판 대상 (토큰은 빼고)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from novel_factory.app.deps import get_db, get_llm, get_novel
from novel_factory.database.models import Novel, Publication
from novel_factory.database.repositories import NovelRepository
from novel_factory.llm.base import LLMProvider
from novel_factory.publishing.adapters import target_configs
from novel_factory.publishing.cover import cover_dir, make_cover
from novel_factory.publishing.ebook import build_ebooks, ebook_dir
from novel_factory.publishing.info import get_info, update_info
from novel_factory.publishing.queue import enqueue_ebook, enqueue_episode, process_queue

router = APIRouter(tags=["publishing"])

EBOOK_FILES = {
    "book.epub": "application/epub+zip",
    "cover.jpg": "image/jpeg",
    "metadata.xml": "application/xml",
    "metadata.json": "application/json",
}


class PublishingIn(BaseModel):
    author: str | None = None
    publisher: str | None = None
    language: str | None = None
    price: int | None = Field(default=None, ge=0)
    currency: str | None = None
    description: str | None = None
    keywords: list[str] | None = None
    categories: list[str] | None = None
    author_note: str | None = None
    volume_size: int | None = Field(default=None, ge=0)
    targets: list[str] | None = None
    publishing_mode: Literal["manual", "automatic"] | None = None


class PublicationIn(BaseModel):
    kind: Literal["episode", "ebook"] = "episode"
    episode_number: int | None = None
    volume: int | None = None
    targets: list[str] | None = None
    republish: bool = False


def _publication(p: Publication) -> dict[str, object]:
    return {
        "id": p.id,
        "kind": p.kind,
        "episode_number": p.episode_number,
        "volume": p.volume,
        "target": p.target,
        "status": p.status,
        "attempts": p.attempts,
        "error": p.error,
        "location": p.location,
        "external_id": p.external_id,
        "published_at": p.published_at.isoformat() if p.published_at else None,
        "created_at": p.created_at.isoformat() if p.created_at else None,
    }


@router.get("/novels/{slug}/publishing")
def get_publishing(novel: Novel = Depends(get_novel)) -> dict[str, object]:
    return {**get_info(novel).as_dict(), "publishing_mode": novel.publishing_mode}


@router.put("/novels/{slug}/publishing")
def put_publishing(
    body: PublishingIn, novel: Novel = Depends(get_novel), db: Session = Depends(get_db)
) -> dict[str, object]:
    changes = body.model_dump(exclude_none=True)
    mode = changes.pop("publishing_mode", None)
    unknown = [t for t in changes.get("targets", []) if t not in target_configs()]
    if unknown:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"설정에 없는 출판 대상: {unknown}. 있는 대상: {list(target_configs())}",
        )
    if mode:
        novel.publishing_mode = mode
    info = update_info(novel, **changes)
    db.flush()
    return {**info.as_dict(), "publishing_mode": novel.publishing_mode}


@router.post("/novels/{slug}/cover")
def create_cover(
    use_image: bool = True,
    use_llm: bool = True,
    novel: Novel = Depends(get_novel),
    db: Session = Depends(get_db),
    llm: LLMProvider = Depends(get_llm),
) -> dict[str, object]:
    provider = llm if use_llm and llm.available else None
    return make_cover(db, novel, provider, use_image=use_image).as_dict()


def _file(path: Path, media_type: str) -> FileResponse:
    if not path.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"{path.name}이(가) 아직 없습니다.")
    return FileResponse(path, media_type=media_type, filename=path.name)


@router.get("/novels/{slug}/cover.jpg")
def get_cover(novel: Novel = Depends(get_novel)) -> FileResponse:
    return _file(cover_dir(novel) / "cover.jpg", "image/jpeg")


@router.get("/novels/{slug}/thumbnail.jpg")
def get_thumbnail(novel: Novel = Depends(get_novel)) -> FileResponse:
    return _file(cover_dir(novel) / "thumbnail.jpg", "image/jpeg")


@router.post("/novels/{slug}/ebooks")
def create_ebooks(
    force: bool = False,
    start: int | None = None,
    end: int | None = None,
    novel: Novel = Depends(get_novel),
    db: Session = Depends(get_db),
    llm: LLMProvider = Depends(get_llm),
) -> dict[str, object]:
    if (start is None) != (end is None):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "start와 end를 같이 주세요."
        )
    provider = llm if llm.available else None
    episode_range = (start, end) if start is not None and end is not None else None
    return build_ebooks(
        db, novel, provider, force=force, episode_range=episode_range
    ).as_dict()


@router.get("/novels/{slug}/ebooks")
def list_ebooks(novel: Novel = Depends(get_novel)) -> list[dict[str, object]]:
    root = ebook_dir(novel)
    out = []
    for folder in sorted(root.glob("*/")) if root.exists() else []:
        meta_path = folder / "metadata.json"
        meta = (
            json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
        )
        out.append(
            {
                "name": folder.name,
                "title": meta.get("title"),
                "volume": meta.get("volume"),
                "start": meta.get("start"),
                "end": meta.get("end"),
                "files": [f for f in EBOOK_FILES if (folder / f).exists()],
            }
        )
    return out


@router.get("/novels/{slug}/ebooks/{name}/{file_name}")
def get_ebook_file(
    name: str, file_name: str, novel: Novel = Depends(get_novel)
) -> FileResponse:
    if file_name not in EBOOK_FILES or "/" in name or name.startswith("."):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "그런 파일은 없습니다.")
    return _file(ebook_dir(novel) / name / file_name, EBOOK_FILES[file_name])


@router.post("/novels/{slug}/publications", status_code=status.HTTP_201_CREATED)
def create_publications(
    body: PublicationIn, novel: Novel = Depends(get_novel), db: Session = Depends(get_db)
) -> list[dict[str, object]]:
    if body.kind == "episode":
        if body.episode_number is None:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY, "episode_number가 필요합니다."
            )
        rows = enqueue_episode(
            db, novel, body.episode_number, targets=body.targets, republish=body.republish
        )
    else:
        if body.volume is None:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY, "volume이 필요합니다."
            )
        rows = enqueue_ebook(
            db, novel, body.volume, targets=body.targets, republish=body.republish
        )
    return [_publication(r) for r in rows]


@router.get("/novels/{slug}/publications")
def list_publications(
    status_filter: str | None = None,
    novel: Novel = Depends(get_novel),
    db: Session = Depends(get_db),
) -> list[dict[str, object]]:
    stmt = select(Publication).where(Publication.novel_id == novel.id)
    if status_filter:
        stmt = stmt.where(Publication.status == status_filter)
    return [_publication(p) for p in db.scalars(stmt.order_by(Publication.id)).all()]


@router.post("/publications/process")
def process_publications(
    novel: str | None = None, limit: int | None = None, db: Session = Depends(get_db)
) -> dict[str, object]:
    novel_id = NovelRepository(db).require_by_slug(novel).id if novel else None
    return process_queue(db, novel_id=novel_id, limit=limit).as_dict()


@router.get("/publish-targets")
def publish_targets() -> list[dict[str, str]]:
    return [
        {k: v for k, v in cfg.items() if k != "token"} for cfg in target_configs().values()
    ]
