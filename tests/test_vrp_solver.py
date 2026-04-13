"""
tests/test_vrp_solver.py
Unit + integration tests for vrp_solver.py (Solomon I1 CVRPTW solver).

Run:
    pytest tests/test_vrp_solver.py -v
Coverage:
    pytest tests/test_vrp_solver.py --cov=vrp_solver --cov-report=term-missing
"""
from __future__ import annotations

import math
import pytest
import networkx as nx
from dataclasses import dataclass
from typing import Optional
from unittest.mock import patch, MagicMock

from vrp_solver import (
    Vehicle,
    VehicleRoute,
    VEHICLE_COLORS,
    PENALTY,
    _bearing,
    _check_reachable,
    _assign_stops_to_modes,
    _solomon_i1,
    _build_solomon_routes,
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


# ══════════════════════════════════════════════════════════════════════════════
#  _bearing()
# ══════════════════════════════════════════════════════════════════════════════

class TestBearing:
    def test_north(self):
        b = _bearing(0.0, 0.0, 1.0, 0.0)
        assert abs(b - 0.0) < 0.5

    def test_east(self):
        b = _bearing(0.0, 0.0, 0.0, 1.0)
        assert abs(b - 90.0) < 0.5

    def test_south(self):
        b = _bearing(0.0, 0.0, -1.0, 0.0)
        assert abs(b - 180.0) < 0.5

    def test_west(self):
        b = _bearing(0.0, 0.0, 0.0, -1.0)
        assert abs(b - 270.0) < 0.5

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
#  _solomon_i1() — Solomon Insertion Heuristic
# ══════════════════════════════════════════════════════════════════════════════

class TestSolomonI1:
    def _build_matrix(self, node_ids, cost=60.0):
        """Build a symmetric complete matrix for the given node IDs."""
        matrix = {}
        for i in node_ids:
            for j in node_ids:
                matrix[(i, j)] = 0.0 if i == j else cost
        return matrix

    def test_empty_stops_returns_empty(self):
        result = _solomon_i1([], [Vehicle("V1", "drive", 10)], _depot(),
                             {}, 0, {})
        assert result == []

    def test_single_stop_single_vehicle(self):
        depot = _depot()
        stops = _stops([1])
        matrix = self._build_matrix([0, 1])
        stop_nodes = {0: 1}
        vehicles = [Vehicle("V1", "drive", 10)]
        result = _solomon_i1(stops, vehicles, depot, matrix, 0, stop_nodes)
        assert len(result) == 1
        vehicle, route_indices = result[0]
        assert vehicle.name == "V1"
        assert route_indices == [0]

    def test_multiple_stops_all_inserted(self):
        depot = _depot()
        stops = _stops([1, 2, 3])
        matrix = self._build_matrix([0, 1, 2, 3])
        stop_nodes = {0: 1, 1: 2, 2: 3}
        vehicles = [Vehicle("V1", "drive", 10)]
        result = _solomon_i1(stops, vehicles, depot, matrix, 0, stop_nodes)
        total_inserted = sum(len(indices) for _, indices in result)
        assert total_inserted == 3

    def test_capacity_constraint_splits_routes(self):
        depot = _depot()
        stops = _stops([1, 2, 3])
        for s in stops:
            s.weight_kg = 5.0
        matrix = self._build_matrix([0, 1, 2, 3])
        stop_nodes = {0: 1, 1: 2, 2: 3}
        vehicles = [
            Vehicle("V1", "drive", 10),
            Vehicle("V2", "drive", 10),
        ]
        result = _solomon_i1(stops, vehicles, depot, matrix, 0, stop_nodes)
        assert len(result) == 2
        total = sum(len(indices) for _, indices in result)
        assert total == 3

    def test_seed_is_farthest_from_depot(self):
        depot = _depot()
        stops = _stops([1, 2])
        # Make stop 1 (node 1) far from depot, stop 2 (node 2) close
        matrix = {
            (0, 0): 0.0, (0, 1): 200.0, (0, 2): 50.0,
            (1, 0): 200.0, (1, 1): 0.0, (1, 2): 60.0,
            (2, 0): 50.0, (2, 1): 60.0, (2, 2): 0.0,
        }
        stop_nodes = {0: 1, 1: 2}
        vehicles = [Vehicle("V1", "drive", 10)]
        result = _solomon_i1(stops, vehicles, depot, matrix, 0, stop_nodes)
        # Both stops should be in one route (farthest is seed, then closer one inserted)
        _, route_indices = result[0]
        assert len(route_indices) == 2
        assert 0 in route_indices  # farthest stop (index 0, node 1) is in the route

    def test_time_window_prevents_insertion(self):
        depot = _depot()
        depot.tw_open = 0.0
        depot.tw_close = 100.0  # depot closes at 100s
        stops = _stops([1])
        stops[0].tw_open = 0.0
        stops[0].tw_close = 50.0  # stop closes at 50s
        # Travel time 200s > tw_close → infeasible
        matrix = {
            (0, 0): 0.0, (0, 1): 200.0,
            (1, 0): 200.0, (1, 1): 0.0,
        }
        stop_nodes = {0: 1}
        vehicles = [Vehicle("V1", "drive", 10)]
        result = _solomon_i1(stops, vehicles, depot, matrix, 0, stop_nodes)
        # Stop cannot be inserted due to time window violation
        total = sum(len(indices) for _, indices in result)
        assert total == 0

    def test_fleet_exhausted_leaves_unrouted(self):
        depot = _depot()
        stops = _stops([1, 2, 3])
        for s in stops:
            s.weight_kg = 5.0
        matrix = self._build_matrix([0, 1, 2, 3])
        stop_nodes = {0: 1, 1: 2, 2: 3}
        # Only 1 vehicle with capacity 5 → can fit 1 stop, 2 unrouted
        vehicles = [Vehicle("V1", "drive", 5)]
        result = _solomon_i1(stops, vehicles, depot, matrix, 0, stop_nodes)
        total = sum(len(indices) for _, indices in result)
        assert total == 1


# ══════════════════════════════════════════════════════════════════════════════
#  compute_objective()
# ══════════════════════════════════════════════════════════════════════════════

class TestComputeObjective:
    def test_zero_routes(self):
        assert compute_objective([]) == 0.0

    def test_single_route(self):
        v = Vehicle("V", "drive", 10)
        vr = VehicleRoute(v, [], [0, 1, 0], [0, 1, 0], total_time_s=120.0, total_dist_m=500.0)
        # F = A*120 + B*1 = 1*120 + 0*1 = 120
        assert compute_objective([vr]) == 120.0

    def test_with_vehicle_penalty(self):
        v = Vehicle("V", "drive", 10)
        vr = VehicleRoute(v, [], [0, 1, 0], [0, 1, 0], total_time_s=100.0, total_dist_m=500.0)
        # F = 1*100 + 10*1 = 110
        assert compute_objective([vr], A=1.0, B=10.0) == 110.0

    def test_multiple_routes(self):
        v1 = Vehicle("V1", "drive", 10)
        v2 = Vehicle("V2", "drive", 10)
        vr1 = VehicleRoute(v1, [], [], [], total_time_s=100.0, total_dist_m=0)
        vr2 = VehicleRoute(v2, [], [], [], total_time_s=200.0, total_dist_m=0)
        # F = 1*(100+200) + 5*2 = 310
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
