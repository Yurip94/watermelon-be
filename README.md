# 수박의정석 — Backend

수박 도매가 7일치를 예측하고 사진으로 품질을 분석하는 데이터 AI 서비스의 백엔드입니다.
기상·유가·물가(CPI)·도매가·반입량 등 공공 API 5종을 매일 자동 수집·병합해 학습용
마스터 데이터를 만들고, FastAPI로 예측 결과를 서빙합니다.

> 팀 프로젝트 (SeSAC · 6인) · 2026.06 · [프로젝트 상세 페이지](https://yurip94.github.io/watermelon.html)

## 아키텍처

```text
공공 API 5종 (기상청·오피넷·KOSIS·가락시장 data36/data22)
  → 수집·전처리 파이프라인 (매일 02:00)
  → Blob Storage (master + CPI sidecar)
  → Ridge 예측 (7일)
  → PostgreSQL → FastAPI / 대시보드
```

- **CPI 사이드카**: 원본가(raw)와 CPI를 분리 보존 — 매일은 어제치만 append,
  새 CPI 발표 시 전 구간을 실질가격으로 재환산하고 월 1회 모델 재학습으로 연결
- **도매가 재현**: 가락시장 `data36` 출처 규명 + CPI 실질가 보정으로 마스터 데이터와
  완전 일치(최근 12일 오차 0) — 규명 과정은
  [docs/wholesale_price_troubleshooting.md](docs/wholesale_price_troubleshooting.md) 참고

## 구성

| 경로 | 역할 |
|------|------|
| `src/app/pipeline/` | 5종 수집기 · 병합 · 파생 피처 · Blob 업로드 · 스케줄러 |
| `src/app/api/v1/` | FastAPI 엔드포인트 (prices, health, database) |
| `docs/` | API 레퍼런스 · 도매가 출처 규명 트러블슈팅 |
| `tests/` | 파이프라인 · API 테스트 |

## 실행

```bash
python3 -m venv .venv && source .venv/bin/activate
python -m pip install -e ".[dev]"
cp .env.example .env   # DATABASE_URL 등 환경변수 설정
uvicorn app.main:app --reload --app-dir src
```

- Swagger UI: http://127.0.0.1:8000/docs
- Health check: http://127.0.0.1:8000/api/v1/health
- Weekly prices: http://127.0.0.1:8000/api/v1/prices?date=2026-06-22

로컬 PostgreSQL은 `docker compose up -d db` 로 띄울 수 있습니다
(`watermelon` DB, `postgres`/`postgres`, `localhost:5432`).

## 테스트

```bash
pytest
```

## 배포

- API: Azure Container Apps (GitHub Actions AutoDeploy)
- 수집 잡: Azure Container Apps Job(cron) — 매일 02:00 수집·정제·Blob 적재 무인 실행
