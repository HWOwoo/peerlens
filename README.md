# PeerLens

공시 근거 기반 글로벌 Peer 비교·투자메모 AI Agent — 「국부펀드 KIC, AI 에이전트 공모전」 출품작.

> 현재 단계: Phase 1 PoC — 1주차 데이터 파이프라인 (SEC XBRL → 표준 지표 → 비교표)

## 빠른 실행

```bash
python -m venv .venv
.venv/Scripts/activate          # macOS/Linux: source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env            # SEC_USER_AGENT에 이름과 이메일 입력
peerlens compare NVDA AMD INTC AVGO QCOM TSM ASML --years 3
peerlens index NVDA AMD INTC AVGO QCOM TSM   # 공시 원문 색인 (.env에 COHERE_API_KEY 필요, 웹 서버는 꺼 둔 상태로)
peerlens search "고객 집중 리스크" --tickers NVDA --keywords "customer concentration"
pytest                          # 단위 테스트 (네트워크 불필요)
pytest -m live                  # SEC 실데이터 회귀 테스트
```

### 웹 데모 실행

```bash
cd web && npm ci && npm run build && cd ..     # 웹 화면 빌드 (web/dist)
uvicorn peerlens.api:app --port 8000          # http://localhost:8000
```

화면 개발 중에는 `uvicorn ... --reload`와 `cd web && npm run dev`(http://localhost:5173, /api는 8000으로 프록시)를 함께 띄운다.

Docker (배포용, 서버·URL 1개):

```bash
docker build -t peerlens .
docker run -p 8000:8000 -e SEC_USER_AGENT="이름 이메일" peerlens
```

| API | 설명 |
|---|---|
| `GET /api/companies?q=` | 티커·회사명 검색 |
| `POST /api/compare` | `{target, peers[], years, align}` → 비교표 셀·Peer 중앙값 |
| `GET /api/metrics/{metric_id}` | 지표 계산식 + 입력 원값의 공시 출처 |

CLI 출력: 터미널 비교표 + `data/output/` 에 `*_facts.csv`(원값·출처), `*_metrics.csv`(계산식·입력 fact_id·품질 플래그), `*_dataset.json`.

## 데이터 파이프라인 설계

| 단계 | 모듈 | 처리 |
|---|---|---|
| 수집 | `edgar/client.py` | companyfacts·submissions API, User-Agent 필수, 초당 8회 제한, 지수 백오프, `data/cache/` 캐시(수집일 기록) |
| 태그 매핑 | `metrics/tags.py` | 표준 지표 10종 → us-gaap/ifrs-full 태그 우선순위. 정의가 다른 대체 태그는 `proxy_tag` 플래그 |
| 기간 정렬 | `metrics/facts.py` | 연간 공시(10-K/20-F/40-F) 중 기간 350~380일 값만, 같은 기간은 최신 공시 값(재작성 시 `restated` + 최초값 보존), 회계연도·달력연도 동시 부여 |
| 원문 수집·분할 | `edgar/filings.py` | 최신 10-K/20-F 원문 → 문단 블록 → 사업·위험요인·MD&A·시장위험 섹션 (Item 제목 / 목차 기반 / PDF 줄 복원) |
| 청킹 | `retrieval/chunking.py` | 문단 경계 유지 ~2,200자, 재무표 행 제외, 청크마다 공시·섹션·소제목·원문 위치 링크(Text Fragment) |
| 검색 | `retrieval/index.py` | Cohere 임베딩(의미) + BM25(키워드) → RRF 결합 → Cohere 리랭커. 단계별 순위를 결과에 기록 |
| 계산 | `metrics/calc.py` | 성장률·매출총이익률·영업이익률·순이익률·ROE(평균자본)·R&D 비중·FCF 마진. 값마다 계산식·입력 fact_id·플래그 |

모든 Fact는 기업, CIK, 공시 종류, 접수번호, 공시일, 회계기간, taxonomy·태그, 공시 원문 URL, API URL, 수집일을 가진다.

## 외부 데이터·라이브러리 출처 및 라이선스

| 구분 | 항목 | 용도 | 출처·조건 |
|---|---|---|---|
| 데이터 | SEC EDGAR XBRL API (companyfacts, submissions, company_tickers) | 재무 수치, 기업 정보 | sec.gov 공개 데이터. [접근 정책](https://www.sec.gov/os/accessing-edgar-data) 준수 (User-Agent, 10 req/s 이하) |
| 데이터 | SEC EDGAR 공시 원문 (10-K, 20-F) | 근거 문단 검색 | 동일 (sec.gov 공개 데이터) |
| 모델·API | Cohere Embed v4 (`embed-v4.0`) | 공시 문단·질문 임베딩 | [Cohere 이용약관](https://cohere.com/terms-of-use), 유료 API (개발 중 체험판 키) |
| 모델·API | Cohere Rerank 4.0 Fast (`rerank-v4.0-fast`) | 검색 결과 재순위 | 동일 |
| 오픈소스 | Qdrant (qdrant-client) | 벡터 검색 (dense + sparse) | Apache-2.0 |
| 오픈소스 | BeautifulSoup4, lxml | 공시 HTML 파싱 | MIT / BSD-3-Clause |
| 오픈소스 | httpx | HTTP 클라이언트 | BSD-3-Clause |
| 오픈소스 | pandas | 표 계산 | BSD-3-Clause |
| 오픈소스 | python-dotenv | 환경변수 | BSD-3-Clause |
| 오픈소스 | tabulate | 표 출력 | MIT |
| 오픈소스 | pytest | 테스트 | MIT |
| 오픈소스 | FastAPI, uvicorn | API 서버 | MIT / BSD-3-Clause |
| 오픈소스 | React, Vite, TypeScript | 웹 화면 | MIT / MIT / Apache-2.0 |
| 폰트 | Pretendard | 웹 화면 글꼴 | SIL Open Font License 1.1 |
| 생성형 AI | Claude (Anthropic) | 코드 작성 보조 | 최종 코드는 참가자가 검토 |
