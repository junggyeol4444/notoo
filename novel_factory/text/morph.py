"""형태소 분석기 없이 쓰는 최소한의 한국어 어절 처리.

konlpy/mecab을 쓰지 않는다. 설치 부담이 크고, 여기서 필요한 건
"어절에서 조사를 떼어 명사형 어간을 얻는다" 하나뿐이다.

이 모듈은 정확한 형태소 분석을 하지 않는다. 조사처럼 생긴 꼬리를
문자열로 떼어낼 뿐이다. 그래서 인명 추출과 복선 후보 추출에서는
여기서 나온 어간을 그대로 믿지 않고 추가 근거를 요구한다.
"""

from __future__ import annotations

import re

_EOJEOL_RE = re.compile(r"[가-힣]+")

# 긴 것부터 확인해야 "이번에는"에서 "는"만 떼고 "이번에"가 남는 일이 없다.
PARTICLES: tuple[str, ...] = (
    "에서부터", "에게서는", "으로서는",
    "에게서", "한테서", "이라고", "라고는", "에게는", "한테는", "이랑은",
    "에게도", "에게만", "께서는", "께서도", "에서는", "에서도", "으로는",
    "으로도", "까지는", "부터는", "만으로", "로서는",
    "이라는", "이가", "이는", "이를", "이도", "이만", "이와", "이의", "이에",
    "에는", "에도", "에서", "으로", "로는", "로도", "만은", "만이",
    "에게", "한테", "께서", "라고", "이랑", "처럼", "보다", "부터", "까지",
    "은", "는", "이", "가", "을", "를", "의", "와", "과", "도", "만", "랑",
    "아", "야", "씨", "님", "에", "로",
)

# 이 음절로 끝나는 어절은 서술어다.
PREDICATE_ENDINGS: tuple[str, ...] = (
    "다", "요", "까", "죠", "네", "군", "지", "자", "며", "고", "서", "면",
    "데", "니", "라", "래", "게", "듯", "봐", "줘", "쳐", "겠", "었", "았",
)

# 어간이 이 음절로 끝나면 용언 활용형이다. "결정해"(결정해야), "믿어"(믿어야).
VERB_STEM_ENDINGS: tuple[str, ...] = (
    "해", "어", "여", "워", "러", "려", "되", "하", "취",
)


def strip_particle(
    eojeol: str, *, min_len: int = 2, max_len: int = 6
) -> tuple[str, str] | None:
    """어절에서 조사를 떼어 (어간, 조사)를 돌려준다.

    조사가 붙지 않은 어절은 None이다. 그 폴백을 열어 두면 "있었다",
    "않았다" 같은 서술어가 전부 명사 후보로 들어온다.
    """
    if eojeol.endswith(PREDICATE_ENDINGS):
        return None
    for particle in PARTICLES:
        if len(eojeol) > len(particle) and eojeol.endswith(particle):
            stem = eojeol[: -len(particle)]
            if (
                min_len <= len(stem) <= max_len
                and not stem.endswith(PREDICATE_ENDINGS)
                and not stem.endswith(VERB_STEM_ENDINGS)
            ):
                return stem, particle
    return None


def iter_eojeols(text: str) -> "list[re.Match[str]]":
    """한글 어절 매치 목록 (위치 정보 포함)."""
    return list(_EOJEOL_RE.finditer(text))


def noun_stems(text: str, *, min_len: int = 2, max_len: int = 6) -> list[str]:
    """조사를 뗀 명사형 어간 목록."""
    out: list[str] = []
    for m in _EOJEOL_RE.finditer(text):
        hit = strip_particle(m.group(0), min_len=min_len, max_len=max_len)
        if hit is not None:
            out.append(hit[0])
    return out
