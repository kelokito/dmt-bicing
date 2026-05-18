"""
Upload Points of Interest to PostgreSQL/PostGIS.

Source:  data/processed/poi/poi.parquet
Target:  pois table

Run after: src/preprocessing/poi.py
"""

import logging
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from _db import PROCESSED, connect, execute, execute_values

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

PARQUET = PROCESSED / "poi" / "poi.parquet"

CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS pois (
    register_id  VARCHAR(50)   PRIMARY KEY,
    name         VARCHAR(500),
    category     VARCHAR(100),
    address      VARCHAR(500),
    neighborhood VARCHAR(100),
    district     VARCHAR(100),
    zip_code     VARCHAR(10),
    lat          DOUBLE PRECISION,
    lon          DOUBLE PRECISION,
    geom         GEOMETRY(Point, 4326)
);
"""

UPSERT = """
INSERT INTO pois (register_id, name, category, address, neighborhood, district, zip_code, lat, lon, geom)
VALUES %s
ON CONFLICT (register_id) DO UPDATE SET
    name         = EXCLUDED.name,
    category     = EXCLUDED.category,
    address      = EXCLUDED.address,
    neighborhood = EXCLUDED.neighborhood,
    district     = EXCLUDED.district,
    zip_code     = EXCLUDED.zip_code,
    lat          = EXCLUDED.lat,
    lon          = EXCLUDED.lon,
    geom         = EXCLUDED.geom;
"""

TEMPLATE = "(%s, %s, %s, %s, %s, %s, %s, %s, %s, ST_SetSRID(ST_Point(%s, %s), 4326))"


def _py(val):
    if pd.isna(val):
        return None
    return val.item() if hasattr(val, "item") else val


def main() -> None:
    if not PARQUET.exists():
        raise FileNotFoundError(
            f"Parquet not found: {PARQUET}\nRun src/preprocessing/poi.py first."
        )

    log.info("Reading %s", PARQUET.name)
    df = pd.read_parquet(PARQUET)

    log.info("Connecting to database…")
    conn = connect()

    log.info("Creating table…")
    execute(conn, CREATE_TABLE)

    records = []
    for _, row in df.iterrows():
        lon = _py(row["geo_epgs_4326_lon"])
        lat = _py(row["geo_epgs_4326_lat"])
        records.append((
            _py(row["register_id"]),
            _py(row["name"]),
            _py(row["category"]),
            _py(row["addresses_road_name"]),
            _py(row["addresses_neighborhood_name"]),
            _py(row["addresses_district_name"]),
            str(int(row["addresses_zip_code"])) if not pd.isna(row["addresses_zip_code"]) else None,
            lat, lon,                   # stored lat/lon
            lon, lat,                   # for ST_Point(x=lon, y=lat)
        ))

    log.info("Upserting %d POIs…", len(records))
    execute_values(conn, UPSERT, records, template=TEMPLATE)

    conn.close()

    counts = df["category"].value_counts()
    print(f"\n=== Summary ===")
    print(f"POIs upserted: {len(records)}")
    print("\nBy category:")
    for cat, n in counts.items():
        print(f"  {n:>4}  {cat}")


if __name__ == "__main__":
    main()
