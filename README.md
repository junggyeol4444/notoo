# AI Novel Factory

참고소설을 **구조로** 분석하고, 그 구조를 바탕으로 장편소설을 쓰기 위한 시스템.

참고작의 원문·인물 이름·세계관을 새 작품으로 옮기지 않는다. 가져오는 것은
"왜 재미있는가"를 설명하는 수치뿐이다.

## 현재 구현 범위

| Phase | 내용 | 상태 |
|---|---|---|
| Phase 1 | 참고소설 분석 (TXT/Markdown/EPUB/DOCX/PDF/**HWP/HWPX** → Reference Profile) | 구현 완료 |
| Phase 2 | 장기기억 DB (Novel Bible / 인물 / 지식 / 관계 / 세계관 / 시간선 / 복선) | 구현 완료 |
| Phase 3 | Writer + Reference Pattern 연동 | 구현 완료 (실제 모델로는 미검증 — 아래 참고) |
| Phase 4~7 | Similarity Checker / 장기 테스트 / EPUB / 출판 | 지문 기반 유사도만 구현 |

Phase 1과 2는 **LLM 없이 전부 동작한다.** 파싱·회차분리·통계·구조분석·DB는
전부 결정적 로직이다. LLM은 Phase 3 집필부터 필요하다.

Phase 3도 수치로 정할 수 있는 것은 LLM에게 맡기지 않는다. 사건 일정, Arc 경계,
클리프행어 유형 배분, 장면 수·분량·역할은 참고작 패턴으로 코드가 정하고, LLM은
그 틀 안에서 내용을 채운다.

## 설치

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[pdf,dev]"
```

Python 3.11 이상. 외부 서비스(PostgreSQL, Redis) 없이 바로 돈다.

## 써보기

```bash
# 참고작 한 편 분석
novel-factory analyze 소설.epub --genre 현대판타지

# 회차 분리만 확인
novel-factory episodes 소설.txt

# 여러 작품 집계 → 장르 공식과 패턴 도출
novel-factory aggregate a.txt b.epub c.docx --genre 현대판타지

# 사건 일정 + Arc 설계 (LLM 없으면 골격만)
novel-factory plan-story hoegwi

# 1~30화 연속 생성 (LLM 필요)
novel-factory write hoegwi 1 --to 30

# API 서버
novel-factory serve --reload      # http://127.0.0.1:8000/docs
```

`analyze` 출력 예:

```
■ 기본 통계
총 분량 1,240,000자 / 250화
평균 회차 4,960자
평균 문장 19자 / 평균 문단 84자
대사 41% / 서술 53% / 속마음 6%

■ 전개
첫 사건: 1화 20% 지점
첫 보상: 2화
중형 사건: 평균 4.2화마다
대형 사건: 평균 23.5화마다
전개 속도: fast

■ 클리프행어
회차말 클리프행어 사용률: 84%
위기발생 27% / 정보공개 31% / 반전 18% ...
```

## 설정

전부 환경변수. 접두사는 `NF_`.

```bash
# 저장소 (기본: SQLite. 외부 서비스 불필요)
NF_DATABASE_URL=sqlite:///data/novel_factory.db
# PostgreSQL로 갈 때
# NF_DATABASE_URL=postgresql+psycopg://user:pw@host/db

# LLM — 로컬 추론 서버를 가리킨다. 비워 두면 분석·DB만 동작한다.
NF_LLM_BASE_URL=http://localhost:11434/v1     # Ollama
# NF_LLM_BASE_URL=http://localhost:1234/v1    # LM Studio
# NF_LLM_BASE_URL=http://localhost:8000/v1    # vLLM
# NF_LLM_BASE_URL=http://localhost:8080/v1    # llama.cpp
NF_LLM_MODEL=<올려 둔 모델 이름>

# 집필에 영향이 큰 값
NF_LLM_CONTEXT_TOKENS=65536       # 모델 컨텍스트 길이
NF_LLM_TOKENS_PER_CHAR=1.0        # 한국어 1글자당 토큰 추정치 (모델 토크나이저로 재 보고 맞출 것)
NF_LLM_JSON_MODE=false            # 서버가 response_format을 지원하면 true
```

OpenAI 호환 `/v1/chat/completions` 형식이면 무엇이든 붙는다.
특정 회사 SDK에 의존하지 않는다.

## 구조

```
novel_factory/
  text/          한국어 텍스트 처리 (인코딩·문장·대사·어휘·어절)
  reference/
    importer/    업로드 저장과 본문 추출
    parser/      TXT · Markdown · EPUB · DOCX · PDF · HWP · HWPX (전부 stdlib, PDF만 pypdf)
    structure/   회차 분리, 회차 내부 구간 비율
    analyzer/    기본통계 · 전개 · 클리프행어 · 캐릭터 · 관계 · 복선 · 감정 · 문체
    pattern/     다중 작품 집계, Reference Pattern Library
    similarity/  해시 지문 기반 유사도
    profile.py   Reference Profile
    pipeline.py  전체 파이프라인
  database/      15개 테이블, 리포지토리, 벡터 검색
  memory/        Writer Context 조립
  generation/    Phase 3 집필 (사건 일정 · Arc · 회차 계획 · 장면 설계 · Writer · 기억 갱신)
  quality/       반복 표현 감지 (기획안 33번)
  llm/           로컬 OpenAI 호환 클라이언트
  app/           FastAPI (37개 엔드포인트)
  cli.py
```

## 핵심 원칙

1. 참고작 원문을 새 작품에 복사하지 않는다.
2. 참고작은 구조 분석 데이터로만 쓴다.
3. 참고작의 고유명사·세계관·캐릭터를 가져오지 않는다.
4. **Novel Bible이 Reference Profile을 이긴다.**
5. 유사도 검사를 항상 실행한다.
6. 여러 참고작의 공통 패턴을 추출할 수 있다.
7. 장르별 패턴 라이브러리를 축적한다.

Reference Profile은 원문 문장도 인물 이름도 담지 않는다. 남는 것은 구조 수치와,
원문을 복원할 수 없는 해시 지문뿐이다. 지문은 새 원고가 참고작과 지나치게
비슷해지는 것을 막는 데만 쓴다.

## 개발

```bash
pytest                       # 260개 테스트
ruff check novel_factory     # 린트
python tests/fixtures/make_fixtures.py    # 한국어 합성 픽스처 재생성
```

테스트 픽스처는 한국 웹소설 표기 관습(제N화 마커, 따옴표 대사, 작은따옴표
속마음, 회차 말미 훅)을 가진 합성 원고다. 같은 원고를 TXT/CP949/MD/EPUB/DOCX/PDF/HWP/HWPX로
내보내 파서별 결과가 일치하는지 검증한다.

## Phase 3 집필 흐름

```
plan-story   사건 일정(결정적) → Arc 골격(결정적) → Arc 내용(LLM)
write N      회차 지시 계산 → 회차 계획(LLM) → 장면 설계(LLM)
             → 장면 단위 집필(LLM, 짧거나 잘리면 이어쓰기)
             → 장면별 참고작 유사도 검사 → FAIL 장면만 재작성
             → 기억 갱신(LLM 추출 → 코드 검증 → DB 반영)
             → data/novels/<slug>/episode_NNN/ 저장 (기획안 40번 구조)
```

LLM 출력은 그대로 믿지 않는다. 없는 인물·복선 코드는 지우고, 지시와 다른 클리프행어
유형은 되돌리고, 이미 있는 인물을 새 인물로 만들지 않는다. 무엇을 고쳤는지는
회차마다 경고로 남는다.

**실제 모델로는 아직 돌려 보지 못했다.** 이 개발 환경에서 모델 가중치를 받을 수
없었다(huggingface.co가 네트워크 정책에서 막혀 있다). 대신 실제 로컬 모델의 버릇
(`<think>` 블록, 코드펜스, 형식 틀린 첫 응답, 없는 코드 섞기, 짧은 답)을 흉내 내는
대본 LLM을 진짜 HTTP 서버로 띄워, `/v1/chat/completions` 경로로 파이프라인 전체를
검증했다. 쓰시는 로컬 서버로 처음 돌릴 때는 `write hoegwi 1` 한 편으로 확인하길 권한다.

## 문서

- [`docs/설계.txt`](docs/설계.txt) — 왜 이 구조인지, 어떤 결정을 왜 내렸는지
- [`docs/분석기_한계.txt`](docs/분석기_한계.txt) — **무엇을 못 하는지.** 결과를 쓰기 전에 읽을 것

## 아직 없는 것

- Continuity · Logic · Style Checker와 그에 따른 자동 수정 (기획안 34~36, 38번)
  (반복 표현 감지(33번)와 유사도 검사(36번)는 있다)
- Reader Simulation (기획안 37번)
- 자동 집필 스케줄러 (기획안 42번)
- 표지 생성 · EPUB 제작 · 출판 어댑터 (기획안 43~46번)
- 관리자 화면 (기획안 48~50번)
- 의미 유사도 (현재 유사도 검사는 표현 유사도만 잡는다)
