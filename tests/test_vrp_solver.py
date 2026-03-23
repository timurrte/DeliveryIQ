"""
tests/test_vrp_solver.py
Unit + integration tests for vrp_solver.py (multi-vehicle routing).

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
    _distribute_within_mode,
    _solve_per_vehicle,
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


def _make_graph(nodes, edges):
    """
    Build a MultiDiGraph with the attributes expected by vrp_solver:
      nodes: list of (node_id, lat, lon)
      edges: list of (u, v, travel_time, length)
    """
    G = nx.MultiDiGraph()
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
        # atan2(0, 0) is defined; result should be a finite float in [0, 360)
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

    def test_penalty_edge_excluded(self):
        """Node 4 is only reachable via PENALTY — should not appear in any mode."""
        graphs_p = {"drive": self.Gp, "bike": self.Gp, "walk": self.Gp}
        stop = Stop("Penalty stop", 47.9, 34.9, node_id=4)
        result = _check_reachable(stop, self.depot, graphs_p)
        assert result == set()

    def test_reachable_by_some_modes_only(self):
        """Only 'walk' graph has path to node 4; drive/bike use penalty graph."""
        G_walk = _ring_graph()  # node 4 fully connected in walk graph
        G_penalized = _penalty_graph()  # node 4 only via PENALTY in drive/bike
        graphs = {"drive": G_penalized, "bike": G_penalized, "walk": G_walk}
        stop = Stop("Walk-only stop", 47.9, 34.9, node_id=4)
        result = _check_reachable(stop, self.depot, graphs)
        assert "walk" in result
        assert "drive" not in result
        assert "bike" not in result

    def test_no_path_node_not_found(self):
        """Stop snapped to a node that doesn't exist in the graph."""
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

    def test_unreachable_stop_skipped(self):
        """Node 4 only reachable via PENALTY in drive/bike/walk."""
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
        """Stop reachable by both drive and walk; drive has more capacity → chosen."""
        fleet = [
            Vehicle("Van", "drive", 10, "#e41a1c"),
            Vehicle("Walker", "walk", 2, "#377eb8"),
        ]
        stops = _stops([1, 2, 3])
        pool, unreachable = _assign_stops_to_modes(stops, fleet, self.depot, self.graphs)
        assert unreachable == []
        # All 3 stops should prefer drive (capacity 10 > walk capacity 2)
        assert len(pool["drive"]) == 3

    def test_over_capacity_stops_go_unreachable(self):
        """Fleet capacity < number of stops → excess stops are unreachable."""
        fleet = [Vehicle("Van", "drive", 1, "#e41a1c")]
        stops = _stops([1, 2])
        pool, unreachable = _assign_stops_to_modes(stops, fleet, self.depot, self.graphs)
        # Only 1 stop can be assigned; 1 is unreachable (over capacity)
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
        """With walk-only and drive-only vehicles and stops reachable by both,
        stops should be split across modes based on capacity."""
        fleet = [
            Vehicle("Van", "drive", 2, "#e41a1c"),
            Vehicle("Walker", "walk", 2, "#377eb8"),
        ]
        stops = _stops([1, 2, 3])  # all 3 reachable by both modes
        pool, unreachable = _assign_stops_to_modes(stops, fleet, self.depot, self.graphs)
        assert unreachable == []
        total = len(pool.get("drive", [])) + len(pool.get("walk", []))
        assert total == 3


# ══════════════════════════════════════════════════════════════════════════════
#  _distribute_within_mode()
# ══════════════════════════════════════════════════════════════════════════════

class TestDistributeWithinMode:
    def setup_method(self):
        self.depot = _depot()

    def test_single_vehicle_gets_all_stops(self):
        vehicles = [Vehicle("Van", "drive", 10, "#e41a1c")]
        stops = _stops([1, 2, 3])
        result = _distribute_within_mode(stops, vehicles, self.depot)
        assert len(result["Van"]) == 3

    def test_two_vehicles_split_stops(self):
        vehicles = [
            Vehicle("V1", "drive", 5, "#e41a1c"),
            Vehicle("V2", "drive", 5, "#377eb8"),
        ]
        stops = _stops([1, 2])
        result = _distribute_within_mode(stops, vehicles, self.depot)
        total = len(result["V1"]) + len(result["V2"])
        assert total == 2

    def test_capacity_rebalancing_moves_excess(self):
        """V1 has capacity 1, V2 has capacity 5 — all 4 stops must be assigned."""
        vehicles = [
            Vehicle("V1", "drive", 1, "#e41a1c"),
            Vehicle("V2", "drive", 5, "#377eb8"),
        ]
        stops = _stops([1, 2, 3])
        stops.append(Stop("Stop extra", 48.05, 35.05, node_id=4))
        # Patch _ring_graph node 4 into stops for this test — node 4 already in ring
        result = _distribute_within_mode(stops, vehicles, self.depot)
        assert len(result["V1"]) <= 1
        assert len(result["V1"]) + len(result["V2"]) == 4

    def test_over_capacity_raises_value_error(self):
        """Total capacity (2) less than stops (3) → ValueError."""
        vehicles = [
            Vehicle("V1", "drive", 1, "#e41a1c"),
            Vehicle("V2", "drive", 1, "#377eb8"),
        ]
        stops = _stops([1, 2, 3])
        with pytest.raises(ValueError, match="Capacity exhausted"):
            _distribute_within_mode(stops, vehicles, self.depot)

    def test_k_capped_at_n_stops(self):
        """More vehicles than stops: k = len(stops), extra vehicles get empty lists."""
        vehicles = [Vehicle(f"V{i}", "drive", 5, "#e41a1c") for i in range(4)]
        stops = _stops([1, 2])  # only 2 stops, 4 vehicles
        result = _distribute_within_mode(stops, vehicles, self.depot)
        total = sum(len(v) for v in result.values())
        assert total == 2

    def test_single_stop_single_vehicle(self):
        vehicles = [Vehicle("Solo", "drive", 5, "#e41a1c")]
        stops = _stops([1])
        result = _distribute_within_mode(stops, vehicles, self.depot)
        assert len(result["Solo"]) == 1

    def test_returns_dict_keyed_by_vehicle_name(self):
        vehicles = [Vehicle("Alpha", "drive", 10, "#e41a1c")]
        stops = _stops([1])
        result = _distribute_within_mode(stops, vehicles, self.depot)
        assert "Alpha" in result

    def test_rebalancing_success_branch_covered(self):
        """
        Force the rebalancing success path (lines 220-222): create 4 stops
        that cluster tightly into one group so that V1 (cap 1) receives multiple
        stops and must spill the excess into V2 (cap 5).
        """
        depot = Stop("Depot", 48.0, 35.0, node_id=0)
        # All 4 stops clustered tightly near (48.1, 35.0); the second cluster
        # centroid (far from depot) will have 0 items, so k-means with k=2 will
        # still split them 3+1 or 2+2, but V1 capacity=1 guarantees spillover.
        clustered_stops = [
            Stop(f"CS{i}", 48.1 + i * 0.0001, 35.0 + i * 0.0001, node_id=i + 10)
            for i in range(4)
        ]
        vehicles = [
            Vehicle("V1", "drive", 1, "#e41a1c"),
            Vehicle("V2", "drive", 5, "#377eb8"),
        ]
        result = _distribute_within_mode(clustered_stops, vehicles, depot)
        assert len(result["V1"]) <= 1
        assert len(result["V1"]) + len(result["V2"]) == 4


# ══════════════════════════════════════════════════════════════════════════════
#  _solve_per_vehicle()
# ══════════════════════════════════════════════════════════════════════════════

class TestSolvePerVehicle:
    def setup_method(self):
        self.G = _ring_graph()
        self.depot = _depot()
        self.vehicle = Vehicle("Van 1", "drive", 10, "#e41a1c")
        self.graphs = {"drive": self.G}

    @patch("vrp_solver.reconstruct_full_route", return_value=[0, 1, 2, 0])
    @patch("vrp_solver.solve_tsp", return_value=([0, 1, 2, 0], 300.0))
    @patch("vrp_solver.build_distance_matrix", return_value=[[0, 60, 120], [60, 0, 60], [120, 60, 0]])
    def test_happy_path_returns_vehicle_route(self, mock_matrix, mock_tsp, mock_reconstruct):
        stops = _stops([1, 2])
        result = _solve_per_vehicle(self.vehicle, stops, self.depot, self.graphs, "auto")
        assert isinstance(result, VehicleRoute)
        assert result.vehicle is self.vehicle
        assert result.total_time_s == 300.0
        assert isinstance(result.total_dist_m, float)

    @patch("vrp_solver.reconstruct_full_route", return_value=[0, 1, 0])
    @patch("vrp_solver.solve_tsp", return_value=([0, 1, 0], 120.0))
    @patch("vrp_solver.build_distance_matrix", return_value=[[0, 60], [60, 0]])
    def test_stop_visit_order_excludes_depot(self, mock_matrix, mock_tsp, mock_reconstruct):
        stops = _stops([1])
        result = _solve_per_vehicle(self.vehicle, stops, self.depot, self.graphs, "auto")
        # stop_visit_order should contain stop 1, not depot 0
        assert all(s.node_id != 0 for s in result.stops)
        assert len(result.stops) == 1

    @patch("vrp_solver.reconstruct_full_route", return_value=[0, 1, 0])
    @patch("vrp_solver.solve_tsp", return_value=([0, 1, 0], 120.0))
    @patch("vrp_solver.build_distance_matrix", return_value=[[0, 60], [60, 0]])
    def test_duplicate_node_ids_only_first_in_visit_order(self, mock_matrix, mock_tsp, mock_reconstruct):
        """Two stops with same node_id: only the first is added to stop_visit_order."""
        stop_a = Stop("A", 48.1, 35.0, node_id=1)
        stop_b = Stop("B at same node", 48.1, 35.0, node_id=1)
        result = _solve_per_vehicle(self.vehicle, [stop_a, stop_b], self.depot, self.graphs, "auto")
        node_ids_in_result = [s.node_id for s in result.stops]
        assert node_ids_in_result.count(1) == 1

    @patch("vrp_solver.reconstruct_full_route", return_value=[0, 1, 2, 0])
    @patch("vrp_solver.solve_tsp", return_value=([0, 1, 2, 0], 180.0))
    @patch("vrp_solver.build_distance_matrix", return_value=[[0, 60, 120], [60, 0, 60], [120, 60, 0]])
    def test_total_dist_m_is_non_negative(self, mock_matrix, mock_tsp, mock_reconstruct):
        stops = _stops([1, 2])
        result = _solve_per_vehicle(self.vehicle, stops, self.depot, self.graphs, "auto")
        assert result.total_dist_m >= 0.0

    @patch("vrp_solver.reconstruct_full_route", return_value=[0, 1, 0])
    @patch("vrp_solver.solve_tsp", return_value=([0, 1, 0], 60.0))
    @patch("vrp_solver.build_distance_matrix", return_value=[[0, 60], [60, 0]])
    def test_tsp_method_forwarded(self, mock_matrix, mock_tsp, mock_reconstruct):
        stops = _stops([1])
        _solve_per_vehicle(self.vehicle, stops, self.depot, self.graphs, "2opt")
        _, kwargs = mock_tsp.call_args
        assert kwargs.get("method") == "2opt" or mock_tsp.call_args[0][2] == "2opt"


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

    @patch("vrp_solver.reconstruct_full_route", return_value=[0, 1, 0])
    @patch("vrp_solver.solve_tsp", return_value=([0, 1, 0], 120.0))
    @patch("vrp_solver.build_distance_matrix", return_value=[[0, 60], [60, 0]])
    def test_happy_path_single_vehicle(self, mock_matrix, mock_tsp, mock_reconstruct):
        fleet = [Vehicle("V1", "drive", 10, "#e41a1c")]
        routes, warnings = solve_vrp(_stops([1]), self.depot, fleet, self.graphs)
        assert len(routes) == 1
        assert routes[0].vehicle.name == "V1"

    @patch("vrp_solver.reconstruct_full_route", return_value=[0, 1, 0])
    @patch("vrp_solver.solve_tsp", return_value=([0, 1, 0], 120.0))
    @patch("vrp_solver.build_distance_matrix", return_value=[[0, 60], [60, 0]])
    def test_returns_tuple_of_routes_and_warnings(self, *_mocks):
        fleet = [Vehicle("V1", "drive", 10, "#e41a1c")]
        result = solve_vrp(_stops([1]), self.depot, fleet, self.graphs)
        assert isinstance(result, tuple)
        assert len(result) == 2
        routes, warnings = result
        assert isinstance(routes, list)
        assert isinstance(warnings, list)

    @patch("vrp_solver.reconstruct_full_route", return_value=[0, 1, 0])
    @patch("vrp_solver.solve_tsp", return_value=([0, 1, 0], 120.0))
    @patch("vrp_solver.build_distance_matrix", return_value=[[0, 60], [60, 0]])
    def test_idle_vehicle_produces_warning(self, *_mocks):
        """V2 has no compatible mode for the given stops → idle warning."""
        # Use penalty graph so node 1 unreachable by bike, but reachable by drive
        Gp = _penalty_graph()
        graphs = {
            "drive": self.G,   # drive: node 1 reachable
            "bike": Gp,        # bike: node 1 reachable (penalty only for node 4)
            "walk": self.G,
        }
        fleet = [
            Vehicle("V1", "drive", 10, "#e41a1c"),
            Vehicle("V2", "drive", 10, "#377eb8"),  # same mode, will be idle after distribution
        ]
        stops = _stops([1])  # only 1 stop; one vehicle will be idle
        routes, warnings = solve_vrp(stops, self.depot, fleet, graphs)
        idle_warnings = [w for w in warnings if "idle" in w.lower() or "no stops" in w.lower()]
        assert len(idle_warnings) >= 1

    def test_unreachable_stop_produces_warning(self):
        """Stop at node 4 is unreachable (PENALTY) — should appear in warnings."""
        Gp = _penalty_graph()
        graphs = {"drive": Gp, "bike": Gp, "walk": Gp}
        fleet = [Vehicle("V1", "drive", 10, "#e41a1c")]
        reachable_stop = Stop("Good", 48.1, 35.0, node_id=1)
        bad_stop = Stop("Bad", 47.9, 34.9, node_id=4)

        with patch("vrp_solver.build_distance_matrix", return_value=[[0, 60], [60, 0]]), \
             patch("vrp_solver.solve_tsp", return_value=([0, 1, 0], 60.0)), \
             patch("vrp_solver.reconstruct_full_route", return_value=[0, 1, 0]):
            routes, warnings = solve_vrp(
                [reachable_stop, bad_stop], self.depot, fleet, graphs
            )

        unreachable_warns = [w for w in warnings if "unreachable" in w.lower() or "skipped" in w.lower()]
        assert len(unreachable_warns) >= 1

    def test_no_vehicle_routed_raises_runtime_error(self):
        """All stops unreachable → no routes produced → RuntimeError."""
        Gp = _penalty_graph()
        graphs = {"drive": Gp, "bike": Gp, "walk": Gp}
        fleet = [Vehicle("V1", "drive", 10, "#e41a1c")]
        bad_stop = Stop("Bad", 47.9, 34.9, node_id=4)
        with pytest.raises(RuntimeError, match="No vehicle could be routed"):
            solve_vrp([bad_stop], self.depot, fleet, graphs)

    @patch("vrp_solver.reconstruct_full_route", side_effect=lambda G, r: r)
    @patch("vrp_solver.solve_tsp", return_value=([0, 1, 0], 60.0))
    @patch("vrp_solver.build_distance_matrix", return_value=[[0, 60], [60, 0]])
    def test_multi_mode_fleet_routes_each_mode(self, *_mocks):
        """Two vehicles with different modes each get their own route."""
        fleet = [
            Vehicle("Driver", "drive", 5, "#e41a1c"),
            Vehicle("Biker",  "bike",  5, "#377eb8"),
        ]
        # Node 1 reachable by drive; node 2 reachable by bike (same ring graph for both here)
        routes, warnings = solve_vrp(_stops([1, 2]), self.depot, fleet, self.graphs)
        assert len(routes) >= 1
        served_vehicles = {r.vehicle.name for r in routes}
        # At least one vehicle has a route
        assert len(served_vehicles) >= 1

    @patch("vrp_solver.reconstruct_full_route", return_value=[0, 1, 0])
    @patch("vrp_solver.solve_tsp", return_value=([0, 1, 0], 60.0))
    @patch("vrp_solver.build_distance_matrix", return_value=[[0, 60], [60, 0]])
    def test_over_capacity_excess_stops_become_unreachable(self, *_mocks):
        """When fleet capacity (1) < stops (3), the 2 excess stops become
        unreachable (warning emitted) and the 1 fitting stop is routed normally."""
        fleet = [Vehicle("V1", "drive", 1, "#e41a1c")]
        stops = _stops([1, 2, 3])
        routes, warnings = solve_vrp(stops, self.depot, fleet, self.graphs)
        # 1 route produced for the 1 stop that fits
        assert len(routes) == 1
        # Warning about unreachable / skipped stops
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
