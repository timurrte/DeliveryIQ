"""
vrp_solver.py — CVRPTW solver for DeliveryIQ (Solomon I1 Insertion Heuristic)
==============================================================================
Implements the Solomon Insertion Heuristic I1 as described in the thesis
"Розробка та програмна реалізація алгоритму оптимізації розподілу вантажів
у транспортній мережі".

Algorithm overview
------------------
Phase 1 — Mode assignment (per stop):
  For every stop, compute which vehicle modes can reach it via the road
  network (Dijkstra with travel_time weight, PENALTY sentinel for
  impassable edges).  Assign each stop to the vehicle-type pool that has
  the most remaining capacity among compatible modes.

Phase 2 — Solomon I1 sequential insertion (per mode):
  Within each mode's stop pool, build routes one at a time using Solomon's
  I1 insertion heuristic:
    1. Seed: pick the unrouted customer farthest from depot.
    2. Iteratively insert the customer with the best c2 score into its
       best feasible position (c1 criterion), checking capacity (Д2) and
       time window (Д3) constraints at every insertion.
    3. When no more customers fit the current route, close it and start
       a new route with a new vehicle (if available).

Key design decisions
--------------------
* Round-trip routing: every vehicle starts AND ends at the depot.
* PENALTY sentinel (1e9): same value as route_solver.py.
* The c1 criterion is a weighted combination of extra travel time (c11)
  and push-forward delay (c12): c1 = α1·c11 + α2·c12.
* The c2 criterion balances depot-distance incentive against insertion
  cost: c2 = λ·t(0,u) − c1*(u, R).
* Time windows and service times are propagated via push-forward (PF)
  checks along the entire route suffix after each candidate insertion.

References
----------
Solomon, M. M. (1987). Algorithms for the vehicle routing and scheduling
problems with time window constraints. Operations Research, 35(2), 254–265.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field

import networkx as nx

from route_solver import (
    build_distance_matrix,
    reconstruct_full_route,
    PENALTY,
)
from graph_builder import nearest_car_accessible_node

logger = logging.getLogger(__name__)

# 10-colour qualitative palette (ColorBrewer Set1 + extensions).
VEHICLE_COLORS: list[str] = [
    "#e41a1c", "#377eb8", "#4daf4a", "#984ea3",
    "#ff7f00", "#a65628", "#f781bf", "#999999",
    "#66c2a5", "#fc8d62",
]

# ── Solomon I1 default parameters (§2.7 of thesis) ──────────────────────────
ALPHA_1: float = 1.0   # weight of extra travel time in c1
ALPHA_2: float = 0.0   # weight of push-forward in c1
MU_1: float = 1.0      # coefficient for direct-path subtraction in c11
LAMBDA_C2: float = 1.0  # weight of depot-distance in c2

# Objective function weights: F = A·T_total + B·|V_act|
OBJ_A: float = 1.0     # weight of total travel time
OBJ_B: float = 0.0     # weight of number of active vehicles


# ══════════════════════════════════════════════════════════════════════════════
#  DATA CLASSES
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class Vehicle:
    name: str
    mode: str           # "drive" | "bike" | "walk"
    capacity_kg: float  # maximum payload weight Q_θ (kg)
    color: str = ""


@dataclass
class VehicleRoute:
    vehicle: Vehicle
    stops: list         # ordered list of DeliveryStop in visit order (depot excluded)
    tsp_route: list     # closed OSM node tour: [depot_node, n1, …, depot_node]
    full_route: list    # expanded OSM node sequence for map polyline rendering
    total_time_s: float
    total_dist_m: float
    legs: list = field(default_factory=list)
    skipped_stops: list = field(default_factory=list)


# ══════════════════════════════════════════════════════════════════════════════
#  SOLOMON I1 INSERTION HEURISTIC
# ══════════════════════════════════════════════════════════════════════════════

def _solomon_i1(
    stops: list,
    vehicles: list,
    depot,
    matrix: dict,
    depot_node: int,
    stop_nodes: dict,
    *,
    alpha_1: float = ALPHA_1,
    alpha_2: float = ALPHA_2,
    mu_1: float = MU_1,
    lambda_c2: float = LAMBDA_C2,
) -> list[tuple[Vehicle, list]]:
    """
    Solomon I1 sequential insertion heuristic for CVRPTW.

    Parameters
    ----------
    stops       : list of DeliveryStop objects (unrouted customers)
    vehicles    : list of Vehicle objects available for this mode
    depot       : DeliveryStop for the depot (with tw_open, tw_close, service_time)
    matrix      : dict[(node_i, node_j) -> float] travel time matrix
    depot_node  : int, OSM node id of the depot
    stop_nodes  : dict[stop_index -> node_id] mapping stop list index to OSM node
    alpha_1, alpha_2, mu_1, lambda_c2 : Solomon I1 parameters

    Returns
    -------
    list of (Vehicle, [stop_indices_in_visit_order])
    """
    n_stops = len(stops)
    if n_stops == 0:
        return []

    # Unrouted customer indices
    unrouted: set[int] = set(range(n_stops))

    # Helper: travel time between two customer indices (or depot=-1)
    def t(i: int, j: int) -> float:
        ni = depot_node if i == -1 else stop_nodes[i]
        nj = depot_node if j == -1 else stop_nodes[j]
        return matrix.get((ni, nj), PENALTY)

    # Helper: get time window and service time for a customer index (or depot=-1)
    def tw_open(i: int) -> float:
        return depot.tw_open if i == -1 else stops[i].tw_open

    def tw_close(i: int) -> float:
        return depot.tw_close if i == -1 else stops[i].tw_close

    def service(i: int) -> float:
        return depot.service_time if i == -1 else stops[i].service_time

    def weight(i: int) -> float:
        return stops[i].weight_kg

    routes: list[tuple[Vehicle, list[int]]] = []
    veh_idx = 0

    while unrouted and veh_idx < len(vehicles):
        vehicle = vehicles[veh_idx]
        veh_idx += 1

        # ── Seed selection: farthest feasible unrouted customer from depot ──
        # Sort by descending travel time from depot, pick first feasible
        seed = None
        for candidate in sorted(unrouted, key=lambda u: t(-1, u), reverse=True):
            t_dep_c = t(-1, candidate)
            t_c_dep = t(candidate, -1)
            if t_dep_c >= PENALTY or t_c_dep >= PENALTY:
                continue
            if weight(candidate) > vehicle.capacity_kg:
                continue
            # Time window check for seed: arrival = depot_open + service(depot) + t(depot, c)
            arr_c = tw_open(-1) + service(-1) + t_dep_c
            b_c = max(arr_c, tw_open(candidate))
            if arr_c > tw_close(candidate):
                continue
            # Check return to depot
            arr_depot_back = b_c + service(candidate) + t_c_dep
            if arr_depot_back > tw_close(-1):
                continue
            seed = candidate
            break

        if seed is None:
            # No feasible seed for this vehicle
            continue

        # Route representation: list of customer indices (depot implicit at start/end)
        route: list[int] = [seed]
        unrouted.discard(seed)
        load = weight(seed)

        # Arrival times for the route: τ[pos] = arrival time at route[pos]
        # Route is: depot -> route[0] -> route[1] -> ... -> route[n-1] -> depot
        # We track arrival and begin-service times for each position
        def compute_schedule(r: list[int]) -> tuple[list[float], list[float], float]:
            """Compute (arrivals, begin_service, return_to_depot_arrival) for route r."""
            arrivals = []
            begins = []
            # Depart depot at tw_open
            prev = -1
            prev_begin = tw_open(-1)
            for idx, cust in enumerate(r):
                arr = prev_begin + service(prev if idx > 0 else -1) + t(prev, cust)
                b = max(arr, tw_open(cust))
                arrivals.append(arr)
                begins.append(b)
                prev = cust
                prev_begin = b
            # Return to depot
            arr_depot = prev_begin + service(prev) + t(prev, -1)
            return arrivals, begins, arr_depot

        # ── Sequential insertion loop ────────────────────────────────────
        improved = True
        while improved:
            improved = False
            best_u_star = None
            best_c2_val = -math.inf
            best_u_pos = -1

            for u in list(unrouted):
                # Д2: capacity check
                if load + weight(u) > vehicle.capacity_kg:
                    continue

                # Find best position for u in current route
                best_c1_for_u = math.inf
                best_pos_for_u = -1

                n_r = len(route)
                for p in range(n_r + 1):
                    # Insert u between position p-1 and p in route
                    # i_p = route[p-1] if p > 0 else depot (-1)
                    # i_p1 = route[p] if p < n_r else depot (-1)
                    i_p = route[p - 1] if p > 0 else -1
                    i_p1 = route[p] if p < n_r else -1

                    # Д1: route feasibility in graph
                    t_ip_u = t(i_p, u)
                    t_u_ip1 = t(u, i_p1)
                    if t_ip_u >= PENALTY or t_u_ip1 >= PENALTY:
                        continue

                    # Build candidate route
                    candidate = route[:p] + [u] + route[p:]

                    # Check Д3: time windows for u and all subsequent customers
                    arrivals, begins, arr_depot = compute_schedule(candidate)

                    feasible = True
                    for idx, cust in enumerate(candidate):
                        if arrivals[idx] > tw_close(cust):
                            feasible = False
                            break
                    if feasible and arr_depot > tw_close(-1):
                        feasible = False

                    if not feasible:
                        continue

                    # Compute c11: extra travel time
                    t_ip_ip1 = t(i_p, i_p1)
                    c11 = t_ip_u + t_u_ip1 - mu_1 * t_ip_ip1

                    # Compute c12: push-forward
                    # PF_u = new arrival at i_{p+1} - old arrival at i_{p+1}
                    if p < n_r:
                        # Old arrival at position p (which is i_{p+1} before insertion)
                        old_arrivals, _, _ = compute_schedule(route)
                        old_arr_ip1 = old_arrivals[p] if p < len(old_arrivals) else tw_open(-1)
                        new_arr_ip1 = arrivals[p + 1] if (p + 1) < len(arrivals) else arr_depot
                        c12 = max(0.0, new_arr_ip1 - old_arr_ip1)
                    else:
                        # Inserting at the end, PF affects only depot return
                        old_arrivals, old_begins, old_arr_depot = compute_schedule(route)
                        c12 = max(0.0, arr_depot - old_arr_depot)

                    c1_val = alpha_1 * c11 + alpha_2 * c12

                    if c1_val < best_c1_for_u:
                        best_c1_for_u = c1_val
                        best_pos_for_u = p

                # If no feasible position found for u, skip
                if best_pos_for_u == -1:
                    continue

                # c2 criterion: λ·t(0,u) - c1*(u, R)
                c2_val = lambda_c2 * t(-1, u) - best_c1_for_u

                if c2_val > best_c2_val:
                    best_c2_val = c2_val
                    best_u_star = u
                    best_u_pos = best_pos_for_u

            # Insert the best customer
            if best_u_star is not None:
                route.insert(best_u_pos, best_u_star)
                unrouted.discard(best_u_star)
                load += weight(best_u_star)
                improved = True

        routes.append((vehicle, route))

    # Any remaining unrouted customers — log warning
    if unrouted:
        logger.warning(
            "Solomon I1: %d customer(s) could not be inserted into any route "
            "(fleet exhausted or infeasible): indices %s",
            len(unrouted), sorted(unrouted),
        )

    return routes


# ══════════════════════════════════════════════════════════════════════════════
#  INTERNAL HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Compass bearing in degrees (0-360) from point 1 to point 2."""
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
    (travel_time weight) is strictly below PENALTY.
    """
    if stop.node_id is None or stop.node_id == depot.node_id:
        return set()

    reachable = set()
    for mode, G in graphs.items():
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
    have the most remaining total capacity is chosen.

    Returns
    -------
    mode_stop_pool : dict[mode -> list[stop]]
    unreachable    : list[stop]
    """
    mode_remaining_kg: dict[str, float] = {}
    for v in fleet:
        mode_remaining_kg[v.mode] = mode_remaining_kg.get(v.mode, 0.0) + v.capacity_kg

    fleet_modes = set(v.mode for v in fleet)
    mode_stop_pool: dict[str, list] = {m: [] for m in fleet_modes}
    unreachable: list = []

    for stop in stops:
        stop_w = getattr(stop, "weight_kg", 1.0)
        compatible = _check_reachable(stop, depot, graphs)
        available = [m for m in compatible if mode_remaining_kg.get(m, 0.0) >= stop_w]

        if not available:
            unreachable.append(stop)
            continue

        chosen = max(available, key=lambda m: mode_remaining_kg[m])
        mode_stop_pool[chosen].append(stop)
        mode_remaining_kg[chosen] -= stop_w

    return mode_stop_pool, unreachable


def _build_solomon_routes(
    pool_stops: list,
    vehicles: list,
    depot,
    graphs: dict,
) -> list[VehicleRoute]:
    """
    Phase 2 — build routes for one mode's stop pool using Solomon I1.

    Steps:
    1. Build the distance matrix for depot + all stops in the pool.
    2. Run Solomon I1 to partition stops across vehicles.
    3. For each vehicle route, reconstruct the full OSM path and compute
       total distance.
    """
    mode = vehicles[0].mode
    G = graphs[mode]

    # Map stops to OSM nodes
    if mode == "drive":
        node_ids = [
            nearest_car_accessible_node(G, s.lat, s.lon) or s.node_id
            for s in pool_stops
        ]
    else:
        node_ids = [s.node_id for s in pool_stops]

    depot_node = depot.node_id

    # Build distance matrix for depot + all stops
    all_nodes = [depot_node] + node_ids
    unique_nodes = list(dict.fromkeys(all_nodes))
    matrix = build_distance_matrix(G, unique_nodes)

    # stop_index -> node_id mapping
    stop_node_map: dict[int, int] = {i: node_ids[i] for i in range(len(pool_stops))}

    # Run Solomon I1
    solomon_routes = _solomon_i1(
        pool_stops, vehicles, depot, matrix, depot_node, stop_node_map,
    )

    results: list[VehicleRoute] = []
    for vehicle, stop_indices in solomon_routes:
        if not stop_indices:
            continue

        # Build TSP-style node route: [depot, n1, n2, ..., depot]
        tsp_route = [depot_node] + [stop_node_map[i] for i in stop_indices] + [depot_node]

        # Reconstruct full OSM path
        full_route = reconstruct_full_route(G, tsp_route)

        # Total travel time from matrix
        total_time_s = 0.0
        for k in range(len(tsp_route) - 1):
            cost = matrix.get((tsp_route[k], tsp_route[k + 1]), PENALTY)
            total_time_s += cost

        # Total road distance
        total_dist_m = 0.0
        for k in range(len(full_route) - 1):
            ed = G.get_edge_data(full_route[k], full_route[k + 1])
            if ed:
                total_dist_m += min(
                    (v.get("length", 0.0) for v in ed.values()),
                    default=0.0,
                )

        # Recover ordered stops
        ordered_stops = [pool_stops[i] for i in stop_indices]

        results.append(VehicleRoute(
            vehicle=vehicle,
            stops=ordered_stops,
            tsp_route=tsp_route,
            full_route=full_route,
            total_time_s=total_time_s,
            total_dist_m=total_dist_m,
        ))

    return results


# ══════════════════════════════════════════════════════════════════════════════
#  OBJECTIVE FUNCTION (§1.4 of thesis)
# ══════════════════════════════════════════════════════════════════════════════

def compute_objective(routes: list[VehicleRoute], A: float = OBJ_A, B: float = OBJ_B) -> float:
    """
    Compute the objective function value:
        F = A · Σ T_total + B · K
    where T_total is the sum of travel times across all routes, and K is
    the number of active vehicles.
    """
    T_total = sum(vr.total_time_s for vr in routes)
    K = len(routes)
    return A * T_total + B * K


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
    Solve a Capacitated VRP with Time Windows using Solomon I1 heuristic.

    Parameters
    ----------
    stops      : list[DeliveryStop]        — all delivery stops (node_id set)
    depot      : DeliveryStop              — start/end point (node_id set)
    fleet      : list[Vehicle]             — vehicles with mode and capacity
    graphs     : dict[str, MultiDiGraph]   — {"drive": G, "bike": G, "walk": G}
    tsp_method : str                       — kept for API compatibility (unused by Solomon)

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

    # ── Phase 2: Solomon I1 insertion per mode ───────────────────────────────
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

        mode_routes = _build_solomon_routes(pool, vehicles, depot, graphs)
        routes.extend(mode_routes)

        # Check for vehicles that ended up idle
        active_names = {vr.vehicle.name for vr in mode_routes}
        for v in vehicles:
            if v.name not in active_names:
                warnings.append(f"ℹ️ {v.name} has no stops assigned (idle).")

    if not routes:
        raise RuntimeError(
            "No vehicle could be routed. Check that vehicle modes are compatible "
            "with the delivery area road network, and that fleet capacity is sufficient."
        )

    obj_val = compute_objective(routes)
    logger.info(
        "Solomon I1 solution: %d active vehicle(s), objective F = %.1f",
        len(routes), obj_val,
    )

    return routes, warnings
