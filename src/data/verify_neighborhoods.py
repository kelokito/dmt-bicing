import geopandas as gpd
from pathlib import Path

# Update this path if your script is in a different directory relative to the data folder
FILE_PATH = "data/neighborhoods/neighborhoods.parquet"

def verify_parquet():
    print(f"📂 Attempting to read: {FILE_PATH}...\n")
    
    # Check if the file actually exists before trying to read it
    if not Path(FILE_PATH).exists():
        print("❌ Error: File not found! Check the file path.")
        return

    try:
        # Load the GeoParquet file
        gdf = gpd.read_parquet(FILE_PATH)
        
        print("✅ Successfully loaded the Parquet file!\n")
        
        print("📊 Columns:")
        print(gdf.columns.tolist())
        print("\n" + "-" * 40)
        
        print(f"📏 Shape: {gdf.shape[0]} rows, {gdf.shape[1]} columns")
        print("-" * 40)
        
        print("🔠 Data Types:")
        print(gdf.dtypes)
        print("-" * 40)
        
        print("👀 First 3 rows:")
        # Drop the geometry column just for a cleaner console printout
        print(gdf.drop(columns=['geometry']).head(3))
        
    except Exception as e:
        print(f"❌ Error reading the file: {e}")

if __name__ == "__main__":
    verify_parquet()