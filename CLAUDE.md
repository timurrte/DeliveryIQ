# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**DeliveryIQ** — a Streamlit-based delivery route optimizer that solves the Travelling Salesman Problem (TSP) over real OSM street networks with multi-modal routing (drive / bike / walk).

## Running the Application

```bash
# Activate virtual environment first
source venv/Scripts/activate  # or venv\Scripts\activate on Windows

# Run the Streamlit app
streamlit run app.py

# Run with debug logging
PYTHONPATH=. python -m streamlit run app.py --logger.level=debug
```

## Install Dependencies

```bash
pip install -r requirements.txt
```

## Architecture

The app is composed of five modules with a clear data-flow pipeline:

```
app.py  (Streamlit UI)
  └─> geocoder.py       — Nominatim address → (lat, lon)
  └─> graph_builder.py  — OSM network download, LSCC pruning, travel-time stamping
  └─> route_solver.py   — distance matrix (Dijkstra) + TSP (NN / 2-opt / genetic / Christofides)
  └─> visualizer.py     — Folium map with AntPath animated routes
```

### Key Design Decisions

**City lock**: `DEFAULT_CITY = "Dnipro, Ukraine"` in `app.py:56`. Change this one line to re-scope all geocoding to a different city.

**OSMnx version**: requires `osmnx>=2.1`. Uses `ox.settings.useful_tags_way`, `ox.truncate.largest_component`, and `ox.nearest_nodes` — all 2.x API. Do not introduce 1.x compatibility shims.

**PENALTY sentinel (1e9 s)**: Used consistently in both `graph_builder.py` and `route_solver.py` to mark impassable edges and unreachable matrix pairs. The value must be identical in both files — `audit_reachability()` in `route_solver.py` flags pairs with cost `>= PENALTY`. Never change one without the other.

**'all' network + LSCC pruning**: The graph is downloaded as `network_type="all"` to capture walk/bike paths, then immediately pruned to the Largest Strongly Connected Component. Always snap node coordinates to the LSCC-pruned graph (output of `get_network()`), not the raw download — snapping before pruning produces nodes absent from the final graph.

**Three modal graph copies**: `add_travel_times()` returns `{"drive": G_drive, "bike": G_bike, "walk": G_walk}` — three independent deep copies each with `travel_time` edge attributes encoding modal access rules. The alternative `add_travel_times_to_single_graph()` puts all three on one graph as `travel_time_drive/bike/walk`.

**TSP method auto-selection**: `solve_tsp(method="auto")` picks `nn` for 1-2 stops, `2opt` for 3-20, `genetic` for 21+. Christofides is available as an explicit method but requires a complete connected graph — it will fail/fall back when any stop is unreachable.

**Hybrid last-meter routing** (drive mode): `build_drive_matrix_hybrid()` and `nearest_car_accessible_node()` support a pattern where the car parks at the nearest driveable node and the last segment is walked. The `_node_car_accessible()` helper checks incident edges.

**OSMnx tag retention**: `_configure_osmnx_tags()` must be called before any `ox.graph_from_*` download to keep modal tags (`bicycle`, `motor_vehicle`, `cycleway:*`, etc.) on edges. It is called automatically inside `get_network()` and `get_network_from_place()`.

**Depot-centred graph download**: During optimization, the OSM graph is downloaded via `cached_network_at(depot.lat, depot.lon, radius)` (`ox.graph_from_point`) rather than by city name string. Nominatim can place a city-name label far from the actual delivery area, causing all addresses to snap to a single boundary node. The city-name based `cached_network(city, radius)` is kept only for the pre-optimization map display.

**Streamlit session state**: All expensive computation (graph download, travel-time stamping, distance matrix) is cached in `st.session_state` to avoid re-running on every widget interaction. The optimization graph is keyed by `(depot.lat, depot.lon, radius)`.

### Modal Routing Rules Summary

| Edge type | drive | bike | walk |
|---|---|---|---|
| `highway=footway/pedestrian/steps` | PENALTY | passable | passable (steps: 50% speed) |
| `motor_vehicle=no/private/destination` | PENALTY | passable | passable |
| `highway=cycleway` or `bicycle=yes` | passable | passable | passable |
| One-way reversed edge | — | PENALTY (unless `oneway:bicycle=no` / `cycleway=opposite*`) | passable |

### Cache Directory

`cache/` stores downloaded OSM graphs as `.graphml` files to avoid repeated Nominatim/Overpass API calls during development. The cache key is derived from the location string and radius.
