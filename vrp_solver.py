"""
vrp_solver.py — CVRPTW solver for DeliveryIQ (Genetic Algorithm)
==============================================================================
Solves the Capacitated Vehicle Routing Problem with Time Windows (CVRPTW)
via a genetic algorithm, as described in the thesis
"Розробка та програмна реалізація алгоритму оптимізації розподілу вантажів
у транспортній мережі".

Algorithm overview
------------------
Phase 1 — Mode assignment (per stop):
  For every stop, compute which vehicle modes can reach it via the road
  network (Dijkstra with travel_time weight, PENALTY sentinel for
  impassable edges).  Assign each stop to the vehicle-type pool that has
  the most remaining capacity among compatible modes.

Phase 2 — Genetic algorithm (per mode):
  Within each mode's stop pool, a steady-state GA searches for a good
  assignment + ordering:
    * Chromosome: a permutation of customer indices (giant tour, no
      route-boundary markers).
    * Decoder (Split): walks the permutation and greedily partitions
      it into capacity- and time-window-feasible sub-sequences,
      dispatching each to the next available vehicle from the fleet.
    * Fitness (minimised):   F = A·T_total + B·K + P·|U|
      where K is the number of active vehicles and U is the set of
      unrouted customers (P >> A, B discourages infeasibility).
    * Operators: tournament selection, Order Crossover (OX), swap
      mutation, elitism.
    * Initialisation: one greedy nearest-neighbour chromosome plus
      random permutations — this seeds the population with a
      reasonable warm start while preserving diversity.

Key design decisions
--------------------
* Round-trip routing: every vehicle starts AND ends at the depot.
* PENALTY sentinel (1e9): same value as route_solver.py.
* Permutation encoding is universally feasible (decode enforces
  all constraints), so operators never produce malformed offspring.
* Elitism preserves the top E chromosomes each generation,
  guaranteeing the best-so-far fitness is non-increasing.
* Deterministic by default (GA_SEED = 42) so repeated runs reproduce
  the same routes — important for debugging and for consistent UI.

References
----------
Holland, J. H. (1975). Adaptation in Natural and Artificial Systems.
Prins, C. (2004). A simple and effective evolutionary algorithm for the
  vehicle routing problem. Computers & Operations Research, 31(12),
  1985–2002.
Oliver, I. M., Smith, D. J., Holland, J. R. C. (1987). A study of
  permutation crossover operators on the TSP. Proc. ICGA '87.
"""

from __future__ import annotations

import logging
import math
import random
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

# ── Genetic-algorithm default parameters (§2.7 of thesis) ───────────────────
POP_SIZE: int = 50
N_GENERATIONS: int = 150
CROSSOVER_RATE: float = 0.85
MUTATION_RATE: float = 0.15
ELITE_SIZE: int = 2
TOURNAMENT_SIZE: int = 3
UNROUTED_PENALTY: float = 1.0e6   # fitness penalty per unrouted customer
GA_SEED: int | None = 42          # None => non-deterministic runs

# Objective-function weights: F = A·T_total + B·|V_act|
OBJ_A: float = 1.0
OBJ_B: float = 0.0


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
#  GENETIC ALGORITHM — DECODER (Split)
# ══════════════════════════════════════════════════════════════════════════════

def _decode_chromosome(
    chromosome: list[int],
    stops: list,
    vehicles: list,
    depot,
    matrix: dict,
    depot_node: int,
    stop_nodes: dict,
) -> tuple[list[tuple[Vehicle, list[int]]], list[int]]:
    """
    Split decoder — converts a permutation into a list of feasible vehicle
    routes.

    Walks through the chromosome in order, adding each customer to the
    current vehicle's route as long as capacity and time-window
    constraints remain satisfied.  When an insertion would violate a
    constraint, the current route is closed and the customer is tried on
    the next vehicle in the fleet.

    Returns
    -------
    routes     : list of (Vehicle, [customer_indices]) for routes with ≥1 stop
    unassigned : list of customer indices that fit no vehicle
    """
    def t(i: int, j: int) -> float:
        ni = depot_node if i == -1 else stop_nodes[i]
        nj = depot_node if j == -1 else stop_nodes[j]
        return matrix.get((ni, nj), PENALTY)

    def tw_open(i: int) -> float:
        return depot.tw_open if i == -1 else stops[i].tw_open

    def tw_close(i: int) -> float:
        return depot.tw_close if i == -1 else stops[i].tw_close

    def service(i: int) -> float:
        return depot.service_time if i == -1 else stops[i].service_time

    def weight(i: int) -> float:
        return stops[i].weight_kg

    def route_feasible(route: list[int], vehicle: Vehicle) -> bool:
        """Capacity + time-window feasibility for a closed route (depot→…→depot)."""
        if not route:
            return True
        if sum(weight(i) for i in route) > vehicle.capacity_kg:
            return False
        prev = -1
        prev_begin = tw_open(-1)
        for cust in route:
            tt = t(prev, cust)
            if tt >= PENALTY:
                return False
            arr = prev_begin + service(prev) + tt
            if arr > tw_close(cust):
                return False
            prev_begin = max(arr, tw_open(cust))
            prev = cust
        tt_back = t(prev, -1)
        if tt_back >= PENALTY:
            return False
        if prev_begin + service(prev) + tt_back > tw_close(-1):
            return False
        return True

    routes: list[tuple[Vehicle, list[int]]] = []
    unassigned: list[int] = []
    veh_idx = 0
    current_route: list[int] = []

    for cust in chromosome:
        placed = False
        while veh_idx < len(vehicles) and not placed:
            vehicle = vehicles[veh_idx]
            candidate = current_route + [cust]
            if route_feasible(candidate, vehicle):
                current_route = candidate
                placed = True
            else:
                if current_route:
                    routes.append((vehicle, current_route))
                    current_route = []
                veh_idx += 1
        if not placed:
            unassigned.append(cust)

    if current_route and veh_idx < len(vehicles):
        routes.append((vehicles[veh_idx], current_route))

    return routes, unassigned


def _compute_route_time(
    route: list[int],
    matrix: dict,
    depot_node: int,
    stop_nodes: dict,
) -> float:
    """Total travel time along a closed route: depot → customers → depot."""
    if not route:
        return 0.0
    total = matrix.get((depot_node, stop_nodes[route[0]]), PENALTY)
    for k in range(len(route) - 1):
        total += matrix.get(
            (stop_nodes[route[k]], stop_nodes[route[k + 1]]),
            PENALTY,
        )
    total += matrix.get((stop_nodes[route[-1]], depot_node), PENALTY)
    return total


def _evaluate(
    chromosome: list[int],
    stops: list,
    vehicles: list,
    depot,
    matrix: dict,
    depot_node: int,
    stop_nodes: dict,
) -> tuple[float, list[tuple[Vehicle, list[int]]], list[int]]:
    """
    Compute fitness F = A·T_total + B·K + P·|U|  (lower is better).

    Returns (fitness, routes, unassigned).
    """
    routes, unassigned = _decode_chromosome(
        chromosome, stops, vehicles, depot, matrix, depot_node, stop_nodes,
    )
    T_total = sum(
        _compute_route_time(r, matrix, depot_node, stop_nodes)
        for _, r in routes
    )
    K = len(routes)
    fitness = OBJ_A * T_total + OBJ_B * K + UNROUTED_PENALTY * len(unassigned)
    return fitness, routes, unassigned


# ══════════════════════════════════════════════════════════════════════════════
#  GENETIC OPERATORS
# ══════════════════════════════════════════════════════════════════════════════

def _order_crossover(
    p1: list[int],
    p2: list[int],
    rng: random.Random,
) -> list[int]:
    """
    Order Crossover (OX).  Copies a contiguous slice of p1 into the child,
    then fills the remaining positions in the order they appear in p2
    (starting after the slice, wrapping around).
    """
    n = len(p1)
    if n < 2:
        return p1[:]
    i, j = sorted(rng.sample(range(n), 2))
    child: list[int] = [-1] * n
    used = [False] * n
    for k in range(i, j + 1):
        child[k] = p1[k]
        used[p1[k]] = True
    fill_pos = (j + 1) % n
    scan_pos = (j + 1) % n
    for _ in range(n):
        gene = p2[scan_pos]
        scan_pos = (scan_pos + 1) % n
        if not used[gene]:
            child[fill_pos] = gene
            fill_pos = (fill_pos + 1) % n
    return child


def _swap_mutation(chromo: list[int], rng: random.Random) -> list[int]:
    """Swap mutation: exchange the values at two random positions."""
    n = len(chromo)
    if n < 2:
        return chromo
    i, j = rng.sample(range(n), 2)
    chromo[i], chromo[j] = chromo[j], chromo[i]
    return chromo


def _tournament_select(
    population: list[list[int]],
    fitnesses: list[float],
    k: int,
    rng: random.Random,
) -> list[int]:
    """Tournament selection: draw k at random, return the fittest."""
    size = min(k, len(population))
    candidates = rng.sample(range(len(population)), size)
    best = min(candidates, key=lambda idx: fitnesses[idx])
    return population[best]


def _nearest_neighbour_seed(
    n_stops: int,
    matrix: dict,
    depot_node: int,
    stop_nodes: dict,
) -> list[int]:
    """Greedy nearest-neighbour permutation — used as one high-quality seed."""
    if n_stops == 0:
        return []
    unvisited = set(range(n_stops))
    current = depot_node
    chromo: list[int] = []
    while unvisited:
        nxt = min(
            unvisited,
            key=lambda c: matrix.get((current, stop_nodes[c]), PENALTY),
        )
        chromo.append(nxt)
        current = stop_nodes[nxt]
        unvisited.discard(nxt)
    return chromo


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN GA LOOP
# ══════════════════════════════════════════════════════════════════════════════

def _genetic_algorithm(
    stops: list,
    vehicles: list,
    depot,
    matrix: dict,
    depot_node: int,
    stop_nodes: dict,
    *,
    pop_size: int = POP_SIZE,
    n_generations: int = N_GENERATIONS,
    crossover_rate: float = CROSSOVER_RATE,
    mutation_rate: float = MUTATION_RATE,
    elite_size: int = ELITE_SIZE,
    tournament_size: int = TOURNAMENT_SIZE,
    seed: int | None = GA_SEED,
) -> list[tuple[Vehicle, list[int]]]:
    """
    Genetic algorithm for CVRPTW.

    Parameters
    ----------
    stops       : list[DeliveryStop] — unrouted customers
    vehicles    : list[Vehicle]      — available fleet for this mode
    depot       : DeliveryStop       — depot (tw_open/close, service_time)
    matrix      : dict[(node_i, node_j) -> float] travel-time matrix
    depot_node  : OSM node id of the depot
    stop_nodes  : dict[customer_index -> OSM node id]
    pop_size, n_generations, crossover_rate, mutation_rate,
    elite_size, tournament_size : GA control parameters.
    seed        : RNG seed for reproducibility (None = non-deterministic).

    Returns
    -------
    list of (Vehicle, [customer_indices_in_visit_order])
    """
    n = len(stops)
    if n == 0:
        return []

    rng = random.Random(seed)

    # ── Initial population: 1 NN seed + random permutations ──────────────
    population: list[list[int]] = []
    nn_chromo = _nearest_neighbour_seed(n, matrix, depot_node, stop_nodes)
    if nn_chromo:
        population.append(nn_chromo)
    while len(population) < pop_size:
        perm = list(range(n))
        rng.shuffle(perm)
        population.append(perm)

    evaluations = [
        _evaluate(c, stops, vehicles, depot, matrix, depot_node, stop_nodes)
        for c in population
    ]
    fitnesses = [e[0] for e in evaluations]

    best_idx = min(range(pop_size), key=lambda i: fitnesses[i])
    best_fitness = fitnesses[best_idx]
    best_eval = evaluations[best_idx]

    # ── Evolutionary loop ───────────────────────────────────────────────
    for _ in range(n_generations):
        order = sorted(range(pop_size), key=lambda i: fitnesses[i])
        new_population: list[list[int]] = [
            population[i][:] for i in order[:elite_size]
        ]

        while len(new_population) < pop_size:
            p1 = _tournament_select(population, fitnesses, tournament_size, rng)
            p2 = _tournament_select(population, fitnesses, tournament_size, rng)

            child = _order_crossover(p1, p2, rng) if rng.random() < crossover_rate else p1[:]
            if rng.random() < mutation_rate:
                child = _swap_mutation(child, rng)

            new_population.append(child)

        population = new_population
        evaluations = [
            _evaluate(c, stops, vehicles, depot, matrix, depot_node, stop_nodes)
            for c in population
        ]
        fitnesses = [e[0] for e in evaluations]

        gen_best = min(range(pop_size), key=lambda i: fitnesses[i])
        if fitnesses[gen_best] < best_fitness:
            best_fitness = fitnesses[gen_best]
            best_eval = evaluations[gen_best]

    _, best_routes, unassigned = best_eval
    if unassigned:
        logger.warning(
            "GA: %d customer(s) could not be routed (fleet exhausted or "
            "infeasible): indices %s",
            len(unassigned), sorted(unassigned),
        )
    logger.info(
        "GA: best fitness %.1f after %d generation(s), %d active route(s)",
        best_fitness, n_generations, len(best_routes),
    )
    return best_routes


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
            depot_node = nearest_car_accessible_node(G, depot.lat, depot.lon)
            if depot_node is None:
                depot_node = depot.node_id
        else:
            check_node = stop.node_id
            depot_node = depot.node_id
        try:
            cost = nx.shortest_path_length(G, depot_node, check_node, weight="travel_time")
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


def _build_ga_routes(
    pool_stops: list,
    vehicles: list,
    depot,
    graphs: dict,
) -> list[VehicleRoute]:
    """
    Phase 2 — build routes for one mode's stop pool using the GA.

    Steps:
    1. Snap depot + stops to OSM nodes (car-accessible for drive mode).
    2. Build the travel-time matrix over depot + stop nodes.
    3. Run the genetic algorithm to partition stops across vehicles.
    4. For each vehicle route, reconstruct the full OSM path and compute
       the total road distance.
    """
    mode = vehicles[0].mode
    G = graphs[mode]

    if mode == "drive":
        node_ids = [
            nearest_car_accessible_node(G, s.lat, s.lon) or s.node_id
            for s in pool_stops
        ]
        depot_node = nearest_car_accessible_node(G, depot.lat, depot.lon) or depot.node_id
    else:
        node_ids = [s.node_id for s in pool_stops]
        depot_node = depot.node_id

    all_nodes = [depot_node] + node_ids
    unique_nodes = list(dict.fromkeys(all_nodes))
    matrix = build_distance_matrix(G, unique_nodes)

    stop_node_map: dict[int, int] = {i: node_ids[i] for i in range(len(pool_stops))}

    ga_routes = _genetic_algorithm(
        pool_stops, vehicles, depot, matrix, depot_node, stop_node_map,
    )

    results: list[VehicleRoute] = []
    for vehicle, stop_indices in ga_routes:
        if not stop_indices:
            continue

        tsp_route = [depot_node] + [stop_node_map[i] for i in stop_indices] + [depot_node]
        full_route = reconstruct_full_route(G, tsp_route)

        total_time_s = 0.0
        for k in range(len(tsp_route) - 1):
            total_time_s += matrix.get((tsp_route[k], tsp_route[k + 1]), PENALTY)

        total_dist_m = 0.0
        for k in range(len(full_route) - 1):
            ed = G.get_edge_data(full_route[k], full_route[k + 1])
            if ed:
                total_dist_m += min(
                    (v.get("length", 0.0) for v in ed.values()),
                    default=0.0,
                )

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
    Objective function value:
        F = A · Σ T_total + B · K
    where T_total is the total travel time across all routes and K is the
    number of active vehicles.
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
    Solve a Capacitated VRP with Time Windows using a genetic algorithm.

    Parameters
    ----------
    stops      : list[DeliveryStop]        — all delivery stops (node_id set)
    depot      : DeliveryStop              — start/end point (node_id set)
    fleet      : list[Vehicle]             — vehicles with mode and capacity
    graphs     : dict[str, MultiDiGraph]   — {"drive": G, "bike": G, "walk": G}
    tsp_method : str                       — kept for API compatibility (unused by GA)

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

    # ── Phase 1: assign each stop to a vehicle-type pool ────────────────
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

    # ── Phase 2: GA per mode ────────────────────────────────────────────
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

        mode_routes = _build_ga_routes(pool, vehicles, depot, graphs)
        routes.extend(mode_routes)

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
        "GA solution: %d active vehicle(s), objective F = %.1f",
        len(routes), obj_val,
    )

    return routes, warnings
