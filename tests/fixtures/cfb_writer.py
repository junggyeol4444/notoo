"""테스트 픽스처용 OLE 복합 문서(CFB) 작성기.

실제 한컴 HWP 샘플은 배포 라이선스 때문에 저장소에 넣을 수 없다.
그래서 테스트용 HWP를 직접 만든다. 이 작성기가 만든 파일은 olefile로 모든
스트림이 읽히고, pyhwp의 레코드 모델 파서로 본문이 읽히는 것을 확인했다.
둘 다 이 프로젝트의 리더와 무관한 독립 구현이다.

pyhwp의 hwp5txt(조판 모델까지 만드는 도구)로는 읽히지 않는다. 한컴 파일은
각 구역 첫 문단에 구역·단 정의 컨트롤을 넣는데, 이 작성기는 그것을 만들지
않기 때문이다. 본문 텍스트 추출과는 무관한 조판 정보다.

단순화한 점
  - 버전 3 (512바이트 섹터)만 쓴다.
  - 형제 노드를 레드블랙 트리가 아니라 오른쪽으로만 이어진 사슬로 만든다.
    이름 순서는 규격대로 정렬하므로 이진 탐색은 맞게 동작한다.
  - FAT이 헤더의 DIFAT 109칸 안에 들어가는 크기(약 7MB)까지만 지원한다.
"""

from __future__ import annotations

import struct
from itertools import pairwise

SIGNATURE = bytes.fromhex("D0CF11E0A1B11AE1")
SECTOR = 512
MINI_SECTOR = 64
MINI_CUTOFF = 4096
FREESECT = 0xFFFFFFFF
ENDOFCHAIN = 0xFFFFFFFE
FATSECT = 0xFFFFFFFD
NOSTREAM = 0xFFFFFFFF


def _pad(data: bytes, size: int) -> bytes:
    rem = len(data) % size
    return data if rem == 0 else data + b"\x00" * (size - rem)


def _sort_key(name: str) -> tuple[int, str]:
    # 규격: 이름 길이가 먼저, 같으면 대문자로 바꿔 비교
    return (len(name), name.upper())


class _Node:
    def __init__(self, name: str, kind: int, data: bytes = b"") -> None:
        self.name = name
        self.kind = kind  # 1 storage, 2 stream, 5 root
        self.data = data
        self.children: dict[str, _Node] = {}
        self.sid = -1
        self.left = self.right = self.child = NOSTREAM
        self.start = ENDOFCHAIN
        self.size = 0


def build_cfb(streams: dict[str, bytes]) -> bytes:
    """{'경로/이름': 바이트}로 CFB 파일을 만든다."""
    root = _Node("Root Entry", 5)
    for path, data in streams.items():
        parts = path.split("/")
        node = root
        for part in parts[:-1]:
            node = node.children.setdefault(part, _Node(part, 1))
        node.children[parts[-1]] = _Node(parts[-1], 2, data)

    # 디렉터리 항목 번호 매기기 (너비 우선)
    order: list[_Node] = [root]
    queue = [root]
    while queue:
        cur = queue.pop(0)
        kids = sorted(cur.children.values(), key=lambda n: _sort_key(n.name))
        for kid in kids:
            order.append(kid)
            queue.append(kid)
    for i, node in enumerate(order):
        node.sid = i
    for node in order:
        kids = sorted(node.children.values(), key=lambda n: _sort_key(n.name))
        if kids:
            node.child = kids[0].sid
            for a, b in pairwise(kids):
                a.right = b.sid

    # 미니 스트림
    mini = bytearray()
    minifat: list[int] = []
    for node in order:
        if node.kind != 2 or not node.data or len(node.data) >= MINI_CUTOFF:
            continue
        first = len(mini) // MINI_SECTOR
        count = (len(node.data) + MINI_SECTOR - 1) // MINI_SECTOR
        mini += _pad(node.data, MINI_SECTOR)
        minifat += [first + k + 1 for k in range(count - 1)] + [ENDOFCHAIN]
        node.start, node.size = first, len(node.data)
    for node in order:
        if node.kind == 2 and not node.data:
            node.start, node.size = ENDOFCHAIN, 0

    # 일반 섹터에 들어갈 덩어리들
    blobs: list[tuple[str, bytes, _Node | None]] = []
    dir_bytes_len = _pad(b"\x00" * (128 * len(order)), SECTOR)
    blobs.append(("dir", dir_bytes_len, None))
    if minifat:
        blobs.append(
            ("minifat", _pad(struct.pack(f"<{len(minifat)}I", *minifat), SECTOR), None)
        )
    if mini:
        blobs.append(("ministream", _pad(bytes(mini), SECTOR), root))
    for node in order:
        if node.kind == 2 and len(node.data) >= MINI_CUTOFF:
            blobs.append(("stream", _pad(node.data, SECTOR), node))

    data_sectors = sum(len(b) // SECTOR for _, b, _ in blobs)
    fat_sectors = 1
    while fat_sectors * (SECTOR // 4) < data_sectors + fat_sectors:
        fat_sectors += 1
    if fat_sectors > 109:
        raise ValueError("픽스처 작성기는 약 7MB까지만 지원합니다.")

    fat = [FATSECT] * fat_sectors
    starts: dict[str, int] = {}
    body = bytearray()
    for kind, blob, node in blobs:
        start = fat_sectors + len(body) // SECTOR
        count = len(blob) // SECTOR
        fat += [start + k + 1 for k in range(count - 1)] + [ENDOFCHAIN]
        body += blob
        if node is root:
            root.start, root.size = start, len(mini)
        elif node is not None:
            node.start, node.size = start, len(node.data)
        starts.setdefault(kind, start)
    fat += [FREESECT] * (fat_sectors * (SECTOR // 4) - len(fat))

    # 디렉터리 실제 내용
    entries = bytearray()
    for node in order:
        name = node.name.encode("utf-16-le") + b"\x00\x00"
        entry = bytearray(128)
        entry[: len(name)] = name
        struct.pack_into("<HBB", entry, 0x40, len(name), node.kind, 1)
        struct.pack_into("<III", entry, 0x44, node.left, node.right, node.child)
        struct.pack_into("<II", entry, 0x74, node.start, node.size)
        entries += entry
    empty = bytearray(128)
    struct.pack_into("<III", empty, 0x44, NOSTREAM, NOSTREAM, NOSTREAM)
    while len(entries) % SECTOR:
        entries += empty
    dir_start = starts["dir"]
    offset = (dir_start - fat_sectors) * SECTOR
    body[offset : offset + len(entries)] = entries

    header = bytearray(SECTOR)
    header[0:8] = SIGNATURE
    struct.pack_into("<HHHHH", header, 0x18, 0x3E, 3, 0xFFFE, 9, 6)
    struct.pack_into(
        "<9I",
        header,
        0x28,
        0,  # 디렉터리 섹터 수 (버전 3은 0)
        fat_sectors,
        dir_start,
        0,
        MINI_CUTOFF,
        starts.get("minifat", ENDOFCHAIN),
        len(minifat) * 4 // SECTOR + (1 if len(minifat) * 4 % SECTOR else 0),
        ENDOFCHAIN,
        0,
    )
    difat = list(range(fat_sectors)) + [FREESECT] * (109 - fat_sectors)
    struct.pack_into("<109I", header, 0x4C, *difat)

    fat_bytes = struct.pack(f"<{len(fat)}I", *fat)
    return bytes(header) + fat_bytes + bytes(body)
