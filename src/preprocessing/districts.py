"""
Preprocess Barcelona district polygons.

Input:
  data/raw/neighborhoods/neighborhoods_polygons_opendata.csv  — neighborhood polygons

Output:
  data/processed/neighborhoods/districts.parquet

One row per district (10 total). District polygons are derived by dissolving
all neighbourhood geometries within each district via shapely union.
Geometry stored as WKT strings (both ETRS89 and WGS84 projections).
"""

import logging
from pathlib import Path

import pandas as pd
from shapely import wkt as shapely_wkt
from shapely.ops import unary_union

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = ROOT / "data" / "raw" / "neighborhoods"
OUT_DIR = ROOT / "data" / "processed" / "neighborhoods"
OUT_FILE = OUT_DIR / "districts.parquet"


def dissolve_geometries(series: pd.Series) -> str:
    geoms = [shapely_wkt.loads(wkt) for wkt in series]
    return unary_union(geoms).wkt


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    log.info("Loading neighbourhood polygons…")
    df = pd.read_csv(RAW_DIR / "neighborhoods_polygons_opendata.csv")

    log.info("Dissolving neighbourhood polygons into district polygons…")
    districts = (
        df.groupby(["codi_districte", "nom_districte"], sort=True)
        .apply(
            lambda g: pd.Series({
                "geometry_etrs89": dissolve_geometries(g["geometria_etrs89"]),
                "geometry_wgs84":  dissolve_geometries(g["geometria_wgs84"]),
            }),
            include_groups=False,
        )
        .reset_index()
    )

    districts.to_parquet(OUT_FILE, index=False)
    log.info("Saved → %s", OUT_FILE)

    print("\n=== Summary ===")
    print(f"Districts: {len(districts)}")
    for _, row in districts.iterrows():
        print(f"  {int(row['codi_districte']):02d}  {row['nom_districte']}")
    print(f"Output:    {OUT_FILE}")


if __name__ == "__main__":
    main()
