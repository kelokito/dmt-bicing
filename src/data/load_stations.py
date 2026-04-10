import pandas as pd
from pathlib import Path

# --- Configuration ---
# Point directly to the local file
INFO_FILE = "2026_03_Marc_BicingNou_INFORMACIO" 
RAW_DIR = Path("./data/raw_csv")
OUTPUT_DIR = Path("./data")

# The specific geographic columns you need
COLS_TO_KEEP = ["station_id", "lat", "lon", "name", "capacity"]

def extract_station_locations():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    
    csv_filename = RAW_DIR / f"{INFO_FILE}.csv"
    output_file = OUTPUT_DIR / "bicing_station_locations.parquet"

    print("📍 Locating local station information...")

    # Safety check to ensure the file is actually in the folder
    if not csv_filename.exists():
        print(f"❌ Error: Could not find the file at {csv_filename}")
        print("Please make sure you have placed the extracted CSV in the 'data/raw_csv' folder.")
        return

    try:
        # 1. Read and Filter
        print(f"⚙️ Processing location data from {csv_filename.name}...")
        df = pd.read_csv(csv_filename, usecols=lambda c: c in COLS_TO_KEEP)

        # These files log the station info multiple times a day.
        # We just need one unique entry per station ID.
        df_unique_stations = df.drop_duplicates(subset=["station_id"], keep="last")

        # 2. Save
        df_unique_stations.to_parquet(output_file, index=False)
        print(f"✅ Successfully extracted {len(df_unique_stations)} station locations!")
        print(f"💾 Saved to: {output_file}")

    except Exception as e:
        print(f"❌ Error processing locations: {e}")

if __name__ == "__main__":
    extract_station_locations()