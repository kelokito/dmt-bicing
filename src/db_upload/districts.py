"""
Upload preprocessed district polygons to PostgreSQL/PostGIS.

Source:  data/processed/neighborhoods/districts.parquet
Target:  districts table

Run after: src/preprocessing/districts.py
"""

import logging
import sys
from pathlib import Path

import pandas as pd
from psycopg2 import Binary
from shapely import wkt as shapely_wkt

sys.path.insert(0, str(Path(__file__).parent))
from _db import PROCESSED, connect, execute, execute_values

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

PARQUET = PROCESSED / "neighborhoods" / "districts.parquet"

CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS districts (
    codi_districte SMALLINT      PRIMARY KEY,
    nom_districte  VARCHAR(100)  NOT NULL,
    geom           GEOMETRY(MultiPolygon, 4326)
);
"""

UPSERT = """
INSERT INTO districts (codi_districte, nom_districte, geom)
VALUES %s
ON CONFLICT (codi_districte) DO UPDATE SET
    nom_districte = EXCLUDED.nom_districte,
    geom          = EXCLUDED.geom;
"""

# WGS84 geometry already in EPSG:4326 — no transform needed
GEOM_TEMPLATE = "(%s, %s, ST_Multi(ST_SetSRID(ST_GeomFromWKB(%s), 4326)))"


def main() -> None:
    if not PARQUET.exists():
        raise FileNotFoundError(f"Parquet not found: {PARQUET}\nRun src/preprocessing/districts.py first.")

    log.info("Reading %s", PARQUET.name)
    df = pd.read_parquet(PARQUET)

    log.info("Connecting to database…")
    conn = connect()

    log.info("Creating table…")
    execute(conn, CREATE_TABLE)

    records = [
        (
            int(row["codi_districte"]),
            row["nom_districte"],
            Binary(shapely_wkt.loads(row["geometry_wgs84"]).wkb),
        )
        for _, row in df.iterrows()
    ]

    log.info("Upserting %d districts…", len(records))
    execute_values(conn, UPSERT, records, template=GEOM_TEMPLATE)

    conn.close()

    print("\n=== Summary ===")
    for r in records:
        print(f"  {r[0]:02d}  {r[1]}")
    print(f"\nRows upserted: {len(records)}")


if __name__ == "__main__":
    main()
