"""

This script implements the full data preparation and querying pipeline
for the Bicing Barcelona bike-sharing dataset using GeoPandas.

Pipeline stages
---------------
  1. Data loading     — stations, neighborhoods, occupancy aggregates, and
                        maintenance counts loaded from PostGIS via psycopg2
                        + shapely WKT parsing into GeoDataFrames.
  2. Data cleaning    — missing-value audit and report, district-mean income
                        imputation, polygon simplification (sampling reduction).
  3. Normalization    — CRS unification (EPSG:25831 for metric ops, EPSG:4326
                        for output), z-score on income index, min-max on
                        station capacity.
  4. Data integration — gpd.sjoin(stations, neighborhoods, predicate='within')
                        resolves spatial heterogeneity without relying on the
                        stored FK; merges occupancy and maintenance data.
  5. KPI queries      — extensible registry pattern. Add new KPIs by defining
                        a function and decorating it with @_register("kpiN").

Supporting table-creation scripts (pass as additional material)
---------------------------------------------------------------
  src/preprocessing/station_information.py  — station metadata → bi-temporal parquet
  src/preprocessing/station_status.py       — 5-min snapshots → 10-min parquet
                                              (sampling-frequency reduction step)
  src/preprocessing/neighborhoods.py        — neighborhood polygon ETL
  src/db_upload/stations.py                 — stations → PostGIS (Point geom, FK)
  src/db_upload/neighborhoods.py            — neighborhoods → PostGIS (MultiPolygon)
  src/db_upload/station_status.py           — status → MobilityDB tint sequences

Registered KPIs
---------------
  kpi1  · Commuting flow balance per neighborhood (8 am vs 8 pm)
  kpi2  · Bicing supply equity by income quartile
  kpi3  · Maintenance intensity per neighborhood

Usage
-----
    python src/bicing_pipeline.py           # run all registered KPIs
    python src/bicing_pipeline.py kpi1 kpi3 # run specific KPIs by name

Requirements: geopandas, shapely, pandas, numpy, matplotlib, psycopg2
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Callable

import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from shapely import wkt
from shapely.geometry.base import BaseGeometry

# ---------------------------------------------------------------------------
# Locate _db.py regardless of working directory (walks up from this file)
# ---------------------------------------------------------------------------
_here = Path(__file__).resolve()
_db_dir = next(
    (p / "src" / "db_upload")
    for p in [_here, *_here.parents]
    if (p / "src" / "db_upload" / "_db.py").exists()
)
sys.path.insert(0, str(_db_dir))
from _db import connect, fetch_all  # noqa: E402

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
CRS_GEO    = "EPSG:4326"   # geographic WGS-84  — for storage and output
CRS_METRIC = "EPSG:25831"  # UTM zone 31N        — for metric ops (area, distance)

# 10 m tolerance removes micro-vertices from cadastral boundaries while
# preserving block-level accuracy — sufficient for neighborhood-level analysis.
SIMPLIFY_TOLERANCE_M = 10.0

# Weekday commuting snapshot hours (matches station_status_mdb query logic)
HOUR_AM, HOUR_PM = 8, 20


# =============================================================================
# SECTION 0  ·  DATA LOADING
# =============================================================================

def _vertex_count(geom: BaseGeometry) -> int:
    """Count total exterior + interior ring vertices in any polygon geometry."""
    if geom.geom_type == "MultiPolygon":
        return sum(
            len(p.exterior.coords) + sum(len(h.coords) for h in p.interiors)
            for p in geom.geoms
        )
    if geom.geom_type == "Polygon":
        return len(geom.exterior.coords) + sum(len(h.coords) for h in geom.interiors)
    return 0


def _rows_to_gdf(rows: list, columns: list, geom_col: str = "geom_wkt") -> gpd.GeoDataFrame:
    """Convert fetch_all result rows with a WKT column into a GeoDataFrame."""
    records = [dict(zip(columns, r)) for r in rows]
    for rec in records:
        rec["geometry"] = wkt.loads(rec.pop(geom_col))
    return gpd.GeoDataFrame(records, crs=CRS_GEO)


def load_data(
    conn,
) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame, pd.DataFrame, gpd.GeoDataFrame, gpd.GeoDataFrame]:
    """
    Load all required datasets from PostGIS.

    Returns
    -------
    stations_gdf             GeoDataFrame (Point)   — one row per station
    neighborhoods_gdf        GeoDataFrame (Polygon) — one row per neighborhood
    occupancy_df             DataFrame              — avg bike counts at HOUR_AM / HOUR_PM
    maintenance_gdf          GeoDataFrame (Point)   — total maintenance events per station
    maintenance_monthly_gdf  GeoDataFrame (Point)   — maintenance events per station per month
    """
    # ── Stations (point geometry, capacity, stored FK) ────────────────────────
    stations_gdf = _rows_to_gdf(
        fetch_all(conn, """
            SELECT station_id, capacity, neighborhood_id,
                   lat, lon, ST_AsText(geom) AS geom_wkt
            FROM   stations
            WHERE  geom IS NOT NULL AND capacity > 0;
        """),
        ["station_id", "capacity", "neighborhood_id", "lat", "lon", "geom_wkt"],
    )

    # ── Neighborhoods (polygon geometry, demographics, income) ────────────────
    neighborhoods_gdf = _rows_to_gdf(
        fetch_all(conn, """
            SELECT codi_barri, nom_barri, codi_districte,
                   population, area_m2, income_idx_2022,
                   ST_AsText(geom) AS geom_wkt
            FROM   neighborhoods
            ORDER  BY codi_barri;
        """),
        ["codi_barri", "nom_barri", "codi_districte",
         "population", "area_m2", "income_idx_2022", "geom_wkt"],
    ).rename(columns={"income_idx_2022": "income_idx"})

    # ── Weekday occupancy aggregates from MobilityDB (bikes_history tint) ─────
    # We average over weekdays (isodow 1–5) to capture commuting patterns and
    # exclude weekend behaviour, which has a structurally different demand profile.
    occupancy_df = pd.DataFrame(
        fetch_all(conn, f"""
            SELECT
                station_id,
                AVG(getValue(i)) FILTER (
                    WHERE EXTRACT(hour FROM getTimestamp(i)
                          AT TIME ZONE 'Europe/Madrid')::int = {HOUR_AM}
                ) AS bikes_8am,
                AVG(getValue(i)) FILTER (
                    WHERE EXTRACT(hour FROM getTimestamp(i)
                          AT TIME ZONE 'Europe/Madrid')::int = {HOUR_PM}
                ) AS bikes_8pm
            FROM   station_status_mdb,
                   LATERAL unnest(instants(bikes_history)) i
            WHERE  EXTRACT(isodow FROM getTimestamp(i)
                           AT TIME ZONE 'Europe/Madrid') BETWEEN 1 AND 5
              AND  EXTRACT(hour   FROM getTimestamp(i)
                           AT TIME ZONE 'Europe/Madrid')::int IN ({HOUR_AM}, {HOUR_PM})
            GROUP  BY station_id;
        """),
        columns=["station_id", "bikes_8am", "bikes_8pm"],
    ).astype({"station_id": int, "bikes_8am": float, "bikes_8pm": float}).dropna()

    # ── Maintenance event counts per station (from MobilityDB status_history) ──
    maintenance_gdf = _rows_to_gdf(
        fetch_all(conn, """
            SELECT s.station_id,
                   ST_AsText(s.geom) AS geom_wkt,
                   COUNT(*)          AS maintenance_count
            FROM   stations s
            JOIN   station_status_mdb ss ON s.station_id = ss.station_id
            CROSS  JOIN LATERAL unnest(instants(ss.status_history)) AS inst
            WHERE  getValue(inst) = 'MAINTENANCE'
            GROUP  BY s.station_id, s.geom;
        """),
        ["station_id", "geom_wkt", "maintenance_count"],
    )

    # ── Monthly maintenance breakdown (for temporal KPI analysis) ──────────────
    maintenance_monthly_gdf = _rows_to_gdf(
        fetch_all(conn, """
            SELECT s.station_id,
                   ST_AsText(s.geom)                                   AS geom_wkt,
                   date_trunc('month', getTimestamp(inst))::date        AS year_month,
                   COUNT(*)                                             AS maintenance_count
            FROM   stations s
            JOIN   station_status_mdb ss ON s.station_id = ss.station_id
            CROSS  JOIN LATERAL unnest(instants(ss.status_history)) AS inst
            WHERE  getValue(inst) = 'MAINTENANCE'
            GROUP  BY s.station_id, ST_AsText(s.geom),
                      date_trunc('month', getTimestamp(inst))
            ORDER  BY year_month, maintenance_count DESC;
        """),
        ["station_id", "geom_wkt", "year_month", "maintenance_count"],
    )
    maintenance_monthly_gdf["station_id"] = maintenance_monthly_gdf["station_id"].astype(int)
    maintenance_monthly_gdf["maintenance_count"] = maintenance_monthly_gdf["maintenance_count"].astype(int)
    maintenance_monthly_gdf["year_month"] = pd.to_datetime(maintenance_monthly_gdf["year_month"])

    print(
        f"Loaded: {len(stations_gdf)} stations | "
        f"{len(neighborhoods_gdf)} neighborhoods | "
        f"{len(occupancy_df)} stations with occupancy | "
        f"{len(maintenance_gdf)} stations with maintenance records | "
        f"{maintenance_monthly_gdf['year_month'].nunique()} maintenance months"
    )
    return stations_gdf, neighborhoods_gdf, occupancy_df, maintenance_gdf, maintenance_monthly_gdf


# =============================================================================
# SECTION 1  ·  DATA CLEANING
# =============================================================================

def report_missing(
    stations_gdf: gpd.GeoDataFrame,
    neighborhoods_gdf: gpd.GeoDataFrame,
) -> None:
    """
    Audit and print the % of missing values for relevant columns.

    Relevant station columns
    ------------------------
    neighborhood_id  — NULL when a station's point is outside all polygon
                       boundaries (e.g. a station on a district border).
    capacity         — should always be present after loading with capacity > 0,
                       but checked for completeness.

    Relevant neighborhood columns
    ------------------------------
    income_idx       — socioeconomic census data may lag administrative updates;
                       newly created neighborhoods may not yet have an income index.
    population       — same source, same risk of being absent.
    area_m2          — geometric property; should never be NULL after upload.
    """
    print("\n── Missing value report ────────────────────────────────────────────")
    for label, gdf, cols in [
        ("Stations",      stations_gdf,      ["neighborhood_id", "capacity", "lat", "lon"]),
        ("Neighborhoods", neighborhoods_gdf, ["income_idx", "population", "area_m2"]),
    ]:
        print(f"\n  {label}  (n = {len(gdf)}):")
        pct_missing = gdf[cols].isna().mean() * 100
        for col, pct in pct_missing.items():
            flag = "  ←  will impute" if pct > 0 else ""
            print(f"    {col:<20} {pct:5.1f}%{flag}")


def impute_missing(neighborhoods_gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """
    Impute missing income_idx values at the neighborhood level.

    Strategy: district-level mean imputation.
    Barcelona has 10 districts, each containing 7–13 neighborhoods with
    similar socioeconomic characteristics. Imputing from the district mean
    is therefore more accurate than the city-wide mean, while remaining
    robust even when only one neighborhood in a district has data.

    Fallback: if an entire district lacks income data, use the city-wide mean.
    """
    nbh = neighborhoods_gdf.copy()
    mask = nbh["income_idx"].isna()
    if not mask.any():
        print("\nNo missing income_idx — imputation skipped.")
        return nbh

    district_mean = nbh.groupby("codi_districte")["income_idx"].transform("mean")
    city_mean     = nbh["income_idx"].mean()
    nbh.loc[mask, "income_idx"] = district_mean[mask].fillna(city_mean)

    print(
        f"\nImputed {mask.sum()} missing income_idx value(s) "
        f"using district mean (city fallback = {city_mean:.1f})."
    )
    return nbh


def simplify_geometries(
    neighborhoods_gdf: gpd.GeoDataFrame,
    tolerance_m: float = SIMPLIFY_TOLERANCE_M,
) -> gpd.GeoDataFrame:
    """
    Reduce polygon vertex count via the Douglas-Peucker algorithm.

    This is the spatial equivalent of downsampling a temporal trajectory:
    we remove redundant intermediate points while preserving the overall shape.

    The operation is performed in EPSG:25831 (metric) so the tolerance is in
    metres, then the result is projected back to EPSG:4326 for storage.
    Topology preservation (preserve_topology=True) prevents self-intersections
    that would break subsequent spatial joins.

    Note: temporal trajectory downsampling (5 min → 10 min resolution) is
    handled upstream in src/preprocessing/station_status.py via
    GroupBy.resample("10min").last(), which takes the last observed integer
    bike count in each bin — a lossless strategy for MobilityDB tint sequences.
    """
    nbh_m = neighborhoods_gdf.to_crs(CRS_METRIC).copy()

    before = nbh_m.geometry.apply(_vertex_count).sum()
    nbh_m["geometry"] = nbh_m.geometry.simplify(tolerance_m, preserve_topology=True)
    after  = nbh_m.geometry.apply(_vertex_count).sum()

    print(
        f"\nGeometry simplification ({tolerance_m} m tolerance): "
        f"{before:,} → {after:,} vertices  ({100*(1 - after/before):.1f}% reduction)"
    )
    return nbh_m.to_crs(CRS_GEO)


# =============================================================================
# SECTION 2  ·  NORMALIZATION / STANDARDIZATION
# =============================================================================

def normalize(
    neighborhoods_gdf: gpd.GeoDataFrame,
    stations_gdf: gpd.GeoDataFrame,
) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    """
    Apply numeric normalization and compute derived spatial attributes.

    income_idx  → z-score (μ=0, σ=1).
        Income is already expressed as an index relative to Barcelona = 100,
        so it has a meaningful centre. Z-scoring preserves that relative
        structure while making the scale comparable with other features.

    capacity    → min-max [0, 1].
        Station capacity ranges from ~10 to ~50 docks with no natural zero;
        min-max normalization is appropriate when the range is bounded and
        there are no extreme outliers.

    area_km²    — computed in EPSG:25831 (metric) for correctness; stored
        as a plain column after reprojection back to EPSG:4326.

    centroid_lat/lon — neighbourhood centroids computed in EPSG:25831 for
        geometric accuracy (avoids distortion at high latitudes).
    """
    nbh = neighborhoods_gdf.copy()
    sta = stations_gdf.copy()

    # Income z-score
    mu, sigma = nbh["income_idx"].mean(), nbh["income_idx"].std()
    nbh["income_idx_z"] = (nbh["income_idx"] - mu) / sigma

    # Capacity min-max
    cap_min, cap_max = sta["capacity"].min(), sta["capacity"].max()
    sta["capacity_norm"] = (sta["capacity"] - cap_min) / (cap_max - cap_min)

    # Area and centroids in metric CRS
    nbh_m = nbh.to_crs(CRS_METRIC)
    nbh["area_km2"] = nbh_m.geometry.area / 1e6

    centroids_geo = nbh_m.geometry.centroid.to_crs(CRS_GEO)
    nbh["centroid_lat"] = centroids_geo.y
    nbh["centroid_lon"] = centroids_geo.x

    print(
        f"\nNormalization applied:"
        f"\n  income_idx  z-score    μ = {mu:.1f},  σ = {sigma:.1f}"
        f"\n  capacity    min-max    [{cap_min}, {cap_max}] → [0, 1]"
    )
    return nbh, sta


# =============================================================================
# SECTION 3  ·  DATA INTEGRATION
# =============================================================================

def integrate(
    stations_gdf: gpd.GeoDataFrame,
    neighborhoods_gdf: gpd.GeoDataFrame,
    occupancy_df: pd.DataFrame,
    maintenance_gdf: gpd.GeoDataFrame,
) -> gpd.GeoDataFrame:
    """
    Integrate all sources into a station-level GeoDataFrame enriched with
    neighborhood attributes, occupancy, and maintenance data.

    Spatial join strategy
    ─────────────────────
    gpd.sjoin(stations, neighborhoods, predicate='within') assigns each station
    to its containing neighborhood polygon.  This is preferred over the stored
    neighborhood_id FK because:
      • It is self-verifying — independent of the ETL pipeline.
      • Stations near polygon borders may have received a different assignment
        during upload due to floating-point precision in PostGIS ST_Within.

    Schema heterogeneity resolved
    ─────────────────────────────
    stations.neighborhood_id (Int, FK)  ↔  neighborhoods.codi_barri (Int, PK)
        → unified spatially via gpd.sjoin; the FK column is retained for
          cross-validation but not used for the join.

    occupancy_df.station_id (int)       ↔  stations_gdf.station_id (int)
        → direct merge on station_id (same source, same type, no conflict).

    maintenance_gdf.station_id (int)    ↔  stations_gdf.station_id (int)
        → direct merge; stations with no maintenance events receive count = 0.
    """
    # Project to metric CRS — avoids floating-point issues at polygon boundaries
    sta_m = stations_gdf.to_crs(CRS_METRIC)
    nbh_m = neighborhoods_gdf.to_crs(CRS_METRIC)

    nbh_cols = [
        "codi_barri", "nom_barri", "codi_districte",
        "population", "area_km2",
        "income_idx", "income_idx_z",
        "centroid_lat", "centroid_lon",
        "geometry",
    ]

    # Spatial join: station points inherit neighborhood polygon attributes
    joined = gpd.sjoin(
        sta_m[["station_id", "capacity", "capacity_norm", "geometry"]],
        nbh_m[nbh_cols],
        how="inner",
        predicate="within",
    ).drop(columns="index_right")

    # Ensure codi_barri is integer for later groupby / merge
    joined["codi_barri"] = joined["codi_barri"].astype(int)

    # Merge weekday occupancy averages (left join — not all stations have status data)
    joined = joined.merge(occupancy_df, on="station_id", how="left")

    # Merge maintenance totals (fill 0 for stations never in MAINTENANCE status)
    joined = joined.merge(
        maintenance_gdf[["station_id", "maintenance_count"]],
        on="station_id", how="left",
    )
    joined["maintenance_count"] = joined["maintenance_count"].fillna(0).astype(int)

    # Derived occupancy ratio — normalises for station size
    joined["occ_8am"] = joined["bikes_8am"] / joined["capacity"]
    joined["occ_8pm"] = joined["bikes_8pm"] / joined["capacity"]

    integrated = joined.to_crs(CRS_GEO)
    print(
        f"\nIntegration: {len(integrated)} station records across "
        f"{integrated['codi_barri'].nunique()} neighborhoods"
    )
    return integrated


# ---------------------------------------------------------------------------
# Monthly maintenance helpers  (used by KPI 3)
# ---------------------------------------------------------------------------

# Months with incomplete data excluded from temporal analysis (matches notebook)
_EXCLUDED_MONTHS = frozenset({
    pd.Timestamp("2024-12-01"),
    pd.Timestamp("2025-10-01"),
    pd.Timestamp("2025-11-01"),
    pd.Timestamp("2026-01-01"),
})


def _expand_monthly_maintenance(maint_gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Reindex to fill missing (station, month) pairs with zero maintenance count."""
    geom_by_id = (
        maint_gdf[["station_id", "geometry"]]
        .drop_duplicates("station_id")
        .set_index("station_id")
    )
    all_months = pd.date_range(
        maint_gdf["year_month"].min(), maint_gdf["year_month"].max(), freq="MS"
    )
    all_months = all_months[~all_months.isin(_EXCLUDED_MONTHS)]
    full_idx = pd.MultiIndex.from_product(
        [maint_gdf["station_id"].unique(), all_months],
        names=["station_id", "year_month"],
    )
    expanded = (
        pd.DataFrame(maint_gdf.drop(columns="geometry"))
        .set_index(["station_id", "year_month"])
        .reindex(full_idx, fill_value=0)
        .reset_index()
    )
    expanded = expanded.merge(geom_by_id.reset_index(), on="station_id", how="left")
    return gpd.GeoDataFrame(expanded, geometry="geometry", crs=CRS_GEO)


# =============================================================================
# SECTION 4  ·  KPI QUERIES
# =============================================================================
# ── Extensible registry ───────────────────────────────────────────────────────
# To add a new KPI:
#   1. Define a function  kpi_xxx(integrated, neighborhoods_gdf) → None
#   2. Decorate it with   @_register("kpiN")
# The main() loop will pick it up automatically.

QUERY_REGISTRY: dict[str, Callable[[gpd.GeoDataFrame, gpd.GeoDataFrame], None]] = {}


def _register(name: str):
    """Decorator that adds a KPI function to QUERY_REGISTRY."""
    def decorator(fn: Callable) -> Callable:
        QUERY_REGISTRY[name] = fn
        return fn
    return decorator


# ── KPI 1  ·  Commuting flow balance ─────────────────────────────────────────

@_register("kpi1")
def kpi1_commuting_flows(
    integrated: gpd.GeoDataFrame,
    neighborhoods_gdf: gpd.GeoDataFrame,
) -> None:
    """
    KPI 1 — Net commuting flow balance per neighborhood (8 am vs 8 pm).

    delta_bikes = Σ(bikes_8pm) − Σ(bikes_8am) per neighborhood.
      Negative  → commuting destination: bikes arrive in the morning,
                  residents leave on foot / by metro and return by bike.
      Positive  → commuting origin: residents cycle out in the morning
                  and the bikes return in the evening.

    Assumption: a single hour snapshot per shift is representative of peak
    occupancy.  Averaging over all weekdays mitigates day-to-day noise.
    """
    sub = integrated.dropna(subset=["bikes_8am", "bikes_8pm"]).copy()
    sub["delta_bikes"] = sub["bikes_8pm"] - sub["bikes_8am"]

    nbh_agg = (
        sub.groupby("codi_barri")
        .agg(
            nom_barri   = ("nom_barri",   "first"),
            delta_bikes = ("delta_bikes", "sum"),
            delta_occ   = ("occ_8pm",     "mean"),
            n_stations  = ("station_id",  "count"),
            income_idx  = ("income_idx",  "first"),
        )
        .reset_index()
    )
    nbh_agg["delta_occ"] -= sub.groupby("codi_barri")["occ_8am"].mean().reindex(
        nbh_agg["codi_barri"].values
    ).values

    print("\n── KPI 1: Commuting flow balance ────────────────────────────────────")
    print("Top 5 commuting DESTINATIONS (lose bikes 8 am → 8 pm):")
    print(
        nbh_agg.nsmallest(5, "delta_bikes")
        [["nom_barri", "delta_bikes", "n_stations", "income_idx"]]
        .to_string(index=False)
    )
    print("\nTop 5 commuting ORIGINS (gain bikes 8 am → 8 pm):")
    print(
        nbh_agg.nlargest(5, "delta_bikes")
        [["nom_barri", "delta_bikes", "n_stations", "income_idx"]]
        .to_string(index=False)
    )

    # Choropleth: net occupancy change
    nbh_balance = neighborhoods_gdf.merge(
        nbh_agg[["codi_barri", "delta_bikes", "delta_occ", "n_stations"]],
        on="codi_barri", how="left",
    )
    fig, ax = plt.subplots(figsize=(8, 7))
    nbh_balance.plot(
        ax=ax, column="delta_bikes",
        cmap="RdYlBu", legend=True,
        missing_kwds={"color": "#e0e0e0", "label": "No Bicing data"},
        legend_kwds={"label": "Net Δ bikes (8 am → 8 pm)", "shrink": 0.6},
        edgecolor="white", linewidth=0.5,
    )
    ax.set_title("KPI 1 — Commuting flow balance by neighborhood")
    ax.axis("off")
    plt.tight_layout()
    plt.savefig("kpi1_commuting_flows.png", dpi=150, bbox_inches="tight")
    print("  → saved kpi1_commuting_flows.png")
    plt.show()


# ── KPI 2  ·  Supply equity ───────────────────────────────────────────────────

@_register("kpi2")
def kpi2_supply_equity(
    integrated: gpd.GeoDataFrame,
    neighborhoods_gdf: gpd.GeoDataFrame,
) -> None:
    """
    KPI 2 — Bicing supply equity: docking capacity per 1 000 inhabitants
    broken down by neighborhood income quartile.

    Q1 = lowest-income quartile, Q4 = highest.

    A systematic capacity gap Q1 << Q4 is significant because lower-income
    neighborhoods tend to be net commuting origins (see KPI 1) and therefore
    have higher need for outbound docking capacity in the morning.
    The KPI makes this inequality observable and quantifiable.
    """
    nbh_supply = (
        integrated
        .groupby("codi_barri")
        .agg(
            nom_barri      = ("nom_barri",   "first"),
            income_idx     = ("income_idx",  "first"),
            population     = ("population",  "first"),
            total_capacity = ("capacity",    "sum"),
            n_stations     = ("station_id",  "count"),
        )
        .reset_index()
    )
    nbh_supply["capacity_per_1k"] = (
        nbh_supply["total_capacity"] / nbh_supply["population"] * 1_000
    )
    nbh_supply["income_quartile"] = pd.qcut(
        nbh_supply["income_idx"], q=4,
        labels=["Q1 (lowest)", "Q2", "Q3", "Q4 (highest)"],
    )

    result = (
        nbh_supply.groupby("income_quartile", observed=True)["capacity_per_1k"]
        .agg(mean="mean", median="median", n="count")
        .round(2)
    )
    print("\n── KPI 2: Supply equity by income quartile ──────────────────────────")
    print(result.to_string())

    # Bar chart with 95 % CI
    stats = (
        nbh_supply.groupby("income_quartile", observed=True)["capacity_per_1k"]
        .agg(["mean", "sem"])
        .reset_index()
    )
    palette = ["#d73027", "#fc8d59", "#91bfdb", "#4575b4"]
    fig, ax = plt.subplots(figsize=(7, 4.5))
    ax.bar(
        stats["income_quartile"].astype(str),
        stats["mean"],
        yerr=stats["sem"] * 1.96,
        color=palette, edgecolor="white", capsize=5,
        error_kw={"elinewidth": 1.2, "ecolor": "#444"},
    )
    for i, row in stats.iterrows():
        ax.text(i, row["mean"] + 0.3, f"{row['mean']:.1f}",
                ha="center", va="bottom", fontsize=9, fontweight="bold")
    ax.set_xlabel("Income quartile")
    ax.set_ylabel("Bicing docks per 1 000 inhabitants")
    ax.set_title("KPI 2 — Bicing supply equity by neighborhood income (95% CI)")
    ax.grid(axis="y", linestyle="--", alpha=0.4)
    ax.spines[["top", "right"]].set_visible(False)
    plt.tight_layout()
    plt.savefig("kpi2_supply_equity.png", dpi=150, bbox_inches="tight")
    print("  → saved kpi2_supply_equity.png")
    plt.show()


# ── KPI 3  ·  Maintenance intensity ──────────────────────────────────────────

@_register("kpi3")
def kpi3_maintenance_intensity(
    integrated: gpd.GeoDataFrame,
    neighborhoods_gdf: gpd.GeoDataFrame,
    maintenance_monthly_gdf: gpd.GeoDataFrame | None = None,
) -> None:
    """
    KPI 3 — Maintenance intensity per neighborhood.

    maintenance_per_station = total MAINTENANCE events / number of stations.
    Normalising by station count makes neighborhoods with different network
    densities comparable — raw counts are trivially correlated with station
    count by construction.

    High maintenance_per_station in high-income areas may indicate heavier
    usage (and therefore wear); in low-income areas it may point to deferred
    investment in hardware quality or older station stock.
    """
    nbh_maint = (
        integrated
        .groupby("codi_barri")
        .agg(
            nom_barri         = ("nom_barri",         "first"),
            income_idx        = ("income_idx",        "first"),
            n_stations        = ("station_id",        "count"),
            total_maintenance = ("maintenance_count", "sum"),
        )
        .reset_index()
    )
    nbh_maint["maintenance_per_station"] = (
        nbh_maint["total_maintenance"] / nbh_maint["n_stations"]
    ).round(2)

    print("\n── KPI 3: Maintenance intensity ─────────────────────────────────────")
    print("Top 10 neighborhoods by maintenance events per station:")
    print(
        nbh_maint.nlargest(10, "maintenance_per_station")
        [["nom_barri", "n_stations", "total_maintenance",
          "maintenance_per_station", "income_idx"]]
        .to_string(index=False)
    )

    # Choropleth — merge KPI back onto polygon GeoDataFrame
    nbh_maint_gdf = neighborhoods_gdf.merge(
        nbh_maint[["codi_barri", "n_stations",
                   "total_maintenance", "maintenance_per_station"]],
        on="codi_barri", how="left",
    )
    fig, ax = plt.subplots(figsize=(8, 7))
    nbh_maint_gdf.plot(
        ax=ax, column="maintenance_per_station",
        cmap="Reds", legend=True,
        missing_kwds={"color": "#e0e0e0", "label": "No Bicing data"},
        legend_kwds={"label": "Maintenance events per station", "shrink": 0.6},
        edgecolor="white", linewidth=0.5,
    )
    ax.set_title("KPI 3 — Maintenance intensity by neighborhood")
    ax.axis("off")
    plt.tight_layout()
    plt.savefig("kpi3_maintenance_intensity.png", dpi=150, bbox_inches="tight")
    print("  → saved kpi3_maintenance_intensity.png")
    plt.show()

    if maintenance_monthly_gdf is None:
        return

    df_monthly = _expand_monthly_maintenance(maintenance_monthly_gdf)

    # ── Top 5 stations: timeline + locator map ────────────────────────────────
    top5_ids = (
        df_monthly.groupby("station_id")["maintenance_count"]
        .sum()
        .nlargest(5)
        .index.tolist()
    )
    df_top5 = df_monthly[df_monthly["station_id"].isin(top5_ids)]
    _COLORS = ["#e41a1c", "#4daf4a", "#377eb8", "#ff7f00", "#984ea3"]

    fig, (ax_line, ax_map) = plt.subplots(1, 2, figsize=(14, 5))
    for sid, color in zip(top5_ids, _COLORS):
        df_s = df_top5[df_top5["station_id"] == sid].sort_values("year_month")
        ax_line.plot(
            df_s["year_month"], df_s["maintenance_count"],
            marker="o", color=color, label=f"Station {sid}", linewidth=1.5,
        )
    ax_line.set_xlabel("Year-Month")
    ax_line.set_ylabel("Maintenance Count")
    ax_line.set_title("Maintenance Count Over Time — Top 5 Stations")
    ax_line.legend()
    ax_line.tick_params(axis="x", rotation=45)
    ax_line.grid(axis="y", linestyle="--", alpha=0.4)
    ax_line.spines[["top", "right"]].set_visible(False)

    neighborhoods_gdf.plot(ax=ax_map, color="lightblue", edgecolor="black", linewidth=0.5)
    df_top5_pts = gpd.GeoDataFrame(
        df_top5.groupby("station_id")["maintenance_count"]
        .sum()
        .reset_index()
        .merge(
            df_top5[["station_id", "geometry"]].drop_duplicates("station_id"),
            on="station_id",
        ),
        geometry="geometry",
        crs=CRS_GEO,
    )
    for sid, color in zip(top5_ids, _COLORS):
        df_top5_pts[df_top5_pts["station_id"] == sid].plot(
            ax=ax_map, color=color, markersize=60, zorder=5, label=f"Station {sid}"
        )
    ax_map.set_title("Top 5 Stations with Most Maintenance")
    ax_map.axis("off")
    ax_map.legend(loc="lower left", fontsize=8)
    plt.tight_layout()
    plt.savefig("kpi3_top_stations_timeline.png", dpi=150, bbox_inches="tight")
    print("  → saved kpi3_top_stations_timeline.png")
    plt.show()

    # ── Total maintenance per month ───────────────────────────────────────────
    month_totals = (
        df_monthly.groupby("year_month")["maintenance_count"]
        .sum()
        .reset_index()
        .sort_values("year_month")
    )
    fig, ax = plt.subplots(figsize=(10, 4))
    ax.plot(
        month_totals["year_month"], month_totals["maintenance_count"],
        marker="o", linewidth=1.5, color="#2c7bb6",
    )
    ax.axvline(pd.Timestamp("2026-01-01"), color="red", linestyle="--",
               linewidth=1.2, label="2026 boundary")
    ax.set_xlabel("Year-Month")
    ax.set_ylabel("Maintenance Count")
    ax.set_title("KPI 3 — Total Maintenance Count Over Time")
    ax.legend()
    ax.tick_params(axis="x", rotation=45)
    ax.grid(axis="y", linestyle="--", alpha=0.4)
    ax.spines[["top", "right"]].set_visible(False)
    plt.tight_layout()
    plt.savefig("kpi3_monthly_total.png", dpi=150, bbox_inches="tight")
    print("  → saved kpi3_monthly_total.png")
    plt.show()

    # ── Maintenance vs demographic variables ──────────────────────────────────
    station_nbh = integrated[
        ["station_id", "codi_barri", "population", "income_idx", "area_km2"]
    ].drop_duplicates("station_id")
    df_demo = (
        df_monthly.merge(station_nbh, on="station_id", how="left")
        .groupby(["codi_barri", "population", "income_idx", "area_km2"])
        .agg(
            total_maintenance=("maintenance_count", "sum"),
            n_stations=("station_id", "nunique"),
        )
        .reset_index()
    )
    df_demo["maintenance_per_station"] = (
        df_demo["total_maintenance"] / df_demo["n_stations"]
    ).round(2)

    fig, axes = plt.subplots(1, 3, figsize=(15, 4.5))
    for ax, x_col, x_label in [
        (axes[0], "population", "Population"),
        (axes[1], "income_idx", "Income Index"),
        (axes[2], "area_km2",   "Area (km²)"),
    ]:
        ax.scatter(
            df_demo[x_col], df_demo["maintenance_per_station"],
            alpha=0.7, edgecolors="white", linewidth=0.5, color="#2c7bb6",
        )
        ax.set_xlabel(x_label)
        ax.set_ylabel("Maintenance per Station")
        ax.set_title(f"Maintenance vs {x_label}")
        ax.grid(linestyle="--", alpha=0.4)
        ax.spines[["top", "right"]].set_visible(False)
    plt.suptitle("KPI 3 — Maintenance vs Demographic Variables")
    plt.tight_layout()
    plt.savefig("kpi3_demographics.png", dpi=150, bbox_inches="tight")
    print("  → saved kpi3_demographics.png")
    plt.show()

    # ── Monthly choropleth grid ───────────────────────────────────────────────
    station_codi = integrated[["station_id", "codi_barri"]].drop_duplicates("station_id")
    df_time_nbh = (
        df_monthly.merge(station_codi, on="station_id", how="left")
        .groupby(["codi_barri", "year_month"])["maintenance_count"]
        .sum()
        .reset_index()
    )
    months_sorted = sorted(df_monthly["year_month"].unique())
    n_cols = 3
    n_rows = (len(months_sorted) + n_cols - 1) // n_cols
    fig, axes = plt.subplots(n_rows, n_cols, figsize=(n_cols * 5, n_rows * 4))
    axes = axes.flatten()
    vmax = df_time_nbh["maintenance_count"].max()

    for idx, month in enumerate(months_sorted):
        ax = axes[idx]
        nbh_month = neighborhoods_gdf.merge(
            df_time_nbh[df_time_nbh["year_month"] == month][["codi_barri", "maintenance_count"]],
            on="codi_barri", how="left",
        )
        nbh_month.plot(
            ax=ax, column="maintenance_count",
            cmap="Reds", vmin=0, vmax=vmax, legend=False,
            missing_kwds={"color": "#e0e0e0"},
            edgecolor="white", linewidth=0.3,
        )
        ax.set_title(month.strftime("%B %Y"), fontsize=9)
        ax.axis("off")

    for idx in range(len(months_sorted), len(axes)):
        axes[idx].set_visible(False)

    sm = plt.cm.ScalarMappable(cmap="Reds", norm=plt.Normalize(vmin=0, vmax=vmax))
    sm.set_array([])
    fig.colorbar(sm, ax=axes[:len(months_sorted)], shrink=0.6, label="Maintenance Count")
    fig.suptitle("KPI 3 — Monthly Maintenance Count by Neighborhood")
    plt.tight_layout()
    plt.savefig("kpi3_monthly_grid.png", dpi=150, bbox_inches="tight")
    print("  → saved kpi3_monthly_grid.png")
    plt.show()


# =============================================================================
# MAIN
# =============================================================================

def main(kpi_keys: list[str] | None = None) -> None:
    """Run the full pipeline then execute the requested KPI queries."""

    # 0. Load from PostGIS
    conn = connect()
    stations_gdf, neighborhoods_gdf, occupancy_df, maintenance_gdf, maintenance_monthly_gdf = load_data(conn)
    conn.close()

    # 1. Clean
    report_missing(stations_gdf, neighborhoods_gdf)
    neighborhoods_gdf = impute_missing(neighborhoods_gdf)
    neighborhoods_gdf = simplify_geometries(neighborhoods_gdf)

    # 2. Normalize
    neighborhoods_gdf, stations_gdf = normalize(neighborhoods_gdf, stations_gdf)

    # 3. Integrate
    integrated = integrate(
        stations_gdf, neighborhoods_gdf, occupancy_df, maintenance_gdf
    )

    # 4. KPI queries
    _extra_kwargs: dict[str, dict] = {
        "kpi3": {"maintenance_monthly_gdf": maintenance_monthly_gdf},
    }
    keys_to_run = kpi_keys or list(QUERY_REGISTRY)
    for key in keys_to_run:
        if key not in QUERY_REGISTRY:
            print(
                f"[WARN] Unknown KPI key '{key}'. "
                f"Available: {list(QUERY_REGISTRY)}"
            )
            continue
        QUERY_REGISTRY[key](integrated, neighborhoods_gdf, **_extra_kwargs.get(key, {}))

    print("\nPipeline complete.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Bicing Barcelona — GeoPandas processing pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"Registered KPIs: {', '.join(QUERY_REGISTRY) or '(none yet)'}",
    )
    parser.add_argument(
        "kpis",
        nargs="*",
        help="KPI keys to run (default: all registered KPIs)",
    )
    args = parser.parse_args()
    main(args.kpis or None)
