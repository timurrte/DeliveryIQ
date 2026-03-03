"""
route_solver.py
---------------
TSP solver for the DeliveryIQ route optimizer.

Key fixes vs v2
───────────────
1. Robust distance matrix (the 0.0 s bug)
   The old code used `single_source_dijkstra_path_length` which returns
   a *generator-backed dict* — accessing a missing key silently returned
   `math.inf`, but the real problem was that the raw graph contained
   weakly-connected nodes whose 'travel_time' was never set (= 0).
   The new `build_distance_matrix` uses `nx.shortest_path_length` with
   an explicit per-pair try/except so a genuinely missing path always
   inserts PENALTY (1e9 s) rather than 0.0.

2. Unreachable-node reporting
   `audit_reachability` inspects the finished matrix and returns a
   structured list of {node_id, label, unreachable_from, unreachable_to}
   objects.  The Streamlit UI renders these as named warnings
   ("Stop #2 – Shevchenka St is unreachable") instead of a silent
   0-cost route.

3. Safe Christofides
   The old fallback swallowed the exception and silently ran 2-opt on
   whatever broken graph was passed in.  The new version:
     a) Pre-checks that the TSP helper graph H is non-null and connected
        before calling christofides().
     b) If H has isolated nodes (from PENALTY edges that were excluded),
        it logs them by name and falls back to 2-opt explicitly.
     c) Never calls christofides on a graph with < 3 nodes.

4. Auto-method selection now prefers 2-opt over Christofides by default
   because Christofides requires a *complete* undirected graph — which
   we cannot guarantee when some stops are truly unreachable.  2-opt
   works fine with PENALTY weights.
"""

from __future__ import annotations

import logging
import math
import random
from dataclasses import dataclass, field

import networkx as nx
from networkx.algorithms.approximation import christofides

logger = logging.getLogger(__name__)

# Sentinel for unreachable pairs — large enough to dominate any real path
# but finite so TSP solvers can still form a valid (if bad) tour.
PENALTY: float = 1e9


# ══════════════════════════════════════════════════════════════════════════════
#  REACHABILITY AUDIT
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class UnreachableStop:
    """Describes one stop that could not be reached from / to some other stop."""
    node_id: int
    label: str                        # "Depot", "Stop #2", …
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
    Scan the distance matrix for PENALTY-weight entries and return one
    UnreachableStop per affected node.

    Parameters
    ----------
    matrix : distance matrix from build_distance_matrix()
    nodes  : ordered list [depot_node, stop1_node, …]
    labels : {node_id: "Depot" | "Stop #N"} mapping for human-readable output

    Returns
    -------
    list[UnreachableStop]  — empty if every pair is reachable
    """
    # Build a per-node problem report
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


# ══════════════════════════════════════════════════════════════════════════════
#  DISTANCE MATRIX  (Phase 1)
# ══════════════════════════════════════════════════════════════════════════════

def build_distance_matrix(
    G: nx.MultiDiGraph,
    nodes: list[int],
    weight: str = "travel_time",
) -> dict[tuple[int, int], float]:
    """
    Compute all-pairs shortest-path travel times (seconds) using Dijkstra.

    Design decisions
    ────────────────
    • We call `nx.shortest_path_length(G, src, dst, weight=weight)` for
      every (src, dst) pair individually rather than using the
      single-source variant.  This is slightly slower for large graphs
      but makes the per-pair exception handling trivial and guarantees
      that a missing path is always caught — even if the node exists in
      the graph but lies in a disconnected component that slipped through
      the LSCC filter (e.g. when the user manually overrides the radius).

    • On `nx.NetworkXNoPath` we insert PENALTY (1e9 s ≈ 277 hours) so
      the TSP solver still receives a finite, complete matrix.  PENALTY
      is large enough that no optimal tour will choose to traverse the
      missing leg, but finite so the solver never crashes.

    • On `nx.NodeNotFound` we raise a clear RuntimeError immediately —
      this means a node_id that was snapped to the graph before SCC
      pruning no longer exists in the pruned graph, which indicates a
      bug in the snapping order (nodes should be snapped *after* pruning).

    Parameters
    ----------
    G      : weighted MultiDiGraph (output of add_travel_times)
    nodes  : [depot_node_id, stop1_node_id, …]
    weight : edge attribute to minimise (default "travel_time")

    Returns
    -------
    dict[(src, dst) → float]
    """
    n = len(nodes)
    logger.info("Building %d×%d distance matrix (weight='%s')…", n, n, weight)
    matrix: dict[tuple[int, int], float] = {}

    for src in nodes:
        # Validate the source node exists before the inner loop
        if src not in G:
            raise RuntimeError(
                f"Node {src} is not present in the optimisation graph.  "
                f"This usually means the node was snapped before the graph was "
                f"pruned to its largest strongly connected component.  "
                f"Re-snap all nodes after calling get_network()."
            )

        for dst in nodes:
            if src == dst:
                matrix[(src, dst)] = 0.0
                continue

            if dst not in G:
                raise RuntimeError(
                    f"Destination node {dst} is not in the optimisation graph. "
                    f"Same cause as above — re-snap after get_network()."
                )

            try:
                cost = nx.shortest_path_length(G, src, dst, weight=weight)
                # Guard against a 0-cost path that still somehow slipped through
                # (e.g. an edge whose travel_time was not stamped correctly).
                if cost == 0.0 and src != dst:
                    logger.warning(
                        "  Dijkstra returned 0.0 s between nodes %s and %s — "
                        "the '%s' attribute may be missing on some edges. "
                        "Inserting penalty weight.",
                        src, dst, weight,
                    )
                    cost = PENALTY
                matrix[(src, dst)] = float(cost)

            except nx.NetworkXNoPath:
                # The two nodes are in the same graph but no directed path
                # connects them.  This should not happen inside the LSCC but
                # can occur if the user bumps the radius and fetches a
                # partially-connected 'all' graph.
                logger.warning(
                    "  No directed path from %s to %s — inserting penalty weight.",
                    src, dst,
                )
                matrix[(src, dst)] = PENALTY

            except nx.NodeNotFound as exc:
                raise RuntimeError(f"Node lookup failed: {exc}") from exc

    reachable_pairs  = sum(1 for v in matrix.values() if 0 < v < PENALTY)
    penalty_pairs    = sum(1 for v in matrix.values() if v >= PENALTY)
    diagonal_pairs   = n  # src == dst zeros

    logger.info(
        "  Matrix complete — %d reachable pairs, %d penalty pairs, %d diagonal.",
        reachable_pairs, penalty_pairs, diagonal_pairs,
    )
    return matrix


# ══════════════════════════════════════════════════════════════════════════════
#  TSP SOLVERS  (Phase 2)
# ══════════════════════════════════════════════════════════════════════════════

# ── Core cost helper  ─────────────────────────────────────────────────────────
# _route_cost MUST be defined before every function that calls it:
#   solve_tsp (line ~314), _two_opt (line ~363), _genetic_algorithm (line ~491).
# Python resolves names at call-time for module-level functions, so placement
# anywhere before the first *runtime* call is technically sufficient — but we
# define it here, at the top of the TSP section, so the read-order matches the
# execution order and the NameError can never occur.

def _route_cost(route: list[int], matrix: dict) -> float:
    """
    Calculate the total travel cost of a complete TSP tour.

    Route structure assumption
    ──────────────────────────
    Every solver in this module returns a *closed* route — a list that
    starts AND ends at the depot:

        [depot, stop_1, stop_2, …, stop_n, depot]

    The return-to-depot leg is therefore already represented as the last
    consecutive pair  (stop_n → depot),  so we must NOT add an extra
    matrix[route[-1]][route[0]] term on top — that would double-count
    the depot→depot leg (cost 0) in the best case and add a spurious
    depot→depot traversal cost in the worst case.

    The correct formula is simply the sum of consecutive pairs:

        Cost = Σ  matrix[ route[i] → route[i+1] ]   for i = 0 … len-2

    which naturally covers:
        depot → stop_1  (first leg)
        stop_1 → stop_2
        …
        stop_n → depot  (return leg, already in the list as the last pair)

    None / inf / missing-key handling
    ───────────────────────────────────
    Any of the following pathological values in a matrix entry is treated
    as PENALTY so the TSP solvers always deal with finite floats:

        • Key absent from matrix dict   → matrix.get() returns None default
        • Explicit None value           → direct None check
        • math.inf                      → isinf() check
        • math.nan                      → isnan() check
        • value ≥ PENALTY               → already the sentinel, kept as-is

    This matches the Dnipro use-case where some street pairs are
    genuinely unreachable (river crossings, one-way-only access roads)
    and end up in the matrix as PENALTY after build_distance_matrix().

    Parameters
    ----------
    route  : list of OSM node ids, first == last == depot
    matrix : dict[(src_node, dst_node) → travel_time_seconds]
             produced by build_distance_matrix()

    Returns
    -------
    float  — total travel time in seconds, or a PENALTY multiple if any
             leg is unreachable.  Never raises; never returns NaN or inf.
    """
    if len(route) < 2:
        # A degenerate route with 0 or 1 node has no edges to traverse.
        return 0.0

    total = 0.0
    for i in range(len(route) - 1):
        src = route[i]
        dst = route[i + 1]

        raw = matrix.get((src, dst), None)

        # Normalise every pathological value to PENALTY
        if raw is None:
            # Key missing — node pair was never computed (should not happen
            # after build_distance_matrix, but guard defensively)
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

        # Early-exit optimisation: once the running total already exceeds
        # PENALTY there is no point accumulating further — this tour is
        # definitively non-viable and the TSP solver will reject it.
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
    Find a near-optimal visit order for *nodes* (nodes[0] is the depot).

    Method selection
    ────────────────
    "auto"          Choose based on stop count (see table below).
    "nn"            Nearest-Neighbour greedy (O(n²), any size).
    "2opt"          NN + 2-opt edge-swap refinement (O(n³) per pass).
    "christofides"  NetworkX Christofides (½-approx, needs complete graph).
    "genetic"       Order-crossover Genetic Algorithm (large instances).

    Auto-selection table
    ┌──────────────┬──────────────┐
    │  Stop count  │  Method      │
    ├──────────────┼──────────────┤
    │  1–2         │  nn          │
    │  3–20        │  2opt        │  ← changed: was christofides
    │  21–60       │  genetic     │
    │  61+         │  genetic     │
    └──────────────┴──────────────┘

    Why 2opt instead of Christofides for small graphs?
    Christofides requires a *complete*, *connected*, *undirected* helper
    graph.  When even one pair has a PENALTY weight (unreachable stop)
    the helper graph becomes disconnected and christofides() raises
    "Connectivity is undefined for the null graph".  2-opt handles
    PENALTY weights gracefully — it will simply never choose to traverse
    a PENALTY edge if any finite alternative exists.

    Returns
    -------
    (ordered_node_ids, total_travel_time_seconds)
        ordered_node_ids starts and ends at nodes[0] (the depot).
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


# ── Nearest-Neighbour ─────────────────────────────────────────────────────────

def _nearest_neighbour(nodes: list[int], matrix: dict) -> list[int]:
    """
    Classic greedy nearest-neighbour starting at the depot.
    Always produces a complete tour even when some edges are PENALTY-weight.
    """
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


# ── 2-opt ─────────────────────────────────────────────────────────────────────

def _two_opt(
    route: list[int],
    matrix: dict,
    max_iter: int = 2_000,
) -> list[int]:
    """
    Standard 2-opt local-search improvement.
    Depot is pinned at index 0 and len-1 and is never swapped.
    Handles PENALTY weights correctly — a swap is only accepted if it
    genuinely reduces total cost, so PENALTY edges are avoided whenever
    a finite-cost alternative exists.
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


# ── Christofides ──────────────────────────────────────────────────────────────

def _christofides_tsp(nodes: list[int], matrix: dict) -> list[int]:
    """
    Attempt NetworkX Christofides approximation.

    Safety checks before calling christofides():
    1. Exclude all PENALTY-weight edges from the helper graph H.
    2. If H has isolated nodes (nodes with no finite-cost neighbour),
       log them by name and fall back to 2-opt immediately — calling
       christofides on a disconnected graph raises
       "Connectivity is undefined for the null graph".
    3. If H has fewer than 3 nodes, fall back to 2-opt (trivial case).

    Falls back to 2-opt on ANY exception so the user always gets a result.
    """
    try:
        # Build undirected complete helper graph with only finite edges
        H = nx.Graph()
        H.add_nodes_from(nodes)
        for (u, v), w in matrix.items():
            if u != v and w < PENALTY:
                # Use the minimum of the two directions for the undirected edge
                existing = H.get_edge_data(u, v)
                if existing is None or w < existing["weight"]:
                    H.add_edge(u, v, weight=w)

        # Check 1: need at least 3 nodes for Christofides
        if H.number_of_nodes() < 3:
            logger.info(
                "  Christofides: graph has < 3 nodes — falling back to 2-opt."
            )
            return _two_opt(_nearest_neighbour(nodes, matrix), matrix)

        # Check 2: every node must have at least one finite-weight neighbour
        isolated = [n for n in nodes if H.degree(n) == 0]
        if isolated:
            logger.warning(
                "  Christofides: %d isolated node(s) found (no finite-cost "
                "path to any other stop): %s.  Falling back to 2-opt.",
                len(isolated), isolated,
            )
            return _two_opt(_nearest_neighbour(nodes, matrix), matrix)

        # Check 3: the helper graph must be connected
        if not nx.is_connected(H):
            components = nx.number_connected_components(H)
            logger.warning(
                "  Christofides: helper graph has %d disconnected component(s) "
                "— this means some stops cannot be linked with finite-cost edges. "
                "Falling back to 2-opt.",
                components,
            )
            return _two_opt(_nearest_neighbour(nodes, matrix), matrix)

        # All checks passed — run Christofides
        cycle = christofides(H, weight="weight")

        # Rotate so depot is first
        depot = nodes[0]
        if depot in cycle:
            idx = cycle.index(depot)
            cycle = cycle[idx:] + cycle[1:idx + 1]
        else:
            # Christofides returned a cycle that doesn't include the depot —
            # this should not happen but handle it defensively
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


# ── Genetic Algorithm ─────────────────────────────────────────────────────────

def _genetic_algorithm(
    nodes: list[int],
    matrix: dict,
    population_size: int = 120,
    generations: int = 400,
    mutation_rate: float = 0.02,
    seed: int = 42,
) -> list[int]:
    """
    Order-crossover (OX) Genetic Algorithm.
    Depot is always at position 0 / −1 and excluded from the chromosome.
    Works with PENALTY weights — the fitness function naturally penalises
    routes that include an unreachable leg.
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


# ══════════════════════════════════════════════════════════════════════════════
#  PATH RECONSTRUCTION  (Phase 1b)
# ══════════════════════════════════════════════════════════════════════════════

def get_full_path(
    G: nx.MultiDiGraph,
    src: int,
    dst: int,
    weight: str = "travel_time",
) -> list[int]:
    """
    Return the list of OSM node ids for the shortest path from *src* to *dst*.

    Returns ``[src]`` if src == dst.
    Returns ``[src, dst]`` with a warning if no path exists (graceful
    degradation — the polyline will be a straight line on the map).
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
    Expand a TSP node sequence into a full sequence of OSM nodes,
    including all intermediate road nodes between each pair of stops.
    """
    full: list[int] = []
    for i in range(len(tsp_route) - 1):
        leg = get_full_path(G, tsp_route[i], tsp_route[i + 1], weight)
        if full:
            leg = leg[1:]   # drop the shared boundary node
        full.extend(leg)
    return full
