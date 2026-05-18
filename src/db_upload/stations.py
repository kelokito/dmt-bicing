"""
Upload current station configuration to PostgreSQL/PostGIS.

Source:  data/processed/stations_information/stations_info_history.parquet
Target:  stations table (references neighborhoods)

The bi-temporal history is collapsed to the current valid state:
  - rows where valid_to IS NULL are still active
  - for retired stations, the most recent period is used

Neighborhood assignment is resolved by PostGIS ST_Within against the
neighborhoods table — stations outside all neighbourhoods get NULL.

Run after: src/preprocessing/station_information.py and db_upload/neighborhoods.py
"""

import logging
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from _db import PROCESSED, connect, execute, execute_values, fetch_all

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

PARQUET = PROCESSED / "stations_information" / "stations_info_history.parquet"

CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS stations (
    station_id           INTEGER       PRIMARY KEY,
    neighborhood_id      INTEGER       REFERENCES neighborhoods(codi_barri),
    name                 VARCHAR(255),
    physical_configuration VARCHAR(50),
    address              VARCHAR(255),
    cross_street         VARCHAR(255),
    post_code            INTEGER,
    capacity             INTEGER,
    is_charging_station  BOOLEAN,
    nearby_distance      INTEGER,
    lat                  DOUBLE PRECISION,
    lon                  DOUBLE PRECISION,
    elevation_m          DOUBLE PRECISION,
    geom                 GEOMETRY(Point, 4326)
);
"""

# Neighbourhood resolved via ST_Within subquery in the DB
UPSERT = """
INSERT INTO stations (
    station_id, neighborhood_id, name, physical_configuration,
    address, cross_street, post_code, capacity,
    is_charging_station, nearby_distance,
    lat, lon, elevation_m, geom
)
VALUES %s
ON CONFLICT (station_id) DO UPDATE SET
    neighborhood_id      = EXCLUDED.neighborhood_id,
    name                 = EXCLUDED.name,
    physical_configuration = EXCLUDED.physical_configuration,
    address              = EXCLUDED.address,
    cross_street         = EXCLUDED.cross_street,
    post_code            = EXCLUDED.post_code,
    capacity             = EXCLUDED.capacity,
    is_charging_station  = EXCLUDED.is_charging_station,
    nearby_distance      = EXCLUDED.nearby_distance,
    lat                  = EXCLUDED.lat,
    lon                  = EXCLUDED.lon,
    elevation_m          = EXCLUDED.elevation_m,
    geom                 = EXCLUDED.geom;
"""

# neighborhood_id is a subquery; lon/lat appear twice (point + ST_Within)
TEMPLATE = """(
    %s,
    (SELECT codi_barri FROM neighborhoods
     WHERE ST_Within(ST_SetSRID(ST_Point(%s, %s), 4326), geom)
     LIMIT 1),
    %s, %s, %s, %s, %s, %s, %s, %s,
    %s, %s, %s,
    ST_SetSRID(ST_Point(%s, %s), 4326)
)"""


def _py(val):
    if pd.isna(val):
        return None
    return val.item() if hasattr(val, "item") else val


def current_state(df: pd.DataFrame) -> pd.DataFrame:
    """Return one row per station reflecting its current configuration."""
    active = df[df["valid_to"].isna()].copy()
    retired_ids = set(df["station_id"].unique()) - set(active["station_id"].unique())
    if retired_ids:
        latest = (
            df[df["station_id"].isin(retired_ids)]
            .sort_values("valid_from")
            .groupby("station_id")
            .last()
            .reset_index()
        )
        active = pd.concat([active, latest], ignore_index=True)
    return active


def main() -> None:
    if not PARQUET.exists():
        raise FileNotFoundError(
            f"Parquet not found: {PARQUET}\n"
            "Run src/preprocessing/station_information.py first."
        )

    log.info("Reading %s", PARQUET.name)
    df = pd.read_parquet(PARQUET)

    log.info("Collapsing to current state…")
    current = current_state(df)
    current = current.dropna(subset=["station_id"])
    log.info("%d stations (of which %d still active)",
             len(current), df["valid_to"].isna().sum())

    log.info("Connecting to database…")
    conn = connect()

    log.info("Creating table…")
    execute(conn, CREATE_TABLE)

    records = []
    for _, row in current.iterrows():
        lon = _py(row["lon"])
        lat = _py(row["lat"])
        sid = _py(row["station_id"])
        if sid is None:
            continue
        records.append((
            int(sid),
            lon, lat,                           # for ST_Within subquery
            _py(row["name"]),
            _py(row["physical_configuration"]),
            _py(row["address"]),
            _py(row["cross_street"]),
            _py(row["post_code"]),
            _py(row["capacity"]),
            _py(row["is_charging_station"]),
            _py(row["nearby_distance"]),
            lat, lon,                           # stored lat/lon columns
            _py(row["elevation_m"]),
            lon, lat,                           # for ST_Point geom
        ))

    log.info("Upserting %d stations…", len(records))
    execute_values(conn, UPSERT, records, template=TEMPLATE)

    # Count how many got a neighbourhood assigned
    matched = fetch_all(conn, "SELECT COUNT(*) FROM stations WHERE neighborhood_id IS NOT NULL")[0][0]
    total = fetch_all(conn, "SELECT COUNT(*) FROM stations")[0][0]

    conn.close()

    print(f"\n=== Summary ===")
    print(f"Stations upserted:       {len(records)}")
    print(f"Neighbourhood matched:   {matched}/{total}")


if __name__ == "__main__":
    main()
