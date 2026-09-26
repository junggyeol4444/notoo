"""참고작 고유명사 재사용 검사 (기획안 36번 '고유 설정 유사', 56번 셋째 원칙).

    "특정 작품의 고유명사·세계관·캐릭터를 자동으로 가져오지 않는다."

그런데 참고작의 인물 이름을 저장하지 않으면(기획안 56번), 새 원고가 그 이름을 썼는지도
알 수 없다. 그래서 이름을 평문이 아니라 해시로만 저장한다.

  - 분석할 때 인물 분석기가 찾은 이름(3~4음절 전체 이름)을 salt를 넣은 SHA-256으로
    바꿔 Reference Profile의 name_hashes에 넣는다. 이름 자체는 남지 않는다.
  - 새 원고의 3~4음절 어절·어간을 같은 방식으로 해시해 겹치는지 본다.
  - salt는 설치마다 한 번 만들어 data 디렉터리(name_salt)에 둔다. 흔한 이름 목록으로
    미리 계산한 해시표를 대조하는 것을 막는다. DB를 옮길 때 이 파일도 같이 옮겨야
    기존 해시와 맞는다.

두 음절 이름(도윤, 서연)은 보지 않는다. 너무 흔해서 우연히 겹친다. 성까지 같은 전체
이름이 겹칠 때만 잡는다.
"""

from __future__ import annotations

import hashlib
import secrets
from pathlib import Path

from novel_factory.config import Settings, get_settings
from novel_factory.text.morph import iter_eojeols, noun_stems

MAIN_ROLES = ("주인공", "주요조연", "적대자")
NAME_LENGTHS = (3, 4)
_SALT_FILE = "name_salt"


def name_salt(settings: Settings | None = None) -> bytes:
    cfg = settings or get_settings()
    path: Path = cfg.data_dir / _SALT_FILE
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(secrets.token_hex(32), encoding="ascii")
    return bytes.fromhex(path.read_text(encoding="ascii").strip())


def hash_name(name: str, salt: bytes) -> str:
    return hashlib.sha256(salt + name.strip().encode("utf-8")).hexdigest()[:32]


def reference_name_hashes(
    characters: list[tuple[str, str]], salt: bytes
) -> dict[str, list[str]]:
    """[(이름, 역할)] → {"main": [...], "other": [...]} (해시만)."""
    main, other = set(), set()
    for name, role in characters:
        if len(name) not in NAME_LENGTHS:
            continue
        (main if role in MAIN_ROLES else other).add(hash_name(name, salt))
    return {"main": sorted(main), "other": sorted(other - main)}


def candidates(text: str) -> set[str]:
    """원고에서 이름일 수 있는 3~4음절 낱말 (조사를 뗀 어간과 조사 없는 어절)."""
    out = set(noun_stems(text, min_len=3, max_len=4))
    out |= {m.group(0) for m in iter_eojeols(text) if len(m.group(0)) in NAME_LENGTHS}
    return out


def find_reused_names(
    text: str, hashes: dict[str, str], salt: bytes
) -> list[tuple[str, str]]:
    """[(원고의 낱말, "main" 또는 "other")]. hashes는 해시 → 등급."""
    found = []
    for word in sorted(candidates(text)):
        level = hashes.get(hash_name(word, salt))
        if level:
            found.append((word, level))
    return found
