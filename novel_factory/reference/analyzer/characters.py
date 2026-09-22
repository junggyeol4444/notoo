"""캐릭터 구조 분석 (기획안 9번).

형태소 분석기도 NER 모델도 쓰지 않는다. 한국어의 두 가지 성질을 이용한다.

  1. 인명은 조사 앞에 온다. "도윤은", "서연이가", "민석에게"
  2. 인명에만 붙는 조사가 있다. 호격 "아/야", 존칭 "씨/님"

여기서 뽑는 것은 '이름 문자열'이지 '인물'이 아니다. 동명이인도,
별명도 구분하지 못한다. 그래도 등장 빈도·등장 간격·대사 점유율 같은
구조 지표를 내기에는 충분하다.

기획안 56번 셋째 원칙에 따라, 참고작의 고유명사는 새 작품으로 옮기지
않는다. 이름 문자열은 분석 과정에만 쓰고 Reference Profile에는
구조 지표만 남긴다.
"""

from __future__ import annotations

import re
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from novel_factory.reference.structure.splitter import Episode
from novel_factory.text.dialogue import Segment, SegmentKind, segment_text
from novel_factory.text.lexicon import DEFAULT_LEXICON, LexiconBundle, count_hits

# 한국 성씨. 3음절 후보의 첫 글자가 여기 있으면 인명일 확률이 크게 오른다.
SURNAMES: frozenset[str] = frozenset(
    "김이박최정강조윤장임한오서신권황안송류전홍고문양손배백허유남심노하곽성차주우구"
    "나민지엄채원천방공현함변염여추도소석선설마길연위표명기반왕금옥육인맹제모탁국어은편용"
)

# 인명 뒤에 붙는 조사. 긴 것부터 확인해야 "에게"가 "에"로 잘리지 않는다.
_PARTICLES: tuple[str, ...] = (
    # 복합 조사를 먼저 둔다. "이번에는"에서 "는"만 떼면 "이번에"가 남아
    # 후보로 올라온다. "에는"까지 떼야 "이번"이 되어 불용어에 걸린다.
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
# 호격/존칭. 이게 붙으면 사람이다.
_PERSON_PARTICLES: frozenset[str] = frozenset({"씨", "님", "께서", "께서는", "께서도"})
# 호격. 용언 어미 -아/-어/-야와 표기가 겹쳐서 따로 다룬다.
_VOCATIVE_PARTICLES: frozenset[str] = frozenset({"아", "야"})

# 대사 귀속에 쓰는 발화 동사
ATTRIBUTION_VERBS: tuple[str, ...] = (
    "말했", "물었", "대답했", "답했", "중얼", "외쳤", "소리쳤", "속삭", "내뱉",
    "덧붙", "이었다", "되물었", "읊조", "웃었", "한숨", "입을 열", "말을 이",
)

_EOJEOL_RE = re.compile(r"[\uac00-\ud7a3]+")
_HANGUL_ONLY_RE = re.compile(r"^[\uac00-\ud7a3]+$")

# 주격/주제 조사. 인물은 문장의 주어로 자주 온다.
_SUBJECT_PARTICLES: frozenset[str] = frozenset({"은", "는", "이", "가", "이는", "이가", "께서", "께서는"})

# 이 음절로 끝나는 어절은 서술어다. 인명이 아니다.
_PREDICATE_ENDINGS: tuple[str, ...] = (
    "다", "요", "까", "죠", "네", "군", "지", "자", "며", "고", "서", "면",
    "데", "니", "라", "래", "게", "듯", "봐", "줘", "쳐", "겠", "었", "았",
)
# 어간이 이 음절로 끝나면 용언 활용형이다. "결정해"(결정해야), "믿어"(믿어야).
# 한국 인명이 이 음절로 끝나는 경우는 드물어서 손실보다 이득이 크다.
_VERB_STEM_ENDINGS: tuple[str, ...] = ("해", "어", "여", "워", "러", "려", "되", "하", "취")

#: 인명 후보로 인정할 음절 수
MIN_NAME_LEN = 2
MAX_NAME_LEN = 4
#: 전체에서 이 횟수 미만이면 인물로 치지 않는다.
MIN_OCCURRENCES = 3
#: 이 회차 수 미만에만 나오면 단역으로 본다(주요 인물 목록에서 제외).
MIN_EPISODE_COVERAGE = 2
#: 대사 귀속을 찾을 때 볼 서술 글자 수
ATTRIBUTION_WINDOW = 40
#: 어절 뒤 몇 글자 안에 발화 동사가 있으면 '발화 근접'으로 본다.
ATTRIBUTION_LOOKAHEAD = 25
#: 발화동사 탐색을 끊는 문장 경계 문자
_SENTENCE_BREAKS = ".!?\u2026\n"

#: 인명으로 인정하기 위한 최소 증거 점수. 아래 _person_evidence() 참고.
PERSON_EVIDENCE_THRESHOLD = 2.0


@dataclass(slots=True)
class _Candidate:
    """인명 후보 원자료."""

    name: str
    occurrences: int = 0
    episode_seqs: list[int] = field(default_factory=list)
    particles: Counter[str] = field(default_factory=Counter)
    person_particle_hits: int = 0     # 씨/님/께서
    vocative_hits: int = 0            # 아/야 (용언 어미와 겹쳐서 따로 센다)
    attribution_hits: int = 0

    @property
    def has_surname_prefix(self) -> bool:
        return len(self.name) == 3 and self.name[0] in SURNAMES

    @property
    def subject_ratio(self) -> float:
        total = sum(self.particles.values())
        if total == 0:
            return 0.0
        subject = sum(c for p, c in self.particles.items() if p in _SUBJECT_PARTICLES)
        return subject / total


def _person_evidence(cand: _Candidate) -> float:
    """이 후보가 사람 이름일 근거의 강도.

    빈도만으로는 인명과 흔한 명사를 못 가른다. "회사는"도 "도윤은"만큼
    자주 나온다. 그래서 사람에게만 나타나는 신호에 점수를 몰아준다.

      - 호격/존칭 조사(아/야/씨/님/께서): 사람한테만 붙는다
      - 발화 동사 근접: "도윤이 말했다"
      - 성씨로 시작하는 3음절: 한국 인명의 전형
      - 주격 편중: 인물은 주어 자리에 자주 온다
    """
    score = 0.0
    if cand.person_particle_hits:
        score += 3.0 * min(cand.person_particle_hits, 3) / 3
    # "결정해야", "믿어야"의 -야는 호격이 아니라 어미다. 같은 후보가
    # 주격 조사와도 함께 쓰일 때만 호격으로 인정한다.
    if cand.vocative_hits and cand.subject_ratio >= 0.3:
        score += 2.0 * min(cand.vocative_hits, 3) / 3
    if cand.attribution_hits:
        score += 2.0 * min(cand.attribution_hits, 4) / 4
    if cand.has_surname_prefix:
        score += 1.5
    if sum(cand.particles.values()) >= 5 and cand.subject_ratio >= 0.5:
        score += 1.0
    return score


@dataclass(slots=True)
class CharacterStat:
    name: str
    occurrences: int
    episode_seqs: list[int]
    first_episode: int
    last_episode: int
    dialogue_lines: int
    dialogue_chars: int
    person_particle_hits: int
    negative_context: int
    score: float = 0.0
    role: str = "미분류"

    @property
    def episode_coverage(self) -> int:
        return len(set(self.episode_seqs))

    @property
    def appearance_gap(self) -> float:
        """등장 회차 사이의 평균 간격."""
        seqs = sorted(set(self.episode_seqs))
        if len(seqs) < 2:
            return 0.0
        return statistics.fmean(b - a for a, b in zip(seqs, seqs[1:]))

    def exit_style(self, last_story_seq: int) -> str:
        """퇴장 방식 추정 (기획안 9번 '캐릭터 퇴장 방식')."""
        if self.last_episode >= last_story_seq - 2:
            return "완주"
        span = self.last_episode - self.first_episode
        if span <= 2:
            return "단발등장"
        return "중도퇴장"

    def as_dict(self, last_story_seq: int) -> dict[str, object]:
        return {
            "name": self.name,
            "role": self.role,
            "occurrences": self.occurrences,
            "episode_coverage": self.episode_coverage,
            "first_episode": self.first_episode,
            "last_episode": self.last_episode,
            "appearance_gap": round(self.appearance_gap, 2),
            "dialogue_lines": self.dialogue_lines,
            "dialogue_chars": self.dialogue_chars,
            "exit_style": self.exit_style(last_story_seq),
            "score": round(self.score, 2),
        }


@dataclass(slots=True)
class CharacterProfile:
    characters: list[CharacterStat]
    protagonist_count: int
    supporting_count: int
    antagonist_count: int
    avg_appearance_gap: float
    dialogue_share: dict[str, float]
    introduction_schedule: list[tuple[int, str]] = field(default_factory=list)
    last_story_seq: int = 0

    def as_dict(self, *, include_names: bool = False) -> dict[str, object]:
        """구조 지표만 담는다.

        include_names=True는 관리자 화면에서 분석 결과를 눈으로 확인할 때만
        쓴다. 저장되는 Reference Profile에는 이름을 넣지 않는다.
        """
        payload: dict[str, object] = {
            "protagonist_count": self.protagonist_count,
            "supporting_count": self.supporting_count,
            "antagonist_count": self.antagonist_count,
            "total_named_characters": len(self.characters),
            "avg_appearance_gap": round(self.avg_appearance_gap, 2),
            "introduction_episodes": [seq for seq, _ in self.introduction_schedule],
            "dialogue_share_top": [
                round(v, 4)
                for _, v in sorted(self.dialogue_share.items(), key=lambda kv: -kv[1])[:8]
            ],
            "exit_styles": _count_exit_styles(self.characters, self.last_story_seq),
        }
        if include_names:
            payload["characters"] = [
                c.as_dict(self.last_story_seq) for c in self.characters
            ]
        return payload

    def role_chain(self) -> list[str]:
        """기획안 9번의 역할 사슬 표기."""
        order = {"주인공": 0, "주요조연": 1, "적대자": 2, "조연": 3, "단역": 4}
        return [
            f"{c.role}" for c in sorted(self.characters, key=lambda c: order.get(c.role, 9))
        ]


def _count_exit_styles(characters: list[CharacterStat], last_seq: int) -> dict[str, int]:
    counts: Counter[str] = Counter(c.exit_style(last_seq) for c in characters)
    return dict(counts)


def _strip_particle(eojeol: str) -> tuple[str, str] | None:
    """어절에서 조사를 떼어 (어간, 조사)를 돌려준다.

    조사가 붙지 않은 어절은 후보로 보지 않는다. 그 폴백을 열어 두면
    "있었다", "않았다" 같은 서술어가 전부 인명 후보로 들어온다.
    """
    if eojeol.endswith(_PREDICATE_ENDINGS):
        return None
    for particle in _PARTICLES:
        if len(eojeol) > len(particle) and eojeol.endswith(particle):
            stem = eojeol[: -len(particle)]
            if (
                MIN_NAME_LEN <= len(stem) <= MAX_NAME_LEN
                and not stem.endswith(_PREDICATE_ENDINGS)
                and not stem.endswith(_VERB_STEM_ENDINGS)
            ):
                return stem, particle
    return None


def _iter_candidates(text: str) -> "list[tuple[str, str, bool]]":
    """(어간, 조사, 발화동사 근접 여부) 목록."""
    out: list[tuple[str, str, bool]] = []
    for m in _EOJEOL_RE.finditer(text):
        hit = _strip_particle(m.group(0))
        if hit is None:
            continue
        # 같은 문장 안에서만 발화동사를 찾는다. 문장 경계를 넘으면
        # "공기가 무거웠다. 아무도 먼저 입을 열지 않았다"의 '입을 열'이
        # 앞 문장의 '공기'를 화자로 만들어 버린다.
        lookahead = text[m.end() : m.end() + ATTRIBUTION_LOOKAHEAD]
        for brk in _SENTENCE_BREAKS:
            cut = lookahead.find(brk)
            if cut != -1:
                lookahead = lookahead[:cut]
        near_speech = any(v in lookahead for v in ATTRIBUTION_VERBS)
        out.append((hit[0], hit[1], near_speech))
    return out


def _attribute_speaker(
    segments: list[Segment], index: int, names: set[str]
) -> str | None:
    """대사 세그먼트의 화자를 앞뒤 서술에서 찾는다."""
    for offset in (1, -1):
        j = index + offset
        while 0 <= j < len(segments):
            seg = segments[j]
            if seg.kind is not SegmentKind.NARRATION:
                break
            window = seg.text[:ATTRIBUTION_WINDOW] if offset == 1 else seg.text[-ATTRIBUTION_WINDOW:]
            if not any(v in window for v in ATTRIBUTION_VERBS):
                break
            for eojeol in _EOJEOL_RE.findall(window):
                hit = _strip_particle(eojeol)
                if hit and hit[0] in names:
                    return hit[0]
            break
    return None


def _merge_given_names(stats: dict[str, CharacterStat]) -> dict[str, CharacterStat]:
    """'김도윤'과 '도윤'을 한 인물로 합친다.

    3음절 후보의 첫 글자가 성씨이고 나머지 2음절이 따로 후보로 잡혀 있으면
    같은 인물로 본다. 한국 소설에서 매우 흔한 표기 혼용이다.
    """
    merged = dict(stats)
    for full in list(merged):
        if len(full) != 3 or full[0] not in SURNAMES:
            continue
        given = full[1:]
        if given not in merged or given == full:
            continue
        keep, drop = merged[full], merged.pop(given)
        keep.occurrences += drop.occurrences
        keep.episode_seqs.extend(drop.episode_seqs)
        keep.first_episode = min(keep.first_episode, drop.first_episode)
        keep.last_episode = max(keep.last_episode, drop.last_episode)
        keep.dialogue_lines += drop.dialogue_lines
        keep.dialogue_chars += drop.dialogue_chars
        keep.person_particle_hits += drop.person_particle_hits
        keep.negative_context += drop.negative_context
    return merged


def analyze_characters(
    episodes: list[Episode],
    *,
    lexicon: LexiconBundle | None = None,
    min_occurrences: int = MIN_OCCURRENCES,
) -> CharacterProfile:
    lex = lexicon or DEFAULT_LEXICON

    # 1차 통과: 후보와 그 근거를 모은다
    raw: dict[str, _Candidate] = {}
    for ep in episodes:
        for stem, particle, near_speech in _iter_candidates(ep.text):
            if stem in lex.name_stopwords or not _HANGUL_ONLY_RE.match(stem):
                continue
            cand = raw.get(stem)
            if cand is None:
                cand = _Candidate(name=stem)
                raw[stem] = cand
            cand.occurrences += 1
            cand.episode_seqs.append(ep.seq)
            cand.particles[particle] += 1
            if particle in _VOCATIVE_PARTICLES:
                cand.vocative_hits += 1
            elif particle in _PERSON_PARTICLES:
                cand.person_particle_hits += 1
            if near_speech:
                cand.attribution_hits += 1

    # 2차 통과: 인명 근거가 충분한 후보만 남긴다
    stats: dict[str, CharacterStat] = {}
    for name, cand in raw.items():
        if cand.occurrences < min_occurrences:
            continue
        if len(set(cand.episode_seqs)) < MIN_EPISODE_COVERAGE:
            continue
        if _person_evidence(cand) < PERSON_EVIDENCE_THRESHOLD:
            continue
        stats[name] = CharacterStat(
            name=name,
            occurrences=cand.occurrences,
            episode_seqs=list(cand.episode_seqs),
            first_episode=min(cand.episode_seqs),
            last_episode=max(cand.episode_seqs),
            dialogue_lines=0,
            dialogue_chars=0,
            person_particle_hits=cand.person_particle_hits + cand.vocative_hits,
            negative_context=0,
        )

    stats = _merge_given_names(stats)
    kept = stats
    names = set(kept)
    # 합쳐진 이름의 짧은 형태도 화자 인식에 쓴다.
    alias_to_canonical = {n: n for n in names}
    for name in list(names):
        if len(name) == 3 and name[0] in SURNAMES:
            alias_to_canonical[name[1:]] = name

    for ep in episodes:
        segments = segment_text(ep.text)
        for i, seg in enumerate(segments):
            if seg.kind is not SegmentKind.DIALOGUE:
                continue
            speaker = _attribute_speaker(segments, i, set(alias_to_canonical))
            if speaker is None:
                continue
            canonical = alias_to_canonical[speaker]
            st = kept.get(canonical)
            if st is not None:
                st.dialogue_lines += 1
                st.dialogue_chars += seg.length

    # 부정적 문맥 동시 등장 (적대자 추정용)
    for ep in episodes:
        neg = count_hits(ep.text, lex.negative)
        if neg == 0:
            continue
        for name, st in kept.items():
            if name in ep.text:
                st.negative_context += neg

    if not kept:
        return CharacterProfile([], 0, 0, 0, 0.0, {}, [], episodes[-1].seq if episodes else 0)

    # 점수: 등장 빈도 + 회차 커버리지 + 사람 조사 + 대사량
    max_occ = max(st.occurrences for st in kept.values())
    total_eps = max(len({e.seq for e in episodes}), 1)
    for st in kept.values():
        st.score = (
            2.0 * st.occurrences / max_occ
            + 1.5 * st.episode_coverage / total_eps
            + 0.5 * min(st.person_particle_hits, 10) / 10
            + 1.0 * (st.dialogue_lines / max(sum(k.dialogue_lines for k in kept.values()), 1))
        )

    ranked = sorted(kept.values(), key=lambda s: -s.score)
    _assign_roles(ranked, total_eps)

    last_seq = max(e.seq for e in episodes)
    total_dialogue = max(sum(st.dialogue_chars for st in ranked), 1)

    return CharacterProfile(
        characters=ranked,
        protagonist_count=sum(1 for s in ranked if s.role == "주인공"),
        supporting_count=sum(1 for s in ranked if s.role in ("주요조연", "조연")),
        antagonist_count=sum(1 for s in ranked if s.role == "적대자"),
        avg_appearance_gap=statistics.fmean(
            [s.appearance_gap for s in ranked if s.appearance_gap > 0] or [0.0]
        ),
        dialogue_share={s.name: s.dialogue_chars / total_dialogue for s in ranked},
        introduction_schedule=sorted((s.first_episode, s.name) for s in ranked),
        last_story_seq=last_seq,
    )


def _assign_roles(ranked: list[CharacterStat], total_eps: int) -> None:
    """역할 추정.

    적대자 판정은 신뢰도가 낮다. 부정 어휘가 많은 회차에 자주 등장한다는
    간접 신호뿐이라, 위기에 함께 휘말리는 동료도 적대자로 잡힐 수 있다.
    """
    if not ranked:
        return
    ranked[0].role = "주인공"

    if len(ranked) > 1:
        neg_rates = [
            s.negative_context / max(s.occurrences, 1) for s in ranked[1:]
        ]
        threshold = (
            statistics.fmean(neg_rates) + statistics.pstdev(neg_rates)
            if len(neg_rates) > 1
            else float("inf")
        )
        for s, rate in zip(ranked[1:], neg_rates):
            coverage = s.episode_coverage / total_eps
            if rate > threshold and coverage >= 0.15:
                s.role = "적대자"
            elif coverage >= 0.35:
                s.role = "주요조연"
            elif coverage >= 0.10:
                s.role = "조연"
            else:
                s.role = "단역"
