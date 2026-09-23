"""회차 저장 구조 (기획안 40번).

    episode_001/
      outline.json
      scenes.json
      draft.md
      final.md
      summary.json
      memory_delta.json
      reference_usage.json
      similarity_report.json

DB가 원본이고 이 폴더는 사본이다. 사람이 원고를 열어 보거나, DB 없이 백업하거나,
다른 도구로 넘길 때 쓴다.
"""

from __future__ import annotations

import json
from pathlib import Path

from novel_factory.config import Settings, get_settings
from novel_factory.database.models import Episode, Novel


def episode_dir(novel: Novel, number: int, settings: Settings | None = None) -> Path:
    cfg = settings or get_settings()
    return cfg.novels_dir / novel.slug / f"episode_{number:03d}"


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def save_episode_files(
    novel: Novel, episode: Episode, settings: Settings | None = None
) -> Path:
    folder = episode_dir(novel, episode.number, settings)
    folder.mkdir(parents=True, exist_ok=True)
    reports = episode.quality_reports or {}
    heading = f"# {episode.number}화 {episode.title}".rstrip()

    _write_json(folder / "outline.json", episode.outline or {})
    _write_json(folder / "scenes.json", episode.scenes or [])
    (folder / "draft.md").write_text(
        f"{heading}\n\n{episode.draft or ''}\n", encoding="utf-8"
    )
    (folder / "final.md").write_text(
        f"{heading}\n\n{episode.final_text or ''}\n", encoding="utf-8"
    )
    _write_json(
        folder / "summary.json",
        {
            "number": episode.number,
            "title": episode.title,
            "summary": episode.summary,
            "char_count": episode.char_count,
            "hook_type": episode.hook_type,
            "status": episode.status,
        },
    )
    _write_json(folder / "memory_delta.json", episode.memory_delta or {})
    _write_json(folder / "reference_usage.json", episode.reference_usage or {})
    _write_json(folder / "similarity_report.json", reports.get("similarity") or {})
    return folder
