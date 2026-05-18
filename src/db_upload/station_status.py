"""
Upload station status snapshots and build MobilityDB temporal sequences.

Source:  data/processed/station_status/station_status.parquet
Targets:
  1. station_status_input  — flat snapshot table (44 M+ rows)
  2. station_status_mdb    — MobilityDB temporal sequences (requires MobilityDB extension)

Two-step process:
  Step 1: batch-insert all snapshots into station_status_input.
  Step 2: run a single GROUP BY migration query to build tint/ttext sequences
          in station_status_mdb.

Rows whose station_id is not present in the stations table are silently skipped
(ON CONFLICT DO NOTHING handles duplicate report times).

Run after: src/preprocessing/station_status.py and db_upload/stations.py

Note: step 2 can take several minutes on large datasets.
"""

import argparse
import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from _db import PROCESSED, connect, execute, execute_values, fetch_all

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

PARQUET = PROCESSED / "station_status" / "station_status.parquet"

# ---------------------------------------------------------------------------
# DDL
# ---------------------------------------------------------------------------

CREATE_INPUT = """
CREATE TABLE IF NOT EXISTS station_status_input (
    station_id   INTEGER    REFERENCES stations(station_id),
    report_time  TIMESTAMPTZ,
    num_bikes    INTEGER,
    num_ebikes   INTEGER,
    num_mechanical INTEGER,
    docks_free   INTEGER,
    status       VARCHAR(20),
    PRIMARY KEY (station_id, report_time)
);
"""

CREATE_MDB = """
CREATE TABLE IF NOT EXISTS station_status_mdb (
    station_id        INTEGER  PRIMARY KEY REFERENCES stations(station_id),
    bikes_history     tint,
    ebikes_history    tint,
    mechanical_history tint,
    docks_history     tint,
    status_history    ttext
);
"""

# ---------------------------------------------------------------------------
# DML
# ---------------------------------------------------------------------------

INSERT_INPUT = """
INSERT INTO station_status_input
    (station_id, report_time, num_bikes, num_ebikes, num_mechanical, docks_free, status)
VALUES %s
ON CONFLICT (station_id, report_time) DO NOTHING;
"""

MIGRATE_TO_MDB = """
INSERT INTO station_status_mdb (
    station_id, bikes_history, ebikes_history,
    mechanical_history, docks_history, status_history
)
SELECT
    station_id,
    tint_seq(array_agg(tint_inst(num_bikes,      report_time) ORDER BY report_time)),
    tint_seq(array_agg(tint_inst(num_ebikes,     report_time) ORDER BY report_time)),
    tint_seq(array_agg(tint_inst(num_mechanical, report_time) ORDER BY report_time)),
    tint_seq(array_agg(tint_inst(docks_free,     report_time) ORDER BY report_time)),
    ttext_seq(array_agg(ttext_inst(status,       report_time) ORDER BY report_time))
FROM station_status_input
WHERE num_bikes      IS NOT NULL
  AND num_ebikes     IS NOT NULL
  AND num_mechanical IS NOT NULL
  AND docks_free     IS NOT NULL
  AND status         IS NOT NULL
GROUP BY station_id
ON CONFLICT (station_id) DO UPDATE SET
    bikes_history      = EXCLUDED.bikes_history,
    ebikes_history     = EXCLUDED.ebikes_history,
    mechanical_history = EXCLUDED.mechanical_history,
    docks_history      = EXCLUDED.docks_history,
    status_history     = EXCLUDED.status_history;
"""

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

COLS = [
    "station_id", "last_updated", "num_bikes_available",
    "num_bikes_ebike", "num_bikes_mechanical", "num_docks_available", "status",
]
CHUNK_SIZE = 1_000_000  # rows per commit (balances memory and transaction log)


def _to_records(df: pd.DataFrame) -> list[tuple]:
    """Convert a chunk DataFrame to a list of tuples with NA → None."""
    arrays = [df[c].to_numpy(dtype=object, na_value=None) for c in COLS]
    return list(zip(*arrays))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--migrate-only",
        action="store_true",
        help="Skip Step 1 (insert) and run only the MobilityDB migration (Step 2).",
    )
    args = parser.parse_args()

    log.info("Connecting to database…")
    conn = connect()

    if not args.migrate_only:
        if not PARQUET.exists():
            raise FileNotFoundError(
                f"Parquet not found: {PARQUET}\n"
                "Run src/preprocessing/station_status.py first."
            )

        log.info("Reading %s…", PARQUET.name)
        df = pd.read_parquet(PARQUET, columns=COLS)

        log.info("Total snapshots: %d rows across %d stations",
                 len(df), df["station_id"].nunique())

        log.info("Creating tables…")
        execute(conn, CREATE_INPUT)
        execute(conn, CREATE_MDB)

        valid_ids = {row[0] for row in fetch_all(conn, "SELECT station_id FROM stations")}
        log.info("Valid station IDs in DB: %d", len(valid_ids))

        df = df[df["station_id"].isin(valid_ids)]
        log.info("Rows after filtering to known stations: %d", len(df))

        # ------------------------------------------------------------------
        # Step 1: insert snapshots in chunks
        # ------------------------------------------------------------------
        total_chunks = (len(df) + CHUNK_SIZE - 1) // CHUNK_SIZE
        inserted = 0
        for i, start in enumerate(range(0, len(df), CHUNK_SIZE)):
            chunk = df.iloc[start : start + CHUNK_SIZE]
            records = _to_records(chunk)
            execute_values(conn, INSERT_INPUT, records, page_size=50_000)
            inserted += len(records)
            log.info("Chunk %d/%d — %d rows committed (total: %d)",
                     i + 1, total_chunks, len(records), inserted)

        log.info("Step 1 complete: %d snapshots inserted into station_status_input", inserted)
    else:
        log.info("--migrate-only: skipping Step 1.")
        execute(conn, CREATE_MDB)
        inserted = fetch_all(conn, "SELECT COUNT(*) FROM station_status_input")[0][0]
        log.info("Rows already in station_status_input: %d", inserted)

    # ------------------------------------------------------------------
    # Step 2: build MobilityDB temporal sequences
    # ------------------------------------------------------------------
    log.info("Step 2: migrating to station_status_mdb (this may take several minutes)…")
    execute(conn, MIGRATE_TO_MDB)
    log.info("Migration complete.")

    count = fetch_all(conn, "SELECT COUNT(*) FROM station_status_mdb")[0][0]
    conn.close()

    print(f"\n=== Summary ===")
    print(f"Snapshots in input table: {inserted:,}")
    print(f"MobilityDB sequences:     {count}")


if __name__ == "__main__":
    main()
