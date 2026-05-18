"""
Upload preprocessed neighborhood data to PostgreSQL/PostGIS.

Source:  data/processed/neighborhoods/neighborhoods.parquet
Target:  neighborhoods table (references districts)

Run after: src/preprocessing/neighborhoods.py and db_upload/districts.py
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

PARQUET = PROCESSED / "neighborhoods" / "neighborhoods.parquet"

INCOME_YEARS = [str(y) for y in range(2015, 2023)]

CREATE_TABLE = f"""
CREATE TABLE IF NOT EXISTS neighborhoods (
    codi_barri       INTEGER       PRIMARY KEY,
    nom_barri        VARCHAR(100)  NOT NULL,
    codi_districte   SMALLINT      NOT NULL REFERENCES districts(codi_districte),
    area_m2          DOUBLE PRECISION,
    population       INTEGER,
    pop_density_km2  DOUBLE PRECISION,
    mean_age         DOUBLE PRECISION,
    pct_youth        DOUBLE PRECISION,
    pct_working_age  DOUBLE PRECISION,
    pct_elderly      DOUBLE PRECISION,
    {", ".join(f"income_idx_{y} DOUBLE PRECISION" for y in INCOME_YEARS)},
    geom             GEOMETRY(MultiPolygon, 4326)
);
"""

INCOME_COLS = ", ".join(f"income_idx_{y}" for y in INCOME_YEARS)
INCOME_EXCL = ", ".join(f"income_idx_{y} = EXCLUDED.income_idx_{y}" for y in INCOME_YEARS)

UPSERT = f"""
INSERT INTO neighborhoods (
    codi_barri, nom_barri, codi_districte,
    area_m2, population, pop_density_km2,
    mean_age, pct_youth, pct_working_age, pct_elderly,
    {INCOME_COLS},
    geom
)
VALUES %s
ON CONFLICT (codi_barri) DO UPDATE SET
    nom_barri        = EXCLUDED.nom_barri,
    codi_districte   = EXCLUDED.codi_districte,
    area_m2          = EXCLUDED.area_m2,
    population       = EXCLUDED.population,
    pop_density_km2  = EXCLUDED.pop_density_km2,
    mean_age         = EXCLUDED.mean_age,
    pct_youth        = EXCLUDED.pct_youth,
    pct_working_age  = EXCLUDED.pct_working_age,
    pct_elderly      = EXCLUDED.pct_elderly,
    {INCOME_EXCL},
    geom             = EXCLUDED.geom;
"""

# 10 non-geometry fields + 8 income years + 1 geometry
_N = 10 + len(INCOME_YEARS) + 1
GEOM_TEMPLATE = "(" + ", ".join(["%s"] * (_N - 1)) + ", ST_Multi(ST_SetSRID(ST_GeomFromWKB(%s), 4326)))"


def _py(val):
    """Convert pandas NA to None; numpy scalars to Python native types."""
    if pd.isna(val):
        return None
    return val.item() if hasattr(val, "item") else val


def main() -> None:
    if not PARQUET.exists():
        raise FileNotFoundError(
            f"Parquet not found: {PARQUET}\nRun src/preprocessing/neighborhoods.py first."
        )

    log.info("Reading %s", PARQUET.name)
    df = pd.read_parquet(PARQUET)

    log.info("Connecting to database…")
    conn = connect()

    log.info("Creating table…")
    execute(conn, CREATE_TABLE)

    records = []
    for _, row in df.iterrows():
        income = [_py(row[f"income_idx_{y}"]) for y in INCOME_YEARS]
        records.append((
            int(row["codi_barri"]),
            row["nom_barri"],
            int(row["codi_districte"]),
            _py(row["area_m2"]),
            _py(row["population"]),
            _py(row["pop_density_km2"]),
            _py(row["mean_age"]),
            _py(row["pct_youth"]),
            _py(row["pct_working_age"]),
            _py(row["pct_elderly"]),
            *income,
            Binary(shapely_wkt.loads(row["geometry_wgs84"]).wkb),
        ))

    log.info("Upserting %d neighbourhoods…", len(records))
    execute_values(conn, UPSERT, records, template=GEOM_TEMPLATE)

    conn.close()

    print(f"\n=== Summary ===")
    print(f"Rows upserted: {len(records)}")


if __name__ == "__main__":
    main()
