"""전역 설정.

모든 값은 환경변수로 덮어쓸 수 있다. 기본값은 외부 서비스 없이
바로 실행되는 조합(SQLite + 로컬 파일 저장소 + LLM 미사용)이다.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="NF_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- 저장소 ---------------------------------------------------------
    # SQLite가 기본. PostgreSQL로 바꾸려면
    #   NF_DATABASE_URL=postgresql+psycopg://user:pw@host/db
    database_url: str = Field(
        default=f"sqlite:///{REPO_ROOT / 'data' / 'novel_factory.db'}"
    )
    # 업로드한 참고소설 원본과 추출 텍스트가 쌓이는 곳
    data_dir: Path = Field(default=REPO_ROOT / "data")
    echo_sql: bool = False

    # --- 벡터 검색 ------------------------------------------------------
    # "sqlite": 파이썬 내 코사인 유사도 (의존성 없음)
    # "pgvector": PostgreSQL pgvector 확장 사용
    vector_backend: str = "sqlite"

    # --- 장편 기억 검색 (기획안 41번) ---------------------------------------
    # 과거 장면을 찾아 Writer Context에 발췌로 넣는다.
    #   auto       임베딩 모델이 설정돼 있으면 의미 검색, 아니면 어휘 검색
    #   embedding  의미 검색만 (임베딩 설정이 없거나 실패하면 검색하지 않는다)
    #   lexical    어휘 검색만 (모델 없이 글자 조각이 겹치는 장면을 찾는다)
    #   off        검색하지 않는다
    memory_search: Literal["auto", "embedding", "lexical", "off"] = "auto"
    # 로컬 서버의 /v1/embeddings. 주소를 비우면 NF_LLM_BASE_URL을 쓴다.
    embedding_base_url: str = ""
    embedding_model: str = ""
    embedding_api_key: str = ""
    embedding_batch_size: int = 32
    # 한 번에 Writer에게 보여 줄 과거 장면 수와 장면당 발췌 길이(글자)
    memory_top_k: int = 4
    memory_excerpt_chars: int = 500
    # 장면을 이 길이(글자) 안팎의 조각으로 나눠 색인한다
    memory_chunk_chars: int = 800

    # --- LLM ------------------------------------------------------------
    # 로컬 OpenAI 호환 엔드포인트를 가리킨다.
    #   Ollama      : http://localhost:11434/v1
    #   LM Studio   : http://localhost:1234/v1
    #   vLLM        : http://localhost:8000/v1
    #   llama.cpp   : http://localhost:8080/v1
    # 비워두면 LLM 호출 없이 휴리스틱만으로 동작한다(Phase 1~2는 이걸로 충분).
    llm_base_url: str = ""
    llm_model: str = ""
    llm_api_key: str = ""
    llm_timeout_sec: float = 300.0
    llm_max_retries: int = 2
    llm_temperature: float = 0.8
    # 계획·추출처럼 정해진 형식이 필요한 호출은 온도를 낮춘다.
    llm_structured_temperature: float = 0.3
    # 모델의 컨텍스트 길이(토큰). Writer에 넘길 설정·요약 분량을 이 안에 맞춘다.
    llm_context_tokens: int = 65536
    # 한국어 1글자당 토큰 수 추정치. 토크나이저마다 다르므로 쓰는 모델에 맞춰
    # 조정한다. 컨텍스트 예산과 max_tokens 계산에만 쓰인다.
    llm_tokens_per_char: float = 1.0
    # 서버가 OpenAI의 response_format={"type": "json_object"}를 지원하면 켠다.
    # 지원하지 않는 서버에 보내면 400이 나므로 기본은 끈다.
    llm_json_mode: bool = False
    # 구조화 응답이 형식에 맞지 않을 때 오류를 알려 주며 다시 요청하는 횟수
    llm_structured_retries: int = 2

    # --- 집필 ------------------------------------------------------------
    # 장면 하나의 목표 글자 수 범위. 회차 목표 분량을 이 범위로 나눠 장면 수를 정한다.
    scene_min_chars: int = 700
    scene_max_chars: int = 1600
    # 다음 장면을 쓸 때 이어 붙여 보여 줄 직전 원고 분량(글자)
    writer_tail_chars: int = 1500
    # 장면이 목표의 이 비율보다 짧으면 이어 쓰게 한다
    writer_min_length_ratio: float = 0.7
    # 유사도 검사에 걸린 장면을 다시 쓰는 최대 횟수 (기획안 20·38번)
    similarity_rewrite_attempts: int = 2

    # --- 품질 검사 (기획안 34~38번) ------------------------------------------
    # FAIL 장면을 고쳐 쓰고 다시 검사하는 최대 횟수. 0이면 검사만 한다.
    quality_fix_rounds: int = 2
    # Logic 검사(LLM)를 집필 파이프라인에서 돌릴지
    quality_logic: bool = True
    # Reader Simulation(LLM). 페르소나마다 원고 전체를 한 번씩 읽히므로
    # 로컬 모델에서는 시간이 많이 든다. 기본은 끈다.
    quality_reader: bool = False
    # Reader Simulation 페르소나. 환경변수로는 JSON 목록으로 준다.
    #   NF_QUALITY_READER_PERSONAS='["웹소설 독자", "까다로운 독자"]'
    quality_reader_personas: list[str] = Field(
        default_factory=lambda: [
            "웹소설 독자",
            "판타지 독자",
            "로맨스 독자",
            "까다로운 독자",
        ]
    )

    # --- 자동 집필 스케줄러 (기획안 42번) ------------------------------------
    # API 서버 안에서 매일 정해진 시각에 돈다. 작품마다 켜야 쓰기 시작한다
    # (PUT /novels/{slug}/schedule). 서버를 여러 워커로 띄우면 워커마다 돈다.
    # 같은 날 같은 작품은 한 번만 쓰도록 막지만, 워커는 하나로 띄우는 것이 안전하다.
    scheduler_enabled: bool = True
    scheduler_time: str = "03:00"
    # IANA 시간대 이름 (예: Asia/Seoul). 비우면 서버의 로컬 시간.
    scheduler_timezone: str = ""

    # --- 표지 (기획안 43번) --------------------------------------------------
    # 이미지 서버. 비우면 그림 없이 글자 표지를 만든다.
    #   openai  OpenAI 호환 /v1/images/generations (LocalAI 등)
    #   a1111   Stable Diffusion WebUI /sdapi/v1/txt2img (Forge 등 같은 API)
    image_base_url: str = ""
    image_api: Literal["openai", "a1111"] = "openai"
    image_model: str = ""
    image_api_key: str = ""
    # 이미지 모델에 요청할 크기. 표지 비율(2:3)에 맞춰 잘라 cover 크기로 맞춘다.
    image_width: int = 832
    image_height: int = 1248
    image_steps: int = 30
    image_timeout_sec: float = 600.0
    cover_width: int = 1600
    cover_height: int = 2400
    thumbnail_width: int = 400
    thumbnail_height: int = 600
    # 한글 글꼴 파일. 비우면 흔한 위치에서 찾는다 (나눔, Noto CJK, 맑은 고딕 등).
    cover_font: str = ""

    # --- 출판 (기획안 45번) --------------------------------------------------
    # 내보낼 대상. 환경변수로는 JSON 목록으로 준다.
    #   {"name": "files", "type": "files"}
    #       episode_title.txt / episode_body.txt / author_note.txt / thumbnail.jpg를
    #       data/novels/<slug>/publish/<name>/에 만든다. 사람이 플랫폼에 올린다.
    #   {"name": "내서버", "type": "webhook", "url": "https://...", "token": "..."}
    #       회차·전자책을 그 주소로 POST한다. 받는 쪽은 사용자가 만든 API다.
    publish_targets: list[dict[str, str]] = Field(
        default_factory=lambda: [{"name": "files", "type": "files"}]
    )
    # 대상이 실패했을 때 다시 시도하는 최대 횟수
    publish_max_attempts: int = 3
    publish_timeout_sec: float = 60.0

    # --- 분석 파라미터 ----------------------------------------------------
    # 회차 구분 마커를 못 찾았을 때 강제 분할할 기준 글자 수
    fallback_episode_chars: int = 5000
    # 클리프행어 판정에 사용할 회차 말미 글자 수
    cliffhanger_tail_chars: int = 400
    # 대형 사건으로 볼 사건 강도 상위 백분위
    major_event_percentile: float = 0.85

    @field_validator("scheduler_time")
    @classmethod
    def _check_time(cls, value: str) -> str:
        m = re.fullmatch(r"(\d{1,2}):(\d{2})", value.strip())
        if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
            raise ValueError(f"NF_SCHEDULER_TIME은 HH:MM 형식이어야 합니다: {value!r}")
        return f"{int(m.group(1)):02d}:{m.group(2)}"

    @field_validator("scheduler_timezone")
    @classmethod
    def _check_timezone(cls, value: str) -> str:
        value = value.strip()
        if value:
            try:
                ZoneInfo(value)
            except (ZoneInfoNotFoundError, ValueError) as exc:
                raise ValueError(
                    f"알 수 없는 시간대입니다: {value!r}. "
                    "Windows라면 'pip install tzdata'가 필요할 수 있습니다."
                ) from exc
        return value

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def extracted_dir(self) -> Path:
        return self.data_dir / "extracted"

    @property
    def novels_dir(self) -> Path:
        """회차 산출물 저장 위치 (기획안 40번 episode_NNN/ 폴더들)."""
        return self.data_dir / "novels"

    def ensure_dirs(self) -> None:
        for d in (self.data_dir, self.uploads_dir, self.extracted_dir, self.novels_dir):
            d.mkdir(parents=True, exist_ok=True)

    @property
    def llm_enabled(self) -> bool:
        return bool(self.llm_base_url and self.llm_model)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    s = Settings()
    s.ensure_dirs()
    return s
