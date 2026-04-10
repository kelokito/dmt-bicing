import pandas as pd
from pathlib import Path
import geopandas as gpd
from shapely import wkt
import difflib

# --- Configuration ---
INCOME_CSV = "data/raw_csv/income_normalized_by_neighborhood.csv"
GEOMETRIES_CSV = "data/raw_csv/neighborhoods_polygons_opendata.csv"
POP_TOTAL_CSV = "data/raw_csv/2025_pad_mdbas.csv"
POP_AGE_CSV = "data/raw_csv/2025_pad_mdbas_edat-1.csv"

OUTPUT_DIR = Path("data/neighborhoods")

# Explicit overrides for common BCN Open Data discrepancies
MANUAL_OVERRIDES = {
    "la Marina del Prat Vermell - AEI Zona Franca": "la Marina del Prat Vermell",
    "el Poble Sec": "el Poble-sec",
    "el Gòtic": "el Barri Gòtic"
}
def fuzzy_match_names(df_target, base_names, col_name="nom_barri", cutoff=0.5):
    """
    Attempts direct matching, manual overrides, and finally lenient fuzzy matching.
    Guarantees no duplicate matches by tracking and removing used base names.
    """
    direct_matches = 0
    fuzzy_matches = 0
    override_matches = 0
    missing = 0
    
    matched_names = []
    
    # Create a copy of the base names that we can remove items from as they are claimed
    available_bases = list(base_names)
    
    for name in df_target[col_name]:
        # 1. Direct Match
        if name in available_bases:
            matched_names.append(name)
            available_bases.remove(name) # Claim this neighborhood
            direct_matches += 1
            
        # 2. Manual Override Check
        elif name in MANUAL_OVERRIDES and MANUAL_OVERRIDES[name] in available_bases:
            matched_names.append(MANUAL_OVERRIDES[name])
            available_bases.remove(MANUAL_OVERRIDES[name]) # Claim this neighborhood
            override_matches += 1
            print(f"    🛠️ Manual Override: '{name}' -> '{MANUAL_OVERRIDES[name]}'")
            
        else:
            # 3. Fuzzy Match against ONLY the REMAINING available bases
            closest = difflib.get_close_matches(name, available_bases, n=1, cutoff=cutoff)
            if closest:
                matched_names.append(closest[0])
                available_bases.remove(closest[0]) # Claim this neighborhood
                fuzzy_matches += 1
                print(f"    🔗 Fuzzy Matched: '{name}' -> '{closest[0]}'")
            else:
                # 4. No match found
                matched_names.append(None)
                missing += 1
                print(f"    ❌ No match found for: '{name}'")
                
    df_target['matched_barri'] = matched_names
    print(f"  -> Direct: {direct_matches} | Overrides: {override_matches} | Fuzzy: {fuzzy_matches} | Missing: {missing}")
    
    return df_target

def process_neighborhoods_data():
    print("⚙️ Processing Neighborhoods Data...")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # ---------------------------------------------------------
    # 0. Base Geometries 
    # ---------------------------------------------------------
    df_geom = pd.read_csv(GEOMETRIES_CSV)
    df_geom['codi_barri'] = df_geom['codi_barri'].astype(int)
    
    # Clean up geometry columns
    if 'geometria_etrs89' in df_geom.columns:
        df_geom = df_geom.drop(columns=['geometria_etrs89'])

    geom_col = [col for col in df_geom.columns if 'geom' in col.lower() or 'wkt' in col.lower()][0]
    df_geom['geometry'] = df_geom[geom_col].apply(wkt.loads)
    if geom_col != 'geometry':
        df_geom = df_geom.drop(columns=[geom_col])
    
    gdf_neighborhoods = gpd.GeoDataFrame(df_geom, geometry='geometry', crs='EPSG:4326')
    master_names = gdf_neighborhoods['nom_barri'].unique().tolist()

    # ---------------------------------------------------------
    # 1. Process Income Data
    # ---------------------------------------------------------
    print("\n📊 Matching Income Data...")
    df_income = pd.read_csv(INCOME_CSV)
    df_income = df_income[df_income['Tipus de territori'] == 'Barri'].copy()
    df_income['income_2022'] = df_income['2022'].str.replace(',', '.').astype(float)
    df_income = df_income[['Territori', 'income_2022']].rename(columns={'Territori': 'nom_barri'})
    
    df_income = fuzzy_match_names(df_income, master_names)
    df_income = df_income.dropna(subset=['matched_barri'])

    # ---------------------------------------------------------
    # 2. Process Total Population Data
    # ---------------------------------------------------------
    print("\n👥 Matching Population Data...")
    df_pop = pd.read_csv(POP_TOTAL_CSV)
    df_pop_total = df_pop.groupby('Nom_Barri')['Valor'].sum().reset_index()
    df_pop_total = df_pop_total.rename(columns={'Nom_Barri': 'nom_barri', 'Valor': 'population_total'})
    
    df_pop_total = fuzzy_match_names(df_pop_total, master_names)
    df_pop_total = df_pop_total.dropna(subset=['matched_barri'])

    # ---------------------------------------------------------
    # 3. Join Everything
    # ---------------------------------------------------------
    print("\n🧩 Joining Datasets...")
    gdf_neighborhoods = gdf_neighborhoods.merge(
        df_income.drop(columns=['nom_barri']), 
        left_on='nom_barri', 
        right_on='matched_barri', 
        how='left'
    ).drop(columns=['matched_barri'])
    
    gdf_neighborhoods = gdf_neighborhoods.merge(
        df_pop_total.drop(columns=['nom_barri']), 
        left_on='nom_barri', 
        right_on='matched_barri', 
        how='left'
    ).drop(columns=['matched_barri'])

    # 🚨 CRITICAL FIXES FOR PYARROW BUG 🚨
    # 1. Reset the index so Pandas doesn't pass corrupted block metadata
    gdf_neighborhoods = gdf_neighborhoods.reset_index(drop=True)
    
    # 2. Fill missing Income data (la Clota) with 0.0 to prevent PyArrow from choking on NaNs
    if 'income_2022' in gdf_neighborhoods.columns:
        gdf_neighborhoods['income_2022'] = gdf_neighborhoods['income_2022'].fillna(0.0)

    # 3. Cast firmly to GeoDataFrame
    gdf_neighborhoods = gpd.GeoDataFrame(gdf_neighborhoods, geometry='geometry', crs='EPSG:4326')

    # 4. Save with schema_version="1.0.0" to bypass buggy histogram statistics 
    output_neighborhoods = OUTPUT_DIR / "neighborhoods.parquet"
    gdf_neighborhoods.to_parquet(output_neighborhoods, index=False, schema_version="1.0.0")
    print(f"\n✅ Successfully saved: {output_neighborhoods.name} (Rows: {len(gdf_neighborhoods)})")

    # ---------------------------------------------------------
    # 4. Process Population by Age Group
    # ---------------------------------------------------------
    print("\n⚙️ Processing Population by Age Data...")
    df_age = pd.read_csv(POP_AGE_CSV)
    df_age_grouped = df_age.groupby(['Codi_Barri', 'EDAT_1'])['Valor'].sum().reset_index()
    df_age_grouped = df_age_grouped.rename(columns={
        'Codi_Barri': 'neighborhood_id',
        'EDAT_1': 'age_group',
        'Valor': 'population'
    })

    output_people = OUTPUT_DIR / "neighborhoods_people.parquet"
    df_age_grouped.to_parquet(output_people, index=False)
    print(f"✅ Successfully saved: {output_people.name} (Rows: {len(df_age_grouped)})")

if __name__ == "__main__":
    process_neighborhoods_data()