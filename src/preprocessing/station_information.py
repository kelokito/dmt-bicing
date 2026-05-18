"""
Preprocess Bicing station information snapshots into a bi-temporal table.

Input:  data/raw/stations_information/  — monthly CSV/.7z files with ~5-min snapshots
Output: data/processed/stations_information/stations_info_history.parquet

Each output row represents a period during which a station's attributes were stable:
  - valid_from / valid_to  →  valid time  (when the config was true in the real world)
  - transaction_from       →  transaction time (when we ingested it; set to now at run time)

This compact representation collapses ~4.5M rows/month into only the rows where
something actually changed, making it suitable for a PostgreSQL bi-temporal table
backed by tstzrange columns.
"""

import logging
from datetime import timezone
from pathlib import Path

import pandas as pd
import py7zr
import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

ELEVATION_API = "http://localhost:80/api/v1/lookup"
ELEVATION_BATCH = 100  # max locations per request

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = ROOT / "data" / "raw" / "stations_information"
OUT_DIR = ROOT / "data" / "processed" / "stations_information"
OUT_FILE = OUT_DIR / "stations_info_history.parquet"

# ---------------------------------------------------------------------------
# Columns that represent the station's observable configuration.
# A new valid-time row is created whenever any of these changes.
# ---------------------------------------------------------------------------
TRACKED_COLS = [
    "name",
    "physical_configuration",
    "lat",
    "lon",
    "address",
    "cross_street",
    "post_code",
    "capacity",
    "is_charging_station",
    "short_name",
    "nearby_distance",
]

READ_DTYPES = {
    "station_id": "Int64",
    "name": "string",
    "physical_configuration": "string",
    "lat": "float64",
    "lon": "float64",
    "altitude": "float64",
    "address": "string",
    "cross_street": "string",
    "post_code": "Int64",
    "capacity": "Int64",
    "is_charging_station": "boolean",
    "short_name": "Int64",
    "nearby_distance": "Int64",
    "last_updated": "Int64",
}


# ---------------------------------------------------------------------------
# I/O helpers
# ---------------------------------------------------------------------------

def _read_csv(path_or_buffer) -> pd.DataFrame:
    return pd.read_csv(
        path_or_buffer,
        usecols=list(READ_DTYPES),
        dtype=READ_DTYPES,
        low_memory=False,
    )


def load_monthly_file(path: Path) -> pd.DataFrame:
    """Read a monthly stations_information file (.csv or .7z containing a CSV)."""
    if path.suffix == ".csv":
        log.info("Reading CSV  %s", path.name)
        return _read_csv(path)

    if path.suffix == ".7z":
        log.info("Reading 7z   %s", path.name)
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            with py7zr.SevenZipFile(path, mode="r") as archive:
                names = archive.getnames()
                csv_names = [n for n in names if n.endswith(".csv")]
                if not csv_names:
                    raise ValueError(f"No CSV inside {path}")
                archive.extractall(path=tmp)
            csv_path = Path(tmp) / csv_names[0]
            return _read_csv(csv_path)

    raise ValueError(f"Unsupported file type: {path.suffix}")


# ---------------------------------------------------------------------------
# Core transformation
# ---------------------------------------------------------------------------

def build_history(df: pd.DataFrame) -> pd.DataFrame:
    """
    Collapse snapshot rows into valid-time periods.

    For each station, rows are sorted by last_updated.  A new period begins
    whenever any TRACKED_COL value changes.  valid_to is the last_updated of
    the *next* change event (exclusive boundary, matching PostgreSQL tstzrange
    default), or NaT if still current.
    """
    df = df.sort_values(["station_id", "last_updated"]).reset_index(drop=True)

    # Convert unix epoch seconds → UTC timestamps
    df["ts"] = pd.to_datetime(df["last_updated"], unit="s", utc=True)

    # Mark rows where anything in TRACKED_COLS differs from the previous row
    # within the same station.  shift() within each group, then compare.
    shifted = df.groupby("station_id")[TRACKED_COLS].shift(1)
    change_mask = df[TRACKED_COLS].ne(shifted).any(axis=1)

    period_starts = df[change_mask].copy()
    period_starts = period_starts.rename(columns={"ts": "valid_from"})

    # valid_to = valid_from of next period for same station, else NaT
    period_starts["valid_to"] = (
        period_starts.groupby("station_id")["valid_from"].shift(-1)
    )

    # Drop the raw unix column; keep only useful columns
    keep = ["station_id"] + TRACKED_COLS + ["valid_from", "valid_to"]
    return period_starts[keep].reset_index(drop=True)


# ---------------------------------------------------------------------------
# Elevation enrichment
# ---------------------------------------------------------------------------

def fetch_elevations(coords: list[tuple[float, float]]) -> dict[tuple[float, float], float]:
    """
    Query the self-hosted Open Elevation API for a list of (lat, lon) pairs.
    Returns a dict mapping each (lat, lon) to its elevation in metres.
    Raises RuntimeError if the API is unreachable.
    """
    results: dict[tuple[float, float], float] = {}
    for i in range(0, len(coords), ELEVATION_BATCH):
        batch = coords[i : i + ELEVATION_BATCH]
        payload = {"locations": [{"latitude": lat, "longitude": lon} for lat, lon in batch]}
        try:
            resp = requests.post(ELEVATION_API, json=payload, timeout=30)
            resp.raise_for_status()
        except requests.exceptions.ConnectionError:
            raise RuntimeError(
                f"Cannot reach Open Elevation API at {ELEVATION_API}. "
                "Make sure the container is running."
            )
        for entry in resp.json()["results"]:
            results[(entry["latitude"], entry["longitude"])] = entry["elevation"]
        log.info("Fetched elevation for batch %d–%d", i + 1, i + len(batch))
    return results


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # Discover files: prefer .7z; fall back to bare .csv if no .7z exists
    all_files: list[Path] = []
    seen_stems: set[str] = set()

    for p in sorted(RAW_DIR.iterdir()):
        if p.suffix not in (".csv", ".7z") or p.stem.startswith("."):
            continue
        stem = p.stem  # e.g. "2025_01_Gener_BicingNou_INFORMACIO"
        if p.suffix == ".7z":
            seen_stems.add(stem)
            all_files.append(p)
        elif stem not in seen_stems:
            # Only add the bare CSV if no 7z counterpart was already registered
            all_files.append(p)

    log.info("Found %d monthly files", len(all_files))

    chunks: list[pd.DataFrame] = []
    for path in all_files:
        try:
            df = load_monthly_file(path)
            chunks.append(df)
        except Exception as exc:
            log.warning("Skipping %s — %s", path.name, exc)

    if not chunks:
        raise RuntimeError("No data loaded — check RAW_DIR path")

    log.info("Concatenating %d monthly files…", len(chunks))
    full = pd.concat(chunks, ignore_index=True)

    # Drop exact duplicates (same station, same timestamp, same values)
    full = full.drop_duplicates()

    # Drop snapshots whose coordinates fall outside Barcelona's bounding box
    BCN_LAT = (41.32, 41.50)
    BCN_LON = (2.05, 2.23)
    outside = ~(full["lat"].between(*BCN_LAT) & full["lon"].between(*BCN_LON))
    if outside.any():
        bad = full.loc[outside, "station_id"].unique()
        log.warning(
            "Dropping %d snapshots from %d station(s) outside Barcelona bbox: %s",
            outside.sum(), len(bad), sorted(bad.tolist()),
        )
        full = full[~outside].reset_index(drop=True)

    log.info("Total snapshots: %d rows, %d unique stations",
             len(full), full["station_id"].nunique())

    log.info("Building valid-time history…")
    history = build_history(full)

    # Annotate with transaction time = when this script ran
    history["transaction_from"] = pd.Timestamp.now(tz=timezone.utc)

    log.info(
        "History: %d rows (compressed from %d snapshots)",
        len(history), len(full),
    )

    # Enrich with DEM elevation from Open Elevation API
    log.info("Fetching elevations from Open Elevation API…")
    unique_coords = (
        history[["lat", "lon"]]
        .drop_duplicates()
        .dropna()
        .itertuples(index=False, name=None)
    )
    coords = list(unique_coords)
    elevation_map = fetch_elevations(coords)
    history["elevation_m"] = history.apply(
        lambda r: elevation_map.get((r["lat"], r["lon"])), axis=1
    )
    matched = history["elevation_m"].notna().sum()
    log.info(
        "Elevation matched for %d / %d history rows", matched, len(history)
    )

    history.to_parquet(OUT_FILE, index=False)
    log.info("Saved → %s", OUT_FILE)

    # Quick summary
    print("\n=== Summary ===")
    print(f"Stations:           {history['station_id'].nunique()}")
    print(f"Total periods:      {len(history)}")
    print(f"Valid-from range:   {history['valid_from'].min()} → {history['valid_from'].max()}")
    cap_changes = history.groupby("station_id")["capacity"].nunique()
    print(f"Stations with ≥2 capacity values: {(cap_changes > 1).sum()}")
    elev = history["elevation_m"].dropna()
    print(f"Elevation range:    {elev.min():.1f} m → {elev.max():.1f} m")
    print(f"Output:             {OUT_FILE}")


if __name__ == "__main__":
    main()
