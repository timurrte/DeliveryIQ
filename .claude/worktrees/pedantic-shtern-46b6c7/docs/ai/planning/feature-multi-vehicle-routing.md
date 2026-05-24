---
phase: planning
title: Multi-Vehicle Routing — Project Plan
description: Task breakdown, dependencies, and effort estimates
status: reviewed
---

# Project Planning & Task Breakdown

## Milestones

- [ ] Milestone 1: Data model + VRP solver (`Vehicle`, `VehicleRoute`, `vrp_solver.py`)
- [ ] Milestone 2: Streamlit fleet configuration UI (dedicated "Fleet Settings" tab)
- [ ] Milestone 3: Multi-route map visualisation
- [ ] Milestone 4: Integration, polish, and edge-case handling

## Task Breakdown

### Phase 1: Foundation — Data Model & VRP Solver

- [ ] Task 1.1: Define `Vehicle` and `VehicleRoute` dataclasses (top of `vrp_solver.py`)
- [ ] Task 1.2: Add `scikit-learn` to `requirements.txt`
- [ ] Task 1.3: Implement `_cluster_stops()` — k-means geographic clustering (lat/lon) with post-cluster capacity rebalancing
- [ ] Task 1.4: Implement `_filter_mode_compatible()` — PENALTY-based reachability filter; collect skipped stops for warnings
- [ ] Task 1.5: Implement `_solve_per_vehicle()` — build modal distance matrix, call `solve_tsp()`, reconstruct round-trip node path (depot → stops → depot)
- [ ] Task 1.6: Implement `solve_vrp()` — orchestrates clustering + per-vehicle TSP, returns `List[VehicleRoute]`
- [ ] Task 1.7: Unit-test `vrp_solver.py` with a mock graph and 2-vehicle, 6-stop scenario

### Phase 2: Fleet Configuration UI

- [ ] Task 2.1: Add fleet state initialisation in `app.py` (`st.session_state["fleet"]` with a default single-vehicle entry on first load)
- [ ] Task 2.2: Add "Fleet Settings" tab to main area tab layout; build vehicle table (name / mode / capacity) using `st.data_editor`
- [ ] Task 2.3: Add "Add vehicle" and "Remove" buttons wired to session state
- [ ] Task 2.4: Implement optional `cache/fleet.json` persistence — load on startup, save on change
- [ ] Task 2.5: Validate fleet before optimisation: total capacity >= number of stops, at least one vehicle

### Phase 3: Multi-Route Visualisation

- [ ] Task 3.1: Extract `draw_route(feature_group, node_path, G, color, label)` helper from existing `build_map()` in `visualizer.py`; refactor `build_map()` to call it (no behaviour change)
- [ ] Task 3.2: Define 10-colour `VEHICLE_COLORS` palette constant in `visualizer.py`
- [ ] Task 3.3: Implement `draw_multi_vehicle_routes(m, vehicle_routes, depot_node, G_base)` — one `FeatureGroup` per vehicle, calls `draw_route()` with vehicle colour
- [ ] Task 3.4: Add vehicle-labelled popups on route segments and stop markers

### Phase 4: Integration & Polish

- [ ] Task 4.1: Replace single-route results section in `app.py` with per-vehicle expanders (stop list, distance, time)
- [ ] Task 4.2: Surface edge case outputs: `st.warning()` for skipped stops, `st.error()` for over-capacity fleet, silent skip for idle vehicles
- [ ] Task 4.3: Ensure single-vehicle fleet (fleet of 1) produces identical output to current single-vehicle mode
- [ ] Task 4.4: Manual end-to-end test: 3 vehicles (1 drive + 1 bike + 1 walk), 15 stops, mixed accessibility

## Dependencies

- Task 1.3 depends on Task 1.2 (sklearn available)
- Task 1.5 depends on Tasks 1.1, 1.3, 1.4
- Task 1.6 depends on Tasks 1.3, 1.4, 1.5
- Task 2.x depends on Task 1.1 (needs `Vehicle` dataclass)
- Task 3.3 depends on Task 3.1 (needs `draw_route()` helper)
- Task 3.3 depends on Task 1.6 (needs `VehicleRoute` type)
- Task 4.1 depends on Tasks 1.6 and 3.3
- `route_solver.py` and `graph_builder.py`: no changes required

## Timeline & Estimates

| Phase | Effort |
|---|---|
| Phase 1: VRP solver | Medium (core logic, new file, new dep) |
| Phase 2: Fleet UI | Small-Medium (Streamlit tab + data_editor) |
| Phase 3: Visualisation | Small (refactor + new function) |
| Phase 4: Integration | Small (wiring + edge cases) |

## Risks & Mitigation

| Risk | Likelihood | Mitigation |
|---|---|---|
| K-means produces capacity-violating clusters requiring heavy rebalancing | Medium | Post-cluster overflow reassignment; fallback to round-robin if k-means fails to converge |
| Mode-incompatible stops with no fallback vehicle | Low | Surface `st.warning()` list; user can manually adjust fleet modes |
| Folium performance with many route layers | Low | Use `PolyLine` instead of `AntPath` for secondary vehicles to reduce DOM weight |
| Per-vehicle distance matrix build time × N vehicles | Low | Matrix is O(k²) Dijkstra per vehicle cluster, much smaller than full n²; cached modal graphs reused |

## Resources Needed

- Existing codebase: `route_solver.py`, `graph_builder.py`, `visualizer.py`, `app.py`
- Python packages: existing stack + `scikit-learn` (new, add to `requirements.txt`)
- No new external services or API keys required
