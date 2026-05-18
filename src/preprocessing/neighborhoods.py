"""
Preprocess Barcelona neighborhood data into a single enriched table.

Inputs:
  data/raw/neighborhoods/neighborhoods_polygons_opendata.csv  — polygon geometry
  data/raw/neighborhoods/2025_pad_mdbas.csv                  — population by census section
  data/raw/neighborhoods/2025_pad_mdbas_edat-1.csv           — population by age and census section
  data/raw/neighborhoods/income_normalized_by_neighborhood.csv — income index 2015–2022

Output:
  data/processed/neighborhoods/neighborhoods.parquet

One row per neighborhood (73 total). Geometry stored as WKT strings.
Area computed from the ETRS89 projected geometry (metres²).
Income index is relative to Barcelona = 100.
"""

import logging
from pathlib import Path

import pandas as pd
from shapely import wkt as shapely_wkt

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = ROOT / "data" / "raw" / "neighborhoods"
OUT_DIR = ROOT / "data" / "processed" / "neighborhoods"
OUT_FILE = OUT_DIR / "neighborhoods.parquet"

INCOME_YEARS = [str(y) for y in range(2015, 2023)]

# Three neighborhoods have different names in the income file vs the polygon file
INCOME_NAME_FIX = {
    "el Poble Sec - AEI Parc de Montjuïc": "el Poble-sec",
    "la Marina del Prat Vermell - AEI Zona Franca": "la Marina del Prat Vermell",
    "Sant Andreu (Barri)": "Sant Andreu",
}


def load_polygons() -> pd.DataFrame:
    df = pd.read_csv(RAW_DIR / "neighborhoods_polygons_opendata.csv")
    df = df.rename(columns={
        "codi_districte": "codi_districte",
        "nom_districte": "nom_districte",
        "codi_barri": "codi_barri",
        "nom_barri": "nom_barri",
        "geometria_etrs89": "geometry_etrs89",
        "geometria_wgs84": "geometry_wgs84",
    })
    # Area from ETRS89 projected CRS (unit = metres)
    df["area_m2"] = df["geometry_etrs89"].apply(
        lambda wkt: shapely_wkt.loads(wkt).area
    )
    return df


def load_population() -> pd.DataFrame:
    df = pd.read_csv(RAW_DIR / "2025_pad_mdbas.csv")
    pop = (
        df.groupby("Codi_Barri")["Valor"]
        .sum()
        .reset_index()
        .rename(columns={"Codi_Barri": "codi_barri", "Valor": "population"})
    )
    return pop


def load_age_stats() -> pd.DataFrame:
    df = pd.read_csv(RAW_DIR / "2025_pad_mdbas_edat-1.csv")
    df["Valor"] = pd.to_numeric(df["Valor"], errors="coerce").fillna(0).astype(int)
    by_age = df.groupby(["Codi_Barri", "EDAT_1"])["Valor"].sum().reset_index()

    total = by_age.groupby("Codi_Barri")["Valor"].sum().rename("total")
    by_age = by_age.join(total, on="Codi_Barri")

    stats = by_age.groupby("Codi_Barri").apply(
        lambda g: pd.Series({
            "mean_age": (g["EDAT_1"] * g["Valor"]).sum() / g["Valor"].sum(),
            "pct_youth":       g.loc[g["EDAT_1"] <= 14,  "Valor"].sum() / g["Valor"].sum() * 100,
            "pct_working_age": g.loc[g["EDAT_1"].between(15, 64), "Valor"].sum() / g["Valor"].sum() * 100,
            "pct_elderly":     g.loc[g["EDAT_1"] >= 65,  "Valor"].sum() / g["Valor"].sum() * 100,
        }),
        include_groups=False,
    ).reset_index().rename(columns={"Codi_Barri": "codi_barri"})

    for col in ["mean_age", "pct_youth", "pct_working_age", "pct_elderly"]:
        stats[col] = stats[col].round(2)

    return stats


def load_income() -> pd.DataFrame:
    df = pd.read_csv(RAW_DIR / "income_normalized_by_neighborhood.csv")

    # Keep rows typed 'Barri' plus the unlabelled Sant Andreu (Barri) row
    barris = df[df["Tipus de territori"] == "Barri"].copy()
    sant_andreu = df[df["Territori"] == "Sant Andreu (Barri)"].copy()
    income = pd.concat([barris, sant_andreu], ignore_index=True)

    # Fix neighbourhood names to match the polygon file
    income["Territori"] = income["Territori"].replace(INCOME_NAME_FIX)

    # Convert European decimal format ("123,45" → 123.45)
    for year in INCOME_YEARS:
        income[year] = income[year].astype(str).str.replace(",", ".").astype(float)

    income = income[["Territori"] + INCOME_YEARS].rename(
        columns={"Territori": "nom_barri"}
        | {y: f"income_idx_{y}" for y in INCOME_YEARS}
    )
    return income


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    log.info("Loading polygons…")
    polygons = load_polygons()

    log.info("Loading population…")
    pop = load_population()

    log.info("Loading age statistics…")
    age = load_age_stats()

    log.info("Loading income…")
    income = load_income()

    df = polygons.merge(pop, on="codi_barri", how="left")
    df = df.merge(age, on="codi_barri", how="left")
    df = df.merge(income, on="nom_barri", how="left")

    # Derived: population density
    df["pop_density_km2"] = (df["population"] / (df["area_m2"] / 1e6)).round(1)

    unmatched_income = df["income_idx_2022"].isna().sum()
    if unmatched_income:
        log.warning("%d neighbourhoods have no income data: %s",
                    unmatched_income, df.loc[df["income_idx_2022"].isna(), "nom_barri"].tolist())

    col_order = [
        "codi_barri", "nom_barri",
        "codi_districte", "nom_districte",
        "geometry_etrs89", "geometry_wgs84",
        "area_m2",
        "population", "pop_density_km2",
        "mean_age", "pct_youth", "pct_working_age", "pct_elderly",
    ] + [f"income_idx_{y}" for y in INCOME_YEARS]
    df = df[col_order]

    df.to_parquet(OUT_FILE, index=False)
    log.info("Saved → %s", OUT_FILE)

    print("\n=== Summary ===")
    print(f"Neighbourhoods:   {len(df)}")
    print(f"Districts:        {df['codi_districte'].nunique()}")
    print(f"Total population: {df['population'].sum():,.0f}")
    print(f"Income matched:   {df['income_idx_2022'].notna().sum()}/{len(df)}")
    print(f"Output:           {OUT_FILE}")


if __name__ == "__main__":
    main()
