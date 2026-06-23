import os
import tempfile
import pandas as pd
import pytest
from unittest.mock import patch, MagicMock

# Import pipeline functions
from app.pipeline.collect_cpi import fetch_cpi_data, save_to_csv as save_cpi_csv
from app.pipeline.collect_opinet import fetch_oil_prices, save_to_csv as save_oil_csv
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
    mock_response.json.side_effect = ValueError("Expecting property name enclosed in double quotes")
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
    # Setup temporary files for target CSV, oil CSV, and CPI CSV
    with tempfile.TemporaryDirectory() as tmpdir:
        target_path = os.path.join(tmpdir, "target.csv")
        temp_oil_path = os.path.join(tmpdir, "temp_oil.csv")
        temp_cpi_path = os.path.join(tmpdir, "temp_cpi.csv")

        # Create dummy target dataset starting from 2026-05-31 to 2026-06-22 to allow CPI ffill
        dates = pd.date_range(start="2026-05-31", end="2026-06-22").strftime("%Y-%m-%d").tolist()
        df_target = pd.DataFrame({
            "date": dates,
            "wholesale_price": [4500.0] * len(dates),
            "oil_gasoline": [2000.0] * len(dates),
            "oil_diesel": [1900.0] * len(dates),
            "oil_ma_7d": [2000.0] * len(dates)
        })
        # Note: CPI column is NOT present initially to test auto-creation
        df_target.to_csv(target_path, index=False)

        # Create dummy temp oil data containing new records
        # Let's say we have updated prices for 2026-06-21 and 2026-06-22
        df_oil = pd.DataFrame({
            "date": ["2026-06-21", "2026-06-22"],
            "oil_gasoline": [2010.0, 2020.0],
            "oil_diesel": [1910.0, 1920.0]
        })
        df_oil.to_csv(temp_oil_path, index=False)

        # Create dummy CPI data (May 2026: 119.92, June 2026: Not published, i.e. missing)
        df_cpi = pd.DataFrame({
            "date": ["2026-05"],
            "cpi": [119.92]
        })
        df_cpi.to_csv(temp_cpi_path, index=False)

        # Run merge
        merge_features(target_path, temp_oil_path, temp_cpi_path)

        # Reload target dataset to verify updates
        df_updated = pd.read_csv(target_path)
        
        # 1. Verification of columns
        assert "cpi" in df_updated.columns
        
        # 2. Verification of oil updates
        # Check row for 2026-06-22
        row_22 = df_updated[df_updated["date"] == "2026-06-22"].iloc[0]
        assert row_22["oil_gasoline"] == 2020.0
        assert row_22["oil_diesel"] == 1920.0

        # 3. Verification of 7-day moving average recalculation
        # Expected gasoline prices: 2000, 2000, 2000, 2000, 2000, 2000, 2010, 2020
        # Rolling mean for 2026-06-22 (last 7 days: 2000, 2000, 2000, 2000, 2000, 2010, 2020)
        # sum = 14030 / 7 = 2004.2857...
        expected_ma = (2000 * 5 + 2010 + 2020) / 7
        assert abs(row_22["oil_ma_7d"] - expected_ma) < 1e-4

        # 4. Verification of CPI mapping and Forward Fill
        # Since CPI for June 2026 is missing, it should be forward-filled from May 2026 CPI (119.92)
        for val in df_updated["cpi"].tolist():
            assert val == 119.92
