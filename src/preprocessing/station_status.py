"""
Preprocess Bicing station status snapshots into a clean time-series parquet.

Input:  data/raw/stations_status/  — monthly CSV/.7z files with ~5-min snapshots
Output: data/processed/station_status/station_status.parquet

Pipeline:
  1. Load and concatenate all monthly files.
  2. Drop exact duplicate rows.
  3. Clean column names and convert timestamps to UTC.
  4. Downsample to RESAMPLE_INTERVAL (default 10 min) by taking the last
     observed snapshot in each time bin per station.  Using the last value
     (rather than mean) preserves integer bike counts, which is required for
     MobilityDB tint temporal sequences.

Raw data is at ~5-min resolution; resampling to 10 min gives ~2× row reduction
while retaining enough granularity for rush-hour demand analysis.
"""

import logging
import tempfile
from pathlib import Path

import pandas as pd
import py7zr

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths & config
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = ROOT / "data" / "raw" / "stations_status"
OUT_DIR = ROOT / "data" / "processed" / "station_status"
OUT_FILE = OUT_DIR / "station_status.parquet"

# Downsample raw 5-min snapshots to this interval.
# "last" strategy: keeps the final observed value in each bin — integer-safe
# and directly usable as MobilityDB tint instants.
RESAMPLE_INTERVAL = "10min"

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------
READ_DTYPES = {
    "station_id": "Int64",
    "num_bikes_available": "Int64",
    "num_bikes_available_types.mechanical": "Int64",
    "num_bikes_available_types.ebike": "Int64",
    "num_docks_available": "Int64",
    "last_reported": "Int64",
    "is_charging_station": "boolean",
    "status": "string",
    "is_installed": "boolean",
    "is_renting": "boolean",
    "is_returning": "boolean",
    "last_updated": "Int64",
}

RENAME = {
    "num_bikes_available_types.mechanical": "num_bikes_mechanical",
    "num_bikes_available_types.ebike": "num_bikes_ebike",
}


# ---------------------------------------------------------------------------
# I/O helpers
# ---------------------------------------------------------------------------

def _read_csv(path_or_bytes) -> pd.DataFrame:
    return pd.read_csv(
        path_or_bytes,
        usecols=list(READ_DTYPES),
        dtype=READ_DTYPES,
        low_memory=False,
    )


def load_monthly_file(path: Path) -> pd.DataFrame:
    """Read a monthly stations_status file (.csv or .7z containing a CSV)."""
    if path.suffix == ".csv":
        log.info("Reading CSV  %s", path.name)
        return _read_csv(path)

    if path.suffix == ".7z":
        log.info("Reading 7z   %s", path.name)
        with tempfile.TemporaryDirectory() as tmp:
            with py7zr.SevenZipFile(path, mode="r") as archive:
                names = archive.getnames()
                csv_names = [n for n in names if n.endswith(".csv")]
                if not csv_names:
                    raise ValueError(f"No CSV inside {path}")
                archive.extractall(path=tmp)
            return _read_csv(Path(tmp) / csv_names[0])

    raise ValueError(f"Unsupported file type: {path.suffix}")


# ---------------------------------------------------------------------------
# Transformation
# ---------------------------------------------------------------------------

def clean(df: pd.DataFrame) -> pd.DataFrame:
    df = df.rename(columns=RENAME)

    df["last_updated"] = pd.to_datetime(df["last_updated"], unit="s", utc=True)
    df["last_reported"] = pd.to_datetime(df["last_reported"], unit="s", utc=True)

    return df.sort_values(["station_id", "last_updated"]).reset_index(drop=True)


def resample_snapshots(df: pd.DataFrame, interval: str = RESAMPLE_INTERVAL) -> pd.DataFrame:
    """
    Downsample station snapshots to a regular interval using the last
    observed value in each time bin.

    Bins are aligned to UTC midnight. Empty bins (no observations) are
    dropped automatically. The resulting last_updated timestamps mark the
    start of each bin, giving MobilityDB tint sequences evenly-spaced
    temporal instants without fractional bike counts.
    """
    resampled = (
        df.set_index("last_updated")
        .groupby("station_id")
        .resample(interval, origin="epoch")
        .last()
        .reset_index()
    )
    return resampled.sort_values(["station_id", "last_updated"]).reset_index(drop=True)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    all_files: list[Path] = []
    seen_stems: set[str] = set()

    for p in sorted(RAW_DIR.iterdir()):
        if p.suffix not in (".csv", ".7z") or p.stem.startswith("."):
            continue
        stem = p.stem
        if p.suffix == ".7z":
            seen_stems.add(stem)
            all_files.append(p)
        elif stem not in seen_stems:
            all_files.append(p)

    log.info("Found %d monthly files", len(all_files))

    chunks: list[pd.DataFrame] = []
    for path in all_files:
        try:
            chunks.append(load_monthly_file(path))
        except Exception as exc:
            log.warning("Skipping %s — %s", path.name, exc)

    if not chunks:
        raise RuntimeError("No data loaded — check RAW_DIR path")

    log.info("Concatenating %d monthly files…", len(chunks))
    full = pd.concat(chunks, ignore_index=True)

    before = len(full)
    full = full.drop_duplicates()
    log.info("Dropped %d exact duplicates (%d → %d rows)", before - len(full), before, len(full))

    log.info("Cleaning and converting timestamps…")
    full = clean(full)
    raw_count = len(full)

    log.info("Total raw snapshots: %d rows, %d unique stations", raw_count, full["station_id"].nunique())

    log.info("Resampling to %s intervals (last value per bin)…", RESAMPLE_INTERVAL)
    full = resample_snapshots(full)
    log.info(
        "Resampled: %d → %d rows (%.1f%% reduction)",
        raw_count, len(full), 100 * (1 - len(full) / raw_count),
    )

    full.to_parquet(OUT_FILE, index=False)
    log.info("Saved → %s", OUT_FILE)

    print("\n=== Summary ===")
    print(f"Stations:           {full['station_id'].nunique()}")
    print(f"Raw snapshots:      {raw_count:,}")
    print(f"After resampling:   {len(full):,}  (interval: {RESAMPLE_INTERVAL}, strategy: last)")
    print(f"Reduction:          {100 * (1 - len(full) / raw_count):.1f}%")
    print(f"Time range:         {full['last_updated'].min()} → {full['last_updated'].max()}")
    print(f"Output:             {OUT_FILE}")


if __name__ == "__main__":
    main()
