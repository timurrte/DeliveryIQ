# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**DeliveryIQ** — a Streamlit-based delivery route optimizer that solves TSP (single-vehicle) and CVRPTW (multi-vehicle) over real OSM street networks with multi-modal routing (drive / bike / walk).

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

Seven modules with a clear data-flow pipeline:

```
app.py          (Streamlit UI — TSP tab, VRP tab, Package Manager tab, Fleet Settings tab)
  └─> geocoder.py       — Nominatim address → (lat, lon)
  └─> graph_builder.py  — OSM network download, LSCC pruning, travel-time stamping
  └─> route_solver.py   — distance matrix (Dijkstra) + TSP (NN / 2-opt / genetic / Christofides)
  └─> vrp_solver.py     — CVRPTW multi-vehicle GA decoder (solve_vrp, VehicleRoute)
  └─> package_db.py     — SQLite-backed package DB with status tracking (PackageDB)
  └─> visualizer.py     — Folium map with AntPath animated routes
```

### Key Design Decisions

**City lock**: `DEFAULT_CITY = "Dnipro, Ukraine"` in `app.py:64`. Change this one line to re-scope all geocoding to a different city.

**OSMnx version**: requires `osmnx>=2.1`. Uses `ox.settings.useful_tags_way`, `ox.truncate.largest_component`, and `ox.nearest_nodes` — all 2.x API. Do not introduce 1.x compatibility shims.

**PENALTY sentinel (1e9 s)**: Defined identically in `graph_builder.py:53` and `route_solver.py:54` to mark impassable edges and unreachable matrix pairs. `audit_reachability()` flags pairs with cost `>= PENALTY`. Never change one file without the other.

**'all' network + LSCC pruning**: Graph downloaded as `network_type="all"` to capture walk/bike paths, then pruned to the Largest Strongly Connected Component. Always snap node coordinates to the LSCC-pruned graph (output of `get_network()`), not the raw download — snapping before pruning produces nodes absent from the final graph.

**Three modal graph copies**: `add_travel_times()` returns `{"drive": G_drive, "bike": G_bike, "walk": G_walk}` — three independent deep copies each with `travel_time` edge attributes encoding modal access rules. The alternative `add_travel_times_to_single_graph()` puts all three on one graph as `travel_time_drive/bike/walk`.

**TSP method auto-selection**: `solve_tsp(method="auto")` picks `nn` for 1–2 stops, `2opt` for 3–20, `genetic` for 21+. Christofides is available as an explicit method but requires a complete connected graph — it will fail/fall back when any stop is unreachable. `2opt` is preferred over Christofides for small instances because it handles PENALTY-weight edges gracefully.

**Hybrid last-meter routing** (drive mode): `build_drive_matrix_hybrid()` and `nearest_car_accessible_node()` implement a pattern where the car parks at the nearest driveable node and the last segment (≤ `LAST_METER_THRESHOLD_M = 100.0 m`) is walked. `_node_car_accessible()` checks incident edges.

**OSMnx tag retention**: `_configure_osmnx_tags()` must be called before any `ox.graph_from_*` download to keep modal tags (`bicycle`, `motor_vehicle`, `cycleway:*`, etc.) on edges. Called automatically inside `get_network()` and `get_network_from_place()`.

**Depot-centred graph download**: During optimization, the OSM graph is downloaded via `cached_network_at(depot.lat, depot.lon, radius)` (`ox.graph_from_point`) rather than by city name string. Nominatim can place a city-name label far from the actual delivery area, causing all addresses to snap to a single boundary node. The city-name based `cached_network(city, radius)` is kept only for the pre-optimization map display.

**Mapbox fallback**: `build_drive_matrix_mapbox()` provides a traffic-aware drive matrix when an API key is available, with automatic fallback to static OSM Dijkstra when the key is absent or the call fails.

**VRP solver**: `solve_vrp()` in `vrp_solver.py` implements CVRPTW with a two-phase GA decoder — Phase 1 assigns stops to vehicles by mode compatibility and capacity, Phase 2 runs an independent TSP GA per mode group. Key GA parameters: `POP_SIZE=50`, `N_GENERATIONS=150`, `CROSSOVER_RATE=0.85`, `MUTATION_RATE=0.15`, `ELITE_SIZE=2`, `TOURNAMENT_SIZE=3`. Objective: `F = A·T_total + B·K` (total travel time + vehicle count penalty).

**Package database**: `PackageDB` in `package_db.py` persists packages in SQLite. Statuses: `PENDING → IN_TRANSIT → DELIVERED`. Packages carry `weight_kg`, `address`, optional `(lat, lon)`, and `package_id`.

**Time windows**: `DeliveryStop` has `tw_open`, `tw_close`, `service_time` fields used by `vrp_solver.py`. These are optional (None = unconstrained).

**Streamlit session state**: All expensive computation is cached in `st.session_state`. Key keys: `depot`, `stops`, `fleet`, `opt_results`, `opt_graphs`, `opt_warnings`, `opt_car_unreachable`, `opt_vrp_results`, `opt_vrp_warnings`, `click_mode`, `last_click`. Optimization graph keyed by `(depot.lat, depot.lon, radius)`.

### Modal Routing Rules Summary

| Edge type | drive | bike | walk |
|---|---|---|---|
| `highway=footway/pedestrian/steps/corridor/elevator/escalator/bridleway` | PENALTY | passable | passable (steps: 50% speed) |
| `motor_vehicle=no/private/destination` | PENALTY | passable | passable |
| `access=no/private/destination` (no motor_vehicle override) | PENALTY | passable | passable |
| `bicycle=no/dismount` | passable | PENALTY | passable |
| `highway=cycleway` or `bicycle=yes/designated/permissive` | passable | fast | passable |
| One-way reversed edge | — | PENALTY (unless `oneway:bicycle=no` / `bicycle:oneway=no` / `cycleway=opposite*`) | passable |
| `foot=no/private` | passable | passable | PENALTY |

Speed constants: drive=30 km/h, bike=15 km/h, walk=5 km/h (steps=2.5 km/h effective).

### Cache Directory

`cache/` stores downloaded OSM graphs as `.graphml` files to avoid repeated Nominatim/Overpass API calls during development. The cache key is derived from the location string and radius.
