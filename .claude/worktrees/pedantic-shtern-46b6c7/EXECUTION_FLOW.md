# DeliveryIQ — Full Execution Flow

This document traces every step from the user filling in the sidebar forms to the optimized
route appearing on the map. Function signatures, arguments, and return values are described
in execution order.

---

## Table of Contents

1. [UI — Input and Geocoding](#1-ui--input-and-geocoding)
2. [Single-Vehicle Optimization Pipeline](#2-single-vehicle-optimization-pipeline)
   - [Step 1 — OSM Network Download](#step-1--osm-network-download)
   - [Step 2 — Address Snapping](#step-2--address-snapping)
   - [Step 3 — Mode Graphs](#step-3--mode-graphs)
   - [Step 3b — Hybrid Last-Meter (Drive)](#step-3b--hybrid-last-meter-drive)
   - [Step 4 — Distance Matrix + TSP (Bike & Walk)](#step-4--distance-matrix--tsp-bike--walk)
   - [Step 4 (Drive) — Matrix + TSP](#step-4-drive--matrix--tsp)
   - [Step 5 — Route Reconstruction](#step-5--route-reconstruction)
3. [Multi-Vehicle VRP Pipeline](#3-multi-vehicle-vrp-pipeline)
   - [Step 1 — OSM Network Download](#step-1--osm-network-download-1)
   - [Step 2 — Address Snapping](#step-2--address-snapping-1)
   - [Step 3 — Modal Graphs](#step-3--modal-graphs)
   - [Step 4 — Fleet Capacity Pre-flight](#step-4--fleet-capacity-pre-flight)
   - [Step 5 — VRP: Phase 1 Mode Assignment](#step-5--vrp-phase-1-mode-assignment)
   - [Step 6 — VRP: Phase 2 Intra-Mode Distribution](#step-6--vrp-phase-2-intra-mode-distribution)
   - [Step 7 — Per-Vehicle TSP](#step-7--per-vehicle-tsp)
   - [Step 8 — Leg Metadata](#step-8--leg-metadata)
4. [Results Rendering](#4-results-rendering)
5. [Data Class Reference](#5-data-class-reference)

---

## 1. UI — Input and Geocoding

Streamlit re-runs the entire `app.py` script on every widget interaction. All persistent
data is stored in `st.session_state` so it survives re-runs.

### 1.1 City Lock

The user selects a city from a dropdown (`st.selectbox`). The chosen value is stored in
`st.session_state.city`. Every subsequent geocoding call appends this city to the raw
query string, scoping all results to the selected metropolitan area.

```python
def _lock(address: str, city: str) -> str
```

- **Input:** raw user text (e.g. `"Shevchenko St"`), active city (`"Dnipro, Ukraine"`)
- **Output:** qualified query string (`"Shevchenko St, Dnipro, Ukraine"`)
- Does nothing if the city name is already present in the address.

### 1.2 Depot Geocoding

The user types a depot address and presses the **➜** button.

```python
def forward_geocode(raw: str, city: str) -> Optional[DeliveryStop]
```

- **Input:** raw address string, active city string.
- **Sends to API:** calls `_lock(raw, city)` to build a qualified query, then calls
  `_geolocator.geocode(query, timeout=10)` — an HTTP GET to the
  **Nominatim / OpenStreetMap** geocoding API
  (`https://nominatim.openstreetmap.org/search`).
- **Output:** `DeliveryStop(address, lat, lon, source="typed")` on success, `None` on
  failure.
- The result is stored in `st.session_state.depot`.

### 1.3 Delivery Stop Geocoding

The user types a delivery address and presses **➜**.  The same `forward_geocode()` is
called; the result is appended to `st.session_state.stops`.

### 1.4 Click-to-Add (Map Click)

When **Map Click** is enabled, `st_folium` captures the click coordinates and returns
them as `{"lat": …, "lng": …}` in `map_output["last_clicked"]`.

```python
def reverse_geocode(lat: float, lon: float) -> Optional[DeliveryStop]
```

- **Input:** WGS-84 latitude and longitude from the click.
- **Sends to API:** `_geolocator.reverse((lat, lon), language="en", timeout=10)` — an
  HTTP GET to `https://nominatim.openstreetmap.org/reverse`.
- **Output:** `DeliveryStop(address, lat, lon, source="map_click")`.
- If the reverse call fails or times out, the address string is set to
  `"<lat>, <lon>"` as a fallback. The stop is still added.

### 1.5 Fleet Configuration (Fleet Settings Tab)

The **Fleet Settings** tab (`fleet_tab`) lets the user manage the vehicle fleet stored
in `st.session_state.fleet` (a `list[Vehicle]`). The fleet is initialised on first load
with a single default drive vehicle:

```python
Vehicle("Vehicle 1", mode="drive", capacity=50, color=VEHICLE_COLORS[0])
```

Users can:
- **Edit** name, mode (`drive` / `bike` / `walk`), and capacity per vehicle via inline
  `st.text_input`, `st.selectbox`, `st.number_input` widgets.
- **Add** a vehicle with the `＋ Add Vehicle` button — appends a new `Vehicle` and
  reassigns palette colours by index.
- **Remove** a vehicle with the `✕` button (disabled when only one vehicle remains) —
  pops the entry and re-colours the remaining fleet.

Any fleet edit resets `st.session_state.opt_vrp_results` and `opt_results` so stale
results are not shown.

### 1.6 Run Button

```python
run_btn = st.button("🚀  Optimize Routes", disabled=not can_run)
```

`can_run` is `True` when `st.session_state.depot is not None` and
`len(st.session_state.stops) >= 1`. Clicking this button dispatches to one of two
pipelines based on fleet size:

| Fleet size | Pipeline |
|---|---|
| 1 vehicle | Single-vehicle — `run_optimization()`, produces 3-mode comparison |
| 2+ vehicles | Multi-vehicle VRP — `run_vrp_optimization()`, partitions stops across fleet |

---

## 2. Single-Vehicle Optimization Pipeline

Entry point:

```python
def run_optimization(
    depot:      DeliveryStop,
    stops:      list[DeliveryStop],
    city:       str,
    radius:     int,
    tsp_method: str,
) -> tuple[dict[str, ModeResult], dict[str, nx.MultiDiGraph], list[str], list[str]]
```

- **Input:**
  - `depot` — the geocoded depot stop.
  - `stops` — list of geocoded delivery stops.
  - `city` — active city string (used only for cache key on the city-name network).
  - `radius` — OSM download radius in metres (user-selected slider, default 4 000 m).
  - `tsp_method` — one of `"auto"`, `"nn"`, `"2opt"`, `"christofides"`, `"genetic"`.
- **Output:**
  - `results` — `dict[mode → ModeResult]` with keys `"drive"`, `"bike"`, `"walk"`.
  - `mode_graphs` — `dict[mode → nx.MultiDiGraph]`, the travel-time-stamped graphs.
  - `warnings_out` — human-readable warning strings shown in the UI.
  - `car_unreachable_notes` — addresses too far from a car-accessible road.

---

### Step 1 — OSM Network Download

```python
G_raw = cached_network_at(depot.lat, depot.lon, radius)
```

`cached_network_at` is a `@st.cache_resource` wrapper that calls:

```python
def get_network_from_point(lat: float, lon: float, dist: int = 3_000) -> nx.MultiDiGraph
```

**What it does (in order):**

1. **`_configure_osmnx_tags()`** — merges a list of required OSM tag names (e.g.
   `"motor_vehicle"`, `"bicycle"`, `"oneway"`) into `ox.settings.useful_tags_way` so
   the Overpass API returns them on every edge. Must run before any `ox.graph_from_*`
   call.

2. **`ox.graph_from_point((lat, lon), dist=dist, network_type="all", simplify=True)`** —
   issues an HTTP query to the **Overpass API** (`https://overpass-api.de`) asking for
   all walkable/cyclable/driveable edges within `dist` metres of the depot coordinates.
   Returns a raw `nx.MultiDiGraph` with every OSM node and edge in the bounding circle.

3. **`_largest_strongly_connected_component(G_raw)`** — calls
   `ox.truncate.largest_component(G, strongly=True)`, which finds the Largest Strongly
   Connected Component (LSCC) using NetworkX's SCC algorithm. Any node or edge not in
   the LSCC is dropped, guaranteeing that every pair of nodes has a directed path in
   both directions.

- **Returns:** `nx.MultiDiGraph` — LSCC-pruned, ready for travel-time stamping.

The graph is cached by `(lat, lon, radius)` so repeated runs with the same depot do not
re-download from Overpass.

After this step, `graph_summary(G_raw)` is called to extract node/edge counts and
connectivity status for the progress indicator.

---

### Step 2 — Address Snapping

Every `DeliveryStop` must be snapped to the nearest OSM node in the pruned graph so
the route solver can reference it by node ID.

**Typed addresses** (source `"typed"`):

```python
def nearest_node(G: nx.MultiDiGraph, lat: float, lon: float) -> int
```

- **Input:** the graph, geocoded latitude and longitude.
- **Calls:** `ox.nearest_nodes(G, X=lon, Y=lat)` — a spatial kd-tree lookup.
- **Returns:** the integer OSM node ID closest to `(lat, lon)`.

**Map-click addresses** (source `"map_click"`):

```python
def nearest_node_safe(G, lat, lon, *, tolerance_m=500.0) -> int
```

- Same as `nearest_node`, but first validates that `(lat, lon)` lies within the graph's
  bounding box plus a 500 m tolerance. Raises `ValueError` if the click landed outside
  the downloaded area; `run_optimization` catches this and falls back to unchecked
  `nearest_node`.

Snapping results are written directly onto each `DeliveryStop` object as `obj.node_id`.

**Node deduplication:** After all snaps, `run_optimization` builds:

```python
raw_nodes    = [depot.node_id] + [s.node_id for s in valid_stops]
unique_nodes = list(dict.fromkeys(raw_nodes))   # depot stays at index 0
```

If two addresses geocode to the exact same coordinates they snap to the same OSM node.
Duplicates are removed here (and again inside `build_distance_matrix`) so the TSP never
sees a zero-cost self-loop disguised as a real stop. A collision warning is appended to
`warnings_out`.

---

### Step 3 — Mode Graphs

```python
mode_graphs = cached_mode_graphs_at(depot.lat, depot.lon, radius)
```

Calls the cached wrapper which calls:

```python
def add_travel_times(G: nx.MultiDiGraph) -> dict[str, nx.MultiDiGraph]
```

- **Input:** the LSCC-pruned base graph.
- **What it does:** makes three independent deep copies of `G` (one per mode), then
  iterates every edge and calls `_compute_travel_time(data, mode, speed_ms)` to set
  `H[u][v][key]["travel_time"]`.
- **Returns:** `{"drive": G_drive, "bike": G_bike, "walk": G_walk}`.

#### `_compute_travel_time(data, mode, speed_ms) -> float`

- **Input:**
  - `data` — raw OSM edge attribute dict (contains keys like `"highway"`, `"access"`,
    `"motor_vehicle"`, `"bicycle"`, `"oneway"`, `"reversed"`, `"length"`).
  - `mode` — `"drive"`, `"bike"`, or `"walk"`.
  - `speed_ms` — mode speed in m/s (`drive=8.33`, `bike=4.17`, `walk=1.39`).
- **Returns:** travel time in seconds, or `PENALTY = 1e9` s if the mode is blocked.

Blocking rules per mode:

| Condition | drive | bike | walk |
|---|---|---|---|
| `highway` ∈ {footway, pedestrian, steps, …} | PENALTY | — | — |
| `motor_vehicle` ∈ {no, private, destination} | PENALTY | — | — |
| `bicycle` ∈ {no, dismount} | — | PENALTY | — |
| One-way reversed edge (no contra-flow tag) | — | PENALTY | — |
| `foot` ∈ {no, private} | — | — | PENALTY |
| `highway == "steps"` | — | PENALTY | 0.5× speed |
| Otherwise | `length / speed_ms` | `length / speed_ms` | `length / speed_ms` |

---

### Step 3b — Hybrid Last-Meter (Drive)

Car routing uses a dual-node model because delivery vehicles cannot always reach the
final address (e.g. pedestrian zones). For each stop:

```python
n_car = nearest_car_accessible_node(G_drive, stop.lat, stop.lon)
d_m   = distance_m_between_nodes(G_drive, n_ped, n_car)
```

#### `nearest_car_accessible_node(G_drive, lat, lon) -> int`

- Calls `ox.nearest_nodes(G_drive, X=lon, Y=lat)` to get the geometrically closest node.
- Checks `_node_car_accessible(G_drive, nn)`: returns `True` if the node has at least
  one incident edge with `travel_time < PENALTY`.
- If the nearest node is not car-accessible, scans all graph nodes and picks the one
  with the smallest haversine distance that is car-accessible.
- **Returns:** OSM node ID of the nearest car-accessible node.

#### `distance_m_between_nodes(G, node_a, node_b) -> float`

- Reads `(y, x)` coordinates of both nodes from the graph.
- Calls `ox.distance.great_circle(y1, x1, y2, x2)` for the geodesic distance in metres.
- Falls back to a manual Haversine formula if the OSMnx helper is unavailable.
- **Returns:** distance in metres.

If `d_m > LAST_METER_THRESHOLD_M` (100 m), the stop is added to `car_unreachable_notes`
and excluded from drive routing. Otherwise:

```python
walk_time_s = d_m / walk_speed_ms   # last-meter walk cost in seconds
car_reachable.append((n_car, walk_time_s, stop, label))
```

---

### Step 4 — Distance Matrix + TSP (Bike & Walk)

For bike and walk modes, all unique snapped nodes (depot + every stop) are used directly.

#### Distance Matrix

```python
def build_distance_matrix(
    G:      nx.MultiDiGraph,
    nodes:  list[int],
    weight: str = "travel_time",
) -> dict[tuple[int, int], float]
```

- **Input:**
  - `G` — the mode-specific graph with `travel_time` on edges.
  - `nodes` — `[depot_node, stop1_node, stop2_node, …]` (already deduplicated upstream,
    but deduplication is repeated here as defence-in-depth).
  - `weight` — edge attribute to minimise (`"travel_time"`).
- **What it does:** for every ordered pair `(src, dst)` in the node list, calls:
  ```python
  nx.shortest_path_length(G, src, dst, weight="travel_time")
  ```
  This runs **Dijkstra's algorithm** from `src`, finding the minimum-weight path to
  `dst`. If no directed path exists, `PENALTY (1e9)` is inserted.
  Diagonal entries (`src == dst`) are always `0.0`.
- **Returns:** `dict[(src_node, dst_node) → travel_time_seconds]`.
  An n×n matrix covering every ordered pair in the deduplicated node list.

After the matrix is built, `audit_reachability()` scans it for penalty entries:

```python
def audit_reachability(
    matrix: dict[tuple[int, int], float],
    nodes:  list[int],
    labels: dict[int, str],
) -> list[UnreachableStop]
```

- Iterates every off-diagonal pair; any entry `>= PENALTY` is recorded.
- **Returns:** a list of `UnreachableStop` objects, each describing which stop cannot
  reach or be reached from which other stops. These become UI warnings.

#### TSP Solver

```python
def solve_tsp(
    nodes:  list[int],
    matrix: dict[tuple[int, int], float],
    method: str = "auto",
    seed:   int = 42,
) -> tuple[list[int], float]
```

- **Input:**
  - `nodes` — ordered node IDs; `nodes[0]` is always the depot.
  - `matrix` — output of `build_distance_matrix`.
  - `method` — solver choice (or `"auto"` for automatic selection).
- **Auto-selection logic:**

  | Stop count | Method chosen |
  |---|---|
  | 1–2 | `nn` |
  | 3–20 | `2opt` |
  | 21+ | `genetic` |

- **Returns:** `(ordered_node_ids, total_travel_time_seconds)`.
  `ordered_node_ids` starts and ends at the depot: `[depot, s1, s2, …, sN, depot]`.

##### Internal TSP Methods

**`_nearest_neighbour(nodes, matrix) -> list[int]`**

Greedy construction: starts at the depot, repeatedly visits the unvisited stop with the
lowest matrix cost from the current position. Closes the tour by returning to the depot.
`O(n²)` complexity. Always produces a complete tour even when some matrix entries are
PENALTY.

**`_two_opt(route, matrix, max_iter=2000) -> list[int]`**

Local search improvement over a given route. On each iteration, tries every pair of
non-depot edges `(i, j)` and reverses the sub-segment `route[i:j+1]` if the reversal
reduces `_route_cost()`. Stops when no improvement is found or `max_iter` is reached.
Depot is pinned and never swapped.

**`_genetic_algorithm(nodes, matrix, population_size=120, generations=400, …) -> list[int]`**

Order-crossover (OX) genetic algorithm. The depot is excluded from the chromosome (it
is always first and last). Each generation: sorts population by fitness
(`1 / (cost + 1)`), keeps the top 10% as elites, produces offspring via OX crossover
and swap mutation, replaces the population. Returns the best chromosome after all
generations.

**`_christofides_tsp(nodes, matrix) -> list[int]`**

Builds an undirected helper graph `H` from all finite-cost matrix edges, then calls
`networkx.algorithms.approximation.christofides(H)`. Falls back to `_two_opt` if `H`
is disconnected, has fewer than 3 nodes, or if `christofides()` raises an exception.

##### `_route_cost(route, matrix) -> float`

Used internally by all TSP methods to evaluate a tour.

- **Input:** `route` — a complete closed tour `[depot, …, depot]`; `matrix` — the
  distance matrix.
- **Returns:** sum of `matrix[(route[i], route[i+1])]` for `i = 0 … len-2`.
  Missing keys, `None`, `inf`, `nan` are all normalised to PENALTY. Short-circuits and
  returns PENALTY as soon as the running total exceeds it.

---

### Step 4 (Drive) — Matrix + TSP

Drive routing uses index-based matrices (not node-ID-keyed) because multiple stops may
share the same parking node (`n_car`).

**With Mapbox API key set:**

```python
def build_drive_matrix_mapbox(
    stops:   list[tuple[float, float]],
    api_key: str,
) -> dict[tuple[int, int], float]
```

- **Input:**
  - `stops` — `[(lat, lon), …]` with the depot at index 0.
  - `api_key` — Mapbox public access token.
- **Sends to API:** issues HTTP GET requests to
  `https://api.mapbox.com/directions-matrix/v1/mapbox/driving-traffic/{coords}`
  using the `driving-traffic` profile, which incorporates historical traffic patterns.
  Coordinates are sent as semicolon-separated `lon,lat` pairs. Because Mapbox limits
  requests to 25 coordinates, the stops list is chunked into groups of 12 and every
  source-chunk × destination-chunk pair is requested separately.
- **Returns:** `dict[(i, j) → seconds]`. Index-keyed (not node-ID-keyed). `None`
  values from the API become PENALTY.

**Without Mapbox API key (static fallback):**

```python
def build_drive_matrix_hybrid(
    G_drive:    nx.MultiDiGraph,
    depot_node: int,
    car_stops:  list[tuple[int, float]],
    weight:     str = "travel_time",
) -> tuple[dict[tuple[int, int], float], list[int]]
```

- **Input:**
  - `G_drive` — drive-mode graph.
  - `depot_node` — OSM node ID of the depot.
  - `car_stops` — `[(n_car, walk_time_s), …]` for each car-reachable stop.
- **What it does:** runs `nx.shortest_path_length(G_drive, i, j)` for every index pair
  and adds the last-meter walk time for the destination:
  `total = drive_seconds + walk_seconds_to_door`.
- **Returns:** `(matrix_dict, nodes_drive)` where `nodes_drive` is
  `[depot_node, n_car_1, n_car_2, …]`.

**TSP for drive** uses integer indices `[0, 1, …, n_drive-1]` as the node list and
calls `solve_tsp(indices_drive, matrix_drive_idx, method=tsp_method)`. The returned
index route is then mapped back to OSM node IDs:

```python
tsp_route_d = [nodes_drive[i] for i in tsp_route_indices]
```

---

### Step 5 — Route Reconstruction

After the TSP produces a stop-to-stop sequence, the full geometry (every intermediate
road node between consecutive stops) is recovered:

```python
def reconstruct_full_route(
    G:         nx.MultiDiGraph,
    tsp_route: list[int],
    weight:    str = "travel_time",
) -> list[int]
```

- **Input:** mode graph, TSP output sequence (depot-to-depot), edge weight attribute.
- **What it does:** for each consecutive pair `(tsp_route[i], tsp_route[i+1])`,
  calls `get_full_path(G, src, dst)`, then concatenates the segments, dropping the
  shared boundary node between segments to avoid duplication.
- **Returns:** flat `list[int]` of all OSM node IDs along the complete route
  (may be thousands of nodes for a large network).

```python
def get_full_path(G, src, dst, weight="travel_time") -> list[int]
```

- Calls `nx.shortest_path(G, src, dst, weight=weight)` — Dijkstra returning the actual
  node sequence, not just the cost.
- Returns `[src]` if `src == dst`.
- Returns `[src, dst]` with a warning if no path exists (graceful degradation: the map
  renders a straight line for that segment).

**Per-leg metadata** is also built during this phase. For each consecutive pair in the
TSP route:

```python
path = get_full_path(G_mode, src, dst)
dist = leg_dist(G_mode, path)   # sums edge "length" attributes along the path
t    = matrix.get((src, dst), PENALTY)
legs.append(LegInfo(from_label, to_label, dist, t, cumulative_t))
```

`leg_dist(G, path)` reads the `"length"` attribute on each edge along the path and
returns the total physical distance in metres.

All results are packaged into a `ModeResult`:

```python
ModeResult(mode, tsp_route, full_route, total_time_s, legs, stop_visit_order)
```

and stored in `st.session_state.opt_results`.

---

## 3. Multi-Vehicle VRP Pipeline

Activated when `len(st.session_state.fleet) >= 2`. Entry point:

```python
def run_vrp_optimization(
    depot:      DeliveryStop,
    stops:      list[DeliveryStop],
    fleet:      list[Vehicle],
    radius:     int,
    tsp_method: str,
) -> tuple[list[VehicleRoute], dict[str, nx.MultiDiGraph], list[str]]
```

- **Output:**
  - `vrp_routes` — one `VehicleRoute` per active vehicle (vehicles with 0 stops omitted).
  - `mode_graphs` — same `{"drive": G, "bike": G, "walk": G}` dict as single-vehicle path.
  - `warnings_out` — human-readable warnings (unreachable stops, idle vehicles, etc.).

Results are stored in `st.session_state.opt_vrp_results` and
`st.session_state.opt_graphs`. `opt_results` is cleared to `None` so the single-vehicle
dashboard is not shown.

---

### Step 1 — OSM Network Download (VRP)

Identical to single-vehicle Step 1:

```python
G_raw = cached_network_at(depot.lat, depot.lon, radius)
```

The same `@st.cache_resource` wrapper is used; no extra Overpass downloads occur if the
single-vehicle path was already run from the same depot.

---

### Step 2 — Address Snapping (VRP)

Identical snapping logic to single-vehicle Step 2. Every stop and the depot have their
`node_id` field written in-place. Map-click stops go through `nearest_node_safe` with
the same 500 m tolerance fallback.

Stops that cannot be snapped (`node_id is None`) are collected and reported as warnings;
only `valid_stops` are passed to `solve_vrp`.

---

### Step 3 — Modal Graphs (VRP)

```python
mode_graphs = cached_mode_graphs_at(depot.lat, depot.lon, radius)
```

Returns the same `{"drive": G_drive, "bike": G_bike, "walk": G_walk}` dict.  All three
copies are shared across every vehicle regardless of mode, so the Overpass download and
travel-time stamping happen only once.

---

### Step 4 — Fleet Capacity Pre-flight

```python
total_cap = sum(v.capacity for v in fleet)
if total_cap < len(valid_stops):
    raise ValueError(...)
```

Raises a `ValueError` (caught as `st.error` in `app.py`) if the fleet cannot
theoretically hold all stops. Per-mode capacity exhaustion is caught separately by
`_distribute_within_mode` and surfaces with a user-facing error message.

---

### Step 5 — VRP: Phase 1 Mode Assignment

```python
vrp_routes, vrp_warnings = solve_vrp(valid_stops, depot, fleet, mode_graphs, tsp_method)
```

`solve_vrp` (in `vrp_solver.py`) calls `_assign_stops_to_modes` first:

```python
def _assign_stops_to_modes(
    stops:  list[DeliveryStop],
    fleet:  list[Vehicle],
    depot:  DeliveryStop,
    graphs: dict[str, nx.MultiDiGraph],
) -> tuple[dict[str, list], list]
```

**Algorithm:**

1. Compute `mode_remaining[mode]` = sum of capacities of all fleet vehicles with that mode.
2. For each stop, call `_check_reachable(stop, depot, graphs)`:
   ```python
   def _check_reachable(stop, depot, graphs) -> set[str]
   ```
   Runs `nx.shortest_path_length(G, depot.node_id, stop.node_id, weight="travel_time")`
   for each mode graph. A mode is reachable if the cost is strictly below `PENALTY`.
   Stops sharing the depot's node or with `node_id is None` return an empty set.
3. From the compatible modes, keep only those with `mode_remaining > 0` (capacity left).
4. If no mode qualifies, the stop goes to `unreachable` (surfaced as a fleet-level warning).
5. Otherwise, assign the stop to the mode with the **most remaining capacity** (greedy
   load-balancing), decrement that mode's counter by 1.

- **Returns:** `(mode_stop_pool, unreachable)`
  - `mode_stop_pool` — `dict[mode → list[stop]]`, one entry per fleet mode.
  - `unreachable` — stops no fleet mode can serve.

---

### Step 6 — VRP: Phase 2 Intra-Mode Distribution

For each mode with assigned stops, `solve_vrp` calls:

```python
def _distribute_within_mode(
    stops:    list[DeliveryStop],
    vehicles: list[Vehicle],
    depot:    DeliveryStop,
) -> dict[str, list[DeliveryStop]]
```

**Algorithm:**

1. Run `sklearn.cluster.KMeans(n_clusters=k)` on stop `(lat, lon)` coordinates,
   where `k = min(len(vehicles), len(stops))`.
2. Compute the compass bearing from depot to each cluster centroid using:
   ```python
   def _bearing(lat1, lon1, lat2, lon2) -> float
   ```
3. Sort clusters by bearing and assign each to the next available vehicle (bearing-sorted
   vehicles cover geographic sectors consistently).
4. **Capacity rebalancing:** for each over-capacity vehicle, pop excess stops and insert
   them into any other vehicle with remaining capacity. The scan checks vehicles with
   higher indices first, then lower indices, so the last vehicle in bearing order can
   spill back to earlier ones without raising a false `ValueError`.
5. If no vehicle has remaining capacity, raises `ValueError` (caught in `app.py`).

- **Returns:** `dict[vehicle.name → list[stop]]`.

---

### Step 7 — Per-Vehicle TSP

For each vehicle with at least one assigned stop:

```python
def _solve_per_vehicle(
    vehicle:       Vehicle,
    cluster_stops: list[DeliveryStop],
    depot:         DeliveryStop,
    graphs:        dict[str, nx.MultiDiGraph],
    tsp_method:    str,
) -> VehicleRoute
```

1. Selects `G = graphs[vehicle.mode]` — uses the vehicle's own modal graph.
2. Builds `nodes = [depot.node_id] + [s.node_id for s in cluster_stops]`, deduplicated
   via `dict.fromkeys` (depot first).
3. Calls `build_distance_matrix(G, nodes)` → Dijkstra n×n matrix.
4. Calls `solve_tsp(nodes, matrix, method=tsp_method)` → closed tour
   `[depot, s1, …, sN, depot]`.
5. Calls `reconstruct_full_route(G, tsp_route)` → full geometry node sequence.
6. Computes `total_dist_m` by summing edge `"length"` attributes along `full_route`
   (missing `"length"` attributes default to `0.0` to avoid `inf`).
7. Recovers `stop_visit_order` from `tsp_route[1:-1]` using a `node_to_stop` dict built
   with explicit collision detection: if two stops share an OSM node, a `logger.warning`
   is emitted and only the first is kept.

- **Returns:** `VehicleRoute` with `vehicle`, `stops`, `tsp_route`, `full_route`,
  `total_time_s`, `total_dist_m`.

---

### Step 8 — Leg Metadata (VRP)

After `solve_vrp` returns, `run_vrp_optimization` populates `vr.legs` for each route:

```python
for vr in vrp_routes:
    G = mode_graphs[vr.vehicle.mode]
    for i in range(len(vr.tsp_route) - 1):
        src, dst = vr.tsp_route[i], vr.tsp_route[i + 1]
        t    = nx.shortest_path_length(G, src, dst, weight="travel_time")
        path = get_full_path(G, src, dst)
        dist = leg_dist(G, path)
        legs.append(LegInfo(from_label, to_label, dist, t, cumulative_t))
    vr.legs = legs
```

Uses the vehicle's own modal graph for both the travel-time query and coordinate lookups,
so bike/walk routes are not contaminated by drive-graph data.

---

## 4. Results Rendering

After either pipeline completes, `st.rerun()` is called. On the next render cycle the
appropriate results dashboard is shown.

### 4.1 Single-Vehicle Dashboard (`opt_results is not None`)

**Mode Comparison Cards** — three `ModeResult` objects compared by `total_time_s`. The
fastest mode receives the "Most Efficient" badge.

**Result Map:**

```python
def build_result_map(mode_graphs, results, depot, stops) -> folium.Map
```

- Creates a `folium.Map` centred on the depot.
- For each mode, creates a `folium.FeatureGroup` layer and adds an
  `AntPath(locations=coords)` — an animated dashed polyline — using the `(lat, lon)`
  coordinates extracted from `full_route` node IDs via `n_coords(G, node)`.
- Adds a green depot marker and numbered red stop markers.
- Adds `folium.LayerControl` so the user can toggle between car / bike / walk routes.
- The map object is rendered in the browser via `st_folium(rmap, …)`.

**Stop Breakdown Tabs** — for each mode, `render_stop_table(result)` generates an HTML
table from the `legs` list. Each row shows: From → To, distance, leg travel time,
estimated arrival (current time + cumulative elapsed), and cumulative elapsed time.

### 4.2 Multi-Vehicle VRP Dashboard (`opt_vrp_results is not None`)

**Fleet Summary Cards** — one card per active `VehicleRoute`, colour-coded with the
vehicle's palette colour, showing name, mode icon, total time, distance, and stop count.

**Vehicle Routes Map:**

```python
def build_vrp_result_map(mode_graphs, vrp_routes, depot) -> folium.Map
```

- One `folium.FeatureGroup` per vehicle for the animated `AntPath` route.
- One `folium.FeatureGroup` per vehicle for numbered stop markers (colour-coded).
- Coordinate lookup uses `mode_graphs[vr.vehicle.mode]` so bike/walk node IDs resolve
  correctly even if absent from the drive graph copy.
- Green depot marker always visible.
- `folium.LayerControl` (top-left) toggles each vehicle's route and stop layer
  independently.

**Per-Vehicle Expanders** — each vehicle's expander shows: Total Time, Total Distance,
Mode metrics, and a `render_stop_table` reusing the existing single-vehicle table
renderer via a `ModeResult` pseudo-object wrapping the vehicle's legs.

Fleet-level warnings (unreachable stops, idle vehicles) appear above the expanders as
`warn-box` blocks.

---

## 5. Data Class Reference

### `DeliveryStop`
```
address : str              full geocoded address string
lat     : float            WGS-84 latitude
lon     : float            WGS-84 longitude
source  : str              "typed" | "map_click"
node_id : int | None       OSM node ID after snapping (set by run_optimization / run_vrp_optimization)
```

### `Vehicle`
```
name     : str    display name (e.g. "Van 1")
mode     : str    "drive" | "bike" | "walk"
capacity : int    maximum number of stops this vehicle can serve
color    : str    hex colour from VEHICLE_COLORS palette, assigned at creation time
```

### `VehicleRoute`
```
vehicle      : Vehicle          the vehicle this route belongs to
stops        : list[DeliveryStop]  ordered stop sequence (depot excluded), in TSP visit order
tsp_route    : list[int]        closed OSM node tour [depot_node, n1, …, depot_node]
full_route   : list[int]        expanded OSM node sequence for map polyline rendering
total_time_s : float            round-trip travel time in seconds
total_dist_m : float            round-trip road distance in metres
legs         : list[LegInfo]    populated by run_vrp_optimization after solve_vrp returns
```

### `LegInfo`
```
from_label        : str    human label of the origin stop ("Depot" | "Stop #N")
to_label          : str    human label of the destination stop
distance_m        : float  road distance in metres for this leg
travel_time_s     : float  leg travel time in seconds (from matrix)
cumulative_time_s : float  total elapsed time at end of this leg
```

### `ModeResult`
```
mode             : str         "drive" | "bike" | "walk"
tsp_route        : list[int]   OSM node IDs in TSP visit order (depot-to-depot)
full_route       : list[int]   all intermediate road node IDs along the complete route
total_time_s     : float       round-trip travel time in seconds
legs             : list[LegInfo]
stop_visit_order : list[DeliveryStop] | None   (drive only) stops in optimized order
```

### `UnreachableStop`
```
node_id          : int
label            : str           "Depot" | "Stop #N"
unreachable_from : list[str]     labels of stops that cannot reach this one
unreachable_to   : list[str]     labels of stops this one cannot reach
```

---

## Penalty Sentinel (`PENALTY = 1e9`)

`PENALTY` is defined identically in both `graph_builder.py` and `route_solver.py`, and
imported into `vrp_solver.py` from `route_solver`.
It serves two purposes:

1. **Edge weight** — `_compute_travel_time` returns `PENALTY` for any edge a mode
   cannot legally use. Dijkstra will always prefer any real detour (≈ 31 years vs.
   seconds), so PENALTY edges are effectively invisible to routing.

2. **Matrix entry** — `build_distance_matrix` inserts `PENALTY` for unreachable node
   pairs. `audit_reachability` flags any pair with cost `>= PENALTY`.
   `_check_reachable` in `vrp_solver.py` uses `cost < PENALTY` (strict less-than),
   consistent with the same sentinel convention.

The threshold in `audit_reachability` (`>= PENALTY`) must equal the sentinel in
`graph_builder`. Changing one without the other would silently suppress or falsely
trigger reachability warnings.
