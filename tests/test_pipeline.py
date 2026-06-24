import os
import tempfile
from unittest.mock import MagicMock, patch

import pandas as pd

# Import pipeline functions
from app.pipeline.collect_cpi import fetch_cpi_data
from app.pipeline.collect_opinet import fetch_oil_prices
from app.pipeline.merge_all_features import merge_features


# --- 1. Test CPI Ingestion ---
@patch('app.pipeline.collect_cpi.requests.get')
def test_fetch_cpi_data_success(mock_get):
    # Mock KOSIS successful response
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = [
        {"PRD_DE": "202601", "DT": "118.03"},
        {"PRD_DE": "202602", "DT": "118.4"}
    ]
    mock_get.return_value = mock_response

    records = fetch_cpi_data("202601", "202602")
    
    assert len(records) == 2
    assert records[0] == {"date": "2026-01", "cpi": 118.03}
    assert records[1] == {"date": "2026-02", "cpi": 118.4}


@patch('app.pipeline.collect_cpi.requests.get')
def test_fetch_cpi_data_error_response(mock_get):
    # Mock KOSIS unquoted-key JSON error response
    mock_response = MagicMock()
    mock_response.status_code = 200
    # response.json() raises ValueError for unquoted-key JSON response, so we mock text
    mock_response.json.side_effect = ValueError(
        "Expecting property name enclosed in double quotes"
    )
    mock_response.text = '{err:"21",errMsg:"요청변수값이 잘못되었습니다."}'
    mock_get.return_value = mock_response

    records = fetch_cpi_data("202601", "202602")
    
    assert len(records) == 0


# --- 2. Test Opinet Ingestion ---
@patch('app.pipeline.collect_opinet.requests.get')
def test_fetch_oil_prices_success(mock_get):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "RESULT": {
            "OIL": [
                {"DATE": "20260620", "PRODCD": "B027", "PRICE": "2008.71"},
                {"DATE": "20260620", "PRODCD": "D047", "PRICE": "2003.39"},
            ]
        }
    }
    mock_get.return_value = mock_response

    oil_data = fetch_oil_prices("20260620", "20260620")
    
    assert "2026-06-20" in oil_data
    assert oil_data["2026-06-20"]["보통휘발유"] == 2008.71
    assert oil_data["2026-06-20"]["자동차경유"] == 2003.39


# --- 3. Test Merge & Recalculate Features ---
def test_merge_features():
    # Setup temporary files for target CSV, oil CSV, CPI CSV, sidecar
    with tempfile.TemporaryDirectory() as tmpdir:
        target_path = os.path.join(tmpdir, "target.csv")
        temp_oil_path = os.path.join(tmpdir, "temp_oil.csv")
        temp_cpi_path = os.path.join(tmpdir, "temp_cpi.csv")
        price_raw_path = os.path.join(tmpdir, "price_raw.csv")

        # 마스터: 2026-05-31 ~ 2026-06-22, wholesale 4500 (실질가, cpi 컬럼 없음)
        dates = pd.date_range(start="2026-05-31", end="2026-06-22").strftime(
            "%Y-%m-%d").tolist()
        df_target = pd.DataFrame({
            "date": dates,
            "wholesale_price": [4500.0] * len(dates),
            "oil_gasoline": [2000.0] * len(dates),
            "oil_diesel": [1900.0] * len(dates),
        })
        df_target.to_csv(target_path, index=False)

        # 유가 신규 (2026-06-21, 22)
        pd.DataFrame({
            "date": ["2026-06-21", "2026-06-22"],
            "oil_gasoline": [2010.0, 2020.0],
            "oil_diesel": [1910.0, 1920.0],
        }).to_csv(temp_oil_path, index=False)

        # CPI: 2026-05=119.92 (6월 미발표 → ffill)
        pd.DataFrame({"date": ["2026-05"], "cpi": [119.92]}).to_csv(
            temp_cpi_path, index=False)

        # 월간 모드(refresh_cpi=True): CPI 재매핑 + sidecar 생성
        merge_features(
            target_path=target_path,
            temp_oil_path=temp_oil_path,
            temp_cpi_path=temp_cpi_path,
            price_raw_path=price_raw_path,
            refresh_cpi=True,
        )

        df_updated = pd.read_csv(target_path)

        # 1. 마스터엔 cpi 컬럼 없음 (sidecar로 분리)
        assert "cpi" not in df_updated.columns

        # 2. 유가 갱신
        row_22 = df_updated[df_updated["date"] == "2026-06-22"].iloc[0]
        assert row_22["oil_gasoline"] == 2020.0
        assert row_22["oil_diesel"] == 1920.0

        # 3. 유가 7일 이동평균 재계산 (당일 포함)
        expected_ma = (2000 * 5 + 2010 + 2020) / 7
        assert abs(row_22["oil_ma_7d"] - expected_ma) < 1e-4

        # 4. wholesale_price 보존 (cpi_base=최신 119.92, 5월 cpi=119.92 → 계수 1)
        assert abs(row_22["wholesale_price"] - 4500.0) < 0.5

        # 5. sidecar 에 raw_price + cpi 저장, cpi=119.92 ffill
        side = pd.read_csv(price_raw_path)
        assert {"date", "raw_price", "cpi"}.issubset(side.columns)
        assert (side["cpi"] == 119.92).all()
