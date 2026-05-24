---
phase: requirements
title: Multi-Vehicle Routing
description: VRP support for a predefined company fleet with per-vehicle modes and capacity limits
status: reviewed
---

# Requirements & Problem Understanding

## Problem Statement
**What problem are we solving?**

- DeliveryIQ currently optimises a single route for one driver. Real delivery companies operate a **fleet of vehicles** (vans, bikes, cargo bikes, etc.), each with different capabilities and load limits.
- Without multi-vehicle support, dispatchers must manually split stop lists between drivers, losing optimality and wasting time.
- Affected users: company dispatchers and logistics managers who manage a fixed, named fleet.
- Current workaround: manually run multiple single-vehicle optimisations with a subset of stops each time.

## Goals & Objectives
**What do we want to achieve?**

- **Primary goals**
  - Allow the company to pre-configure a named vehicle fleet (name, transport mode, stop capacity) via a dedicated settings tab.
  - Solve a Vehicle Routing Problem (VRP) that partitions delivery stops across the fleet using k-means geographic clustering and finds the optimal route per vehicle.
  - Display each vehicle's route as a distinct colour-coded path on the Folium map.

- **Secondary goals**
  - Show per-vehicle summary (total distance, estimated travel time, stop count).
  - Persist the fleet configuration across Streamlit sessions.

- **Non-goals**
  - Dynamic fleet management (adding/removing vehicles during an active dispatch session) — out of scope for v1.
  - Time-window constraints (TSPTW) — separate future feature.
  - Real-time vehicle tracking.

## User Stories & Use Cases

- As a **dispatcher**, I want to define my company's fleet (e.g. "Van 1 – drive, 20 stops", "Bike 1 – bike, 10 stops") so that the system knows what vehicles are available before optimisation.
- As a **dispatcher**, I want to click "Optimise Routes" and have stops automatically split across all vehicles in the fleet, minimising total travel time/distance.
- As a **dispatcher**, I want to see each vehicle's route highlighted in a distinct colour on the map so I can visually verify assignments.
- As a **dispatcher**, I want each vehicle's route summary (stops, distance, time) shown in a table/expander so I can brief drivers quickly.
- As a **manager**, I want the fleet configuration saved so I do not have to re-enter it every session.

**Edge cases**
- More vehicles in the fleet than stops: some vehicles get zero stops (idle); they are silently skipped in the output.
- A vehicle's transport mode cannot reach a stop (e.g. a drive-only van and a pedestrian-only zone): the stop is flagged in a Streamlit warning and skipped — optimisation continues for serviceable stops.
- All vehicles at capacity before all stops are assigned: surface an error requiring the user to add capacity or reduce stops.

## Success Criteria
**How will we know when we're done?**

- Fleet configuration UI (dedicated settings tab): add/edit/delete named vehicles with mode and capacity.
- VRP solver partitions stops correctly using k-means geographic clustering, respecting per-vehicle capacity.
- Each vehicle route is displayed with a unique colour on the map.
- Per-vehicle summary table rendered after optimisation.
- Fleet config persists in `st.session_state` (and optionally a JSON file in `cache/`).
- Existing single-vehicle flow continues to work (fleet of 1 vehicle = current behaviour).
- **Performance**: optimisation completes in < 5 seconds for 3 vehicles × 20 stops on a cached OSM graph.

## Constraints & Assumptions

- **Technical constraints**
  - Must stay within the existing OSMnx + NetworkX + Folium stack.
  - TSP solver (`route_solver.py`) is called once per vehicle — no global VRP solver library required for v1.
  - The shared OSM graph download / cache must be reused across all vehicles to avoid repeated API calls.

- **Assumptions**
  - All vehicles share the same depot (start **and** end point — round-trip routing).
  - A stop is assigned to exactly one vehicle.
  - The company fleet is small enough (< 20 vehicles) that k-means clustering + per-vehicle TSP is acceptable for v1.
  - Stops unreachable by all fleet vehicles are skipped with a warning (not a hard error).

- **Business constraints**
  - University project deadline — implementation must be achievable in a single sprint.

## Questions & Open Items

All questions resolved:

| # | Question | Decision |
|---|---|---|
| Q1 | Round-trip or open path? | **Round-trip** — vehicles return to depot |
| Q2 | Clustering strategy? | **K-means geographic** — cluster stops by lat/lon into k groups |
| Q3 | Fleet config UI location? | **Dedicated settings tab** in the main area |
| Q4 | Unreachable stop handling? | **Warning + skip** — list unserviceable stops, continue optimisation |
