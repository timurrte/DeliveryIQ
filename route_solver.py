"""
TSP solver for the DeliveryIQ route optimizer.

Notes:
- build_distance_matrix uses nx.shortest_path_length per pair, inserting
  PENALTY (1e9 s) for missing paths rather than a silent 0.0.
- audit_reachability inspects the matrix and reports unreachable stops by
  name instead of producing a silent 0-cost route.
- _christofides_tsp pre-checks that the helper graph is connected and has
  >= 3 nodes, falling back to 2-opt otherwise.
- "auto" prefers 2-opt over Christofides for small instances: Christofides
  needs a complete undirected graph, which fails when stops are unreachable;
  2-opt handles PENALTY weights gracefully.
"""

from __future__ import annotations

import logging
import math
import random
from dataclasses import dataclass, field

import networkx as nx
import requests
from networkx.algorithms.approximation import christofides

logger = logging.getLogger(__name__)

# Unreachable-pair sentinel: large enough to dominate any real path, but
# finite so TSP solvers can still form a valid (if bad) tour.
PENALTY: float = 1e9


# --- Reachability audit ---

@dataclass
class UnreachableStop:
    """One stop that could not be reached from / to some other stop."""
    node_id: int
    label: str                        # "Depot", "Stop #2", ...
    unreachable_from: list[str] = field(default_factory=list)
    unreachable_to:   list[str] = field(default_factory=list)

    @property
    def is_isolated(self) -> bool:
        """True if the node cannot reach OR be reached from ANY other node."""
        return bool(self.unreachable_from) or bool(self.unreachable_to)

    def summary(self) -> str:
        parts = []
        if self.unreachable_to:
            parts.append(f"cannot reach: {', '.join(self.unreachable_to)}")
        if self.unreachable_from:
            parts.append(f"unreachable from: {', '.join(self.unreachable_from)}")
        return f"[{self.label}] " + "; ".join(parts)


def audit_reachability(
    matrix: dict[tuple[int, int], float],
    nodes: list[int],
    labels: dict[int, str],
) -> list[UnreachableStop]:
    """
    Scan the matrix for PENALTY-weight entries and return one UnreachableStop
    per affected node. Empty if every pair is reachable.

    nodes is the ordered list [depot_node, stop1_node, ...]; labels maps each
    node_id to a human-readable name.
    """
    report: dict[int, UnreachableStop] = {}

    for src in nodes:
        for dst in nodes:
            if src == dst:
                continue
            cost = matrix.get((src, dst), PENALTY)
            if cost >= PENALTY:
                # src cannot reach dst
                if src not in report:
                    report[src] = UnreachableStop(
                        node_id=src, label=labels.get(src, str(src)))
                report[src].unreachable_to.append(labels.get(dst, str(dst)))

                # dst is unreachable from src — record on dst side too
                if dst not in report:
                    report[dst] = UnreachableStop(
                        node_id=dst, label=labels.get(dst, str(dst)))
                report[dst].unreachable_from.append(labels.get(src, str(src)))

    # Deduplicate the lists
    for us in report.values():
        us.unreachable_to   = sorted(set(us.unreachable_to))
        us.unreachable_from = sorted(set(us.unreachable_from))

    problems = list(report.values())
    if problems:
        logger.warning(
            "Reachability audit found %d problematic node(s):\n  %s",
            len(problems),
            "\n  ".join(p.summary() for p in problems),
        )
    return problems


# --- Distance matrix ---

def build_distance_matrix(
    G: nx.MultiDiGraph,
    nodes: list[int],
    weight: str = "travel_time",
) -> dict[tuple[int, int], float]:
    """
    All-pairs shortest-path travel times (seconds) via Dijkstra.

    nodes is deduplicated first (dict.fromkeys preserves order, so the depot
    stays at index 0). This is critical: if two addresses snap to the same
    OSM node, the src==dst diagonal writes 0.0 and the TSP total collapses to
    0.0 s. Deduplication ensures every off-diagonal pair gets a real path.

    Returns {(src, dst): travel_time_s}. Diagonal is 0.0; unreachable pairs
    carry PENALTY (1e9 s).

    Raises RuntimeError if a node is absent from G (snapping happened before
    LSCC pruning — always snap after get_network()), or ValueError if fewer
    than 2 unique nodes remain.
    """
    # Deduplicate while preserving order

    n_raw = len(nodes)
    unique_nodes: list[int] = list(dict.fromkeys(nodes))
    n = len(unique_nodes)
    n_dupes = n_raw - n

    if n_dupes > 0:
        # Log every duplicate so the terminal output reveals which addresses
        # are collapsing to the same node.
        seen: set[int] = set()
        for pos, node_id in enumerate(nodes):
            if node_id in seen:
                logger.warning(
                    "  build_distance_matrix: duplicate node %d at position %d "
                    "removed.  Two or more addresses snapped to the same OSM "
                    "node — check the NODE SNAP REPORT printed above for the "
                    "specific lat/lon values.",
                    node_id, pos,
                )
                print(
                    f"  ⚠️  Duplicate node {node_id} at position {pos} removed "
                    f"from distance matrix input."
                )
            seen.add(node_id)

    if n < 2:
        raise ValueError(
            f"build_distance_matrix requires at least 2 unique nodes; "
            f"got {n_raw} input node(s) that deduplicate to {n} unique node(s).  "
            f"All delivery addresses have resolved to the same road node.  "
            f"Use more specific street addresses (include building numbers), "
            f"verify the city lock is correct, or increase the network radius."
        )

    logger.info(
        "Building distance matrix: %d input node(s) → %d unique (weight='%s')…",
        n_raw, n, weight,
    )
    print(
        f"  Building {n}×{n} distance matrix"
        + (f" ({n_dupes} duplicate(s) removed)" if n_dupes else "")
        + f"  weight='{weight}'"
    )

    # Validate every node exists in the graph up front, so the error fires
    # immediately rather than mid-way through the O(n^2) Dijkstra loop.
    for node_id in unique_nodes:
        if node_id not in G:
            raise RuntimeError(
                f"Node {node_id} is not present in the travel-time graph.  "
                f"This means the address was snapped to an OSM node BEFORE "
                f"the graph was pruned to its Largest Strongly Connected "
                f"Component.  Always call nearest_node() / nearest_node_safe() "
                f"using the graph returned by get_network(), not the raw download."
            )

    # All-pairs Dijkstra
    matrix: dict[tuple[int, int], float] = {}

    for src in unique_nodes:
        for dst in unique_nodes:
            if src == dst:
                matrix[(src, dst)] = 0.0
                continue

            try:
                cost = nx.shortest_path_length(G, src, dst, weight=weight)

                # 0.0 cost between two different nodes means travel_time was
                # not stamped on the intermediate edges; treat as PENALTY.
                if cost == 0.0:
                    logger.warning(
                        "  Dijkstra: %s → %s returned 0.0 s — "
                        "'%s' attribute missing on one or more edges.  "
                        "Inserting PENALTY.",
                        src, dst, weight,
                    )
                    print(
                        f"  ⚠️  Dijkstra returned 0.0 s for {src}→{dst}.  "
                        f"The '{weight}' edge attribute may be missing.  "
                        f"PENALTY inserted."
                    )
                    cost = PENALTY

                matrix[(src, dst)] = float(cost)
                logger.debug("    %s → %s : %.2f s", src, dst, cost)

            except nx.NetworkXNoPath:
                # No directed path; should not happen inside the LSCC.
                logger.warning(
                    "  No directed path %s → %s — inserting PENALTY.", src, dst
                )
                matrix[(src, dst)] = PENALTY

            except nx.NodeNotFound as exc:
                # Unreachable after the validation loop above; re-raise with context.
                raise RuntimeError(
                    f"Node lookup failed during Dijkstra ({src} → {dst}): {exc}"
                ) from exc

    # Summary
    reachable = sum(1 for (s, d), v in matrix.items() if s != d and v < PENALTY)
    penalised = sum(1 for (s, d), v in matrix.items() if s != d and v >= PENALTY)
    diagonal  = n

    logger.info(
        "  Matrix complete — %d×%d — reachable=%d  penalty=%d  diagonal=%d",
        n, n, reachable, penalised, diagonal,
    )
    print(
        f"  Matrix complete: {n}×{n}  |  "
        f"reachable={reachable}  penalty={penalised}  diagonal={diagonal}"
    )

    if penalised > 0:
        logger.warning(
            "  %d off-diagonal pair(s) are unreachable (PENALTY weight).  "
            "The reachability audit will identify the specific stop names.",
            penalised,
        )

    return matrix


def build_drive_matrix_hybrid(
    G_drive: nx.MultiDiGraph,
    depot_node: int,
    car_stops: list[tuple[int, float]],
    *,
    weight: str = "travel_time",
) -> tuple[dict[tuple[int, int], float], list[int]]:
    """
    Drive-mode distance matrix for hybrid last-meter routing.

    Each car-reachable stop is represented by its nearest car-accessible node
    (N_car). Cost to reach stop j = drive time to N_car_j + walk_time_j. Keyed
    by (i, j) indices so stops sharing one N_car still get distinct columns.

    car_stops is a list of (n_car, walk_time_s). Returns (matrix, nodes_drive)
    where nodes_drive is [depot_node, n_car_1, ...].
    """
    nodes_drive: list[int] = [depot_node] + [n_car for (n_car, _) in car_stops]
    walk_times: list[float] = [0.0] + [wt for (_, wt) in car_stops]
    n = len(nodes_drive)

    for node_id in nodes_drive:
        if node_id not in G_drive:
            raise RuntimeError(
                f"Node {node_id} is not in the drive graph.  "
                "Snap addresses after get_network() and use car-accessible nodes."
            )

    matrix: dict[tuple[int, int], float] = {}
    for i in range(n):
        for j in range(n):
            if i == j:
                matrix[(i, j)] = 0.0
                continue
            src_id, dst_id = nodes_drive[i], nodes_drive[j]
            try:
                drive_s = nx.shortest_path_length(
                    G_drive, src_id, dst_id, weight=weight
                )
            except (nx.NetworkXNoPath, nx.NodeNotFound):
                drive_s = PENALTY
            if isinstance(drive_s, (int, float)) and drive_s == 0.0:
                drive_s = PENALTY
            total = float(drive_s) + walk_times[j]
            matrix[(i, j)] = min(total, PENALTY)
    logger.info(
        "  Drive hybrid matrix: %d×%d (depot + %d stops, last-meter walk added)",
        n, n, len(car_stops),
    )
    return matrix, nodes_drive


_MAPBOX_MATRIX_URL = (
    "https://api.mapbox.com/directions-matrix/v1/mapbox/driving-traffic/{coords}"
)
# Mapbox Matrix allows at most 25 coordinates per request. Chunks of 12 keep
# each request to at most 24 unique coordinates (12 sources + 12 dests).
_MAPBOX_CHUNK = 12


def build_drive_matrix_mapbox(
    stops: list[tuple[float, float]],
    api_key: str,
) -> dict[tuple[int, int], float]:
    """
    n x n drive-time matrix (seconds) via the Mapbox Directions Matrix API
    (driving-traffic profile), which uses historical traffic patterns for more
    realistic times than the static OSMnx Dijkstra approach.

    stops is [(lat, lon), ...] with the depot at index 0. Returns {(i, j): s};
    diagonal is 0.0, unreachable pairs carry PENALTY.

    Raises requests.HTTPError on a non-2xx response, ValueError if < 2 stops.
    """
    n = len(stops)
    if n < 2:
        raise ValueError("build_drive_matrix_mapbox requires at least 2 stops.")

    matrix: dict[tuple[int, int], float] = {(i, i): 0.0 for i in range(n)}

    def _request_chunk(
        src_indices: list[int],
        dst_indices: list[int],
    ) -> None:
        """Make one Mapbox Matrix API call for a subset of source/dest pairs."""
        # Build a deduplicated coordinate list for this request.
        # Mapbox sources/destinations are expressed as indices into this list.
        coord_pos: dict[int, int] = {}   # global stop index → position in coord_list
        coord_list: list[tuple[float, float]] = []

        for idx in dict.fromkeys(src_indices + dst_indices):  # stable-dedup order
            coord_pos[idx] = len(coord_list)
            coord_list.append(stops[idx])

        coords_str = ";".join(f"{lon},{lat}" for lat, lon in coord_list)
        url = _MAPBOX_MATRIX_URL.format(coords=coords_str)

        resp = requests.get(
            url,
            params={
                "access_token": api_key,
                "annotations":  "duration",
                "sources":      ";".join(str(coord_pos[i]) for i in src_indices),
                "destinations": ";".join(str(coord_pos[j]) for j in dst_indices),
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()

        durations = data.get("durations") or []
        for r, row in enumerate(durations):
            for c, val in enumerate(row):
                gi, gj = src_indices[r], dst_indices[c]
                if gi == gj:
                    matrix[(gi, gj)] = 0.0
                elif val is None:
                    matrix[(gi, gj)] = PENALTY
                else:
                    matrix[(gi, gj)] = float(val)

    # Iterate over (source_chunk x destination_chunk) pairs; each chunk is at
    # most _MAPBOX_CHUNK indices, keeping unique coords per request <= 24.
    all_indices = list(range(n))
    for src_start in range(0, n, _MAPBOX_CHUNK):
        src_chunk = all_indices[src_start : src_start + _MAPBOX_CHUNK]
        for dst_start in range(0, n, _MAPBOX_CHUNK):
            dst_chunk = all_indices[dst_start : dst_start + _MAPBOX_CHUNK]
            _request_chunk(src_chunk, dst_chunk)

    reachable = sum(1 for (i, j), v in matrix.items() if i != j and v < PENALTY)
    penalised = sum(1 for (i, j), v in matrix.items() if i != j and v >= PENALTY)
    logger.info(
        "  Mapbox drive matrix: %d×%d — reachable=%d  penalty=%d",
        n, n, reachable, penalised,
    )
    return matrix


# --- TSP solvers ---

def _route_cost(route: list[int], matrix: dict) -> float:
    """
    Total travel cost of a closed TSP tour [depot, s1, ..., sn, depot].

    The return-to-depot leg is already the last consecutive pair, so the cost
    is just the sum of matrix[route[i] -> route[i+1]] — no extra wrap-around
    term (that would double-count depot->depot).

    Any pathological matrix value (missing key, None, inf, nan) is treated as
    PENALTY, so solvers always work with finite floats. Never raises.
    """
    if len(route) < 2:
        # A 0- or 1-node route has no edges to traverse.
        return 0.0

    total = 0.0
    for i in range(len(route) - 1):
        src = route[i]
        dst = route[i + 1]

        raw = matrix.get((src, dst), None)

        # Normalise every pathological value to PENALTY
        if raw is None:
            # Key missing — pair never computed; should not happen.
            logger.debug(
                "_route_cost: matrix key (%s, %s) missing — using PENALTY.", src, dst
            )
            cost = PENALTY

        elif not isinstance(raw, (int, float)):
            # Non-numeric value in matrix (e.g. a string from bad serialisation)
            logger.warning(
                "_route_cost: non-numeric matrix value %r for (%s, %s) — using PENALTY.",
                raw, src, dst,
            )
            cost = PENALTY

        else:
            f = float(raw)
            if math.isnan(f) or math.isinf(f):
                logger.debug(
                    "_route_cost: matrix[(%s, %s)] = %s — replacing with PENALTY.",
                    src, dst, f,
                )
                cost = PENALTY
            else:
                cost = f  # normal finite value

        total += cost

        # Early exit: once total hits PENALTY the tour is non-viable.
        if total >= PENALTY:
            return PENALTY

    return total


def solve_tsp(
    nodes: list[int],
    matrix: dict[tuple[int, int], float],
    method: str = "auto",
    seed: int = 42,
) -> tuple[list[int], float]:
    """
    Find a near-optimal visit order for nodes (nodes[0] is the depot).

    Methods:
      "auto"          pick by stop count: nn (1-2), 2opt (3-20), genetic (21+)
      "nn"            nearest-neighbour greedy
      "2opt"          NN + 2-opt refinement
      "christofides"  NetworkX Christofides (needs a complete graph)
      "genetic"       order-crossover genetic algorithm

    auto uses 2opt rather than Christofides for small graphs because
    Christofides needs a complete connected graph and fails when any stop is
    unreachable; 2opt handles PENALTY weights gracefully.

    Returns (ordered_node_ids, total_travel_time_s); the order starts and ends
    at the depot.
    """
    n = len(nodes)
    if n == 0:
        raise ValueError("Cannot solve TSP: node list is empty.")
    if n == 1:
        return [nodes[0], nodes[0]], 0.0

    if method == "auto":
        if n <= 2:
            method = "nn"
        elif n <= 20:
            method = "2opt"
        else:
            method = "genetic"

    logger.info("Solving TSP for %d stop(s) with method='%s'…", n - 1, method)

    if method == "nn":
        route = _nearest_neighbour(nodes, matrix)
    elif method == "2opt":
        route = _two_opt(_nearest_neighbour(nodes, matrix), matrix)
    elif method == "christofides":
        route = _christofides_tsp(nodes, matrix)
    elif method == "genetic":
        route = _genetic_algorithm(nodes, matrix, seed=seed)
    else:
        raise ValueError(f"Unknown TSP method: {method!r}")

    total = _route_cost(route, matrix)
    logger.info("  Route cost: %.1f s (%.2f min)", total, total / 60.0)

    if total >= PENALTY:
        logger.error(
            "  Route cost is ≥ PENALTY — at least one stop is unreachable. "
            "Check the unreachability audit output above for the specific address."
        )

    return route, total


# --- Nearest-neighbour ---

def _nearest_neighbour(nodes: list[int], matrix: dict) -> list[int]:
    """Greedy nearest-neighbour from the depot; always returns a full tour."""
    depot = nodes[0]
    unvisited = set(nodes[1:])
    route = [depot]
    current = depot

    while unvisited:
        nearest = min(unvisited, key=lambda n: matrix.get((current, n), PENALTY))
        route.append(nearest)
        unvisited.remove(nearest)
        current = nearest

    route.append(depot)
    return route


# --- 2-opt ---

def _two_opt(
    route: list[int],
    matrix: dict,
    max_iter: int = 2_000,
) -> list[int]:
    """
    2-opt local search. Depot is pinned at index 0 / len-1 and never swapped.
    A swap is accepted only if it lowers total cost, so PENALTY edges are
    avoided whenever a finite alternative exists.
    """
    best = route[:]
    best_cost = _route_cost(best, matrix)
    improved = True
    iterations = 0

    while improved and iterations < max_iter:
        improved = False
        iterations += 1
        for i in range(1, len(best) - 2):
            for j in range(i + 1, len(best) - 1):
                candidate = best[:i] + best[i:j + 1][::-1] + best[j + 1:]
                candidate_cost = _route_cost(candidate, matrix)
                if candidate_cost < best_cost:
                    best = candidate
                    best_cost = candidate_cost
                    improved = True

    logger.debug("  2-opt: %d iteration(s), final cost=%.1f s", iterations, best_cost)
    return best


# --- Christofides ---

def _christofides_tsp(nodes: list[int], matrix: dict) -> list[int]:
    """
    NetworkX Christofides approximation, with fallbacks to 2-opt when the
    helper graph H (PENALTY edges excluded) has isolated nodes, fewer than 3
    nodes, or is disconnected. Falls back on any exception, so a result is
    always returned.
    """
    try:
        # Build an undirected helper graph with only finite edges.
        H = nx.Graph()
        H.add_nodes_from(nodes)
        for (u, v), w in matrix.items():
            if u != v and w < PENALTY:
                # Keep the cheaper of the two directions.
                existing = H.get_edge_data(u, v)
                if existing is None or w < existing["weight"]:
                    H.add_edge(u, v, weight=w)

        # Christofides needs at least 3 nodes.
        if H.number_of_nodes() < 3:
            logger.info(
                "  Christofides: graph has < 3 nodes — falling back to 2-opt."
            )
            return _two_opt(_nearest_neighbour(nodes, matrix), matrix)

        # Every node must have at least one finite-weight neighbour.
        isolated = [n for n in nodes if H.degree(n) == 0]
        if isolated:
            logger.warning(
                "  Christofides: %d isolated node(s) found (no finite-cost "
                "path to any other stop): %s.  Falling back to 2-opt.",
                len(isolated), isolated,
            )
            return _two_opt(_nearest_neighbour(nodes, matrix), matrix)

        # The helper graph must be connected.
        if not nx.is_connected(H):
            components = nx.number_connected_components(H)
            logger.warning(
                "  Christofides: helper graph has %d disconnected component(s) "
                "— this means some stops cannot be linked with finite-cost edges. "
                "Falling back to 2-opt.",
                components,
            )
            return _two_opt(_nearest_neighbour(nodes, matrix), matrix)

        # All checks passed — run Christofides.
        cycle = christofides(H, weight="weight")

        # Rotate so the depot is first.
        depot = nodes[0]
        if depot in cycle:
            idx = cycle.index(depot)
            cycle = cycle[idx:] + cycle[1:idx + 1]
        else:
            # Cycle missing the depot; should not happen.
            cycle = cycle + [cycle[0]]

        logger.debug("  Christofides succeeded — %d-node cycle.", len(cycle))
        return cycle

    except Exception as exc:
        logger.warning(
            "  Christofides raised an unexpected exception (%s: %s). "
            "Falling back to 2-opt.",
            type(exc).__name__, exc,
        )
        return _two_opt(_nearest_neighbour(nodes, matrix), matrix)


# --- Genetic algorithm ---

def _genetic_algorithm(
    nodes: list[int],
    matrix: dict,
    population_size: int = 120,
    generations: int = 400,
    mutation_rate: float = 0.02,
    seed: int = 42,
) -> list[int]:
    """
    Order-crossover (OX) genetic algorithm. The depot sits at position 0 / -1
    and is excluded from the chromosome. PENALTY weights are handled by the
    fitness function, which penalises any unreachable leg.
    """
    random.seed(seed)
    depot = nodes[0]
    stops = nodes[1:]
    n = len(stops)

    if n == 0:
        return [depot, depot]
    if n == 1:
        return [depot, stops[0], depot]

    def fitness(chrom: list[int]) -> float:
        route = [depot] + chrom + [depot]
        cost = _route_cost(route, matrix)
        # Reciprocal fitness — lower cost = higher fitness
        # Add small epsilon so PENALTY routes don't divide by zero
        return 1.0 / (cost + 1.0)

    def ox_crossover(p1: list[int], p2: list[int]) -> list[int]:
        """Order crossover: preserve a slice of p1, fill rest from p2."""
        a, b = sorted(random.sample(range(n), 2))
        child: list[int | None] = [None] * n
        child[a:b] = p1[a:b]
        fill = [g for g in p2 if g not in child]
        fill_idx = 0
        for i in range(n):
            if child[i] is None:
                child[i] = fill[fill_idx]
                fill_idx += 1
        return child  # type: ignore[return-value]

    def mutate(chrom: list[int]) -> list[int]:
        """Swap mutation: exchange two random positions."""
        if random.random() < mutation_rate and n >= 2:
            i, j = random.sample(range(n), 2)
            chrom[i], chrom[j] = chrom[j], chrom[i]
        return chrom

    # Initialise with random permutations
    population = [random.sample(stops, n) for _ in range(population_size)]

    for _ in range(generations):
        population.sort(key=fitness, reverse=True)
        elites = population[:max(2, population_size // 10)]
        children: list[list[int]] = elites[:]
        while len(children) < population_size:
            p1, p2 = random.choices(elites, k=2)
            children.append(mutate(ox_crossover(p1, p2)))
        population = children

    best_chrom = max(population, key=fitness)
    return [depot] + best_chrom + [depot]


# --- Path reconstruction ---

def get_full_path(
    G: nx.MultiDiGraph,
    src: int,
    dst: int,
    weight: str = "travel_time",
) -> list[int]:
    """
    OSM node ids for the shortest path src -> dst.

    Returns [src] if src == dst, or [src, dst] with a warning if no path
    exists (the polyline degrades to a straight line on the map).
    """
    if src == dst:
        return [src]
    try:
        return nx.shortest_path(G, src, dst, weight=weight)
    except nx.NetworkXNoPath:
        logger.warning(
            "get_full_path: no path from %s to %s — route segment will be "
            "a straight line.  This stop may need to be removed.",
            src, dst,
        )
        return [src, dst]
    except nx.NodeNotFound as exc:
        logger.error("get_full_path: node not found — %s", exc)
        return [src, dst]


def reconstruct_full_route(
    G: nx.MultiDiGraph,
    tsp_route: list[int],
    weight: str = "travel_time",
) -> list[int]:
    """
    Expand a TSP node sequence into the full OSM node sequence, including all
    intermediate road nodes between each pair of stops.
    """
    full: list[int] = []
    for i in range(len(tsp_route) - 1):
        leg = get_full_path(G, tsp_route[i], tsp_route[i + 1], weight)
        if full:
            leg = leg[1:]   # drop the shared boundary node
        full.extend(leg)
    return full
