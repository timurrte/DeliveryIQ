"""
vrp_solver.py — Capacitated Vehicle Routing Problem for DeliveryIQ
==================================================================
Algorithm: mode-first assignment, then k-means geographic clustering.

Two-phase approach
------------------
Phase 1 — Mode assignment (per stop):
  For every stop, compute which vehicle modes can reach it via the road
  network (Dijkstra with travel_time weight, PENALTY sentinel for
  impassable edges).  Assign each stop to the vehicle-type pool that has
  the most remaining capacity among compatible modes, so the overall fleet
  capacity is consumed evenly.  Stops unreachable by any fleet mode are
  collected as warnings and skipped.

Phase 2 — Intra-type distribution (per mode):
  Within each mode's stop pool, run k-means geographic clustering scoped
  only to that mode's vehicles, then apply capacity rebalancing.  Solve
  TSP independently for each vehicle's cluster.

Key design decisions
--------------------
* Round-trip routing: every vehicle starts AND ends at the depot.
* Compatibility is checked BEFORE clustering, not after.  A stop is
  guaranteed to be assigned only to vehicles whose mode can reach it.
* PENALTY sentinel (1e9): same value as route_solver.py — a
  shortest-path cost >= PENALTY indicates a mode-impassable edge.
* Stops that snap to the same OSM node as the depot cannot be
  represented as distinct TSP stops and are treated as unreachable.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field

import numpy as np
import networkx as nx
from sklearn.cluster import KMeans

from route_solver import (
    build_distance_matrix,
    solve_tsp,
    reconstruct_full_route,
    PENALTY,
)
from graph_builder import nearest_car_accessible_node

logger = logging.getLogger(__name__)

# 10-colour qualitative palette (ColorBrewer Set1 + extensions).
# Colours are assigned by vehicle index at creation time and are stable
# across UI reruns.
VEHICLE_COLORS: list[str] = [
    "#e41a1c", "#377eb8", "#4daf4a", "#984ea3",
    "#ff7f00", "#a65628", "#f781bf", "#999999",
    "#66c2a5", "#fc8d62",
]


# ══════════════════════════════════════════════════════════════════════════════
#  DATA CLASSES
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class Vehicle:
    name: str
    mode: str       # "drive" | "bike" | "walk"
    capacity: int   # maximum number of stops this vehicle can serve
    color: str = ""


@dataclass
class VehicleRoute:
    vehicle: Vehicle
    stops: list         # ordered list of DeliveryStop in visit order (depot excluded)
    tsp_route: list     # closed OSM node tour: [depot_node, n1, …, depot_node]
    full_route: list    # expanded OSM node sequence for map polyline rendering
    total_time_s: float
    total_dist_m: float
    legs: list = field(default_factory=list)         # LegInfo list; populated by run_vrp_optimization()
    skipped_stops: list = field(default_factory=list)  # not used in new flow; kept for API compat


# ══════════════════════════════════════════════════════════════════════════════
#  INTERNAL HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Compass bearing in degrees (0–360) from point 1 to point 2."""
    lat1r, lat2r = math.radians(lat1), math.radians(lat2)
    dlon = math.radians(lon2 - lon1)
    x = math.sin(dlon) * math.cos(lat2r)
    y = (math.cos(lat1r) * math.sin(lat2r)
         - math.sin(lat1r) * math.cos(lat2r) * math.cos(dlon))
    return (math.degrees(math.atan2(x, y)) + 360) % 360


def _check_reachable(stop, depot, graphs: dict) -> set:
    """
    Return the set of mode names that can reach *stop* from *depot*.

    A mode is considered reachable when the Dijkstra shortest-path cost
    (travel_time weight) is strictly below PENALTY.  Stops that have no
    node_id or share the depot's node are never reachable.
    """
    if stop.node_id is None or stop.node_id == depot.node_id:
        return set()

    reachable = set()
    for mode, G in graphs.items():
        # For drive mode use the nearest car-accessible node (hybrid last-meter):
        # the pedestrian snap node may only be reachable via footways (PENALTY edges).
        if mode == "drive":
            check_node = nearest_car_accessible_node(G, stop.lat, stop.lon)
            if check_node is None:
                check_node = stop.node_id
        else:
            check_node = stop.node_id
        try:
            cost = nx.shortest_path_length(G, depot.node_id, check_node, weight="travel_time")
            if cost < PENALTY:
                reachable.add(mode)
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            pass
    return reachable


def _assign_stops_to_modes(
    stops: list,
    fleet: list,
    depot,
    graphs: dict,
) -> tuple[dict, list]:
    """
    Phase 1 — assign each stop to a vehicle-type pool.

    For stops compatible with multiple fleet modes, the mode whose vehicles
    have the most remaining total capacity is chosen, so capacity is
    consumed evenly across vehicle types.

    Returns
    -------
    mode_stop_pool : dict[mode -> list[stop]]
        Stops assigned to each vehicle type.  Only modes present in the
        fleet appear as keys.
    unreachable : list[stop]
        Stops that no fleet mode can reach (or that are over capacity).
    """
    # Total remaining capacity per mode across all vehicles of that mode
    mode_remaining: dict[str, int] = {}
    for v in fleet:
        mode_remaining[v.mode] = mode_remaining.get(v.mode, 0) + v.capacity

    # Initialise a pool for every mode present in the fleet
    fleet_modes = set(v.mode for v in fleet)
    mode_stop_pool: dict[str, list] = {m: [] for m in fleet_modes}

    unreachable: list = []

    for stop in stops:
        compatible = _check_reachable(stop, depot, graphs)
        # Keep only modes that have fleet vehicles with remaining capacity
        available = [m for m in compatible if mode_remaining.get(m, 0) > 0]

        if not available:
            unreachable.append(stop)
            continue

        # Prefer the mode whose vehicles have the most remaining capacity
        chosen = max(available, key=lambda m: mode_remaining[m])
        mode_stop_pool[chosen].append(stop)
        mode_remaining[chosen] -= 1

    return mode_stop_pool, unreachable


def _distribute_within_mode(stops: list, vehicles: list, depot) -> dict:
    """
    Phase 2 — distribute a mode's stop pool across its vehicles.

    Applies k-means geographic clustering (with centroid bearing-sort so
    geographically adjacent stops stay together) and then capacity
    rebalancing.

    Returns
    -------
    dict[vehicle.name -> list[stop]]
    """
    n_stops = len(stops)
    k = min(len(vehicles), n_stops)
    coords = np.array([[s.lat, s.lon] for s in stops])

    if k == 1:
        labels = np.zeros(n_stops, dtype=int)
        centroids = coords.mean(axis=0, keepdims=True)
    else:
        km = KMeans(n_clusters=k, n_init=10, random_state=42)
        km.fit(coords)
        labels = km.labels_
        centroids = km.cluster_centers_

    # Sort cluster centroids by bearing from depot
    centroid_bearings = [
        _bearing(depot.lat, depot.lon, float(c[0]), float(c[1]))
        for c in centroids
    ]
    cluster_order = sorted(range(k), key=lambda i: centroid_bearings[i])
    cluster_to_veh_idx = {cluster_order[i]: i for i in range(k)}

    # Build initial per-vehicle assignment
    assignment: dict[int, list] = {i: [] for i in range(len(vehicles))}
    for stop_idx, cluster_id in enumerate(labels):
        veh_idx = cluster_to_veh_idx[int(cluster_id)]
        assignment[veh_idx].append(stops[stop_idx])

    # Capacity rebalancing: move excess stops to any vehicle with remaining capacity.
    # Scan all other vehicles (forward first, then backward) so the last vehicle in
    # bearing order can spill back to earlier ones rather than raising a false error.
    for veh_idx in range(len(vehicles)):
        cap = vehicles[veh_idx].capacity
        while len(assignment[veh_idx]) > cap:
            excess = assignment[veh_idx].pop()
            placed = False
            candidates = list(range(veh_idx + 1, len(vehicles))) + list(range(0, veh_idx))
            for next_idx in candidates:
                if len(assignment[next_idx]) < vehicles[next_idx].capacity:
                    assignment[next_idx].insert(0, excess)
                    placed = True
                    break
            if not placed:
                mode = vehicles[0].mode
                raise ValueError(
                    f"Capacity exhausted for {mode!r} vehicles: "
                    f"{n_stops} stop(s) exceed total {mode} fleet capacity. "
                    f"Increase vehicle capacities or add more {mode} vehicles."
                )

    return {vehicles[i].name: assignment[i] for i in range(len(vehicles))}


def _solve_per_vehicle(
    vehicle: Vehicle,
    cluster_stops: list,
    depot,
    graphs: dict,
    tsp_method: str,
) -> VehicleRoute:
    """Build distance matrix and solve TSP for one vehicle's stop cluster."""
    G = graphs[vehicle.mode]

    # Depot first, then stops; dedup preserves depot-first order.
    # For drive mode use the nearest car-accessible node per stop so the
    # distance matrix is built on the driveable graph (pedestrian snap nodes
    # are surrounded by PENALTY edges and would produce an all-PENALTY matrix).
    if vehicle.mode == "drive":
        stop_nodes = [
            nearest_car_accessible_node(G, s.lat, s.lon) or s.node_id
            for s in cluster_stops
        ]
    else:
        stop_nodes = [s.node_id for s in cluster_stops]
    nodes = [depot.node_id] + stop_nodes
    nodes = list(dict.fromkeys(nodes))

    matrix = build_distance_matrix(G, nodes)
    tsp_route, total_time_s = solve_tsp(nodes, matrix, method=tsp_method)
    full_route = reconstruct_full_route(G, tsp_route)

    # Total road distance along the expanded full_route
    total_dist_m = 0.0
    for i in range(len(full_route) - 1):
        ed = G.get_edge_data(full_route[i], full_route[i + 1])
        if ed:
            total_dist_m += min(
                (v.get("length", 0.0) for v in ed.values()),
                default=0.0,
            )

    # Recover stop visit order from tsp_route (exclude depot nodes at ends).
    # Build node_to_stop explicitly so collisions (two stops at the same OSM node)
    # are logged rather than silently dropped.
    node_to_stop: dict = {}
    for s in cluster_stops:
        if s.node_id in node_to_stop:
            logger.warning(
                "Vehicle %r: two stops share OSM node %d (%r vs %r) — "
                "only the first will appear in the route.",
                vehicle.name, s.node_id,
                node_to_stop[s.node_id].address[:30], s.address[:30],
            )
        else:
            node_to_stop[s.node_id] = s
    stop_visit_order: list = []
    seen: set = set()
    for n in tsp_route[1:-1]:
        if n not in seen and n in node_to_stop:
            seen.add(n)
            stop_visit_order.append(node_to_stop[n])

    logger.info(
        "Vehicle %r (%s): %d stop(s) routed — %.0f s total",
        vehicle.name, vehicle.mode, len(stop_visit_order), total_time_s,
    )

    return VehicleRoute(
        vehicle=vehicle,
        stops=stop_visit_order,
        tsp_route=tsp_route,
        full_route=full_route,
        total_time_s=total_time_s,
        total_dist_m=total_dist_m,
    )


# ══════════════════════════════════════════════════════════════════════════════
#  PUBLIC API
# ══════════════════════════════════════════════════════════════════════════════

def solve_vrp(
    stops: list,
    depot,
    fleet: list,
    graphs: dict,
    tsp_method: str = "auto",
) -> tuple[list, list]:
    """
    Solve a Capacitated VRP for the given fleet and stops.

    Parameters
    ----------
    stops      : list[DeliveryStop]        — all delivery stops (node_id set)
    depot      : DeliveryStop              — start/end point (node_id set)
    fleet      : list[Vehicle]             — vehicles with mode and capacity
    graphs     : dict[str, MultiDiGraph]   — {"drive": G, "bike": G, "walk": G}
    tsp_method : str                       — TSP algorithm for each cluster

    Returns
    -------
    (routes, warnings)
    routes   : list[VehicleRoute]  — one entry per active vehicle
    warnings : list[str]           — user-facing warning messages
    """
    if not fleet:
        raise ValueError("Fleet is empty. Add at least one vehicle.")
    if not stops:
        raise ValueError("No stops to route.")
    if depot.node_id is None:
        raise ValueError("Depot node_id is None — snap depot to the OSM graph before calling solve_vrp().")

    warnings: list[str] = []

    # ── Phase 1: assign each stop to a vehicle-type pool ─────────────────────
    mode_stop_pool, unreachable = _assign_stops_to_modes(stops, fleet, depot, graphs)

    if unreachable:
        names = "; ".join(s.address[:35] for s in unreachable)
        warnings.append(
            f"⚠️ {len(unreachable)} stop(s) unreachable by any fleet vehicle mode "
            f"(or fleet at capacity) — skipped: {names}"
        )
        logger.warning(
            "%d stop(s) could not be assigned to any vehicle: %s",
            len(unreachable), [s.address[:40] for s in unreachable],
        )

    # ── Phase 2: distribute within each mode and solve TSP ───────────────────
    # Group fleet vehicles by mode, preserving original order within each group
    mode_to_vehicles: dict[str, list] = {}
    for v in fleet:
        mode_to_vehicles.setdefault(v.mode, []).append(v)

    routes: list[VehicleRoute] = []

    for mode, vehicles in mode_to_vehicles.items():
        pool = mode_stop_pool.get(mode, [])

        if not pool:
            for v in vehicles:
                warnings.append(f"ℹ️ {v.name} has no stops assigned (idle).")
                logger.info("Vehicle %r (%s) is idle — no compatible stops.", v.name, mode)
            continue

        # Distribute stops within this mode's vehicles
        assignment = _distribute_within_mode(pool, vehicles, depot)

        for vehicle in vehicles:
            cluster = assignment[vehicle.name]
            if not cluster:
                warnings.append(f"ℹ️ {vehicle.name} has no stops assigned (idle).")
                logger.info("Vehicle %r is idle after intra-mode distribution.", vehicle.name)
                continue

            vr = _solve_per_vehicle(vehicle, cluster, depot, graphs, tsp_method)
            routes.append(vr)

    if not routes:
        raise RuntimeError(
            "No vehicle could be routed. Check that vehicle modes are compatible "
            "with the delivery area road network, and that fleet capacity is sufficient."
        )

    return routes, warnings
