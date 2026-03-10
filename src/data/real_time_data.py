import requests
import pandas as pd
import folium

# --- API endpoints ---
status_url = "https://api.citybik.es/gbfs/2/bicing/station_status.json"
info_url = "https://api.citybik.es/gbfs/2/bicing/station_information.json"

# --- Load data ---
status = requests.get(status_url).json()
info = requests.get(info_url).json()

status_df = pd.DataFrame(status["data"]["stations"])
info_df = pd.DataFrame(info["data"]["stations"])

# --- Merge datasets ---
df = pd.merge(info_df, status_df, on="station_id")

# --- Create base map (Barcelona center) ---
m = folium.Map(
    location=[41.3851, 2.1734],
    zoom_start=13,
    tiles="CartoDB positron"
)

# --- Function for red -> green colors ---
def bike_color(n):
    if n < 3:
        return "red"
    elif n < 8:
        return "orange"
    else:
        return "green"

# --- Add stations ---
for _, row in df.iterrows():
    folium.CircleMarker(
        location=[row["lat"], row["lon"]],
        radius=6,
        color=bike_color(row["num_bikes_available"]),
        fill=True,
        fill_color=bike_color(row["num_bikes_available"]),
        fill_opacity=0.8,
        popup=f"Station: {row['name']}<br>Bikes available: {row['num_bikes_available']}"
    ).add_to(m)

# --- Save map ---
m.save("/reports/figures/bicing_map.html")

print("Map saved as bicing_map.html")