import csv
from datetime import datetime

import requests

from app.core.config import settings


def fetch_cpi_data(start_prd, end_prd):
    """
    Fetches Monthly Consumer Price Index (CPI) from KOSIS API.
    Table: DT_1J22001 (지출목적별 소비자물가지수 2020=100)
    Org: 101
    """
    api_key = settings.kosis_api_key
    user_id = settings.kosis_user_id

    if not api_key:
        print("Warning: KOSIS_API_KEY is not configured in .env. API call will fail.")
    if not user_id:
        print(
            "Warning: KOSIS_USER_ID is not configured in .env. "
            "API call might fail with Code 20/21."
        )
        
    url = 'https://kosis.kr/openapi/Param/statisticsParameterData.do'
    
    params = {
        'method': 'getList',
        'apiKey': api_key,
        'format': 'json',
        'jsonVD': 'Y',
        'orgId': '101',
        'tblId': 'DT_1J22001',
        'prdSe': 'M',
        'newPrdCan': '1',
        'startPrdDe': start_prd,
        'endPrdDe': end_prd,
        'itmId': 'T',
        'objL1': 'T10',
        'objL2': '0',
        'userStatsId': user_id
    }
    
    try:
        response = requests.get(url, params=params, timeout=15)
        if response.status_code == 200:
            # Handle KOSIS non-standard JSON error format
            try:
                res_json = response.json()
            except ValueError:
                import json
                import re
                quoted_text = re.sub(
                    r'([{,])\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*:',
                    r'\1"\2":',
                    response.text,
                )
                res_json = json.loads(quoted_text)

            # KOSIS API can return an error description inside a dictionary/list
            if isinstance(res_json, dict) and 'err' in res_json:
                print(
                    f"KOSIS API Error: {res_json.get('errMsg')} "
                    f"(Code: {res_json.get('err')})"
                )
                return []
                
            cpi_records = []
            for item in res_json:
                prd_de = item.get('PRD_DE') # YYYYMM format
                dt_val = item.get('DT')
                
                if prd_de and dt_val:
                    formatted_date = f"{prd_de[0:4]}-{prd_de[4:6]}"
                    cpi_records.append({
                        'date': formatted_date,
                        'cpi': float(dt_val)
                    })
            return cpi_records
        else:
            print(f"Error fetching CPI: HTTP {response.status_code}")
            return []
    except Exception as e:
        print(f"Exception occurred fetching CPI: {str(e)}")
        return []

def save_to_csv(cpi_records, filepath):
    if not cpi_records:
        print("No CPI data to save.")
        return
        
    unique_records = {}
    for r in cpi_records:
        unique_records[r['date']] = r['cpi']
        
    sorted_dates = sorted(unique_records.keys())
    
    headers = ['date', 'cpi']
    with open(filepath, 'w', encoding='utf-8', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(headers)
        for date_key in sorted_dates:
            writer.writerow([date_key, unique_records[date_key]])
            
    print(f"Saved CPI data to {filepath} (Total: {len(sorted_dates)} monthly records)")

if __name__ == '__main__':
    # Fetch from Jan 2020 to current month
    current_month_str = datetime.today().strftime('%Y%m')
    cpi_data = fetch_cpi_data('202001', current_month_str)

    settings.data_dir.mkdir(parents=True, exist_ok=True)
    output_path = str(settings.data_dir / 'temp_cpi.csv')
    save_to_csv(cpi_data, output_path)
