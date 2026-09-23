"""OLE 복합 문서(Compound File Binary) 읽기. stdlib만 쓴다.

HWP 5.0 파일은 이 형식이다. 한 파일 안에 폴더(storage)와 파일(stream)이
들어 있는 작은 파일시스템이라고 보면 된다.

olefile에 의존하지 않고 직접 읽는 이유는 EPUB·DOCX 파서와 같다. 필요한 것이
"경로로 스트림 하나를 꺼낸다" 뿐이라 의존성을 늘릴 이유가 없다.

형식 요약 (MS-CFB)
  헤더 512바이트. 섹터 n은 파일 오프셋 (n + 1) * 섹터크기 에 있다.
  FAT       섹터 체인 표. DIFAT이 FAT 섹터들의 위치를 알려 준다.
  디렉터리  128바이트 항목의 레드블랙 트리. 이름, 종류, 시작 섹터, 크기.
  미니 스트림  4,096바이트 미만 스트림은 64바이트 미니 섹터에 따로 모인다.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

from novel_factory.errors import ParseError

SIGNATURE = bytes.fromhex("D0CF11E0A1B11AE1")

FREESECT = 0xFFFFFFFF
ENDOFCHAIN = 0xFFFFFFFE
FATSECT = 0xFFFFFFFD
DIFSECT = 0xFFFFFFFC
NOSTREAM = 0xFFFFFFFF

TYPE_STORAGE = 1
TYPE_STREAM = 2
TYPE_ROOT = 5

HEADER_DIFAT_ENTRIES = 109
DIR_ENTRY_SIZE = 128


@dataclass(slots=True)
class DirEntry:
    sid: int
    name: str
    kind: int
    left: int
    right: int
    child: int
    start: int
    size: int


class CompoundFile:
    """읽기 전용 CFB 컨테이너."""

    def __init__(self, data: bytes) -> None:
        if len(data) < 512 or data[:8] != SIGNATURE:
            raise ParseError("OLE 복합 문서가 아닙니다 (서명 불일치).")
        self._data = data

        (
            major,
            byte_order,
            sector_shift,
            mini_shift,
        ) = struct.unpack_from("<HHHH", data, 0x1A)
        if byte_order != 0xFFFE:
            raise ParseError("OLE 헤더의 바이트 순서 표시가 잘못됐습니다.")
        if major not in (3, 4):
            raise ParseError(f"지원하지 않는 OLE 버전입니다: {major}")

        self.sector_size = 1 << sector_shift
        self.mini_sector_size = 1 << mini_shift
        (
            _num_dir_sectors,
            num_fat_sectors,
            first_dir_sector,
            _transaction,
            self.mini_cutoff,
            first_minifat,
            num_minifat,
            first_difat,
            num_difat,
        ) = struct.unpack_from("<9I", data, 0x28)

        self._fat = self._load_fat(num_fat_sectors, first_difat, num_difat)
        self._entries = self._load_directory(first_dir_sector)
        root = self._entries[0]
        if root.kind != TYPE_ROOT:
            raise ParseError("OLE 디렉터리의 첫 항목이 루트가 아닙니다.")

        self._minifat = (
            self._read_u32_chain(first_minifat)
            if num_minifat and first_minifat < FATSECT
            else []
        )
        self._ministream = (
            self._read_chain(root.start, root.size)
            if root.size and root.start < FATSECT
            else b""
        )
        self._paths = self._build_paths()

    # ------------------------------------------------------------------
    # 섹터 / 체인
    # ------------------------------------------------------------------
    def _sector(self, sid: int) -> bytes:
        offset = (sid + 1) * self.sector_size
        if offset >= len(self._data):
            raise ParseError(f"OLE 섹터 {sid}가 파일 범위를 벗어났습니다.")
        # 마지막 섹터가 잘려 저장된 파일이 있다. 그 경우 있는 만큼만 돌려준다.
        return self._data[offset : offset + self.sector_size]

    def _load_fat(self, num_fat: int, first_difat: int, num_difat: int) -> list[int]:
        difat = list(struct.unpack_from(f"<{HEADER_DIFAT_ENTRIES}I", self._data, 0x4C))
        per_sector = self.sector_size // 4 - 1
        sid = first_difat
        seen: set[int] = set()
        for _ in range(num_difat):
            if sid >= FATSECT or sid in seen:
                break
            seen.add(sid)
            values = struct.unpack(f"<{per_sector + 1}I", self._sector(sid))
            difat.extend(values[:per_sector])
            sid = values[per_sector]

        fat_sectors = [s for s in difat if s < FATSECT][:num_fat]
        fat: list[int] = []
        count = self.sector_size // 4
        for s in fat_sectors:
            fat.extend(struct.unpack(f"<{count}I", self._sector(s)))
        return fat

    def _chain(self, start: int, table: list[int]) -> list[int]:
        """체인을 따라간다. 순환 참조가 있는 손상 파일에서 무한 루프를 막는다."""
        out: list[int] = []
        seen: set[int] = set()
        sid = start
        while sid < FATSECT:
            if sid in seen or sid >= len(table):
                raise ParseError("OLE 섹터 체인이 손상됐습니다.")
            seen.add(sid)
            out.append(sid)
            sid = table[sid]
        return out

    def _read_chain(self, start: int, size: int | None = None) -> bytes:
        data = b"".join(self._sector(s) for s in self._chain(start, self._fat))
        return data if size is None else data[:size]

    def _read_u32_chain(self, start: int) -> list[int]:
        raw = self._read_chain(start)
        return list(struct.unpack(f"<{len(raw) // 4}I", raw))

    def _read_mini(self, start: int, size: int) -> bytes:
        parts: list[bytes] = []
        for sid in self._chain(start, self._minifat):
            offset = sid * self.mini_sector_size
            parts.append(self._ministream[offset : offset + self.mini_sector_size])
        return b"".join(parts)[:size]

    # ------------------------------------------------------------------
    # 디렉터리
    # ------------------------------------------------------------------
    def _load_directory(self, first_dir_sector: int) -> list[DirEntry]:
        raw = self._read_chain(first_dir_sector)
        entries: list[DirEntry] = []
        for i in range(len(raw) // DIR_ENTRY_SIZE):
            chunk = raw[i * DIR_ENTRY_SIZE : (i + 1) * DIR_ENTRY_SIZE]
            name_len = struct.unpack_from("<H", chunk, 0x40)[0]
            kind = chunk[0x42]
            left, right, child = struct.unpack_from("<III", chunk, 0x44)
            start = struct.unpack_from("<I", chunk, 0x74)[0]
            size_low, size_high = struct.unpack_from("<II", chunk, 0x78)
            # 버전 3 파일은 크기 상위 4바이트에 쓰레기가 들어 있기도 하다.
            size = size_low if self.sector_size == 512 else size_low | (size_high << 32)
            name = chunk[: max(name_len - 2, 0)].decode("utf-16-le", errors="replace")
            entries.append(DirEntry(i, name, kind, left, right, child, start, size))
        if not entries:
            raise ParseError("OLE 디렉터리가 비어 있습니다.")
        return entries

    def _siblings(self, sid: int) -> list[int]:
        """레드블랙 트리 하나의 모든 노드를 돌려준다 (순서 무관)."""
        out: list[int] = []
        stack = [sid]
        seen: set[int] = set()
        while stack:
            cur = stack.pop()
            if cur == NOSTREAM or cur in seen or cur >= len(self._entries):
                continue
            seen.add(cur)
            out.append(cur)
            entry = self._entries[cur]
            stack.append(entry.left)
            stack.append(entry.right)
        return out

    def _build_paths(self) -> dict[str, DirEntry]:
        paths: dict[str, DirEntry] = {}
        stack: list[tuple[int, str]] = [(self._entries[0].child, "")]
        visited_storages: set[int] = set()
        while stack:
            child_sid, prefix = stack.pop()
            for sid in self._siblings(child_sid):
                entry = self._entries[sid]
                path = f"{prefix}{entry.name}"
                if entry.kind == TYPE_STREAM:
                    paths[path] = entry
                elif entry.kind == TYPE_STORAGE and sid not in visited_storages:
                    visited_storages.add(sid)
                    stack.append((entry.child, f"{path}/"))
        return paths

    # ------------------------------------------------------------------
    # 공개 API
    # ------------------------------------------------------------------
    def list_streams(self) -> list[str]:
        return sorted(self._paths)

    def exists(self, path: str) -> bool:
        return path in self._paths

    def read(self, path: str) -> bytes:
        entry = self._paths.get(path)
        if entry is None:
            raise KeyError(path)
        if entry.size == 0:
            return b""
        if entry.size < self.mini_cutoff:
            return self._read_mini(entry.start, entry.size)
        return self._read_chain(entry.start, entry.size)
