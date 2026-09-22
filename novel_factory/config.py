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
    database_url: str = Field(default=f"sqlite:///{REPO_ROOT / 'data' / 'novel_factory.db'}")
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

    def ensure_dirs(self) -> None:
        for d in (self.data_dir, self.uploads_dir, self.extracted_dir):
            d.mkdir(parents=True, exist_ok=True)

    @property
    def llm_enabled(self) -> bool:
        return bool(self.llm_base_url and self.llm_model)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    s = Settings()
    s.ensure_dirs()
    return s
