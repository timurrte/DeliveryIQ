"""
tests/test_vrp_solver.py
Unit + integration tests for vrp_solver.py (Genetic-Algorithm CVRPTW solver).

Run:
    pytest tests/test_vrp_solver.py -v
Coverage:
    pytest tests/test_vrp_solver.py --cov=vrp_solver --cov-report=term-missing
"""
from __future__ import annotations

import math
import random
import pytest
import networkx as nx
from dataclasses import dataclass
from typing import Optional
from unittest.mock import patch

from vrp_solver import (
    Vehicle,
    VehicleRoute,
    VEHICLE_COLORS,
    PENALTY,
    _bearing,
    _check_reachable,
    _assign_stops_to_modes,
    _decode_chromosome,
    _compute_route_time,
    _evaluate,
    _order_crossover,
    _swap_mutation,
    _tournament_select,
    _nearest_neighbour_seed,
    _genetic_algorithm,
    _build_ga_routes,
    compute_objective,
    solve_vrp,
)


# ══════════════════════════════════════════════════════════════════════════════
#  Test fixtures & helpers
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class Stop:
    """Minimal duck-type for DeliveryStop used by vrp_solver."""
    address: str
    lat: float
    lon: float
    node_id: Optional[int] = None
    weight_kg: float = 1.0
    tw_open: float = 0.0
    tw_close: float = 86400.0
    service_time: float = 0.0


def _make_graph(nodes, edges):
    """
    Build a MultiDiGraph with the attributes expected by vrp_solver:
      nodes: list of (node_id, lat, lon)
      edges: list of (u, v, travel_time, length)
    """
    G = nx.MultiDiGraph(crs="EPSG:4326")
    for nid, lat, lon in nodes:
        G.add_node(nid, y=lat, x=lon)
    for u, v, tt, length in edges:
        G.add_edge(u, v, travel_time=tt, length=length)
    return G


def _ring_graph():
    """
    Fully connected 5-node ring. All travel_times well below PENALTY.
    Nodes: 0 (depot), 1, 2, 3, 4
    """
    nodes = [(i, 48.0 + i * 0.01, 35.0 + i * 0.01) for i in range(5)]
    edges = []
    for i in range(5):
        j = (i + 1) % 5
        edges.append((i, j, 60.0, 500.0))
        edges.append((j, i, 60.0, 500.0))
    return _make_graph(nodes, edges)


def _penalty_graph():
    """
    Graph where node 4 is reachable from 0 only via a PENALTY edge.
    Nodes 0,1,2 are fully connected with normal costs.
    """
    nodes = [(0, 48.0, 35.0), (1, 48.1, 35.0), (2, 48.1, 35.1), (4, 47.9, 34.9)]
    edges = [
        (0, 1, 60.0, 500.0), (1, 0, 60.0, 500.0),
        (0, 2, 60.0, 500.0), (2, 0, 60.0, 500.0),
        (1, 2, 60.0, 500.0), (2, 1, 60.0, 500.0),
        (0, 4, PENALTY, 500.0), (4, 0, PENALTY, 500.0),
    ]
    return _make_graph(nodes, edges)


def _depot():
    return Stop("Depot", lat=48.0, lon=35.0, node_id=0)


def _stops(node_ids):
    """Create a list of Stop objects with distinct addresses."""
    coords = {1: (48.1, 35.0), 2: (48.1, 35.1), 3: (48.0, 35.1), 4: (47.9, 34.9)}
    return [
        Stop(f"Stop {nid}", lat=coords[nid][0], lon=coords[nid][1], node_id=nid)
        for nid in node_ids
    ]


def _complete_matrix(node_ids, cost=60.0):
    """Symmetric complete matrix for the given node IDs."""
    matrix = {}
    for i in node_ids:
        for j in node_ids:
            matrix[(i, j)] = 0.0 if i == j else cost
    return matrix


# ══════════════════════════════════════════════════════════════════════════════
#  _bearing()
# ══════════════════════════════════════════════════════════════════════════════

class TestBearing:
    def test_north(self):
        assert abs(_bearing(0.0, 0.0, 1.0, 0.0) - 0.0) < 0.5

    def test_east(self):
        assert abs(_bearing(0.0, 0.0, 0.0, 1.0) - 90.0) < 0.5

    def test_south(self):
        assert abs(_bearing(0.0, 0.0, -1.0, 0.0) - 180.0) < 0.5

    def test_west(self):
        assert abs(_bearing(0.0, 0.0, 0.0, -1.0) - 270.0) < 0.5

    def test_result_in_0_360_range(self):
        for lat2, lon2 in [(1, 1), (-1, 1), (-1, -1), (1, -1)]:
            b = _bearing(0.0, 0.0, lat2, lon2)
            assert 0.0 <= b < 360.0

    def test_same_point_defined(self):
        b = _bearing(48.0, 35.0, 48.0, 35.0)
        assert math.isfinite(b)
        assert 0.0 <= b < 360.0


# ══════════════════════════════════════════════════════════════════════════════
#  _check_reachable()
# ══════════════════════════════════════════════════════════════════════════════

class TestCheckReachable:
    def setup_method(self):
        self.G = _ring_graph()
        self.Gp = _penalty_graph()
        self.depot = _depot()
        self.graphs = {"drive": self.G, "bike": self.G, "walk": self.G}

    def test_none_node_id_returns_empty(self):
        stop = Stop("X", 48.0, 35.0, node_id=None)
        assert _check_reachable(stop, self.depot, self.graphs) == set()

    def test_same_node_as_depot_returns_empty(self):
        stop = Stop("Depot copy", 48.0, 35.0, node_id=0)
        assert _check_reachable(stop, self.depot, self.graphs) == set()

    def test_reachable_stop_all_modes(self):
        stop = _stops([1])[0]
        result = _check_reachable(stop, self.depot, self.graphs)
        assert result == {"drive", "bike", "walk"}

    @patch("vrp_solver.nearest_car_accessible_node", return_value=None)
    def test_penalty_edge_excluded(self, _mock_car):
        graphs_p = {"drive": self.Gp, "bike": self.Gp, "walk": self.Gp}
        stop = Stop("Penalty stop", 47.9, 34.9, node_id=4)
        result = _check_reachable(stop, self.depot, graphs_p)
        assert result == set()

    @patch("vrp_solver.nearest_car_accessible_node", return_value=None)
    def test_reachable_by_some_modes_only(self, _mock_car):
        G_walk = _ring_graph()
        G_penalized = _penalty_graph()
        graphs = {"drive": G_penalized, "bike": G_penalized, "walk": G_walk}
        stop = Stop("Walk-only stop", 47.9, 34.9, node_id=4)
        result = _check_reachable(stop, self.depot, graphs)
        assert "walk" in result
        assert "drive" not in result
        assert "bike" not in result

    @patch("vrp_solver.nearest_car_accessible_node", return_value=None)
    def test_no_path_node_not_found(self, _mock_car):
        stop = Stop("Missing node", 48.0, 35.0, node_id=999)
        result = _check_reachable(stop, self.depot, self.graphs)
        assert result == set()


# ══════════════════════════════════════════════════════════════════════════════
#  _assign_stops_to_modes()
# ══════════════════════════════════════════════════════════════════════════════

class TestAssignStopsToModes:
    def setup_method(self):
        self.G = _ring_graph()
        self.Gp = _penalty_graph()
        self.depot = _depot()
        self.graphs = {"drive": self.G, "bike": self.G, "walk": self.G}

    def test_all_stops_assigned_single_mode_fleet(self):
        fleet = [Vehicle("Van 1", "drive", 10, "#e41a1c")]
        stops = _stops([1, 2, 3])
        pool, unreachable = _assign_stops_to_modes(stops, fleet, self.depot, self.graphs)
        assert len(pool["drive"]) == 3
        assert unreachable == []

    @patch("vrp_solver.nearest_car_accessible_node", return_value=None)
    def test_unreachable_stop_skipped(self, _mock_car):
        G_p = _penalty_graph()
        graphs = {"drive": G_p, "bike": G_p, "walk": G_p}
        fleet = [Vehicle("Van 1", "drive", 10, "#e41a1c")]
        stop_bad = Stop("Bad stop", 47.9, 34.9, node_id=4)
        stop_ok = Stop("Good stop", 48.1, 35.0, node_id=1)
        pool, unreachable = _assign_stops_to_modes(
            [stop_bad, stop_ok], fleet, self.depot, graphs
        )
        assert len(unreachable) == 1
        assert unreachable[0].node_id == 4
        assert len(pool["drive"]) == 1

    def test_stop_assigned_to_mode_with_most_capacity(self):
        fleet = [
            Vehicle("Van", "drive", 10, "#e41a1c"),
            Vehicle("Walker", "walk", 2, "#377eb8"),
        ]
        stops = _stops([1, 2, 3])
        pool, unreachable = _assign_stops_to_modes(stops, fleet, self.depot, self.graphs)
        assert unreachable == []
        assert len(pool["drive"]) == 3

    def test_over_capacity_stops_go_unreachable(self):
        fleet = [Vehicle("Van", "drive", 1, "#e41a1c")]
        stops = _stops([1, 2])
        pool, unreachable = _assign_stops_to_modes(stops, fleet, self.depot, self.graphs)
        assert len(pool["drive"]) == 1
        assert len(unreachable) == 1

    def test_none_node_id_stop_unreachable(self):
        fleet = [Vehicle("Van", "drive", 10, "#e41a1c")]
        stop = Stop("No node", 48.1, 35.0, node_id=None)
        pool, unreachable = _assign_stops_to_modes([stop], fleet, self.depot, self.graphs)
        assert len(unreachable) == 1
        assert len(pool["drive"]) == 0

    def test_empty_stops_returns_empty_pools(self):
        fleet = [Vehicle("Van", "drive", 10, "#e41a1c")]
        pool, unreachable = _assign_stops_to_modes([], fleet, self.depot, self.graphs)
        assert pool["drive"] == []
        assert unreachable == []

    def test_multi_mode_fleet_distributes_across_modes(self):
        fleet = [
            Vehicle("Van", "drive", 2, "#e41a1c"),
            Vehicle("Walker", "walk", 2, "#377eb8"),
        ]
        stops = _stops([1, 2, 3])
        pool, unreachable = _assign_stops_to_modes(stops, fleet, self.depot, self.graphs)
        assert unreachable == []
        total = len(pool.get("drive", [])) + len(pool.get("walk", []))
        assert total == 3


# ══════════════════════════════════════════════════════════════════════════════
#  _decode_chromosome() — Split decoder
# ══════════════════════════════════════════════════════════════════════════════

class TestDecodeChromosome:
    def test_empty_chromosome(self):
        routes, unassigned = _decode_chromosome(
            [], [], [Vehicle("V", "drive", 10)], _depot(), {}, 0, {}
        )
        assert routes == []
        assert unassigned == []

    def test_single_customer_single_vehicle(self):
        depot = _depot()
        stops = _stops([1])
        matrix = _complete_matrix([0, 1])
        routes, unassigned = _decode_chromosome(
            [0], stops, [Vehicle("V", "drive", 10)], depot, matrix, 0, {0: 1}
        )
        assert len(routes) == 1
        assert routes[0][1] == [0]
        assert unassigned == []

    def test_capacity_splits_across_vehicles(self):
        depot = _depot()
        stops = _stops([1, 2, 3])
        for s in stops:
            s.weight_kg = 5.0
        matrix = _complete_matrix([0, 1, 2, 3])
        vehicles = [
            Vehicle("V1", "drive", 10),
            Vehicle("V2", "drive", 10),
        ]
        routes, unassigned = _decode_chromosome(
            [0, 1, 2], stops, vehicles, depot, matrix, 0, {0: 1, 1: 2, 2: 3}
        )
        total = sum(len(r) for _, r in routes)
        assert total == 3
        # Two vehicles needed (10 kg capacity each, 15 kg total load)
        assert len(routes) == 2
        assert unassigned == []

    def test_fleet_exhausted_leaves_unassigned(self):
        depot = _depot()
        stops = _stops([1, 2, 3])
        for s in stops:
            s.weight_kg = 5.0
        matrix = _complete_matrix([0, 1, 2, 3])
        vehicles = [Vehicle("V1", "drive", 5)]  # fits one stop only
        routes, unassigned = _decode_chromosome(
            [0, 1, 2], stops, vehicles, depot, matrix, 0, {0: 1, 1: 2, 2: 3}
        )
        total = sum(len(r) for _, r in routes)
        assert total == 1
        assert len(unassigned) == 2

    def test_penalty_travel_time_prevents_feasibility(self):
        depot = _depot()
        stops = _stops([1])
        matrix = {
            (0, 0): 0.0, (0, 1): PENALTY,
            (1, 0): PENALTY, (1, 1): 0.0,
        }
        vehicles = [Vehicle("V", "drive", 10)]
        routes, unassigned = _decode_chromosome(
            [0], stops, vehicles, depot, matrix, 0, {0: 1}
        )
        assert routes == []
        assert unassigned == [0]

    def test_time_window_prevents_feasibility(self):
        depot = _depot()
        depot.tw_open = 0.0
        depot.tw_close = 100.0
        stops = _stops([1])
        stops[0].tw_close = 50.0  # stop must be reached before 50s
        matrix = {
            (0, 0): 0.0, (0, 1): 200.0,  # travel of 200s > tw_close
            (1, 0): 200.0, (1, 1): 0.0,
        }
        routes, unassigned = _decode_chromosome(
            [0], stops, [Vehicle("V", "drive", 10)], depot, matrix, 0, {0: 1}
        )
        assert routes == []
        assert unassigned == [0]


# ══════════════════════════════════════════════════════════════════════════════
#  _compute_route_time() and _evaluate()
# ══════════════════════════════════════════════════════════════════════════════

class TestComputeRouteTime:
    def test_empty_route(self):
        assert _compute_route_time([], {}, 0, {}) == 0.0

    def test_single_customer(self):
        matrix = _complete_matrix([0, 1], cost=60.0)
        assert _compute_route_time([0], matrix, 0, {0: 1}) == 120.0  # 60 out + 60 back

    def test_multi_customer(self):
        matrix = _complete_matrix([0, 1, 2, 3], cost=30.0)
        t = _compute_route_time([0, 1, 2], matrix, 0, {0: 1, 1: 2, 2: 3})
        # depot→1→2→3→depot = 4 × 30 = 120
        assert t == 120.0


class TestEvaluate:
    def test_routed_customers_contribute_travel_time(self):
        depot = _depot()
        stops = _stops([1])
        matrix = _complete_matrix([0, 1], cost=60.0)
        fitness, routes, unassigned = _evaluate(
            [0], stops, [Vehicle("V", "drive", 10)], depot, matrix, 0, {0: 1}
        )
        assert unassigned == []
        assert len(routes) == 1
        assert fitness == 120.0  # 60 + 60, A=1, B=0

    def test_unrouted_customers_penalised(self):
        depot = _depot()
        stops = _stops([1])
        matrix = _complete_matrix([0, 1], cost=60.0)
        # Zero-capacity vehicle — customer can't be placed
        fitness, _, unassigned = _evaluate(
            [0], stops, [Vehicle("V", "drive", 0.0)], depot, matrix, 0, {0: 1}
        )
        assert len(unassigned) == 1
        assert fitness >= 1.0e6  # UNROUTED_PENALTY dominates


# ══════════════════════════════════════════════════════════════════════════════
#  Genetic operators
# ══════════════════════════════════════════════════════════════════════════════

class TestOrderCrossover:
    def test_child_is_permutation(self):
        rng = random.Random(0)
        p1 = [0, 1, 2, 3, 4, 5]
        p2 = [5, 4, 3, 2, 1, 0]
        child = _order_crossover(p1, p2, rng)
        assert sorted(child) == [0, 1, 2, 3, 4, 5]

    def test_empty_and_singleton_safe(self):
        rng = random.Random(0)
        assert _order_crossover([], [], rng) == []
        assert _order_crossover([0], [0], rng) == [0]

    def test_identical_parents_yields_same_permutation(self):
        rng = random.Random(123)
        p = [0, 1, 2, 3, 4]
        child = _order_crossover(p, p, rng)
        assert sorted(child) == p


class TestSwapMutation:
    def test_permutation_preserved(self):
        rng = random.Random(0)
        p = [0, 1, 2, 3, 4]
        m = _swap_mutation(p[:], rng)
        assert sorted(m) == [0, 1, 2, 3, 4]

    def test_singleton_unchanged(self):
        rng = random.Random(0)
        assert _swap_mutation([7], rng) == [7]

    def test_empty_unchanged(self):
        rng = random.Random(0)
        assert _swap_mutation([], rng) == []


class TestTournamentSelect:
    def test_returns_fittest_of_group(self):
        rng = random.Random(42)
        pop = [[0], [1], [2], [3]]
        fitnesses = [10.0, 2.0, 50.0, 100.0]
        # Larger k => more likely to hit the best
        winner = _tournament_select(pop, fitnesses, k=4, rng=rng)
        assert winner == [1]

    def test_k_greater_than_population_size(self):
        rng = random.Random(0)
        pop = [[0], [1]]
        fitnesses = [5.0, 1.0]
        winner = _tournament_select(pop, fitnesses, k=100, rng=rng)
        assert winner == [1]


class TestNearestNeighbourSeed:
    def test_empty(self):
        assert _nearest_neighbour_seed(0, {}, 0, {}) == []

    def test_greedy_order(self):
        # Depot closest to customer 0 (idx 0), then 1, then 2.
        matrix = {
            (0, 10): 1.0, (0, 20): 5.0, (0, 30): 9.0,
            (10, 20): 1.0, (10, 30): 5.0, (10, 0): 1.0,
            (20, 10): 1.0, (20, 30): 1.0, (20, 0): 5.0,
            (30, 10): 5.0, (30, 20): 1.0, (30, 0): 9.0,
        }
        stop_nodes = {0: 10, 1: 20, 2: 30}
        chromo = _nearest_neighbour_seed(3, matrix, 0, stop_nodes)
        assert chromo == [0, 1, 2]


# ══════════════════════════════════════════════════════════════════════════════
#  _genetic_algorithm()
# ══════════════════════════════════════════════════════════════════════════════

class TestGeneticAlgorithm:
    def test_empty_stops_returns_empty(self):
        result = _genetic_algorithm(
            [], [Vehicle("V", "drive", 10)], _depot(), {}, 0, {},
            pop_size=4, n_generations=2, seed=0,
        )
        assert result == []

    def test_single_stop(self):
        depot = _depot()
        stops = _stops([1])
        matrix = _complete_matrix([0, 1])
        result = _genetic_algorithm(
            stops, [Vehicle("V", "drive", 10)], depot, matrix, 0, {0: 1},
            pop_size=4, n_generations=2, seed=0,
        )
        assert len(result) == 1
        assert result[0][1] == [0]

    def test_multiple_stops_all_routed(self):
        depot = _depot()
        stops = _stops([1, 2, 3])
        matrix = _complete_matrix([0, 1, 2, 3])
        result = _genetic_algorithm(
            stops, [Vehicle("V", "drive", 10)], depot, matrix, 0,
            {0: 1, 1: 2, 2: 3},
            pop_size=10, n_generations=10, seed=0,
        )
        total = sum(len(r) for _, r in result)
        assert total == 3

    def test_capacity_forces_multiple_vehicles(self):
        depot = _depot()
        stops = _stops([1, 2, 3])
        for s in stops:
            s.weight_kg = 5.0
        matrix = _complete_matrix([0, 1, 2, 3])
        vehicles = [
            Vehicle("V1", "drive", 10),
            Vehicle("V2", "drive", 10),
        ]
        result = _genetic_algorithm(
            stops, vehicles, depot, matrix, 0, {0: 1, 1: 2, 2: 3},
            pop_size=10, n_generations=10, seed=0,
        )
        total = sum(len(r) for _, r in result)
        assert total == 3
        assert len(result) == 2

    def test_time_window_infeasible_stop_dropped(self):
        depot = _depot()
        depot.tw_close = 100.0
        stops = _stops([1])
        stops[0].tw_close = 50.0
        matrix = {
            (0, 0): 0.0, (0, 1): 200.0,
            (1, 0): 200.0, (1, 1): 0.0,
        }
        result = _genetic_algorithm(
            stops, [Vehicle("V", "drive", 10)], depot, matrix, 0, {0: 1},
            pop_size=6, n_generations=3, seed=0,
        )
        # No route contains the infeasible customer
        total = sum(len(r) for _, r in result)
        assert total == 0

    def test_deterministic_with_seed(self):
        depot = _depot()
        stops = _stops([1, 2, 3])
        matrix = _complete_matrix([0, 1, 2, 3])
        r1 = _genetic_algorithm(
            stops, [Vehicle("V", "drive", 10)], depot, matrix, 0,
            {0: 1, 1: 2, 2: 3},
            pop_size=8, n_generations=5, seed=123,
        )
        r2 = _genetic_algorithm(
            stops, [Vehicle("V", "drive", 10)], depot, matrix, 0,
            {0: 1, 1: 2, 2: 3},
            pop_size=8, n_generations=5, seed=123,
        )
        assert [r[1] for r in r1] == [r[1] for r in r2]


# ══════════════════════════════════════════════════════════════════════════════
#  compute_objective()
# ══════════════════════════════════════════════════════════════════════════════

class TestComputeObjective:
    def test_zero_routes(self):
        assert compute_objective([]) == 0.0

    def test_single_route(self):
        v = Vehicle("V", "drive", 10)
        vr = VehicleRoute(v, [], [0, 1, 0], [0, 1, 0], total_time_s=120.0, total_dist_m=500.0)
        assert compute_objective([vr]) == 120.0

    def test_with_vehicle_penalty(self):
        v = Vehicle("V", "drive", 10)
        vr = VehicleRoute(v, [], [0, 1, 0], [0, 1, 0], total_time_s=100.0, total_dist_m=500.0)
        assert compute_objective([vr], A=1.0, B=10.0) == 110.0

    def test_multiple_routes(self):
        v1 = Vehicle("V1", "drive", 10)
        v2 = Vehicle("V2", "drive", 10)
        vr1 = VehicleRoute(v1, [], [], [], total_time_s=100.0, total_dist_m=0)
        vr2 = VehicleRoute(v2, [], [], [], total_time_s=200.0, total_dist_m=0)
        assert compute_objective([vr1, vr2], A=1.0, B=5.0) == 310.0


# ══════════════════════════════════════════════════════════════════════════════
#  solve_vrp() — public API
# ══════════════════════════════════════════════════════════════════════════════

class TestSolveVrp:
    def setup_method(self):
        self.G = _ring_graph()
        self.depot = _depot()
        self.graphs = {"drive": self.G, "bike": self.G, "walk": self.G}

    def test_empty_fleet_raises(self):
        with pytest.raises(ValueError, match="Fleet is empty"):
            solve_vrp(_stops([1]), self.depot, [], self.graphs)

    def test_empty_stops_raises(self):
        fleet = [Vehicle("V1", "drive", 10, "#e41a1c")]
        with pytest.raises(ValueError, match="No stops"):
            solve_vrp([], self.depot, fleet, self.graphs)

    def test_depot_node_id_none_raises(self):
        depot = Stop("Depot", 48.0, 35.0, node_id=None)
        fleet = [Vehicle("V1", "drive", 10, "#e41a1c")]
        with pytest.raises(ValueError, match="node_id is None"):
            solve_vrp(_stops([1]), depot, fleet, self.graphs)

    def test_happy_path_single_vehicle(self):
        fleet = [Vehicle("V1", "drive", 10, "#e41a1c")]
        routes, warnings = solve_vrp(_stops([1]), self.depot, fleet, self.graphs)
        assert len(routes) == 1
        assert routes[0].vehicle.name == "V1"

    def test_returns_tuple_of_routes_and_warnings(self):
        fleet = [Vehicle("V1", "drive", 10, "#e41a1c")]
        result = solve_vrp(_stops([1]), self.depot, fleet, self.graphs)
        assert isinstance(result, tuple)
        assert len(result) == 2
        routes, warnings = result
        assert isinstance(routes, list)
        assert isinstance(warnings, list)

    def test_idle_vehicle_produces_warning(self):
        fleet = [
            Vehicle("V1", "drive", 10, "#e41a1c"),
            Vehicle("V2", "drive", 10, "#377eb8"),
        ]
        stops = _stops([1])
        routes, warnings = solve_vrp(stops, self.depot, fleet, self.graphs)
        idle_warnings = [w for w in warnings if "idle" in w.lower() or "no stops" in w.lower()]
        assert len(idle_warnings) >= 1

    @patch("vrp_solver.nearest_car_accessible_node", return_value=None)
    def test_unreachable_stop_produces_warning(self, _mock_car):
        Gp = _penalty_graph()
        graphs = {"drive": Gp, "bike": Gp, "walk": Gp}
        fleet = [Vehicle("V1", "drive", 10, "#e41a1c")]
        reachable_stop = Stop("Good", 48.1, 35.0, node_id=1)
        bad_stop = Stop("Bad", 47.9, 34.9, node_id=4)
        routes, warnings = solve_vrp(
            [reachable_stop, bad_stop], self.depot, fleet, graphs
        )
        unreachable_warns = [w for w in warnings if "unreachable" in w.lower() or "skipped" in w.lower()]
        assert len(unreachable_warns) >= 1

    def test_no_vehicle_routed_raises_runtime_error(self):
        Gp = _penalty_graph()
        graphs = {"drive": Gp, "bike": Gp, "walk": Gp}
        fleet = [Vehicle("V1", "drive", 10, "#e41a1c")]
        bad_stop = Stop("Bad", 47.9, 34.9, node_id=4)
        with pytest.raises(RuntimeError, match="No vehicle could be routed"):
            solve_vrp([bad_stop], self.depot, fleet, graphs)

    def test_multi_mode_fleet_routes_each_mode(self):
        fleet = [
            Vehicle("Driver", "drive", 5, "#e41a1c"),
            Vehicle("Biker",  "bike",  5, "#377eb8"),
        ]
        routes, warnings = solve_vrp(_stops([1, 2]), self.depot, fleet, self.graphs)
        assert len(routes) >= 1
        served_vehicles = {r.vehicle.name for r in routes}
        assert len(served_vehicles) >= 1

    def test_over_capacity_excess_stops_become_unreachable(self):
        fleet = [Vehicle("V1", "drive", 1, "#e41a1c")]
        stops = _stops([1, 2, 3])
        routes, warnings = solve_vrp(stops, self.depot, fleet, self.graphs)
        assert len(routes) == 1
        skipped_warns = [w for w in warnings if "unreachable" in w.lower() or "skipped" in w.lower()]
        assert len(skipped_warns) >= 1


# ══════════════════════════════════════════════════════════════════════════════
#  VEHICLE_COLORS palette
# ══════════════════════════════════════════════════════════════════════════════

class TestVehicleColors:
    def test_has_ten_colors(self):
        assert len(VEHICLE_COLORS) == 10

    def test_all_hex_format(self):
        for c in VEHICLE_COLORS:
            assert c.startswith("#"), f"Expected hex color, got: {c}"
            assert len(c) == 7, f"Expected 7-char hex, got: {c}"

    def test_all_unique(self):
        assert len(set(VEHICLE_COLORS)) == len(VEHICLE_COLORS)


# ══════════════════════════════════════════════════════════════════════════════
#  Vehicle / VehicleRoute dataclasses
# ══════════════════════════════════════════════════════════════════════════════

class TestDataclasses:
    def test_vehicle_fields(self):
        v = Vehicle("Van 1", "drive", 20, "#e41a1c")
        assert v.name == "Van 1"
        assert v.mode == "drive"
        assert v.capacity_kg == 20
        assert v.color == "#e41a1c"

    def test_vehicle_default_color_empty_string(self):
        v = Vehicle("Van", "walk", 5)
        assert v.color == ""

    def test_vehicle_route_fields(self):
        v = Vehicle("V", "drive", 10, "#e41a1c")
        vr = VehicleRoute(
            vehicle=v,
            stops=[],
            tsp_route=[0, 1, 0],
            full_route=[0, 1, 0],
            total_time_s=120.0,
            total_dist_m=500.0,
        )
        assert vr.vehicle is v
        assert vr.total_time_s == 120.0
        assert vr.total_dist_m == 500.0
        assert vr.legs == []
        assert vr.skipped_stops == []

    def test_penalty_sentinel_value(self):
        assert PENALTY == 1e9
