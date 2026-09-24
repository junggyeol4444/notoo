"""Publisher Adapter (기획안 45번).

    공식 API가 있는 경우: 자동 업로드
    공식 API가 없는 경우: episode_title.txt / episode_body.txt / author_note.txt /
                          thumbnail.jpg 까지 자동 생성

어댑터 두 가지를 둔다.

  files    파일로 내보낸다. 결과 상태는 exported (사람이 올린다).
  webhook  사용자가 지정한 주소로 POST한다. 2xx면 published.

특정 연재 플랫폼 전용 어댑터는 넣지 않았다. 개인 작가가 쓸 수 있는 공개 업로드
API를 확인하지 못했기 때문이다. 공개 API가 있는 플랫폼이면 webhook 형식에 맞춘
중계 서버를 두거나, PublisherAdapter를 상속해 register_adapter()로 등록한다.
"""

from __future__ import annotations

import base64
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

import httpx

from novel_factory.config import Settings, get_settings
from novel_factory.database.models import Episode, Novel
from novel_factory.errors import NovelFactoryError
from novel_factory.publishing.info import PublishingInfo


class PublishError(NovelFactoryError):
    """대상이 받지 않았다. 다시 시도할 수 있다."""


@dataclass(slots=True)
class PublishOutcome:
    status: str  # exported / published
    location: str = ""
    external_id: str = ""


def episode_body(episode: Episode) -> str:
    """플랫폼에 붙여 넣을 본문. 문단 사이 빈 줄 하나, 끝에 줄바꿈."""
    return (episode.final_text or "").strip() + "\n"


class PublisherAdapter:
    type_name: ClassVar[str] = ""

    def __init__(self, name: str, config: dict[str, str], settings: Settings) -> None:
        self.name = name
        self.config = config
        self.settings = settings

    def publish_episode(
        self, novel: Novel, episode: Episode, info: PublishingInfo, thumbnail: Path | None
    ) -> PublishOutcome:
        raise NotImplementedError

    def publish_ebook(
        self, novel: Novel, volume: int, folder: Path, info: PublishingInfo
    ) -> PublishOutcome:
        raise NotImplementedError


class FileExportAdapter(PublisherAdapter):
    type_name = "files"

    def _root(self, novel: Novel) -> Path:
        return self.settings.novels_dir / novel.slug / "publish" / self.name

    def publish_episode(self, novel, episode, info, thumbnail):
        folder = self._root(novel) / f"episode_{episode.number:03d}"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "episode_title.txt").write_text(f"{episode.title}\n", encoding="utf-8")
        (folder / "episode_body.txt").write_text(episode_body(episode), encoding="utf-8")
        note = f"{info.author_note.strip()}\n" if info.author_note.strip() else ""
        (folder / "author_note.txt").write_text(note, encoding="utf-8")
        if thumbnail is not None and thumbnail.exists():
            shutil.copyfile(thumbnail, folder / "thumbnail.jpg")
        return PublishOutcome("exported", str(folder))

    def publish_ebook(self, novel, volume, folder, info):
        target = self._root(novel) / f"ebook_vol{volume:02d}"
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(folder, target)
        return PublishOutcome("exported", str(target))


class WebhookAdapter(PublisherAdapter):
    """사용자가 만든 API로 보낸다.

    회차:   POST {url}  JSON {"kind": "episode", "novel": {...}, "episode": {...},
                              "author_note": "...", "thumbnail_jpeg_base64": "..."}
    전자책: POST {url}  multipart  meta(JSON 문자열) + epub + cover + metadata
    응답이 2xx면 성공이다. 응답 JSON의 "id"를 external_id, "url"을 location으로 남긴다.
    """

    type_name = "webhook"

    def _client(self) -> httpx.Client:
        url = self.config.get("url", "")
        if not url:
            raise NovelFactoryError(f"대상 '{self.name}'에 url이 없습니다.")
        headers = {}
        if self.config.get("token"):
            headers["Authorization"] = f"Bearer {self.config['token']}"
        return httpx.Client(headers=headers, timeout=self.settings.publish_timeout_sec)

    @staticmethod
    def _outcome(response: httpx.Response) -> PublishOutcome:
        if response.status_code >= 400:
            raise PublishError(f"[{response.status_code}] {response.text[:300]}")
        try:
            data = response.json()
        except ValueError:
            data = {}
        if not isinstance(data, dict):
            data = {}
        return PublishOutcome(
            "published", str(data.get("url") or ""), str(data.get("id") or "")
        )

    def publish_episode(self, novel, episode, info, thumbnail):
        payload: dict[str, object] = {
            "kind": "episode",
            "novel": {
                "slug": novel.slug,
                "title": novel.title,
                "genre": novel.genre,
                "author": info.author,
            },
            "episode": {
                "number": episode.number,
                "title": episode.title,
                "body": episode_body(episode),
                "char_count": episode.char_count,
            },
            "author_note": info.author_note,
        }
        if thumbnail is not None and thumbnail.exists():
            payload["thumbnail_jpeg_base64"] = base64.b64encode(
                thumbnail.read_bytes()
            ).decode()
        try:
            with self._client() as client:
                return self._outcome(client.post(self.config["url"], json=payload))
        except httpx.HTTPError as exc:
            raise PublishError(str(exc)) from exc

    def publish_ebook(self, novel, volume, folder, info):
        import json

        meta = {
            "kind": "ebook",
            "novel": novel.slug,
            "title": novel.title,
            "volume": volume,
            **info.as_dict(),
        }
        files = {
            "epub": (
                "book.epub",
                (folder / "book.epub").read_bytes(),
                "application/epub+zip",
            ),
            "cover": ("cover.jpg", (folder / "cover.jpg").read_bytes(), "image/jpeg"),
            "metadata": (
                "metadata.xml",
                (folder / "metadata.xml").read_bytes(),
                "application/xml",
            ),
        }
        try:
            with self._client() as client:
                response = client.post(
                    self.config["url"],
                    data={"meta": json.dumps(meta, ensure_ascii=False)},
                    files=files,
                )
                return self._outcome(response)
        except httpx.HTTPError as exc:
            raise PublishError(str(exc)) from exc


_ADAPTER_TYPES: dict[str, type[PublisherAdapter]] = {
    FileExportAdapter.type_name: FileExportAdapter,
    WebhookAdapter.type_name: WebhookAdapter,
}


def register_adapter(cls: type[PublisherAdapter]) -> None:
    """플랫폼 전용 어댑터를 추가한다. NF_PUBLISH_TARGETS의 type에 type_name을 쓴다."""
    _ADAPTER_TYPES[cls.type_name] = cls


def target_configs(settings: Settings | None = None) -> dict[str, dict[str, str]]:
    cfg = settings or get_settings()
    out: dict[str, dict[str, str]] = {}
    for item in cfg.publish_targets:
        name = str(item.get("name") or "").strip()
        if name:
            out[name] = {k: str(v) for k, v in item.items()}
    return out


def get_adapter(name: str, settings: Settings | None = None) -> PublisherAdapter:
    cfg = settings or get_settings()
    config = target_configs(cfg).get(name)
    if config is None:
        raise NovelFactoryError(
            f"출판 대상 '{name}'이 설정에 없습니다 (NF_PUBLISH_TARGETS). "
            f"있는 대상: {', '.join(target_configs(cfg)) or '없음'}"
        )
    cls = _ADAPTER_TYPES.get(config.get("type", ""))
    if cls is None:
        raise NovelFactoryError(
            f"출판 대상 '{name}'의 type '{config.get('type')}'을 모릅니다. "
            f"가능한 type: {', '.join(_ADAPTER_TYPES)}"
        )
    return cls(name, config, cfg)
