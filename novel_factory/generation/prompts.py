"""집필 단계별 프롬프트.

모든 프롬프트에 공통으로 들어가는 원칙 (기획안 21·31·56번)
  1. Novel Bible이 최우선이다. 참고 패턴은 구조 지침일 뿐이다.
  2. 참고작의 문장·인물·고유명사·줄거리를 가져오지 않는다.
  3. 인물은 자기가 모르는 정보를 말하거나 행동의 근거로 쓰지 않는다.

구조화 응답을 요구하는 프롬프트는 JSON 예시를 그대로 보여 준다. 로컬 모델은
설명보다 예시를 더 잘 따른다.
"""

from __future__ import annotations

import json
from collections.abc import Iterable

PRINCIPLES = """\
# 반드시 지킬 원칙
1. '작품 기준(Novel Bible)'이 최우선이다. 참고 패턴과 충돌하면 작품 기준을 따른다.
2. 참고 패턴은 구조 지침일 뿐이다. 다른 작품의 문장, 인물, 고유명사, 줄거리를 가져오지 않는다.
3. '정보 제한'에 적힌 사실을 그 인물이 말하거나 행동의 근거로 쓰게 하지 않는다.
4. 이미 정해진 설정(이름, 나이, 관계, 사망 여부, 시간선)과 모순되는 내용을 만들지 않는다."""


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


def _bullets(items: Iterable[str]) -> str:
    lines = [f"- {item}" for item in items if item]
    return "\n".join(lines) if lines else "- (없음)"


# ---------------------------------------------------------------------------
# Arc (기획안 28번)
# ---------------------------------------------------------------------------
ARC_SYSTEM = f"""\
당신은 한국 장편 웹소설의 전체 구조를 설계하는 기획자다.
회차 범위와 대형 사건 위치는 이미 정해져 있다. 당신은 각 Arc의 이름, 목표, 갈등,
해소, 감정 목표, 대형 사건 내용만 채운다.

{PRINCIPLES}

설명 없이 JSON 하나만 출력한다."""


def build_arc_prompt(
    bible_block: str,
    skeleton: list[dict[str, object]],
    pattern_instructions: list[str],
) -> str:
    example = {
        "arcs": [
            {
                "order": 1,
                "name": "Arc 이름",
                "goal": "이 Arc에서 주인공이 이루려는 것",
                "conflict": "가로막는 힘",
                "resolution": "Arc가 끝날 때의 결과",
                "emotion_target": "불안→반격",
                "major_events": ["대형 사건 1의 내용", "대형 사건 2의 내용"],
            }
        ]
    }
    return f"""{bible_block}

# 정해진 Arc 골격 (회차 범위와 대형 사건 회차는 바꾸지 말 것)
{_json(skeleton)}

# 참고 패턴 (구조 지침)
{_bullets(pattern_instructions)}

# 할 일
골격의 Arc마다 내용을 채운다. major_events는 골격의 major_event_episodes와
같은 개수, 같은 순서로 쓴다. 마지막 Arc는 작품의 최종 결말로 이어져야 한다.

# 출력 형식 (이 모양 그대로)
{_json(example)}"""


# ---------------------------------------------------------------------------
# Episode (기획안 29번)
# ---------------------------------------------------------------------------
EPISODE_SYSTEM = f"""\
당신은 한국 장편 웹소설의 회차를 설계하는 작가다. 이번 화 한 편의 계획을 세운다.
주어진 '이번 화 지시'는 작품 전체 리듬을 위해 계산된 값이므로 반드시 따른다.

{PRINCIPLES}

설명 없이 JSON 하나만 출력한다."""

EVENT_SCALE_TEXT = {
    "major": "대형 사건 회차다. 판도가 바뀌는 사건이 일어나야 한다.",
    "minor": "중형 사건 회차다. 눈에 띄는 진전이나 충돌이 하나 있어야 한다.",
    "rest": "숨 고르기 회차다. 관계·정보·준비를 쌓되 지루하지 않게 작은 긴장을 둔다.",
}


def build_episode_prompt(
    context_block: str,
    *,
    episode_number: int,
    event_kind: str,
    hook_required: bool,
    hook_kind: str | None,
    hook_reason: str,
    target_chars: int,
    due_foreshadowings: list[str],
    cast_codes: list[str],
    open_foreshadowing_codes: list[str],
) -> str:
    hook_line = (
        f"회차 끝을 '{hook_kind}' 유형의 클리프행어로 끊는다. ({hook_reason})"
        if hook_required
        else f"이번 화는 클리프행어 없이 여운으로 끝낸다. ({hook_reason})"
    )
    example = {
        "title": "회차 제목",
        "purpose": "이 회차가 작품 전체에서 하는 일",
        "required_events": ["반드시 일어나야 할 사건 1", "사건 2"],
        "characters": cast_codes[:3] or ["C001"],
        "emotion_flow": ["긴장", "의심", "결단"],
        "foreshadowings_used": open_foreshadowing_codes[:1],
        "new_foreshadowings": [
            {"description": "새로 심을 복선", "planned_payoff": episode_number + 20}
        ],
        "reward": "독자가 이번 화에서 얻는 만족",
        "conflict": "이번 화의 갈등",
        "hook": {"type": hook_kind or "", "content": "마지막 장면의 훅 내용"},
    }
    return f"""{context_block}

# 이번 화 지시 ({episode_number}화, 계산된 값이므로 따를 것)
- 사건 규모: {EVENT_SCALE_TEXT.get(event_kind, event_kind)}
- 마무리: {hook_line}
- 목표 분량: 공백 제외 약 {target_chars:,}자
- 이번 화나 가까운 회차에 회수해야 할 복선: {", ".join(due_foreshadowings) or "없음"}

# 쓸 수 있는 코드
- 인물 코드: {", ".join(cast_codes) or "없음"} (characters에는 이 코드만 쓴다)
- 살아 있는 복선 코드: {", ".join(open_foreshadowing_codes) or "없음"} (foreshadowings_used에는 이 코드만 쓴다)
- 새 인물이 필요하면 characters에 넣지 말고 required_events에 등장을 적는다.

# 출력 형식 (이 모양 그대로)
{_json(example)}"""


# ---------------------------------------------------------------------------
# Scene (기획안 30번)
# ---------------------------------------------------------------------------
SCENE_SYSTEM = f"""\
당신은 회차 계획을 장면 단위로 나누는 작가다. 장면 수와 장면별 분량은 정해져 있다.

{PRINCIPLES}

설명 없이 JSON 하나만 출력한다."""


def build_scene_prompt(
    context_block: str,
    episode_plan: dict[str, object],
    *,
    scene_count: int,
    scene_roles: list[str],
) -> str:
    example = {
        "scenes": [
            {
                "purpose": "이 장면이 하는 일",
                "characters": ["C001"],
                "location": "장소",
                "conflict": "장면의 갈등",
                "emotion": "장면의 감정",
                "beats": ["일어나는 일 1", "일어나는 일 2"],
            }
        ]
    }
    roles = "\n".join(f"- 장면 {i + 1}: {role}" for i, role in enumerate(scene_roles))
    return f"""{context_block}

# 이번 화 계획
{_json(episode_plan)}

# 장면 구성 (정확히 {scene_count}개)
{roles}

마지막 장면은 계획의 hook으로 끝나야 한다. characters에는 인물 코드만 쓴다.

# 출력 형식 (이 모양 그대로, scenes는 {scene_count}개)
{_json(example)}"""


# ---------------------------------------------------------------------------
# Writer (기획안 31번)
# ---------------------------------------------------------------------------
WRITER_SYSTEM = f"""\
당신은 한국 장편 웹소설 작가다. 주어진 장면 하나를 소설 본문으로 쓴다.

{PRINCIPLES}

# 본문 규칙
- 소설 본문만 출력한다. 제목, 장면 번호, 해설, 마크다운을 붙이지 않는다.
- 대사는 큰따옴표("…"), 속마음은 작은따옴표('…')로 쓴다.
- 문단을 짧게 나누고 문단 사이는 빈 줄로 띄운다.
- 앞 장면에서 이어지는 부분을 반복하지 말고 바로 다음부터 쓴다."""


def build_scene_writing_prompt(
    context_block: str,
    episode_plan: dict[str, object],
    scene: dict[str, object],
    *,
    scene_index: int,
    scene_count: int,
    target_chars: int,
    previous_tail: str,
    hook: dict[str, object] | None,
    avoid_phrases: list[str],
    dialogue_ratio: float,
) -> str:
    parts = [
        context_block,
        f"# 이번 화 계획\n{_json(episode_plan)}",
        f"# 지금 쓸 장면 ({scene_index + 1}/{scene_count})\n{_json(scene)}",
        "# 분량과 문체\n"
        f"- 공백 제외 약 {target_chars:,}자로 쓴다.\n"
        f"- 대사 비중은 약 {dialogue_ratio:.0%} 안팎.",
    ]
    if avoid_phrases:
        parts.append(
            "# 최근 회차에서 과하게 쓴 표현 (이번 장면에서 쓰지 말 것)\n"
            + _bullets(avoid_phrases)
        )
    if hook and scene_index == scene_count - 1:
        parts.append(
            "# 마무리\n"
            f"이 장면이 회차의 끝이다. '{hook.get('type')}' 유형으로 끊는다: {hook.get('content')}"
        )
    if previous_tail:
        parts.append(
            f"# 바로 앞 원고 (여기서 이어서 쓴다. 반복하지 말 것)\n{previous_tail}"
        )
    parts.append("이제 장면 본문을 쓴다.")
    return "\n\n".join(parts)


def build_continue_prompt(current_text_tail: str, *, missing_chars: int) -> str:
    return (
        "# 이어 쓰기\n"
        f"장면이 목표보다 약 {missing_chars:,}자 짧다. 아래 원고의 바로 다음부터 이어서 쓴다.\n"
        "이미 쓴 내용을 반복하지 말고, 장면의 목적과 비트를 끝까지 채운다.\n"
        "본문만 출력한다.\n\n"
        f"# 지금까지의 원고 끝부분\n{current_text_tail}"
    )


def build_rewrite_prompt(scene_text: str, *, reason: str) -> str:
    return (
        "# 장면 다시 쓰기\n"
        f"아래 장면을 다시 쓴다. 이유: {reason}\n"
        "사건과 인물, 장면의 목적은 그대로 두고 문장과 전개 방식을 새로 쓴다.\n"
        "원래 장면의 문장을 그대로 쓰지 않는다. 본문만 출력한다.\n\n"
        f"# 원래 장면\n{scene_text}"
    )


# ---------------------------------------------------------------------------
# Memory Update (기획안 39번)
# ---------------------------------------------------------------------------
MEMORY_SYSTEM = """\
당신은 연재 소설의 설정 관리자다. 방금 완성된 회차 원고를 읽고, 장기 기억 DB에
새로 기록해야 할 사실만 뽑는다.

# 규칙
- 원고에 실제로 쓰인 것만 적는다. 추측하거나 앞으로의 전개를 지어내지 않는다.
- 인물은 주어진 코드로 가리킨다. 목록에 없는 인물이 새로 등장했으면 new_characters에 넣는다.
- 복선 코드는 주어진 목록에 있는 것만 쓴다.
- 해당 사항이 없는 항목은 빈 목록으로 둔다.

설명 없이 JSON 하나만 출력한다."""


def build_memory_prompt(
    *,
    episode_number: int,
    text: str,
    cast: list[dict[str, object]],
    open_foreshadowings: list[dict[str, object]],
    planned_hook_type: str | None,
) -> str:
    example = {
        "summary": "이번 화 줄거리 3~5문장",
        "new_characters": [
            {
                "name": "새 인물",
                "role": "조연",
                "job": "",
                "personality": [],
                "speech_style": "",
            }
        ],
        "world": [
            {"category": "회사", "name": "새로 나온 조직/장소", "description": "설명"}
        ],
        "timeline": [
            {
                "title": "일어난 사건",
                "occurred_at": "작중 날짜나 시점",
                "participants": ["C001"],
                "importance": 3,
            }
        ],
        "relationships": [
            {
                "source": "C001",
                "target": "C002",
                "state": "협력",
                "intensity": 0.3,
                "note": "계기",
            }
        ],
        "knowledge": [
            {
                "character": "C002",
                "fact_key": "짧은_영문_키",
                "fact": "새로 알게 된 사실",
                "knows": True,
            }
        ],
        "items": [{"character": "C001", "item": "물건", "action": "gained"}],
        "deaths": [],
        "new_foreshadowings": [{"description": "새로 심어진 복선", "planned_payoff": None}],
        "advanced_foreshadowings": ["F001"],
        "resolved_foreshadowings": [],
        "hook_type": planned_hook_type or "",
    }
    return f"""# {episode_number}화 원고
{text}

# 등장인물 코드
{_json(cast)}

# 살아 있는 복선
{_json(open_foreshadowings)}

# 클리프행어 유형
원고의 마지막이 실제로 어떤 유형으로 끝났는지 hook_type에 적는다.
유형: 정보공개, 위기발생, 새로운적, 반전, 약속, 미스터리, 보상직전, 전투직전, 없음

# 출력 형식 (이 모양 그대로)
{_json(example)}"""


# ---------------------------------------------------------------------------
# Logic 검사 (기획안 35번)
# ---------------------------------------------------------------------------
LOGIC_SYSTEM = """\
당신은 연재 소설의 편집자다. 회차 원고를 읽고 논리 문제만 찾는다.
문장이 좋은지, 재미있는지는 평가하지 않는다.

# 규칙
- 원고에 실제로 있는 문제만 적는다. 문제가 없으면 issues를 빈 목록으로 둔다.
- quote에는 문제가 드러나는 원고 구절을 한 글자도 바꾸지 말고 그대로 옮긴다.
  한 문장이면 충분하다. 원고에 없는 문장을 quote로 쓰면 그 지적은 버려진다.
- severity는 독자가 바로 알아챌 만큼 분명한 문제면 high, 애매하면 low.
- '작품 기준'과 '정보 제한', 시간선에 적힌 사실을 근거로 판단한다.

설명 없이 JSON 하나만 출력한다."""


def build_logic_prompt(
    context_block: str,
    *,
    episode_number: int,
    episode_plan: dict[str, object],
    text: str,
    kinds: dict[str, str],
) -> str:
    example = {
        "issues": [
            {
                "kind": next(iter(kinds)),
                "quote": "원고에서 그대로 옮긴 문장",
                "explanation": "왜 문제인지 한두 문장",
                "severity": "high",
            }
        ]
    }
    kind_lines = "\n".join(f"- {k}: {v}" for k, v in kinds.items())
    return f"""{context_block}

# 이번 화 계획
{_json(episode_plan)}

# {episode_number}화 원고
{text}

# 찾을 문제 (kind)
{kind_lines}

# 출력 형식 (이 모양 그대로)
{_json(example)}"""


# ---------------------------------------------------------------------------
# Reader Simulation (기획안 37번)
# ---------------------------------------------------------------------------
READER_SYSTEM = """\
당신은 연재 웹소설을 읽는 독자다. 주어진 독자 성향대로 방금 읽은 회차를 평가한다.
고칠 방법을 제안하지 말고, 읽으면서 느낀 대로 점수를 매긴다.

# 규칙
- 점수는 0~10 사이 숫자다.
- boring_parts의 quote에는 지루했던 구간의 원고 문장을 그대로 옮긴다. 없으면 빈 목록.

설명 없이 JSON 하나만 출력한다."""

READER_PERSONAS: dict[str, str] = {
    "웹소설 독자": "모바일로 매일 연재를 따라 읽는다. 전개가 늘어지면 바로 이탈한다.",
    "판타지 독자": "능력 체계와 세계관의 일관성, 성장과 보상의 쾌감을 중시한다.",
    "로맨스 독자": "인물 사이의 감정선과 관계 변화, 설렘을 중시한다.",
    "까다로운 독자": "개연성과 문장, 클리셰에 민감하다. 쉽게 높은 점수를 주지 않는다.",
}


def build_reader_prompt(persona: str, *, episode_number: int, genre: str, text: str) -> str:
    example = {
        "scores": {
            "immersion": 7,
            "pacing": 6,
            "character_appeal": 7,
            "conflict": 5,
            "reward": 6,
            "cliffhanger": 8,
        },
        "boring_parts": [{"quote": "지루했던 원고 문장", "reason": "이유"}],
        "comment": "한두 문장 감상",
    }
    description = READER_PERSONAS.get(persona, "")
    return f"""# 당신은
{persona}{f": {description}" if description else ""}

# 작품 장르
{genre or "미정"}

# {episode_number}화 원고
{text}

# 점수 항목
- immersion: 몰입도
- pacing: 전개속도 (늘어지지 않는가)
- character_appeal: 캐릭터 매력
- conflict: 갈등
- reward: 보상 (읽은 보람, 사이다)
- cliffhanger: 다음 화를 보고 싶은가

# 출력 형식 (이 모양 그대로)
{_json(example)}"""


# ---------------------------------------------------------------------------
# 자동 수정 (기획안 38번)
# ---------------------------------------------------------------------------
def build_fix_prompt(
    context_block: str,
    episode_plan: dict[str, object],
    scene: dict[str, object],
    *,
    scene_index: int,
    scene_count: int,
    target_chars: int,
    scene_text: str,
    problems: list[str],
    previous_tail: str,
    next_head: str,
    hook: dict[str, object] | None,
) -> str:
    parts = [
        "# 장면 고치기\n"
        "아래 장면에서 검사에 걸린 문제를 고쳐 장면 전체를 다시 쓴다.\n"
        "문제가 된 부분만 고치고, 장면의 사건과 목적, 등장인물은 그대로 둔다.",
        context_block,
        f"# 이번 화 계획\n{_json(episode_plan)}",
        f"# 고칠 장면 ({scene_index + 1}/{scene_count})\n{_json(scene)}",
        f"# 검사에 걸린 문제\n{_bullets(problems)}",
        f"# 분량\n- 공백 제외 약 {target_chars:,}자로 쓴다.",
    ]
    if hook and scene_index == scene_count - 1:
        parts.append(
            "# 마무리\n"
            f"이 장면이 회차의 끝이다. '{hook.get('type')}' 유형으로 끊는다: {hook.get('content')}"
        )
    if previous_tail:
        parts.append(f"# 바로 앞 원고 (이어지게 쓴다. 반복하지 말 것)\n{previous_tail}")
    if next_head:
        parts.append(f"# 바로 뒤 원고 (여기로 자연스럽게 넘어가게 쓴다)\n{next_head}")
    parts.append(f"# 원래 장면\n{scene_text}")
    parts.append("고친 장면 본문만 출력한다.")
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# 표지 (기획안 43번)
# ---------------------------------------------------------------------------
COVER_SYSTEM = """\
You write prompts for an image generation model that paints book cover art for a Korean web novel.

# Rules
- Write the prompt in English. Image models follow English best.
- Describe one clear scene: the protagonist, the setting, lighting, mood, composition.
- Leave empty space in the lower third for the title. Portrait orientation.
- Do NOT ask for any text, letters, title, logo or watermark in the image.
- Do NOT name or imitate existing books, covers, films, games, franchises or artists.
  The art must be an original composition.

Output one JSON object only, no explanation."""


def build_cover_prompt(
    *,
    title: str,
    genre: str,
    logline: str,
    mood: str,
    protagonist: dict[str, object] | None,
    world: list[dict[str, object]],
) -> str:
    example = {
        "prompt": "digital painting, a young man in a dark suit standing on a rooftop at night, ...",
        "negative_prompt": "text, letters, watermark, logo, signature, blurry, extra fingers",
    }
    return f"""# Novel (Korean)
Title: {title}
Genre: {genre}
Logline: {logline}
Mood: {mood or "(none)"}
Protagonist: {_json(protagonist or {})}
World: {_json(world)}

# Output format (exactly this shape)
{_json(example)}"""


# ---------------------------------------------------------------------------
# 전자책 작품 소개 (기획안 46번)
# ---------------------------------------------------------------------------
BLURB_SYSTEM = """\
당신은 한국 웹소설 전자책의 소개문을 쓰는 편집자다.
작품 기준과 회차 줄거리를 바탕으로 판매 페이지에 올릴 작품설명, 키워드, 카테고리를 쓴다.

# 규칙
- 작품설명은 3~6문장. 결말과 반전을 누설하지 않는다.
- 키워드는 5~10개. 독자가 검색할 만한 소재·설정 낱말 (예: 회귀, 기업물, 복수).
- 카테고리는 1~3개. 장르 이름으로.
- 다른 작품의 제목이나 인물 이름을 쓰지 않는다.

설명 없이 JSON 하나만 출력한다."""


def build_blurb_prompt(
    *, title: str, genre: str, logline: str, premise: str, summaries: list[str]
) -> str:
    example = {
        "description": "작품설명 3~6문장",
        "keywords": ["회귀", "기업물"],
        "categories": [genre or "웹소설"],
    }
    lines = "\n".join(f"- {s}" for s in summaries) or "- (없음)"
    return f"""# 작품 기준
제목: {title}
장르: {genre}
로그라인: {logline}
핵심 소재: {premise}

# 회차 줄거리 (앞부분)
{lines}

# 출력 형식 (이 모양 그대로)
{_json(example)}"""


# ---------------------------------------------------------------------------
# 완결 검사 (기획안 47번)
# ---------------------------------------------------------------------------
COMPLETION_SYSTEM = """\
당신은 연재를 마친 장편소설을 검수하는 편집자다. 작품 기준, Arc 설계, 전 회차 줄거리,
마지막 회 원고를 읽고 완결로 내보내도 되는지 판단한다.

# 찾을 것
- unresolved_conflicts: 작품의 핵심 갈등이나 Arc 갈등 중 결말까지 해소되지 않은 것.
- setting_conflicts: 회차 줄거리끼리 설정이 서로 맞지 않는 곳. 어느 회차들인지 적는다.
- ending_consistent: 마지막 회가 작품 기준(로그라인, 핵심 갈등, 계획한 결말)과 맞게
  끝났는가. 맞지 않으면 ending_issues에 이유를 적는다.

# 규칙
- 줄거리와 원고에 실제로 있는 것만 근거로 삼는다. 없는 사건을 지어내지 않는다.
- 문제가 없으면 목록을 비운다. severity는 분명하면 high, 애매하면 low.

설명 없이 JSON 하나만 출력한다."""


def build_completion_prompt(
    *,
    bible: dict[str, object],
    arcs: list[dict[str, object]],
    summaries: list[str],
    final_episode: str,
) -> str:
    example = {
        "unresolved_conflicts": [
            {"conflict": "해소되지 않은 갈등", "reason": "근거", "severity": "high"}
        ],
        "setting_conflicts": [
            {"description": "어긋나는 설정", "episodes": [12, 87], "severity": "low"}
        ],
        "ending_consistent": True,
        "ending_issues": [],
    }
    lines = "\n".join(summaries) or "(없음)"
    return f"""# 작품 기준 (Novel Bible)
{_json(bible)}

# Arc 설계
{_json(arcs)}

# 전 회차 줄거리
{lines}

# 마지막 회 원고
{final_episode}

# 출력 형식 (이 모양 그대로)
{_json(example)}"""
