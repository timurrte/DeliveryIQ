---
phase: implementation
title: Multi-Vehicle Routing — Implementation Guide
description: Technical notes, patterns, and integration details for VRP
---

# Implementation Guide

## Development Setup

- New dependency: add `scikit-learn` to `requirements.txt` (for `KMeans`).
- Activate venv: `source venv/Scripts/activate`
- Install: `pip install -r requirements.txt`
- Run app: `streamlit run app.py`

## Code Structure

```
DeliveryIQ/
  vrp_solver.py          # NEW — VRP partitioner + orchestrator
  route_solver.py        # unchanged
  graph_builder.py       # unchanged
  visualizer.py          # extended — draw_multi_vehicle_routes()
  app.py                 # extended — fleet UI + multi-vehicle results
  cache/
    fleet.json           # optional persistent fleet config
```

## Implementation Notes

### Core Features

**`Vehicle` dataclass**
- Fields: `name: str`, `mode: str`, `capacity: int`, `color: str`
- Keep it in `vrp_solver.py` unless other modules need it (then move to a `models.py`)

**`_cluster_stops(stops, fleet, depot)` — k-means geographic clustering**
```python
# 1. k = min(len(fleet), len(stops))
# 2. Run KMeans(n_clusters=k) on stop (lat, lon) coordinates
# 3. Assign each cluster to a vehicle (round-robin by centroid bearing from depot)
# 4. Capacity rebalancing: if a vehicle exceeds capacity, move excess stops
#    to the next vehicle with remaining capacity
# 5. Raise ValueError if all vehicles are full before all stops are assigned
# 6. Return dict[vehicle_name, List[Location]]
```

**PENALTY sentinel**
- Import `PENALTY = 1e9` from `route_solver.py` (or redefine identically).
- Use `matrix[depot_idx][stop_idx] >= PENALTY` to detect mode incompatibility — consistent with `audit_reachability()`.

**`solve_vrp()` return type**
- Always return a `List[VehicleRoute]` even if some vehicles have 0 stops — filter empties before rendering.

### Patterns & Best Practices

- **Reuse modal graphs**: pass `graphs = st.session_state["graphs"]` (the dict of three modal copies) into `solve_vrp()` — do not re-download.
- **Call `solve_tsp()` once per vehicle**: the existing auto-method selection (`nn` / `2opt` / `genetic`) handles small clusters correctly.
- **Colour palette**: assign colours at vehicle-creation time (index into a fixed list), not at render time, so colours are stable across re-renders.

```python
VEHICLE_COLORS = [
    "#e41a1c", "#377eb8", "#4daf4a", "#984ea3",
    "#ff7f00", "#a65628", "#f781bf", "#999999",
    "#66c2a5", "#fc8d62",
]
```

## Integration Points

### `app.py` changes

1. **Session state init** (top of `app.py`, after imports):
```python
if "fleet" not in st.session_state:
    st.session_state["fleet"] = [
        Vehicle("Vehicle 1", mode="drive", capacity=20, color=VEHICLE_COLORS[0])
    ]
```

2. **Fleet sidebar expander** — use `st.data_editor` or a manual loop with `st.columns` for name/mode/capacity inputs.

3. **Optimise button** — replace `solve_tsp()` call with `solve_vrp()`, pass `st.session_state["fleet"]`.

4. **Results section** — iterate `List[VehicleRoute]`, render one `st.expander` per vehicle with a stop table and summary metrics.

### `visualizer.py` changes

`draw_multi_vehicle_routes(m, vehicle_routes, depot)`:
- Call existing `draw_route(m, vr.node_path, G_modal, color=vr.vehicle.color)` per vehicle.
- Add a `folium.Marker` at depot with a special icon.
- Use `folium.PolyLine` (lighter than `AntPath`) for secondary vehicles to reduce DOM weight.

## Error Handling

- **Over-capacity fleet**: raise `ValueError("Total fleet capacity ({total}) < stops ({n}). Add more vehicles or increase capacity.")` — catch in `app.py` and surface via `st.error()`.
- **No compatible vehicle for a stop**: collect incompatible stops into a warning list, surface with `st.warning()`. Do not crash.
- **Empty vehicle cluster**: skip silently — do not call `solve_tsp()` with 0 stops.

## Performance Considerations

- Modal distance matrices are built per-vehicle using only that vehicle's stop subset — O(k²) Dijkstra where k = stops per vehicle, much cheaper than the full n² matrix.
- The OSM graph download is shared — no extra Overpass API calls.
- For > 50 stops total, consider caching the full n×n distance matrix once and slicing per vehicle, rather than re-running Dijkstra per vehicle.

## Security Notes

- Fleet config written to `cache/fleet.json` — local filesystem only, no user data leaves the machine.
- No new external API endpoints introduced.
- Stop/vehicle names are display-only strings, not evaluated — no injection risk.
