# %%
import numpy as np
import pandas as pd
import geopandas as gpd
import altair as alt
import json
from scipy.spatial.distance import pdist, squareform
from shapely.geometry import Point
from shapely import wkt

# Set seed for reproducible simulations
np.random.seed(42)

# ==========================================
# 1. DATA LOADING & PREPROCESSING
# ==========================================

# Load Stations
bicing_stations_status = pd.read_csv('../data/raw_csv/2026_04_Abril_BicingNou_INFORMACIO.csv')
bicing_stations_location = bicing_stations_status[['station_id', 'name', 'lat', 'lon']].drop_duplicates()

# Convert Stations to GeoDataFrame (EPSG:4326 to EPSG:25831 for meters)
geometry = [Point(xy) for xy in zip(bicing_stations_location['lon'], bicing_stations_location['lat'])]
stations_gdf = gpd.GeoDataFrame(bicing_stations_location, geometry=geometry, crs="EPSG:4326")
stations_gdf = stations_gdf.to_crs("EPSG:25831")

# Load Neighborhood Polygons
with open('../data/neighborhoods-poligons.json', 'r', encoding='utf-8') as f:
    nhood_data = json.load(f)
nhood_df = pd.DataFrame(nhood_data)
nhood_df['geometry'] = nhood_df['geometria_etrs89'].apply(wkt.loads)
nhood_gdf = gpd.GeoDataFrame(nhood_df, geometry='geometry', crs="EPSG:25831")

# ==========================================
# 2. SPATIAL JOIN (The Granularity Reduction)
# ==========================================

# This links each station point to the neighborhood polygon it falls inside
stations_with_nhood = gpd.sjoin(stations_gdf, nhood_gdf, how="inner", predicate="intersects")

# ==========================================
# 3. MATHEMATICAL CORE & SIMULATION
# ==========================================

def ripleys_l(coords, study_area, max_d, steps=20):
    """Calculates Ripley's L-function for a set of coordinates."""
    N = coords.shape[0]
    if N < 3: 
        return None, None # PPA requires a minimum number of points to be meaningful
    
    density = N / study_area
    dist_matrix = squareform(pdist(coords))
    radii = np.linspace(0, max_d, steps)
    l_values = []
    
    for r in radii:
        # Subtract N to remove self-distance (distance of 0)
        pairs_within_r = np.sum(dist_matrix <= r) - N
        k_r = (pairs_within_r / N) / density if density > 0 else 0
        l_r = np.sqrt(k_r / np.pi)
        l_values.append(l_r)
        
    return radii, np.array(l_values)

def generate_random_points_in_polygon(polygon, num_points):
    """Generates strictly bounded random points inside a specific polygon shape."""
    minx, miny, maxx, maxy = polygon.bounds
    points = []
    while len(points) < num_points:
        p = Point(np.random.uniform(minx, maxx), np.random.uniform(miny, maxy))
        if polygon.contains(p): # Rejection sampling: only keep if inside the actual border
            points.append((p.x, p.y))
    return np.array(points)

def simulate_csr_envelope_polygon(N, polygon, max_d, steps, simulations=39):
    """Generates the CSR envelope tailored to the specific neighborhood shape."""
    area = polygon.area
    sim_l_values = np.zeros((simulations, steps))
    
    for i in range(simulations):
        rand_coords = generate_random_points_in_polygon(polygon, N)
        _, l_sim = ripleys_l(rand_coords, area, max_d, steps)
        sim_l_values[i, :] = l_sim
        
    l_lower = np.percentile(sim_l_values, 2.5, axis=0)
    l_upper = np.percentile(sim_l_values, 97.5, axis=0)
    return l_lower, l_upper

# ==========================================
# 4. ANALYSIS & PLOTTING PIPELINE
# ==========================================

def analyze_and_plot_neighborhood(neighborhood_name, max_distance=400, steps=20):
    """Executes the full pipeline for a chosen neighborhood and returns an Altair chart."""
    
    # Extract data for the specific neighborhood
    nhood_stations = stations_with_nhood[stations_with_nhood['nom_barri'] == neighborhood_name]
    polygon = nhood_gdf[nhood_gdf['nom_barri'] == neighborhood_name].geometry.iloc[0]
    
    coords = np.column_stack((nhood_stations.geometry.x, nhood_stations.geometry.y))
    N_points = coords.shape[0]
    
    if N_points < 3:
        return f"Not enough stations in {neighborhood_name} ({N_points} found)."
    
    print(f"Analyzing {neighborhood_name}: {N_points} stations in {polygon.area / 1e6:.2f} km²")
    
    # 1. Observed L(d)
    d_vals, l_obs = ripleys_l(coords, polygon.area, max_d=max_distance, steps=steps)
    
    # 2. Expected Envelope
    l_lower, l_upper = simulate_csr_envelope_polygon(
        N_points, polygon, max_d=max_distance, steps=steps, simulations=39
    )
    
    # 3. Prepare Altair DataFrame
    results_df = pd.DataFrame({
        'Distance': d_vals,
        'Observed L(d)': l_obs,
        'Expected CSR': d_vals,
        'Lower CI': l_lower,
        'Upper CI': l_upper
    })
    
    lines_df = results_df.melt(id_vars=['Distance', 'Lower CI', 'Upper CI'], 
                               value_vars=['Observed L(d)', 'Expected CSR'],
                               var_name='Metric', value_name='L_value')

    # 4. Create Altair Chart
    base = alt.Chart(lines_df).encode(x=alt.X('Distance:Q', title='Distance d (meters)'))
    
    band = alt.Chart(results_df).mark_area(opacity=0.3, color='#808080').encode(
        x='Distance:Q', y='Lower CI:Q', y2='Upper CI:Q'
    )
    
    lines = base.mark_line(size=2.5).encode(
        y=alt.Y('L_value:Q', title='L(d)'),
        color=alt.Color('Metric:N', scale=alt.Scale(domain=['Observed L(d)', 'Expected CSR'], range=['#1f77b4', '#d62728'])),
        strokeDash=alt.condition(alt.datum.Metric == 'Expected CSR', alt.value([5, 5]), alt.value([0]))
    )
    
    chart = (band + lines).properties(
        title=f"Ripley's L-Function: {neighborhood_name}",
        width=600, height=350
    ).configure_title(fontSize=16, font='Arial', anchor='start')
    
    return chart

# %%
# ==========================================
# 5. EXECUTE EXAMPLES
# ==========================================

# Example 1: A highly planned area
chart_eixample = analyze_and_plot_neighborhood("la Dreta de l'Eixample", max_distance=500)
chart_eixample.display()

# Example 2: An older, organic area
chart_raval = analyze_and_plot_neighborhood("el Raval", max_distance=500)
chart_raval.display()