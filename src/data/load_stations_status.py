import os
import requests
import py7zr
import pandas as pd
import tempfile
from pathlib import Path

# --- Configuration ---
BASE_URL = "https://opendata-ajuntament.barcelona.cat/resources/bicing/"
RAW_DIR = Path("./data/raw_csv")
PROCESSED_DIR = Path("./data/bicing_stations_status")

# The specific columns requested
COLS_TO_KEEP = [
    "station_id", "num_bikes_available", "num_bikes_available_types.mechanical",
    "num_bikes_available_types.ebike", "num_docks_available", "last_reported",
    "is_charging_station", "status", "traffic", "last_updated"
]

YEARS = [2025, 2026]
MONTHS = [
    "01_Gener", "02_Febrer", "03_Marc", "04_Abril", "05_Maig", "06_Juny",
    "07_Juliol", "08_Agost", "09_Setembre", "10_Octubre", "11_Novembre", "12_Desembre"
]

def download_raw_data():
    """Phase 1: Download 7z files and extract the raw CSVs."""
    print("📥 --- Starting Phase 1: Download and Extract ---")
    RAW_DIR.mkdir(parents=True, exist_ok=True)

    for year in YEARS:
        for month_name in MONTHS:
            file_prefix = f"{year}_{month_name}_BicingNou_ESTACIONS"
            url = f"{BASE_URL}{file_prefix}.7z"
            final_csv_path = RAW_DIR / f"{file_prefix}.csv"

            if final_csv_path.exists():
                print(f"⏭️  Skipping download for {file_prefix}: CSV already exists.")
                continue

            print(f"⏳ Downloading {year}-{month_name}...")
            try:
                with tempfile.TemporaryDirectory() as temp_dir:
                    temp_path = Path(temp_dir)
                    local_7z = temp_path / f"{file_prefix}.7z"

                    # Download in chunks
                    response = requests.get(url, stream=True)
                    if response.status_code != 200:
                        print(f"  ❌ -> Skipping: Not found on server (HTTP {response.status_code}).")
                        continue

                    with open(local_7z, 'wb') as f:
                        for chunk in response.iter_content(chunk_size=8192):
                            f.write(chunk)

                    # Extract archive directly into the RAW_DIR
                    with py7zr.SevenZipFile(local_7z, mode='r') as z:
                        z.extractall(path=RAW_DIR)
                    
                    print(f"  ✅ -> Extracted raw CSV: {final_csv_path.name}")

            except Exception as e:
                print(f"  ❌ -> Error downloading {file_prefix}: {e}")

def transform_data():
    """Phase 2: Read raw CSVs, filter columns, filter time, and resample to 15min intervals."""
    print("\n⚙️  --- Starting Phase 2: Transform and Save to Parquet ---")
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

    for year in YEARS:
        for month_name in MONTHS:
            file_prefix = f"{year}_{month_name}_BicingNou_ESTACIONS"
            input_csv = RAW_DIR / f"{file_prefix}.csv"
            output_parquet = PROCESSED_DIR / f"{file_prefix}_filtered.parquet"

            if not input_csv.exists():
                # We skip silently here, as Phase 1 would have noted the missing file
                continue

            if output_parquet.exists():
                print(f"⏭️  Skipping transform for {file_prefix}: Parquet already exists.")
                continue

            print(f"⏳ Transforming {year}-{month_name}...")
            try:
                # 1. Read Data
                # low_memory=False fixes the DtypeWarning
                # lambda for usecols prevents crashes if a month's CSV is missing some expected columns
                df = pd.read_csv(
                    input_csv, 
                    usecols=lambda c: c in COLS_TO_KEEP, 
                    low_memory=False
                )
                
                # Safety check: Ensure the critical columns actually exist in this month's file
                if 'last_reported' not in df.columns or 'station_id' not in df.columns:
                    print(f"  ❌ -> Error: Essential columns ('last_reported' or 'station_id') missing in {file_prefix}.")
                    continue

                # Convert Unix timestamp to datetime, coerce errors to NaT (Not a Time) to handle bad rows
                df['dt_reported'] = pd.to_datetime(df['last_reported'], unit='s', errors='coerce')
                
                # Drop any rows where the timestamp was completely broken/unreadable
                df = df.dropna(subset=['dt_reported'])

                # 2. Filter by Time (6:00 to 22:00)
                mask = (df['dt_reported'].dt.hour >= 6) & (df['dt_reported'].dt.hour < 22)
                df_filtered = df.loc[mask].copy()

                # 3. Filter to 15-minute intervals
                # Create a 15-minute interval bucket (e.g., 06:12 becomes 06:00)
                df_filtered['interval_15m'] = df_filtered['dt_reported'].dt.floor('15min')
                
                # Sort by station and time, then keep the LAST report within each 15 min bucket per station
                df_filtered = df_filtered.sort_values(by=['station_id', 'dt_reported'])
                df_15m = df_filtered.drop_duplicates(subset=['station_id', 'interval_15m'], keep='last')

                # 4. Save to Parquet
                # Drop the temporary datetime columns used for filtering
                df_15m = df_15m.drop(columns=['dt_reported', 'interval_15m'])
                df_15m.to_parquet(output_parquet, index=False)
                
                print(f"  ✅ -> Successfully saved: {output_parquet.name}")

            except Exception as e:
                print(f"  ❌ -> Error transforming {file_prefix}: {e}")

if __name__ == "__main__":
    # download_raw_data()
    transform_data()