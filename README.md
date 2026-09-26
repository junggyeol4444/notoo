# AI Novel Factory

참고소설을 **구조로** 분석하고, 그 구조를 바탕으로 완전히 새로운 장편소설을 쓰는 시스템.

참고작의 원문·인물 이름·세계관을 새 작품으로 옮기지 않는다. 가져오는 것은
"왜 재미있는가"를 설명하는 수치뿐이다.

## 구현 범위 (기획안 1~57번)

| 기획안 | 내용 | 상태 |
|---|---|---|
| Phase 1 | 참고소설 분석 (TXT/Markdown/EPUB/DOCX/PDF/HWP/HWPX → Reference Profile) | 구현 |
| Phase 2 | 장기기억 DB (Novel Bible / 인물 / 지식 / 관계 / 세계관 / 시간선 / 복선) | 구현 |
| Phase 3 | Writer + Reference Pattern 연동 (사건 일정 · Arc · 회차 · 장면 · 집필 · 기억 갱신) | 구현 |
| Phase 4 | Similarity Checker + 품질 검사(Continuity · Logic · Style · Hook · Reader) + 자동 수정 | 구현 |
| Phase 5·6 | 30화·100화 연속 생성 평가 (설정 오류 · 패턴 적용 · 장기 기억 · 복선 회수) | 구현 |
| Phase 7 | 표지 · EPUB · 전자책 메타데이터 · 출판 어댑터 · Publishing Queue | 구현 |
| 41·42 | 장편 기억 검색(의미/어휘) · 자동 집필 스케줄러 | 구현 |
| 47 | 완결 검사 | 구현 |
| 48~50 | 관리자 화면 (`/admin`) | 구현 |
| 52 | PostgreSQL + pgvector | 구현 · 전체 테스트를 PostgreSQL 16 + pgvector로도 통과 |
| 57 | 요청 글 하나로 분석부터 출판까지 (`POST /projects`, `novel-factory create`) | 구현 |

**실제 LLM·임베딩·이미지 모델로는 돌려 보지 못했다.** 이 개발 환경에서 모델 가중치를
받을 수 없었다(huggingface.co가 네트워크 정책에서 막혀 있다). 대신 실제 로컬 모델의
버릇(`<think>` 블록, 코드펜스, 형식 틀린 첫 응답, 없는 코드 섞기, 짧은 답, 원고에 없는
인용)을 흉내 내는 대본 LLM을 진짜 HTTP 서버로 띄워 흐름 전체를 검증했다. 확인한 것은
배관이지 글의 품질이 아니다. 쓰시는 로컬 서버로 처음 돌릴 때는 한 회차로 확인하길 권한다.
못 하는 것은 [`docs/분석기_한계.txt`](docs/분석기_한계.txt)에 적었다.

## 설치

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[pdf,publish,dev]"          # publish = 표지 JPEG(Pillow)
pip install -e ".[postgres]"                 # PostgreSQL + pgvector를 쓸 때
```

Python 3.11 이상. 기본은 SQLite라 외부 서비스 없이 바로 돈다.

## 써보기

```bash
# 기획안 57번: 요청 글 하나로 작품을 만든다 (LLM 필요)
novel-factory create --file 요청.txt          # 분석 → 패턴 → 세계관·인물 → 스토리 → 표지 → 집필 예약
novel-factory create --file 요청.txt --write  # 만든 뒤 멈출 때까지 바로 쓴다

# 참고작 분석 (LLM 불필요)
novel-factory analyze 소설.epub --genre 현대판타지
novel-factory aggregate a.txt b.epub c.hwpx --genre 현대판타지

# 회차 단위로 직접
novel-factory plan-story hoegwi
novel-factory write hoegwi 1 --to 30
novel-factory check hoegwi 3
novel-factory evaluate hoegwi --end 30        # 설정 오류·패턴 적용·복선 회수 수치
novel-factory run-schedule                    # 자동 집필 한 번 실행

# API 서버 + 관리자 화면
novel-factory serve                           # http://127.0.0.1:8000/admin , /docs
```

요청 글 예 (기획안 57번 그대로 읽는다):

```
현대판타지 소설을 만들어줘.

참고소설:
A
B
C

A에서는 빠른 전개만 참고.
B에서는 복선 구조 참고.
C에서는 캐릭터 관계 변화 참고.

250화.
회차당 약 5,000자.
```

"A에서는 X 참고"는 A에서 X만 1.0, 나머지 항목은 0으로 둔다. 참고 문장이 없는 참고작은
전 항목을 참고한다. 참고작은 `/references/upload`로 먼저 올려 두고 제목이나
reference_id로 부른다. 규칙으로 못 읽은 부분만 LLM이 채운다.

## 설정

전부 환경변수(접두사 `NF_`). 전체 목록과 설명은 [`.env.example`](.env.example).

```bash
NF_DATABASE_URL=sqlite:///data/novel_factory.db
# NF_DATABASE_URL=postgresql+psycopg://user:pw@host/db   # + NF_VECTOR_BACKEND=pgvector

NF_LLM_BASE_URL=http://localhost:11434/v1   # 로컬 추론 서버 (OpenAI 호환 /v1/chat/completions)
NF_LLM_MODEL=<올려 둔 모델 이름>
NF_EMBEDDING_MODEL=<임베딩 모델>             # 선택. 없으면 기억 검색은 어휘 검색
NF_IMAGE_BASE_URL=                          # 선택. 없으면 글자 표지
```

OpenAI 호환 형식이면 무엇이든 붙는다. 특정 회사 SDK에 의존하지 않는다.

## 흐름

```
[작품 생성 57번]  요청 해석 → 참고소설 분석 → 공통 패턴(참고 강도 가중) → 작품 설계
                  (Novel Bible·인물·세계관·복선·문체) → 사건 일정·Arc → 표지 → 집필 예약

[회차마다]        회차 지시(사건 규모·클리프행어·복선 회수 — 코드가 정함)
                  → 앞 회차 관련 장면 검색 → 회차 계획(LLM) → 장면 설계(LLM)
                  → 장면 단위 집필(LLM) → 장면별 참고작 유사도 → 겹치는 장면만 재작성
                  → 품질 검사 Continuity · Logic · Similarity · Style · Hook
                  → FAIL 장면만 고쳐 쓰고 재검사 (나아지지 않으면 원래 판본)
                  → 기억 갱신(LLM 추출 → 코드 검증 → DB) → 장면 색인 → 출판 대기열

[스케줄러]        매일 03:00 (작품마다 켬). FAIL이 남으면 기억 갱신 없이 held로 멈춤.
                  목표 회차를 다 쓰면 완결 검사 → 통과하면 completed
                  → automatic 모드면 전자책 권 생성 → 출판 대기열 처리
```

수치로 정할 수 있는 것(사건 일정, Arc 경계, 클리프행어 배분, 장면 수·분량, 인물 수,
복선 간격)은 참고작 패턴으로 코드가 정하고, LLM은 그 틀 안에서 내용을 채운다.
LLM 출력은 그대로 믿지 않는다(없는 코드 삭제, 지시와 다른 유형 되돌림, 원고에 없는
인용 버림, 참고작 인물 이름 차단).

## 구조

```
novel_factory/
  text/          한국어 텍스트 처리 (인코딩·문장·대사·어휘·어절)
  reference/     참고작: 파서(TXT·MD·EPUB·DOCX·PDF·HWP·HWPX) · 회차 분리 · 분석기 8종
                 · 패턴 집계 · 유사도(문장 지문, 인물 이름 해시) · 분석 서비스
  database/      16개 테이블(+ PostgreSQL에서 벡터 테이블), 리포지토리, 벡터 검색
  memory/        Writer Context 조립, 장편 기억 검색 (41번)
  generation/    작품 설계 · 사건 일정 · Arc · 회차 계획 · 장면 설계 · Writer · 기억 갱신
  quality/       Continuity · Logic · Style · Hook · Reader · 자동 수정 · 완결 검사 · 장기 평가
  publishing/    표지 · EPUB · 전자책 패키지 · 출판 어댑터 · 출판 대기열 (43~46번)
  orchestrator/  요청 해석, 작품 생성 전체 흐름 (57번)
  scheduler/     자동 집필 스케줄러 (42번, 서버 내장)
  llm/           로컬 OpenAI 호환 클라이언트 (채팅 · 임베딩)
  app/           FastAPI (73개 엔드포인트) + 관리자 화면 static/admin
  cli.py
```

## 주요 기능

**품질 검사 (34~38번).** Continuity(규칙) · Logic(LLM, 인용 확인) · Similarity(문장 지문 +
참고작 인물 이름) · Style · Hook · Reader(선택). FAIL은 자동 수정, WARN은 사람이 본다.
규칙으로 추정한 것은 전부 WARN이다.

**장편 기억 검색 (41번).** 앞 회차 장면을 찾아 원고 발췌로 Writer에게 보여 준다.
`NF_MEMORY_SEARCH` = auto(임베딩 있으면 의미, 없으면 어휘) / embedding / lexical / off.
PostgreSQL + pgvector면 DB가 순위를 매긴다.

**표지 (43번).** LLM이 영어 이미지 프롬프트를 쓰고 로컬 이미지 서버(OpenAI 호환 또는
A1111)로 그림을 받아 제목을 얹는다. 이미지 서버가 없으면 글자 표지.

**EPUB·전자책 (44·46번).** 표준 라이브러리로 EPUB 3을 만든다(W3C epubcheck 5.1.0
검증 0 오류 0 경고). 권마다 `book.epub / cover.jpg / metadata.xml`, 가격·작품설명·
키워드·카테고리.

**출판 (45번).** 공개 업로드 API를 확인한 연재 플랫폼이 없어서 전용 어댑터는 없다.
`files`(episode_title.txt / episode_body.txt / author_note.txt / thumbnail.jpg)와
`webhook`(사용자 API로 POST) 어댑터, 재시도하는 대기열이 있다.

**완결 검사 (47번).** 미확정 회차 · 미회수 복선 · 사라진 인물 · Timeline 오류 ·
남은 설정 FAIL · 미해결 갈등 · 결말 불일치. FAIL이 없어야 completed(사람이 force로 넘길 수 있다).

**관리자 화면 (48~50번).** `/admin`. 작품 · Novel Bible · Characters · World · Timeline ·
Relationships · Foreshadowing · Reference Novels(참고 강도 슬라이더) · Reference Patterns ·
Episodes(검사·수정·확정) · Similarity Reports · Publication, 참고소설 관리(업로드·분석·
감정곡선 등), 스케줄러.

**장기 평가 (55번).** `GET /novels/{slug}/evaluation`. 기준선은 긋지 않고 수치와 목표와의
차이만 낸다.

## 핵심 원칙 (56번)

1. 참고작 원문을 새 작품에 복사하지 않는다.
2. 참고작은 구조 분석 데이터로만 쓴다.
3. 참고작의 고유명사·세계관·캐릭터를 가져오지 않는다 — 참고작 인물 이름은 salt 해시로만
   저장하고, 작품 설계와 원고에서 같은 이름이 나오면 막는다.
4. **Novel Bible이 Reference Profile을 이긴다.**
5. 유사도 검사를 항상 실행한다.
6. 여러 참고작의 공통 패턴을 추출한다.
7. 장르별 패턴 라이브러리를 축적한다.

## 개발

```bash
pytest                                  # 412개 (100화 장기 테스트 1개 제외)
pytest -m long                          # 100화 연속 생성 (수십 초)
EPUBCHECK_JAR=/path/epubcheck.jar pytest tests/test_publishing.py    # W3C 검증기로 EPUB 확인
NF_TEST_DATABASE_URL="postgresql+psycopg://user@/db?host=/tmp&port=5432" \
NF_TEST_VECTOR_BACKEND=pgvector pytest  # PostgreSQL + pgvector로 전체 테스트
ruff check novel_factory tests
```

## 문서

- [`docs/설계.txt`](docs/설계.txt) — 왜 이 구조인지, 어떤 결정을 왜 내렸는지
- [`docs/분석기_한계.txt`](docs/분석기_한계.txt) — **무엇을 못 하는지.** 결과를 쓰기 전에 읽을 것

## 아직 없는 것

- 기획안 36번 유사도 중 **장면 진행 순서 · 캐릭터 조합 · 사건 해결 방식** 유사. 비교하려면
  참고작의 줄거리 내용이 있어야 하는데, 참고작 원문·줄거리를 저장하지 않는 원칙(56번)과
  부딪힌다. 구조 서명만으로 비교하는 방법은 판정 기준을 잡을 자료가 없어 넣지 않았다.
- 특정 연재 플랫폼 전용 업로드 어댑터 (위 출판 참고)
- Redis·Celery 대기열 (기획안 52번 기술 스택). 스케줄러는 요청대로 서버에 내장했다.
