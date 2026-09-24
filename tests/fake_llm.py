"""테스트용 대본 LLM.

실제 모델 없이 집필 파이프라인 전체를 돌리기 위한 것이다. 프롬프트의 시스템
메시지로 단계(Arc / 회차 / 장면 / 집필 / 기억 갱신)를 알아보고, 프롬프트에 적힌
지시(인물 코드, 클리프행어 유형, 장면 수, 목표 분량)를 읽어 그에 맞는 답을 준다.

실제 로컬 모델의 버릇도 흉내 낸다.
  - 구조화 응답에 <think> 블록과 코드펜스를 붙인다
  - messy=True면 단계마다 첫 응답을 규격에 안 맞게 내서 재시도를 타게 한다
  - Logic·Reader 검사에서 원고에 없는 문장을 인용하고, 없는 kind를 섞는다

품질 검사 시험용
  plant      첫 장면 초고 끝에 이 문장을 한 번 넣는다 (금지 표현, 논리 문제 등)
  logic_flag 원고에 이 문장이 있으면 Logic 검사가 high로 지적한다
  fix_text   장면 고치기 요청에 늘 이 글을 준다 (고친 판본이 더 나쁜 경우 시험용)
"""

from __future__ import annotations

import json
import re
from collections import Counter
from typing import ClassVar

from novel_factory.generation.prompts import (
    ARC_SYSTEM,
    BLURB_SYSTEM,
    COMPLETION_SYSTEM,
    COVER_SYSTEM,
    EPISODE_SYSTEM,
    LOGIC_SYSTEM,
    MEMORY_SYSTEM,
    READER_SYSTEM,
    SCENE_SYSTEM,
    WRITER_SYSTEM,
)
from novel_factory.llm.base import Completion, LLMProvider, Message

_PROSE_POOL = [
    "도윤은 서류철을 덮고 창밖을 내려다보았다.",
    '"이번 계약은 제가 직접 들고 가겠습니다."',
    "회의실 공기가 한층 무거워졌다.",
    "서연은 대답 대신 메모지에 숫자 몇 개를 적었다.",
    "'여기서 밀리면 끝이다.'",
    "엘리베이터 문이 닫히자 복도의 소음이 끊겼다.",
    '"조건은 하나입니다. 기한을 지키세요."',
    "비가 그친 거리에 젖은 간판 불빛이 번졌다.",
    "그는 휴대폰 화면을 한참 들여다보다 뒤집어 놓았다.",
    '"그 사람, 어제 누구를 만났는지 알아봐 주세요."',
    "책상 위의 커피는 이미 식어 있었다.",
    "민석이 문을 두드리고 고개만 들이밀었다.",
]


def _stage(messages: list[Message]) -> str:
    system = messages[0].content if messages and messages[0].role == "system" else ""
    for name, prompt in (
        ("arc", ARC_SYSTEM),
        ("episode", EPISODE_SYSTEM),
        ("scene", SCENE_SYSTEM),
        ("writer", WRITER_SYSTEM),
        ("memory", MEMORY_SYSTEM),
        ("logic", LOGIC_SYSTEM),
        ("reader", READER_SYSTEM),
        ("cover", COVER_SYSTEM),
        ("blurb", BLURB_SYSTEM),
        ("completion", COMPLETION_SYSTEM),
    ):
        if system == prompt:
            return name
    return "unknown"


def _wrap(payload: dict[str, object]) -> str:
    """실제 로컬 모델처럼 포장해서 준다."""
    body = json.dumps(payload, ensure_ascii=False, indent=2)
    return (
        f"<think>지시를 확인한다.</think>\n다음과 같이 정리했습니다.\n```json\n{body}\n```"
    )


def _prose(target: int, seed: int) -> str:
    out: list[str] = []
    size = 0
    i = seed
    while size < target:
        sentence = _PROSE_POOL[i % len(_PROSE_POOL)]
        out.append(sentence)
        size += len(sentence.replace(" ", ""))
        i += 1
    return "\n\n".join(out)


class ScriptedNovelist(LLMProvider):
    name = "scripted"

    def __init__(
        self,
        *,
        messy: bool = False,
        short_first: bool = False,
        copy_text: str | None = None,
        plant: str | None = None,
        logic_flag: str | None = None,
        fix_text: str | None = None,
        completion_problems: bool = False,
    ) -> None:
        self.completion_problems = completion_problems
        self.messy = messy
        self.plant = plant
        self.logic_flag = logic_flag
        self.fix_text = fix_text
        self.short_first = short_first
        self.copy_text = copy_text  # 집필 첫 응답을 이 글로 (유사도 재작성 시험용)
        self.calls: list[tuple[str, list[Message], dict[str, object]]] = []
        self.stage_counts: Counter[str] = Counter()
        self._seed = 0

    def complete(
        self,
        messages: list[Message],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        stop: list[str] | None = None,
        json_mode: bool = False,
    ) -> Completion:
        stage = _stage(messages)
        self.stage_counts[stage] += 1
        self.calls.append(
            (
                stage,
                list(messages),
                {
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                    "json_mode": json_mode,
                },
            )
        )
        last = messages[-1].content
        is_retry = last.startswith("앞의 답을 쓸 수 없습니다")
        # 지시는 첫 요청에 있다. 재요청·이어쓰기 메시지는 마지막에 붙는다.
        first = next((m.content for m in messages if m.role == "user"), "")
        user = last if last.startswith(("# 이어 쓰기", "# 장면 다시 쓰기")) else first
        text = getattr(self, f"_{stage}")(user, messages, is_retry)
        return Completion(text=text, model="scripted", finish_reason="stop")

    # ------------------------------------------------------------------
    def _messy_first(self, is_retry: bool) -> bool:
        return self.messy and not is_retry

    def _arc(self, user: str, messages: list[Message], is_retry: bool) -> str:
        if self._messy_first(is_retry):
            return '{"arcs": []}'
        orders = [int(x) for x in re.findall(r'"order":\s*(\d+)', user)]
        return _wrap(
            {
                "arcs": [
                    {
                        "order": o,
                        "name": f"제{o}막",
                        "goal": f"{o}막의 목표",
                        "conflict": "대성그룹의 견제",
                        "resolution": "한 걸음 전진",
                        "emotion_target": "불안→반격",
                        "major_events": ["판을 뒤집는 계약"],
                    }
                    for o in orders
                ]
            }
        )

    def _episode(self, user: str, messages: list[Message], is_retry: bool) -> str:
        if self._messy_first(is_retry):
            return '{"title": "제목만 있음"}'
        codes_line = re.search(r"인물 코드: ([^\n(]+)", user)
        codes = re.findall(r"C\d{3}", codes_line.group(1)) if codes_line else []
        hook = re.search(r"회차 끝을 '([^']+)' 유형", user)
        number = int(re.search(r"# 이번 화 지시 \((\d+)화", user).group(1))  # type: ignore[union-attr]
        open_fs = re.search(r"살아 있는 복선 코드: ([^\n(]+)", user)
        fs_codes = re.findall(r"F\d{3}", open_fs.group(1)) if open_fs else []
        return _wrap(
            {
                "title": f"{number}화의 거래",
                "purpose": "주인공이 첫 인수 대상을 좁힌다",
                "required_events": ["실사 자료 확보", "상대의 견제"],
                "characters": [*codes[:2], "C999"],  # 없는 코드 하나 섞음
                "emotion_flow": "긴장",  # 목록 대신 문자열 (로컬 모델에서 흔함)
                "foreshadowings_used": [*fs_codes[:1], "F999"],
                "new_foreshadowings": [{"description": f"{number}화의 검은 봉투"}],
                "reward": "첫 단서",
                "conflict": "시간 압박",
                "hook": {"type": "반전" if hook else "", "content": "봉투 속 이름"},
            }
        )

    def _scene(self, user: str, messages: list[Message], is_retry: bool) -> str:
        count = int(re.search(r"정확히 (\d+)개", user).group(1))  # type: ignore[union-attr]
        if self._messy_first(is_retry):
            count = max(1, count - 1)
        codes = re.findall(r'"(C\d{3})"', user)[:2]
        return _wrap(
            {
                "scenes": [
                    {
                        "purpose": f"장면 {i + 1}의 목적",
                        "characters": codes,
                        "location": "사무실",
                        "conflict": "정보를 누가 먼저 쥐는가",
                        "emotion": "긴장",
                        "beats": ["서류 확인", "전화"],
                    }
                    for i in range(count)
                ]
            }
        )

    def _writer(self, user: str, messages: list[Message], is_retry: bool) -> str:
        self._seed += 3
        if user.startswith("# 이어 쓰기"):
            missing = int(re.search(r"약 ([\d,]+)자 짧다", user).group(1).replace(",", ""))  # type: ignore[union-attr]
            return _prose(missing, self._seed)
        if user.startswith("# 장면 다시 쓰기"):
            return "## 다시 쓴 장면\n\n" + _prose(900, self._seed + 5).replace("도윤", "그")
        target = int(
            re.search(r"공백 제외 약 ([\d,]+)자로", user).group(1).replace(",", "")
        )  # type: ignore[union-attr]
        if user.startswith("# 장면 고치기"):
            if self.fix_text is not None:
                return self.fix_text
            return "## 고친 장면\n\n" + _prose(target, self._seed + 7)
        if self.copy_text is not None:
            copied, self.copy_text = self.copy_text, None
            return copied
        if self.short_first:
            self.short_first = False
            return "## 장면\n\n" + _prose(target // 3, self._seed)
        body = _prose(target, self._seed)
        if self.plant is not None:
            body, self.plant = f"{body}\n\n{self.plant}", None
        return "## 장면\n\n" + body

    def _memory(self, user: str, messages: list[Message], is_retry: bool) -> str:
        codes = re.findall(r'"code":\s*"(C\d{3})"', user)
        fs = re.findall(r'"code":\s*"(F\d{3})"', user)
        number = int(re.search(r"# (\d+)화 원고", user).group(1))  # type: ignore[union-attr]
        hook = re.search(r'"hook_type":\s*"([^"]*)"', user)
        delta: dict[str, object] = {
            "summary": f"{number}화: 도윤이 실사 자료를 확보하고 상대의 견제를 확인했다.",
            "new_characters": [{"name": f"조연{number}", "role": "조연", "job": "변호사"}],
            "world": [
                {"category": "회사", "name": f"자회사{number}", "description": "인수 후보"}
            ],
            "timeline": [
                {
                    "title": f"{number}화 실사",
                    "occurred_at": f"2026-03-{number:02d}",
                    "participants": codes[:1],
                    "importance": 3,
                }
            ],
            "relationships": (
                [
                    {
                        "source": codes[0],
                        "target": codes[1],
                        "state": "협력",
                        "intensity": 0.3,
                    }
                ]
                if len(codes) >= 2
                else []
            )
            + [
                {
                    "source": "없는사람",
                    "target": codes[0] if codes else "x",
                    "state": "적대",
                }
            ],
            "knowledge": (
                [
                    {
                        "character": codes[1],
                        "fact_key": f"clue_{number}",
                        "fact": "봉투의 존재",
                    }
                ]
                if len(codes) >= 2
                else []
            ),
            "items": [{"character": codes[0], "item": "검은 봉투"}] if codes else [],
            "deaths": [],
            "new_foreshadowings": [],
            "advanced_foreshadowings": fs[:1],
            "resolved_foreshadowings": ["F999"],
            "hook_type": hook.group(1) if hook else "",
        }
        return _wrap(delta)

    def _logic(self, user: str, messages: list[Message], is_retry: bool) -> str:
        if self._messy_first(is_retry):
            return "문제가 몇 가지 보입니다."
        manuscript = user.split("화 원고\n", 1)[1].split("\n\n# 찾을 문제", 1)[0]
        issues: list[dict[str, object]] = [
            {
                "kind": "coincidence",
                "quote": "원고 어디에도 없는 지어낸 문장이다.",
                "explanation": "우연",
                "severity": "high",
            },
            {
                "kind": "문체",  # 정해 둔 종류가 아니다
                "quote": manuscript.split("\n")[0],
                "explanation": "문장이 밋밋하다",
                "severity": "low",
            },
        ]
        if self.logic_flag and self.logic_flag in manuscript:
            issues.append(
                {
                    "kind": "knowledge_leak",
                    "quote": f"“{self.logic_flag}”",  # 둥근 따옴표로 감싸 옴
                    "explanation": "서연은 아직 이 사실을 모른다.",
                    "severity": "high",
                }
            )
        return _wrap({"issues": issues})

    def _reader(self, user: str, messages: list[Message], is_retry: bool) -> str:
        persona = user.split("\n")[1].split(":")[0]
        manuscript = user.split("화 원고\n", 1)[1].split("\n\n# 점수 항목", 1)[0]
        first = manuscript.split("\n")[0]
        return _wrap(
            {
                "scores": {
                    "immersion": "7점",
                    "pacing": "6/10",
                    "character_appeal": 8,
                    "conflict": 5.5,
                    "reward": 12,  # 범위 밖 → 10으로
                    "cliffhanger": 7,
                },
                "boring_parts": [
                    {"quote": first, "reason": f"{persona}에게는 도입이 늘어진다"},
                    {"quote": "없는 문장을 지루하다고 한다.", "reason": "지어냄"},
                ],
                "comment": "무난하다",
            }
        )

    def _cover(self, user: str, messages: list[Message], is_retry: bool) -> str:
        return _wrap(
            {
                "prompt": "digital painting, a man in a dark suit on a rooftop at night, "
                "city lights below, cinematic lighting, empty lower third",
                "negative_prompt": "blurry, extra fingers",  # text가 빠져 있다
            }
        )

    def _blurb(self, user: str, messages: list[Message], is_retry: bool) -> str:
        return _wrap(
            {
                "description": "죽은 인수합병가가 회귀한다. 이번에는 판을 먼저 짠다. "
                "첫 거래가 시작된다.",
                "keywords": "회귀",  # 목록 대신 문자열
                "categories": ["현대판타지"],
            }
        )

    def _completion(self, user: str, messages: list[Message], is_retry: bool) -> str:
        if not self.completion_problems:
            return _wrap(
                {
                    "unresolved_conflicts": [],
                    # 없는 회차를 가리키는 지적은 버려져야 한다
                    "setting_conflicts": [
                        {"description": "지어낸 충돌", "episodes": [999]}
                    ],
                    "ending_consistent": True,
                    "ending_issues": [],
                }
            )
        return _wrap(
            {
                "unresolved_conflicts": [
                    {
                        "conflict": "대성그룹과의 대결",
                        "reason": "결말까지 언급 없음",
                        "severity": "high",
                    }
                ],
                "setting_conflicts": [
                    {"description": "회사 이름이 바뀜", "episodes": [1, 2]}
                ],
                "ending_consistent": False,
                "ending_issues": ["계획한 결말은 복수 완성인데 화해로 끝남"],
            }
        )

    def _unknown(self, user: str, messages: list[Message], is_retry: bool) -> str:
        return "무슨 요청인지 모르겠습니다."


class FakeEmbedder:
    """결정적인 가짜 임베더.

    음절 2-gram을 해시해 벡터로 만든다. 단, SYNONYMS에 있는 낱말은 같은 개념
    토큰으로 바꾼 뒤에 만든다. 그래서 '협약'으로 찾으면 '계약'이 든 장면이 걸린다.
    어휘 검색은 이걸 못 한다. 테스트가 의미 검색 경로를 실제로 탔는지 가르는 데 쓴다.
    """

    SYNONYMS: ClassVar[dict[str, str]] = {
        "계약": "개념가",
        "협약": "개념가",
        "약정": "개념가",
    }
    DIM = 64

    def __init__(self, model: str = "fake-embed") -> None:
        self.model = model
        self.calls = 0

    def embed(self, texts: list[str]) -> list[list[float]]:
        import hashlib
        import math

        self.calls += 1
        out = []
        for text in texts:
            for word, concept in self.SYNONYMS.items():
                text = text.replace(word, concept)
            vec = [0.0] * self.DIM
            for word in re.findall(r"[가-힣A-Za-z0-9]+", text):
                grams = (
                    [word]
                    if len(word) == 1
                    else [word[i : i + 2] for i in range(len(word) - 1)]
                )
                for g in grams:
                    h = int(hashlib.md5(g.encode()).hexdigest(), 16)
                    vec[h % self.DIM] += 1.0
            norm = math.sqrt(sum(v * v for v in vec)) or 1.0
            out.append([v / norm for v in vec])
        return out

    def close(self) -> None:
        pass
