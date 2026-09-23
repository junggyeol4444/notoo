"""전역 설정.

모든 값은 환경변수로 덮어쓸 수 있다. 기본값은 외부 서비스 없이
바로 실행되는 조합(SQLite + 로컬 파일 저장소 + LLM 미사용)이다.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
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
    embedding_dim: int = 768

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

    # --- 분석 파라미터 ----------------------------------------------------
    # 회차 구분 마커를 못 찾았을 때 강제 분할할 기준 글자 수
    fallback_episode_chars: int = 5000
    # 클리프행어 판정에 사용할 회차 말미 글자 수
    cliffhanger_tail_chars: int = 400
    # 대형 사건으로 볼 사건 강도 상위 백분위
    major_event_percentile: float = 0.85

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
