"""테스트용 합성 한국어 웹소설 생성기.

실제 상업 작품을 테스트에 넣을 수 없으므로, 한국 웹소설의 표기 관습
(제N화 마커, 따옴표 대사, 작은따옴표 속마음, 회차 말미 훅)을 그대로 가진
가짜 원고를 만든다. 시드를 고정해서 매번 같은 결과가 나온다.

  python tests/fixtures/make_fixtures.py
"""

from __future__ import annotations

import random
import sys
import zipfile
from pathlib import Path

FIXTURE_DIR = Path(__file__).resolve().parent
EPISODES = 30

CHARACTERS = ["김도윤", "박서연", "최민석", "강태호", "이준혁"]

TITLES = [
    "다시, 처음으로", "첫 번째 거래", "세광전자", "숨은 카드", "적의 얼굴",
    "계약서 한 장", "무너지는 벽", "옛 친구", "검은 수첩", "이사회",
    "배신의 값", "두 번째 기회", "역풍", "잠긴 문", "새벽의 결단",
    "흔들리는 손", "숫자의 진실", "빈 사무실", "되돌아온 이름", "마지막 경고",
    "균열", "폭로", "낯선 제안", "무너진 신뢰", "반격의 서막",
    "회장의 유언", "선택", "최후의 협상", "끝과 시작", "남겨진 것들",
]

NARRATION = [
    "{a}은 창밖을 내려다보았다. 도시는 아무것도 달라지지 않은 얼굴로 그를 맞았다.",
    "{a}은 서류를 한 장씩 넘겼다. 숫자는 정직했고, 그래서 더 잔인했다.",
    "회의실 공기가 무거웠다. 아무도 먼저 입을 열지 않았다.",
    "{a}은 천천히 자리에서 일어섰다. 의자가 바닥을 긁는 소리가 길게 남았다.",
    "{b}이 사무실로 들어섰다. 문이 닫히는 소리가 유난히 컸다.",
    "{a}은 커피를 한 모금 마셨다. 이미 식어 있었다.",
    "복도 끝에서 발소리가 들렸다. 점점 가까워지고 있었다.",
    "{a}은 손끝으로 책상을 두드렸다. 생각이 정리되지 않았다.",
    "{b}은 아무 말 없이 창가로 걸어갔다. 어깨가 굳어 있었다.",
    "밖에서는 비가 내리기 시작했다. 유리창을 타고 물줄기가 흘러내렸다.",
]

DIALOGUE = [
    '"이 조건으로는 어렵습니다."',
    '"그쪽에서 먼저 제안한 겁니다."',
    '"정말 그렇게 생각하세요?"',
    '"시간이 없어요. 오늘 안에 결정해야 합니다."',
    '"당신, 뭔가 숨기고 있죠."',
    '"내가 왜 당신을 믿어야 하지?"',
    '"세광전자는 3개월 안에 무너집니다."',
    '"그 정보, 어디서 들었습니까?"',
    '"한 번만 더 기회를 주십시오."',
    '"계약은 오늘로 끝입니다."',
    '"저는 처음부터 알고 있었습니다."',
    '"이건 경고가 아니라 통보입니다."',
]

INNER = [
    "'이 여자, 뭔가 알고 있다.'",
    "'여기서 물러서면 끝이다.'",
    "'분명 같은 날짜였다. 그때와 똑같이.'",
    "'시간이 부족하다.'",
    "'믿을 수 있는 사람이 아니다.'",
    "'이번에는 다르게 가야 한다.'",
]

ACTION = [
    "{a}은 서류를 덮고 자리에서 일어섰다.",
    "{b}은 손을 뻗어 문고리를 잡았다.",
    "{a}은 고개를 돌려 {b}을 바라보았다.",
    "{b}은 휴대폰을 꺼내 화면을 확인했다.",
    "{a}은 펜을 내려놓고 두 손을 모았다.",
]

PSYCH = [
    "{a}은 잠시 생각했다. 지난 생의 기억이 선명하게 떠올랐다.",
    "{a}의 심장이 빠르게 뛰었다. 확신이 서지 않았다.",
    "{a}은 후회하지 않기로 마음먹었다.",
    "불안이 목까지 차올랐다. {a}은 애써 표정을 지웠다.",
]

# 회차 말미 훅. 기획안 8번 유형에 대응한다.
HOOKS = {
    "정보공개": '그 순간, {b}이 낮게 말했다.\n"사실은, 그 계약을 막은 사람이 당신 아버지였습니다."',
    "위기발생": "전화가 울렸다. 화면에 뜬 이름을 본 순간, {a}의 얼굴이 굳었다.\n세광전자가 무너졌다는 소식이었다.",
    "새로운적": "문이 열렸다. 처음 보는 남자가 서 있었다.\n그는 {a}를 똑바로 노려보았다.",
    "반전": "하지만 {a}은 알고 있었다.\n그 서류가 진짜가 아니라는 것을.",
    "약속": '"반드시 갚아 드리겠습니다."\n{a}은 그렇게 말하고 돌아섰다.',
    "미스터리": "책상 위에 검은 수첩이 놓여 있었다.\n{a}은 그것이 왜 거기 있는지 알 수 없었다.",
    "보상직전": "{a}은 봉투를 열었다. 드디어 기다리던 것이 손에 들어왔다.",
    "전투직전": "{b}이 자리에서 일어섰다.\n두 사람의 시선이 정면으로 부딪쳤다.",
}
HOOK_ORDER = list(HOOKS)


def build_episode(n: int, rng: random.Random) -> tuple[str, str]:
    a, b = rng.sample(CHARACTERS, 2)
    title = f"제{n}화 - {TITLES[(n - 1) % len(TITLES)]}"

    blocks: list[str] = []
    # 도입
    blocks.append(rng.choice(NARRATION).format(a=a, b=b))
    # 전개: 대사와 서술을 섞는다
    for _ in range(rng.randint(5, 9)):
        roll = rng.random()
        if roll < 0.40:
            blocks.append(rng.choice(DIALOGUE))
        elif roll < 0.60:
            blocks.append(rng.choice(NARRATION).format(a=a, b=b))
        elif roll < 0.75:
            blocks.append(rng.choice(ACTION).format(a=a, b=b))
        elif roll < 0.88:
            blocks.append(rng.choice(PSYCH).format(a=a, b=b))
        else:
            blocks.append(rng.choice(INNER))
    # 회차 말미 훅. 5화마다 대형 사건을 배치해 pacing 분석이 잡히는지 확인한다.
    hook_key = "위기발생" if n % 5 == 0 else HOOK_ORDER[n % len(HOOK_ORDER)]
    blocks.append(HOOKS[hook_key].format(a=a, b=b))

    return title, "\n\n".join(blocks)


def build_novel(episodes: int = EPISODES, seed: int = 20260322) -> str:
    rng = random.Random(seed)
    parts = ["회귀한 인수합병가\n\n프롤로그\n\n죽었다고 생각한 순간, 그는 다시 눈을 떴다.\n2026년 3월 1일. 모든 것이 시작되기 전이었다.\n"]
    for n in range(1, episodes + 1):
        title, body = build_episode(n, rng)
        parts.append(f"{title}\n\n{body}\n")
    return "\n".join(parts)


def write_txt(text: str) -> Path:
    path = FIXTURE_DIR / "sample_novel.txt"
    path.write_text(text, encoding="utf-8")
    return path


def write_cp949(text: str) -> Path:
    """CP949로 저장된 파일. 인코딩 감지 테스트용."""
    path = FIXTURE_DIR / "sample_novel_cp949.txt"
    path.write_bytes(text.encode("cp949", errors="replace"))
    return path


def write_markdown(text: str) -> Path:
    lines = []
    for line in text.split("\n"):
        if line.startswith("제") and "화 - " in line:
            lines.append(f"## {line}")
        elif line == "프롤로그":
            lines.append("## 프롤로그")
        else:
            lines.append(line)
    path = FIXTURE_DIR / "sample_novel.md"
    path.write_text("# 회귀한 인수합병가\n\n" + "\n".join(lines), encoding="utf-8")
    return path


def _xhtml(title: str, body: str) -> bytes:
    paragraphs = "\n".join(
        f"    <p>{_escape(p)}</p>" for p in body.split("\n\n") if p.strip()
    )
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml"><head>'
        f"<title>{_escape(title)}</title></head><body>\n"
        f"    <h2>{_escape(title)}</h2>\n{paragraphs}\n</body></html>"
    ).encode("utf-8")


def _escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def write_epub(episodes: int = EPISODES, seed: int = 20260322) -> Path:
    rng = random.Random(seed)
    chapters = [("프롤로그", "죽었다고 생각한 순간, 그는 다시 눈을 떴다.\n\n2026년 3월 1일. 모든 것이 시작되기 전이었다.")]
    for n in range(1, episodes + 1):
        chapters.append(build_episode(n, rng))

    path = FIXTURE_DIR / "sample_novel.epub"
    manifest, spine, files = [], [], []
    for i, (title, body) in enumerate(chapters):
        name = f"OEBPS/ch{i:03d}.xhtml"
        files.append((name, _xhtml(title, body)))
        manifest.append(
            f'    <item id="ch{i:03d}" href="ch{i:03d}.xhtml" '
            f'media-type="application/xhtml+xml"/>'
        )
        spine.append(f'    <itemref idref="ch{i:03d}"/>')

    opf = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bid">\n'
        '  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">\n'
        '    <dc:identifier id="bid">urn:uuid:test-novel-0001</dc:identifier>\n'
        "    <dc:title>회귀한 인수합병가</dc:title>\n"
        "    <dc:creator>테스트 작가</dc:creator>\n"
        "    <dc:language>ko</dc:language>\n"
        "  </metadata>\n"
        "  <manifest>\n" + "\n".join(manifest) + "\n  </manifest>\n"
        "  <spine>\n" + "\n".join(spine) + "\n  </spine>\n"
        "</package>"
    ).encode("utf-8")

    container = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">\n'
        "  <rootfiles>\n"
        '    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>\n'
        "  </rootfiles>\n</container>"
    ).encode("utf-8")

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        # mimetype은 무압축으로 맨 앞에 와야 한다는 게 EPUB 규격이다.
        zf.writestr(
            zipfile.ZipInfo("mimetype"), "application/epub+zip", zipfile.ZIP_STORED
        )
        zf.writestr("META-INF/container.xml", container)
        zf.writestr("OEBPS/content.opf", opf)
        for name, data in files:
            zf.writestr(name, data)
    return path


def write_docx(episodes: int = 8, seed: int = 20260322) -> Path:
    """DOCX 픽스처. 제목 스타일로 회차를 구분한다."""
    rng = random.Random(seed)
    body_parts: list[str] = []

    def para(text: str, style: str | None = None) -> str:
        ppr = f"<w:pPr><w:pStyle w:val=\"{style}\"/></w:pPr>" if style else ""
        return f"<w:p>{ppr}<w:r><w:t xml:space=\"preserve\">{_escape(text)}</w:t></w:r></w:p>"

    body_parts.append(para("회귀한 인수합병가", "Title"))
    for n in range(1, episodes + 1):
        title, text = build_episode(n, rng)
        body_parts.append(para(title, "Heading1"))
        for block in text.split("\n\n"):
            for line in block.split("\n"):
                body_parts.append(para(line))
            body_parts.append("<w:p/>")

    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body>" + "".join(body_parts) + "</w:body></w:document>"
    ).encode("utf-8")

    core = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<cp:coreProperties '
        'xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/">'
        "<dc:title>회귀한 인수합병가</dc:title><dc:creator>테스트 작가</dc:creator>"
        "</cp:coreProperties>"
    ).encode("utf-8")

    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
        "</Types>"
    ).encode("utf-8")

    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
        '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>'
        "</Relationships>"
    ).encode("utf-8")

    path = FIXTURE_DIR / "sample_novel.docx"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("_rels/.rels", rels)
        zf.writestr("word/document.xml", document)
        zf.writestr("docProps/core.xml", core)
    return path


def write_pdf() -> Path:
    """최소 PDF. 한글 글꼴 임베딩 없이 만들 수 있는 ASCII 본문만 담는다.

    한글 PDF 추출 품질은 이 픽스처로 검증할 수 없다. 여기서 확인하는 것은
    PdfParser가 페이지를 읽고 쪽번호를 걷어내는 배관이 도는지까지다.
    """
    lines = [
        "Chapter 1 The Return",
        "He opened his eyes again. Nothing had changed yet.",
        "1",
        "Chapter 2 The First Deal",
        "The numbers were honest, and that made them cruel.",
        "2",
    ]
    content = "BT /F1 12 Tf 50 750 Td 14 TL\n"
    content += "".join(f"({ln}) Tj T*\n" for ln in lines)
    content += "ET"
    stream = content.encode("ascii")

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]

    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for i, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref_pos = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for off in offsets[1:]:
        out += f"{off:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_pos}\n%%EOF\n"
    ).encode()

    path = FIXTURE_DIR / "sample_novel.pdf"
    path.write_bytes(bytes(out))
    return path


def main() -> int:
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    text = build_novel()
    made = [
        write_txt(text),
        write_cp949(text),
        write_markdown(text),
        write_epub(),
        write_docx(),
        write_pdf(),
    ]
    for p in made:
        print(f"{p.name:28s} {p.stat().st_size:>9,} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
