# 데이터 파이프라인 API 목록

수박 도매가 예측용 마스터 데이터셋을 매일 갱신하기 위해 사용하는 외부 API와 저장소 정리.

> 데이터 흐름: **5종 수집 API → 병합/파생피처 → Azure Blob → 예측 모델**

---

## 1. 데이터 수집 API (5종)

| # | 데이터 | 제공처 | 엔드포인트 | 인증 환경변수 |
|---|--------|--------|-----------|--------------|
| 1 | **유가** (휘발유/경유) | 오피넷 | `https://www.opinet.co.kr/api/dateAvgRecentPrice.do` | `OPINET_API_KEY` |
| 2 | **소비자물가지수 (CPI)** | KOSIS (통계청) | `https://kosis.kr/openapi/Param/statisticsParameterData.do` | `KOSIS_API_KEY` + `KOSIS_USER_ID` |
| 3 | **수박 도매가** (원/kg) | 가락시장 (서울농수산식품공사) | `http://www.garak.co.kr/homepage/publicdata/dataJsonOpen.do` (`dataid=data36`) | `GARAK_PRICE_ID` + `GARAK_PRICE_PASSWD` |
| 4 | **수박 반입량** (톤) | 가락시장 (서울농수산식품공사) | `http://www.garak.co.kr/homepage/publicdata/dataJsonOpen.do` (`dataid=data22`) | `GARAK_API_ID` + `GARAK_API_PASSWD` |
| 5 | **기상** (기온/습도/일조/일사/강수) | 기상청 KMA API 허브 | `https://apihub.kma.go.kr/api/typ01/url/kma_sfcdd3.php` (서울 `stn=108`) | `AGRI_WEATHER_SERVICE_KEY` (KMA authKey) |

### 수집 컬럼 매핑

| API | 마스터 컬럼 |
|-----|------------|
| 오피넷 | `oil_gasoline`, `oil_diesel` |
| KOSIS | `cpi` (월별 → 일별 forward-fill) |
| 가락 data36 | `wholesale_price` (수박일반·상등급·전단위 원/kg 평균 → CPI 물가보정) |
| 가락 data22 | `trade_volume` (수박 SUM_TOT, 톤) |
| KMA ASOS | `avg_temp`, `max_temp`, `min_temp`, `humidity`, `sunshine_hours`, `solar_radiation`, `precipitation` |

---

## 2. 저장소 (적재)

| 용도 | 서비스 | 위치 | 인증 |
|------|--------|------|------|
| 마스터 데이터셋 | Azure Blob Storage | 컨테이너 `data` / `watermelon_dataset_targets.csv` | `BLOB_STORAGE_URL` + `BLOB_STORAGE_ACCESS_KEY` |

- 파이프라인이 이 위치에 업로드하고, 예측 모델(`app.model.predictor`)이 같은 위치에서 읽는다.
- 날짜별 스냅샷은 `data/snapshots/YYYY-MM-DD/` 에 함께 보관.

---

## 3. 검토했으나 채택하지 않은 API

| API | 채택 안 한 이유 |
|-----|----------------|
| **KAMIS** (`periodWholesaleProductList`, `dailyPriceByCategoryList`) | 수박이 "원/개" 단위로만 제공되고 무게(kg) 정보가 없어 원/kg 재현 불가. `p_convert_kg_yn=Y`도 수박엔 미적용 → 도매가는 가락 data36로 대체 |
| 가락 **경매결과** (`data12`) | 건별 경락가(거래단량 포함) 제공하나 집계값이 마스터와 불일치 → data36(단위별 평균가)으로 결정 |
| 가락 **data20 / data21** | 반입량의 다른 집계 단위. data22(품목 기준)가 마스터와 정확히 일치 |

---

## 4. 참고 사항

- **KAMIS 호출 시**: Python 기본 요청은 TLS 핸드셰이크 실패 → 커스텀 cipher(`DEFAULT@SECLEVEL=1`) + 브라우저 User-Agent 필요. (현재 파이프라인은 KAMIS 미사용)
- **가락 휴장일**(일요일/공휴일): API가 빈 응답 → 도매가/반입량은 선형 보간 + 마지막 구간 ffill로 채움.
- **도매가 물가보정**: `wholesale_price = raw × (기준CPI / 그달CPI)`, 기준CPI = 최신 발표월(매달 갱신). 새 CPI 발표 시 전 구간 재환산.
- 상세 발견사항은 [wholesale_price_troubleshooting.md](wholesale_price_troubleshooting.md) 참고.
