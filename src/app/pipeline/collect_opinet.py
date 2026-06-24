import csv
import time
from datetime import datetime, timedelta

import pandas as pd
import requests

from app.core.config import settings


def fetch_oil_prices(start_date_str, end_date_str):
    """
    Fetches oil prices from Opinet API for the given range.
    Opinet dateAvgRecentPrice.do returns prices for the last 7 days ending at 'date'.
    """
    api_key = settings.opinet_api_key
    if not api_key:
        print("Warning: OPINET_API_KEY is not configured in .env. API call will fail.")
    url = 'https://www.opinet.co.kr/api/dateAvgRecentPrice.do'
    
    start_date = datetime.strptime(start_date_str, '%Y%m%d')
    end_date = datetime.strptime(end_date_str, '%Y%m%d')
    
    data_by_date = {}
    current_end_date = end_date
    
    product_mapping = {
        'B027': '보통휘발유',
        'D047': '자동차경유'
    }
    
    print(f"Fetching oil prices from {start_date_str} to {end_date_str}...")
    
    while current_end_date >= start_date:
        date_str = current_end_date.strftime('%Y%m%d')
        params = {
            'code': api_key,
            'out': 'json',
            'date': date_str
        }
        
        try:
            response = requests.get(url, params=params, timeout=10)
            if response.status_code == 200:
                res_json = response.json()
                oil_list = res_json.get('RESULT', {}).get('OIL', [])
                
                for item in oil_list:
                    raw_date = item.get('DATE')
                    if not raw_date:
                        continue
                        
                    item_date = datetime.strptime(raw_date, '%Y%m%d')
                    if item_date < start_date or item_date > end_date:
                        continue
                        
                    formatted_date = item_date.strftime('%Y-%m-%d')
                    prod_code = item.get('PRODCD')
                    price = item.get('PRICE')
                    
                    if prod_code in product_mapping:
                        if formatted_date not in data_by_date:
                            data_by_date[formatted_date] = {}
                        prod_name = product_mapping[prod_code]
                        data_by_date[formatted_date][prod_name] = float(price)
            else:
                print(
                    f"Error fetching for date {date_str}: "
                    f"HTTP {response.status_code}"
                )
        except Exception as e:
            print(f"Exception occurred for date {date_str}: {str(e)}")
            
        # Step back by 7 days since API yields 7 days of historical data
        current_end_date -= timedelta(days=7)
        time.sleep(0.5)
        
    return data_by_date

def save_to_csv(data_by_date, filepath):
    if not data_by_date:
        print("No oil data to save.")
        return
        
    sorted_dates = sorted(data_by_date.keys())
    headers = ['date', 'oil_gasoline', 'oil_diesel']
    
    with open(filepath, 'w', encoding='utf-8', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(headers)
        for date_key in sorted_dates:
            prices = data_by_date[date_key]
            writer.writerow([
                date_key,
                prices.get('보통휘발유', ''),
                prices.get('자동차경유', '')
            ])
    print(f"Saved oil prices to {filepath} (Total: {len(sorted_dates)} records)")

def resolve_incremental_start(master_path, backfill_start):
    """
    Determine the incremental fetch start date (YYYYMMDD).

    Reads the master dataset and returns the day AFTER the most recent date that
    already has a gasoline price. Falls back to ``backfill_start`` when the master
    dataset is missing or has no oil data yet (first-time backfill).
    """
    try:
        df = pd.read_csv(master_path, usecols=['date', 'oil_gasoline'])
    except (FileNotFoundError, ValueError):
        return backfill_start

    valid = df.dropna(subset=['oil_gasoline'])
    if valid.empty:
        return backfill_start

    last_date = pd.to_datetime(valid['date']).max()
    return (last_date + timedelta(days=1)).strftime('%Y%m%d')


if __name__ == '__main__':
    settings.data_dir.mkdir(parents=True, exist_ok=True)

    # Incremental: start the day after the latest oil record already in the master
    # dataset; end at yesterday (Opinet has no same-day data).
    yesterday = datetime.today() - timedelta(days=1)
    yesterday_str = yesterday.strftime('%Y%m%d')
    start_str = resolve_incremental_start(
        str(settings.master_dataset_path), settings.opinet_backfill_start
    )

    if start_str > yesterday_str:
        print(f"Oil prices already up to date (last < {start_str}). Nothing to fetch.")
    else:
        oil_data = fetch_oil_prices(start_str, yesterday_str)
        output_path = str(settings.data_dir / 'temp_oil.csv')
        save_to_csv(oil_data, output_path)
