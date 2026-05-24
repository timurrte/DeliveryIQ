---
phase: testing
title: Multi-Vehicle Routing — Test Plan
description: Unit and integration tests for vrp_solver.py
status: complete
---

# Testing — Multi-Vehicle Routing

## Test File

| Module | Test File | Coverage |
|---|---|---|
| `vrp_solver.py` | `tests/test_vrp_solver.py` | **100%** (155/155 stmts) |

## Running Tests

```bash
# Activate venv first
source venv/Scripts/activate   # or venv\Scripts\activate on Windows

# Run tests
python -m pytest tests/test_vrp_solver.py -v

# With coverage
python -m pytest tests/test_vrp_solver.py --cov=vrp_solver --cov-report=term-missing
```

## Test Summary

**49 tests, 49 passed, 0 failed — 100% coverage**

## Test Classes & Coverage

### `TestBearing` (6 tests)
- Cardinal directions (N, E, S, W)
- Output always in [0, 360) range
- Same-point edge case (defined, finite)

### `TestCheckReachable` (6 tests)
- `None` node_id → empty set
- Stop at same node as depot → empty set
- All modes reachable → full set
- PENALTY-only edge → not in reachable set
- Mode-selective reachability (walk-only vs drive/bike blocked)
- Missing node in graph → empty set (NodeNotFound handled)

### `TestAssignStopsToModes` (7 tests)
- All stops to single-mode fleet
- Unreachable stops collected (PENALTY path)
- Mode chosen by most remaining capacity
- Excess stops go to unreachable when fleet at capacity
- `None` node_id stop → unreachable
- Empty stops list → empty pools
- Multi-mode fleet distributes across modes

### `TestDistributeWithinMode` (8 tests)
- Single vehicle: all stops assigned
- Two vehicles: stops split, total preserved
- Capacity rebalancing: excess moved to next vehicle (covers success branch)
- Over-capacity raises `ValueError("Capacity exhausted")`
- k capped at `len(stops)` when more vehicles than stops
- Single stop / single vehicle
- Result dict keyed by vehicle name
- Rebalancing success branch (forced clustered scenario)

### `TestSolvePerVehicle` (5 tests)
- Happy path: returns `VehicleRoute` with correct fields
- Stop visit order excludes depot
- Duplicate node_id stops: only first in visit order (logged warning)
- `total_dist_m` is non-negative
- `tsp_method` forwarded to `solve_tsp()`

### `TestSolveVrp` (10 tests)
- Empty fleet → `ValueError`
- Empty stops → `ValueError`
- `depot.node_id is None` → `ValueError`
- Happy path: returns `(List[VehicleRoute], List[str])`
- Return type is `tuple[list, list]`
- Idle vehicle produces info warning
- Unreachable stop produces warning
- No vehicle routed → `RuntimeError`
- Multi-mode fleet: at least one route produced
- Excess stops (over capacity) → warning, not crash

### `TestVehicleColors` (3 tests)
- Exactly 10 colours in palette
- All colours in `#rrggbb` hex format
- All colours unique

### `TestDataclasses` (4 tests)
- `Vehicle` fields and defaults
- `VehicleRoute` fields and defaults (`legs=[]`, `skipped_stops=[]`)
- `PENALTY == 1e9`

## Mocking Strategy

`route_solver` functions are patched at the `vrp_solver` module level for unit isolation:
- `vrp_solver.build_distance_matrix`
- `vrp_solver.solve_tsp`
- `vrp_solver.reconstruct_full_route`

Graph fixtures use real `nx.MultiDiGraph` instances with proper `travel_time` and `length` edge attributes, and `y`/`x` node attributes — matching the production graph format.

## Edge Cases Verified

- Stop with same OSM node as depot (treated as unreachable)
- Two stops sharing the same OSM node (only first in visit order, logged)
- Fleet capacity exhausted partway through stop list (excess → unreachable warning)
- All stops unreachable (RuntimeError surfaced to caller)
- More vehicles than stops (k capped at n_stops, extra vehicles idle)
- Mixed-mode fleet with some modes having no compatible stops (idle warning)
