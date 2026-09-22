"""인코딩 감지.

한국어 소설 파일은 UTF-8 말고도 CP949/EUC-KR로 저장된 경우가 흔하다.
charset_normalizer만 믿으면 CP949를 다른 단일바이트 인코딩으로 오인하는
경우가 있어서, 한국어 후보를 먼저 직접 시도한 뒤 마지막에만 위임한다.
"""

from __future__ import annotations

import unicodedata

# 시도 순서가 중요하다. utf-8-sig가 utf-8보다 먼저 와야 BOM이 본문에 남지 않고,
# cp949가 euc-kr보다 먼저 와야 확장 완성형 글자가 깨지지 않는다.
CANDIDATE_ENCODINGS: tuple[str, ...] = (
    "utf-8-sig",
    "utf-8",
    "cp949",
    "euc-kr",
    "utf-16",
    "utf-16-le",
    "utf-16-be",
)

# 디코딩은 성공했지만 실은 틀린 인코딩인 경우를 걸러내기 위한 기준.
# 제대로 디코딩됐다면 한글/ASCII가 대부분이고, 정의되지 않은 문자는 거의 없다.
_MAX_REPLACEMENT_RATIO = 0.001
_MIN_PLAUSIBLE_RATIO = 0.80


def _plausibility(text: str) -> float:
    """디코딩 결과가 한국어 텍스트로 얼마나 그럴듯한지 0~1로 점수화."""
    if not text:
        return 0.0
    sample = text[:20000]
    good = 0
    for ch in sample:
        code = ord(ch)
        if 0xAC00 <= code <= 0xD7A3:  # 한글 음절
            good += 1
        elif 0x1100 <= code <= 0x11FF or 0x3130 <= code <= 0x318F:  # 자모
            good += 1
        elif ch.isascii() and (ch.isprintable() or ch in "\r\n\t"):
            good += 1
        elif unicodedata.category(ch) in {"Pd", "Pi", "Pf", "Ps", "Pe", "Po", "Zs"}:
            good += 1  # 따옴표, 말줄임표, 전각 문장부호
    return good / len(sample)


def detect_encoding(raw: bytes) -> str:
    """바이트열의 인코딩 이름을 돌려준다. 실패하면 'utf-8'."""
    if not raw:
        return "utf-8"

    best: tuple[float, str] | None = None
    for enc in CANDIDATE_ENCODINGS:
        try:
            text = raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
        if text.count("�") / max(len(text), 1) > _MAX_REPLACEMENT_RATIO:
            continue
        score = _plausibility(text)
        if score >= _MIN_PLAUSIBLE_RATIO:
            return enc
        if best is None or score > best[0]:
            best = (score, enc)

    # 후보가 전부 미덥지 않으면 charset_normalizer에 물어본다.
    try:
        from charset_normalizer import from_bytes

        match = from_bytes(raw).best()
        if match is not None and match.encoding:
            return match.encoding
    except Exception:  # pragma: no cover - 선택 의존성
        pass

    return best[1] if best else "utf-8"


def decode_bytes(raw: bytes) -> tuple[str, str]:
    """(디코딩된 문자열, 사용한 인코딩)을 돌려준다.

    어떤 후보로도 깔끔히 읽히지 않으면 손실을 감수하고 UTF-8 replace로 읽는다.
    통계 분석에는 몇 글자 손실보다 파이프라인이 끝까지 도는 편이 낫다.
    """
    enc = detect_encoding(raw)
    # utf-8-sig는 BOM이 없는 파일도 그냥 읽어낸다. 실제로 BOM이 없었다면
    # 보고하는 이름은 utf-8이어야 한다.
    if enc == "utf-8-sig" and not raw.startswith(b"\xef\xbb\xbf"):
        enc = "utf-8"
    try:
        return raw.decode(enc), enc
    except (UnicodeDecodeError, LookupError):
        return raw.decode("utf-8", errors="replace"), "utf-8/replace"
