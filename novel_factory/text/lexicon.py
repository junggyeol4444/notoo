"""규칙 기반 분석에 쓰는 한국어 어휘 사전.

한계를 먼저 밝힌다. 이 사전은 형태소 분석 없이 어간 문자열 포함 여부로
매칭한다. 따라서 부정("죽지 않았다")이나 인용("죽는다고 말했다")을
구분하지 못한다. 회차 하나를 정확히 읽어내는 용도가 아니라, 수백 회차를
누적했을 때 작품 간 상대 비교가 가능한 지표를 얻는 용도다.

사전은 코드에 박아두되 `extend_lexicon()`으로 장르별 보강이 가능하다.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass, field
from functools import lru_cache

# --------------------------------------------------------------------------
# 감정 극성 (emotion valence)
# --------------------------------------------------------------------------
POSITIVE_WORDS: set[str] = {
    "기쁘",
    "기뻐",
    "행복",
    "웃었",
    "웃음",
    "웃으",
    "미소",
    "환호",
    "안도",
    "따뜻",
    "다행",
    "성공",
    "승리",
    "이겼",
    "해냈",
    "완벽",
    "만족",
    "뿌듯",
    "감동",
    "사랑",
    "설레",
    "기대",
    "희망",
    "빛나",
    "개운",
    "시원",
    "여유",
    "평온",
    "든든",
    "믿음",
    "고맙",
    "감사",
    "칭찬",
    "인정",
    "보상",
    "획득",
    "각성",
    "성장",
    "돌파",
    "승진",
    "계약",
    "체결",
    "합격",
    "부활",
    "회복",
    "구했",
    "살렸",
    "재회",
    "화해",
    "축하",
    "환하",
    "확신",
}

NEGATIVE_WORDS: set[str] = {
    "슬프",
    "슬퍼",
    "눈물",
    "울었",
    "울음",
    "흐느",
    "분노",
    "화났",
    "짜증",
    "불안",
    "두려",
    "무서",
    "공포",
    "절망",
    "좌절",
    "패배",
    "졌다",
    "실패",
    "무너",
    "부서",
    "죽었",
    "죽음",
    "죽어",
    "죽일",
    "시체",
    "핏자국",
    "피를",
    "피가",
    "출혈",
    "비명",
    "고통",
    "아프",
    "상처",
    "배신",
    "속였",
    "거짓",
    "위기",
    "위험",
    "경고",
    "협박",
    "위협",
    "적대",
    "원수",
    "증오",
    "미워",
    "혐오",
    "역겨",
    "구역",
    "외로",
    "고독",
    "쓸쓸",
    "허무",
    "막막",
    "답답",
    "초조",
    "긴장",
    "식은땀",
    "떨렸",
    "싸늘",
    "차갑",
    "굳었",
    "얼어",
    "끔찍",
    "참담",
    "비참",
    "억울",
    "원망",
    "포기",
    "상실",
    "잃었",
    "무력",
    "파산",
    "폭락",
    "해고",
    "부도",
    "소송",
    "고소",
}

# --------------------------------------------------------------------------
# 사건 강도 (event intensity)
#   가중치가 클수록 '대형 사건'으로 잡힐 확률이 높다.
# --------------------------------------------------------------------------
EVENT_WEIGHTS: dict[str, float] = {
    # 전투/충돌
    "전투": 2.0,
    "싸움": 1.5,
    "격돌": 2.0,
    "공격": 1.5,
    "베었": 1.8,
    "찔렀": 1.8,
    "쓰러": 1.5,
    "폭발": 2.5,
    "무너졌": 2.0,
    "총성": 2.2,
    "칼날": 1.5,
    # 생사
    "죽었": 3.0,
    "죽음": 2.8,
    "사망": 3.0,
    "시체": 2.5,
    "장례": 2.2,
    "살해": 3.0,
    "부활": 2.8,
    "회귀": 3.0,
    "각성": 2.5,
    # 관계 전환
    "배신": 2.8,
    "고백": 2.0,
    "이별": 2.0,
    "재회": 1.8,
    "약속": 1.2,
    "맹세": 1.8,
    "결혼": 2.2,
    "이혼": 2.2,
    # 정보/반전
    "진실": 2.0,
    "정체": 2.2,
    "밝혀졌": 2.2,
    "알고 있었": 2.0,
    "비밀": 1.8,
    "함정": 2.2,
    "음모": 2.0,
    "증거": 1.8,
    "폭로": 2.5,
    # 경영/현대물
    "인수": 2.2,
    "합병": 2.2,
    "계약": 1.5,
    "체결": 1.5,
    "투자": 1.5,
    "상장": 2.2,
    "부도": 2.5,
    "파산": 2.5,
    "소송": 2.0,
    "고소": 2.0,
    "해고": 1.8,
    "폭락": 2.2,
    "급등": 1.8,
    "인사": 1.0,
    # 지위 변화
    "승진": 1.8,
    "취임": 2.0,
    "몰락": 2.5,
    "추방": 2.2,
    "탈출": 2.0,
    "체포": 2.2,
    "구속": 2.2,
    "석방": 2.0,
    # 등장/퇴장
    "나타났": 1.5,
    "등장": 1.3,
    "사라졌": 1.8,
    "떠났": 1.5,
}

# --------------------------------------------------------------------------
# 클리프행어 유형 (기획안 8번)
#   회차 말미 텍스트에서 아래 신호를 찾아 유형을 고른다.
# --------------------------------------------------------------------------
CLIFFHANGER_PATTERNS: dict[str, list[str]] = {
    "정보공개": [
        "사실은",
        "진실",
        "정체는",
        "알고 보니",
        "밝혀졌",
        "이었다니",
        "였다니",
        "그 사람이",
        "네가",
        "당신이",
        "바로 그",
        "폭로",
    ],
    "위기발생": [
        "위험",
        "위기",
        "경보",
        "비명",
        "쓰러졌",
        "피를",
        "무너",
        "터졌",
        "불길",
        "늦었",
        "당했",
        "덮쳤",
        "포위",
        "갇혔",
        "도망",
    ],
    "새로운적": [
        "처음 보는",
        "낯선",
        "누구",
        "정체불명",
        "그림자",
        "나타났",
        "등장했",
        "가로막",
        "막아섰",
        "노려보",
    ],
    "반전": [
        "하지만",
        "그러나",
        "설마",
        "말도 안",
        "믿을 수 없",
        "예상",
        "뒤집",
        "반대로",
        "착각",
        "속았",
        "함정",
    ],
    "약속": [
        "반드시",
        "기다려",
        "다음에",
        "약속",
        "맹세",
        "돌아오",
        "끝내겠",
        "갚아",
        "두고 보",
    ],
    "미스터리": [
        # "왜"는 단독일 때만 의미가 있다. 어절 경계만으로는 "왜곡"을 못 거른다.
        "왜 ",
        "왜?",
        "왜지",
        "왜일까",
        "무슨",
        "어떻게",
        "의문",
        "이상한",
        "낌새",
        "수상",
        "알 수 없",
        "짐작",
        "모를",
        "알 수가 없",
    ],
    "보상직전": [
        "열렸",
        "손을 뻗",
        "받았",
        "눈앞",
        "드디어",
        "마침내",
        "완성",
        "도착",
        "확인하",
        "열어",
    ],
    "전투직전": [
        "칼을",
        "총을",
        "주먹",
        "자세를",
        "노려",
        "맞섰",
        "달려들",
        "겨눴",
        "겨누",
        "싸울",
        "덤벼",
        "맞붙",
        "부딪",
        "정면으로",
        "몸을 날",
    ],
}

# --------------------------------------------------------------------------
# 묘사 유형 (행동 vs 내면)
# --------------------------------------------------------------------------
ACTION_WORDS: set[str] = {
    "걸었",
    "걸어",
    "뛰었",
    "달렸",
    "움직였",
    "일어섰",
    "앉았",
    "누웠",
    "돌아섰",
    "멈췄",
    "다가갔",
    "물러섰",
    "뻗었",
    "잡았",
    "놓았",
    "던졌",
    "밀었",
    "당겼",
    "열었",
    "닫았",
    "들었",
    "내려놓",
    "건넸",
    "받았",
    "꺼냈",
    "집었",
    "휘둘",
    "내리쳤",
    "후려쳤",
    "때렸",
    "걷어찼",
    "피했",
    "막았",
    "올랐",
    "내렸",
    "들어섰",
    "나섰",
    "향했",
    "고개를",
    "손을",
    "발을",
    "몸을",
    "눈을",
    "입을",
    "어깨를",
    "시선을",
}

PSYCH_WORDS: set[str] = {
    "생각했",
    "생각이",
    "느꼈",
    "느낌",
    "깨달았",
    "떠올",
    "기억",
    "판단",
    "짐작",
    "확신",
    "의심",
    "궁금",
    "바랐",
    "원했",
    "싶었",
    "두려",
    "불안",
    "안도",
    "후회",
    "결심",
    "각오",
    "마음",
    "속으로",
    "머릿속",
    "가슴이",
    "심장이",
    "머리가",
    "이해",
    "납득",
    "혼란",
    "복잡",
    "직감",
    "예감",
    "본능",
}

# --------------------------------------------------------------------------
# 문체 지표
# --------------------------------------------------------------------------
# 비유: "~처럼", "~같이", "마치 ~듯"
SIMILE_RE = re.compile(r"(?:마치\s|처럼|같이|듯이|듯한|양\s)")
# 의성어/의태어: 같은 음절이 반복되는 2~3음절 덩어리 (쿵쿵, 두근두근, 스르륵)
ONOMATOPOEIA_RE = re.compile(
    r"(?:([가-힣]{2})\1|[가-힣]{1,2}(?:득|끗|칵|쾅|쿵|탁|툭|철썩|스륵|르륵|르르))"
)
# 인물 지칭 뒤에 붙는 조사. 인물 후보 추출에 쓴다.
NAME_PARTICLES = (
    "은",
    "는",
    "이",
    "가",
    "을",
    "를",
    "에게",
    "한테",
    "의",
    "와",
    "과",
    "도",
    "만",
    "아",
    "야",
    "씨",
    "님",
    "이는",
    "이가",
    "이를",
    "이에게",
    "이도",
)

# 인물 후보에서 걸러내야 하는 흔한 명사/부사. 2~4음절 한글 덩어리를 뽑으면
# 아래 단어들이 이름처럼 잡힌다.
NAME_STOPWORDS: set[str] = {
    "그것",
    "이것",
    "저것",
    "여기",
    "거기",
    "저기",
    "사람",
    "사람들",
    "남자",
    "여자",
    "아이",
    "어른",
    "모두",
    "우리",
    "너희",
    "자신",
    "자기",
    "당신",
    "누구",
    "무엇",
    "어디",
    "언제",
    "지금",
    "오늘",
    "내일",
    "어제",
    "방금",
    "순간",
    "잠시",
    "동안",
    "사이",
    "안에",
    "밖에",
    "위에",
    "아래",
    "앞에",
    "뒤에",
    "옆에",
    "속에",
    "다시",
    "아직",
    "이미",
    "벌써",
    "정말",
    "진짜",
    "조금",
    "많이",
    "너무",
    "가장",
    "제일",
    "그냥",
    "역시",
    "결국",
    "물론",
    "하지만",
    "그러나",
    "그리고",
    "그래서",
    "그러자",
    "그때",
    "이번",
    "다음",
    "마지막",
    "처음",
    "얼굴",
    "목소리",
    "시선",
    "고개",
    "머리",
    "가슴",
    "마음",
    "생각",
    "기분",
    "표정",
    "눈빛",
    "손끝",
    "입가",
    "회사",
    "사무실",
    "건물",
    "대표",
    "사장",
    "회장",
    "부장",
    "과장",
    "팀장",
    "직원",
    "비서",
    "기자",
    "말이",
    "말을",
    "말은",
    "일이",
    "일을",
    "것이",
    "것을",
    "것은",
    "때문",
    "정도",
    "이유",
    "방법",
    "문제",
    "상황",
    "경우",
    "관계",
    "이야기",
    "소리",
    # 부정칭·수량·지시 표현
    "아무",
    "아무도",
    "누군가",
    "무언가",
    "서로",
    "여러",
    "어느",
    "이런",
    "그런",
    "저런",
    "얼마",
    "며칠",
    "하나",
    "모든",
    "각자",
    "대신",
    "만큼",
    "잠깐",
    "한참",
    "이제",
    "이곳",
    "그곳",
    "저곳",
    "전부",
    "약간",
    "훨씬",
    # 장면 묘사에 자주 오는 무생물 주어
    "공기",
    "분위기",
    "침묵",
    "공간",
    "세상",
    "세계",
    "시간",
    "자리",
    "바람",
    "햇빛",
    "어둠",
    "그림자",
    "전화",
    "화면",
    "서류",
    "계약",
    "회의",
    "결과",
    "결정",
    "사실",
    "진실",
    "숫자",
    "가격",
    "조건",
    "제안",
    "대답",
    "질문",
}


@dataclass
class LexiconBundle:
    """분석기에 주입되는 사전 묶음. 장르별로 갈아끼울 수 있다."""

    positive: set[str] = field(default_factory=lambda: set(POSITIVE_WORDS))
    negative: set[str] = field(default_factory=lambda: set(NEGATIVE_WORDS))
    events: dict[str, float] = field(default_factory=lambda: dict(EVENT_WEIGHTS))
    cliffhangers: dict[str, list[str]] = field(
        default_factory=lambda: {k: list(v) for k, v in CLIFFHANGER_PATTERNS.items()}
    )
    action: set[str] = field(default_factory=lambda: set(ACTION_WORDS))
    psych: set[str] = field(default_factory=lambda: set(PSYCH_WORDS))
    name_stopwords: set[str] = field(default_factory=lambda: set(NAME_STOPWORDS))

    def extend(self, **kwargs: set[str] | dict[str, float] | dict[str, list[str]]) -> None:
        """사전을 추가 어휘로 보강한다."""
        for key, value in kwargs.items():
            current = getattr(self, key, None)
            if current is None:
                raise KeyError(f"알 수 없는 사전 이름: {key}")
            if isinstance(current, set) and isinstance(value, set):
                current |= value
            elif isinstance(current, dict) and isinstance(value, dict):
                current.update(value)  # type: ignore[arg-type]
            else:
                raise TypeError(f"{key}에 맞지 않는 타입: {type(value)}")


DEFAULT_LEXICON = LexiconBundle()


def count_similes(text: str) -> int:
    """비유 표현("마치 ~처럼", "~같이", "~듯이") 등장 횟수."""
    return sum(1 for _ in SIMILE_RE.finditer(text))


def count_onomatopoeia(text: str) -> int:
    """의성어/의태어 등장 횟수.

    ONOMATOPOEIA_RE는 분기 중 하나만 그룹을 쓰기 때문에 findall을 쓰면
    빈 문자열이 섞여 나온다. 세는 용도로는 항상 이 함수를 쓴다.
    """
    return sum(1 for _ in ONOMATOPOEIA_RE.finditer(text))


# --------------------------------------------------------------------------
# 사전 매칭
#
# 단순 부분문자열 검색은 한국어에서 위험하다. "커피"의 "피"가 부정어로,
# "실적"의 "적"이 적대 표현으로 잡힌다. 그래서 짧은 항목은 어절 시작
# 위치에서만 인정한다. 한국어 어간은 어절 앞머리에 오기 때문이다.
#
# 세 음절 이상은 그대로 찾는다. "이었다니"처럼 어미에 붙는 표현이 있고,
# 세 음절 이상이 다른 단어 안에 우연히 들어가는 경우는 드물다.
# --------------------------------------------------------------------------

#: 이 길이 이하인 항목은 어절 시작에서만 매칭한다.
ANCHOR_MAX_LEN = 2
_HANGUL_LOOKBEHIND = "(?<![\uac00-\ud7a3])"


@lru_cache(maxsize=256)
def _compile_vocabulary(words: frozenset[str]) -> re.Pattern[str] | None:
    """어휘 집합을 정규식 하나로 컴파일한다.

    긴 항목을 먼저 두어 "계약"보다 "계약서"가 있으면 긴 쪽이 잡히게 한다.
    """
    short = sorted((w for w in words if len(w) <= ANCHOR_MAX_LEN), key=len, reverse=True)
    long_ = sorted((w for w in words if len(w) > ANCHOR_MAX_LEN), key=len, reverse=True)
    parts: list[str] = []
    if long_:
        parts.append("(?:" + "|".join(map(re.escape, long_)) + ")")
    if short:
        parts.append(_HANGUL_LOOKBEHIND + "(?:" + "|".join(map(re.escape, short)) + ")")
    if not parts:
        return None
    return re.compile("|".join(parts))


def find_hits(text: str, vocabulary: set[str] | frozenset[str]) -> Counter[str]:
    """사전 단어가 실제로 등장한 표면형과 횟수.

    한 위치는 한 번만 센다. 부분문자열 중복 집계가 없다.
    """
    pattern = _compile_vocabulary(frozenset(vocabulary))
    if pattern is None or not text:
        return Counter()
    return Counter(m.group(0) for m in pattern.finditer(text))


def iter_hits(
    text: str, vocabulary: set[str] | frozenset[str]
) -> Iterator[tuple[int, str]]:
    """사전 단어가 등장한 (문자 위치, 표면형)을 앞에서부터 돌려준다."""
    pattern = _compile_vocabulary(frozenset(vocabulary))
    if pattern is None or not text:
        return
    for m in pattern.finditer(text):
        yield m.start(), m.group(0)


def first_hit_position(
    text: str, vocabulary: set[str] | frozenset[str]
) -> tuple[int, str] | None:
    """사전 단어가 처음 등장한 (문자 위치, 표면형). 없으면 None."""
    pattern = _compile_vocabulary(frozenset(vocabulary))
    if pattern is None or not text:
        return None
    m = pattern.search(text)
    return (m.start(), m.group(0)) if m else None


def count_hits(text: str, vocabulary: set[str] | frozenset[str]) -> int:
    """사전 단어가 본문에 등장한 총 횟수."""
    return sum(find_hits(text, vocabulary).values())


def weighted_hits(text: str, weights: dict[str, float]) -> tuple[float, Counter[str]]:
    """가중치 사전으로 점수와 등장 내역을 함께 계산한다."""
    hits = find_hits(text, frozenset(weights))
    score = sum(weights[word] * count for word, count in hits.items())
    return score, hits
