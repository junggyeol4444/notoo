"""작품의 출판 정보 (기획안 46번).

    작가명 / 가격 / 작품설명 / 키워드 / 카테고리 / 출판사 / 언어

novel.extra["publishing"]에 둔다. 사람이 API로 채우거나, 비어 있으면 전자책
패키지를 만들 때 generate_blurb()가 채운다(publishing/ebook.py).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields

from novel_factory.database.models import Novel


@dataclass(slots=True)
class PublishingInfo:
    author: str = ""
    publisher: str = ""
    language: str = "ko"
    # 가격은 정수 금액과 통화. 0이면 가격 정보를 내보내지 않는다.
    price: int = 0
    currency: str = "KRW"
    description: str = ""
    keywords: list[str] = field(default_factory=list)
    categories: list[str] = field(default_factory=list)
    # 연재 플랫폼에 올릴 때 회차마다 붙일 작가의 말. 비우면 빈 파일이 나간다.
    author_note: str = ""
    # 전자책 한 권에 넣을 회차 수. 0이면 완결 뒤 전체를 한 권으로 만든다.
    volume_size: int = 0
    # 이 작품을 내보낼 대상 이름 (NF_PUBLISH_TARGETS의 name)
    targets: list[str] = field(default_factory=lambda: ["files"])

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def get_info(novel: Novel) -> PublishingInfo:
    raw = (novel.extra or {}).get("publishing") or {}
    known = {f.name for f in fields(PublishingInfo)}
    return PublishingInfo(**{k: v for k, v in raw.items() if k in known})


def save_info(novel: Novel, info: PublishingInfo) -> None:
    # JSON 컬럼은 안쪽 변경을 모른다. 새 dict로 갈아 끼운다.
    novel.extra = {**(novel.extra or {}), "publishing": info.as_dict()}


def update_info(novel: Novel, **changes: object) -> PublishingInfo:
    info = get_info(novel)
    for key, value in changes.items():
        if value is not None and hasattr(info, key):
            setattr(info, key, value)
    save_info(novel, info)
    return info
