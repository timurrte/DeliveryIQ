"""
route_solver.py
---------------
Solves the Travelling Salesman Problem (TSP) for delivery stops using a
two-phase approach:

  Phase 1 – Pairwise shortest paths (Dijkstra)
      Build a complete distance matrix between every pair of delivery nodes
      using networkx's Dijkstra implementation weighted by 'travel_time'.

  Phase 2 – TSP heuristic (Nearest-Neighbour + 2-opt refinement)
      Find a good (not necessarily optimal) visit order.
      For small instances (≤ 10 stops) we also try a Christofides-style
      greedy approach via networkx's approximation module.

Returns the ordered list of OSM node ids and the total travel time (seconds).
"""

import itertools
import logging
import math
import random

import networkx as nx
from networkx.algorithms.approximation import christofides

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
#  PHASE 1 – PAIRWISE SHORTEST PATHS
# ─────────────────────────────────────────────────────────────────────────────

def build_distance_matrix(
    G: nx.MultiDiGraph,
    nodes: list[int],
    weight: str = "travel_time",
) -> dict[tuple[int, int], float]:
    """
    Compute all-pairs shortest-path travel times (seconds) between *nodes*
    using Dijkstra's algorithm on *G*.

    Parameters
    ----------
    G : nx.MultiDiGraph
    nodes : list[int]
        OSM node ids for depot + all delivery stops (depot is nodes[0]).
    weight : str
        Edge attribute to use as cost (default ``"travel_time"``).

    Returns
    -------
    dict[(src, dst) -> float]
        Travel time in seconds for every ordered pair.
    """
    logger.info("Building %dx%d distance matrix …", len(nodes), len(nodes))
    matrix: dict[tuple[int, int], float] = {}

    for src in nodes:
        try:
            lengths = nx.single_source_dijkstra_path_length(G, src, weight=weight)
        except nx.NetworkXError as exc:
            raise RuntimeError(f"Dijkstra failed from node {src}: {exc}") from exc

        for dst in nodes:
            if src == dst:
                matrix[(src, dst)] = 0.0
            else:
                matrix[(src, dst)] = lengths.get(dst, math.inf)

    _warn_disconnected(matrix, nodes)
    return matrix


def _warn_disconnected(
    matrix: dict[tuple[int, int], float],
    nodes: list[int],
) -> None:
    """Log a warning for any node pair that is unreachable."""
    bad = [(s, d) for (s, d), v in matrix.items() if v == math.inf and s != d]
    if bad:
        logger.warning(
            "%d node pairs are unreachable in the graph "
            "(consider increasing the download radius). Pairs: %s",
            len(bad),
            bad[:5],
        )


# ─────────────────────────────────────────────────────────────────────────────
#  PHASE 2 – TSP SOLVERS
# ─────────────────────────────────────────────────────────────────────────────

def solve_tsp(
    nodes: list[int],
    matrix: dict[tuple[int, int], float],
    method: str = "auto",
    seed: int = 42,
) -> tuple[list[int], float]:
    """
    Find a near-optimal visit order for *nodes* (nodes[0] is the depot).

    Parameters
    ----------
    nodes : list[int]
        Full list: [depot, stop1, stop2, …].
    matrix : dict
        All-pairs travel-time matrix.
    method : str
        ``"nn"``         – Nearest-Neighbour heuristic (fast, any size).
        ``"2opt"``       – Nearest-Neighbour + 2-opt refinement.
        ``"christofides"`` – NetworkX Christofides approximation (small graphs).
        ``"genetic"``    – Simple Genetic Algorithm (medium graphs).
        ``"auto"``       – Choose automatically based on number of stops.
    seed : int
        Random seed for stochastic methods.

    Returns
    -------
    (ordered_nodes, total_time_seconds)
        ``ordered_nodes`` starts and ends at the depot.
    """
    n = len(nodes)
    if method == "auto":
        if n <= 2:
            method = "nn"
        elif n <= 12:
            method = "christofides"
        elif n <= 40:
            method = "2opt"
        else:
            method = "genetic"

    logger.info("Solving TSP for %d stops with method='%s' …", n - 1, method)

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
    logger.info("  Best route cost: %.1f s (%.2f min)", total, total / 60)
    return route, total


# ── Nearest-Neighbour ─────────────────────────────────────────────────────────

def _nearest_neighbour(nodes: list[int], matrix: dict) -> list[int]:
    depot = nodes[0]
    unvisited = set(nodes[1:])
    route = [depot]
    current = depot

    while unvisited:
        nearest = min(unvisited, key=lambda n: matrix[(current, n)])
        route.append(nearest)
        unvisited.remove(nearest)
        current = nearest

    route.append(depot)   # return to depot
    return route


# ── 2-opt refinement ──────────────────────────────────────────────────────────

def _two_opt(route: list[int], matrix: dict, max_iter: int = 1_000) -> list[int]:
    """Standard 2-opt swap; keeps depot fixed at start/end."""
    best = route[:]
    improved = True
    iterations = 0

    while improved and iterations < max_iter:
        improved = False
        iterations += 1
        # route[0] and route[-1] are depot – don't swap them
        for i in range(1, len(best) - 2):
            for j in range(i + 1, len(best) - 1):
                new_route = best[:i] + best[i:j + 1][::-1] + best[j + 1:]
                if _route_cost(new_route, matrix) < _route_cost(best, matrix):
                    best = new_route
                    improved = True
    return best


# ── Christofides (via NetworkX) ───────────────────────────────────────────────

def _christofides_tsp(nodes: list[int], matrix: dict) -> list[int]:
    """
    Build a complete weighted graph and call NetworkX's Christofides
    approximation.  Falls back to 2-opt if the library call fails.
    """
    try:
        H = nx.Graph()
        for (u, v), w in matrix.items():
            if u != v and w < math.inf:
                H.add_edge(u, v, weight=w)

        # christofides returns a cycle as a list of nodes
        cycle = christofides(H, weight="weight")
        # Rotate so depot is first
        depot = nodes[0]
        if depot in cycle:
            idx = cycle.index(depot)
            cycle = cycle[idx:] + cycle[1:idx + 1]
        else:
            cycle = cycle + [cycle[0]]
        return cycle
    except Exception as exc:
        logger.warning("Christofides failed (%s); falling back to 2-opt.", exc)
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
    A simple order-based Genetic Algorithm for the TSP.
    Chromosome = permutation of delivery nodes (depot excluded from permutation,
    always prepended/appended).
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
        return 1.0 / (cost + 1e-9)

    def crossover(p1: list[int], p2: list[int]) -> list[int]:
        """Order crossover (OX)."""
        a, b = sorted(random.sample(range(n), 2))
        child = [None] * n
        child[a:b] = p1[a:b]
        fill = [g for g in p2 if g not in child]
        idx = 0
        for i in range(n):
            if child[i] is None:
                child[i] = fill[idx]
                idx += 1
        return child

    def mutate(chrom: list[int]) -> list[int]:
        """Swap mutation."""
        if random.random() < mutation_rate and n >= 2:
            i, j = random.sample(range(n), 2)
            chrom[i], chrom[j] = chrom[j], chrom[i]
        return chrom

    # Initialise population
    population = [random.sample(stops, n) for _ in range(population_size)]

    for gen in range(generations):
        population.sort(key=fitness, reverse=True)
        elites = population[:max(2, population_size // 10)]
        children = elites[:]
        while len(children) < population_size:
            p1, p2 = random.choices(elites, k=2)
            child = mutate(crossover(p1, p2))
            children.append(child)
        population = children

    best_chrom = max(population, key=fitness)
    return [depot] + best_chrom + [depot]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _route_cost(route: list[int], matrix: dict) -> float:
    return sum(matrix.get((route[i], route[i + 1]), math.inf) for i in range(len(route) - 1))


# ─────────────────────────────────────────────────────────────────────────────
#  PHASE 1b – RECONSTRUCT FULL OSM PATHS
# ─────────────────────────────────────────────────────────────────────────────

def get_full_path(
    G: nx.MultiDiGraph,
    src: int,
    dst: int,
    weight: str = "travel_time",
) -> list[int]:
    """
    Return the list of OSM node ids for the shortest path from *src* to *dst*.
    Returns [src] if src == dst.
    """
    if src == dst:
        return [src]
    try:
        return nx.shortest_path(G, src, dst, weight=weight)
    except nx.NetworkXNoPath:
        logger.warning("No path found between %d and %d.", src, dst)
        return [src, dst]


def reconstruct_full_route(
    G: nx.MultiDiGraph,
    tsp_route: list[int],
    weight: str = "travel_time",
) -> list[int]:
    """
    Expand a TSP node sequence into a full sequence of OSM nodes
    (including all intermediate nodes on each leg).
    """
    full: list[int] = []
    for i in range(len(tsp_route) - 1):
        leg = get_full_path(G, tsp_route[i], tsp_route[i + 1], weight)
        if full:
            leg = leg[1:]   # avoid duplicating the shared node
        full.extend(leg)
    return full
