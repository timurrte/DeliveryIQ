"""
main.py - Entry-point for the Delivery Route Optimizer.

Usage:
    python main.py
"""
from __future__ import annotations
import logging, math, sys
from pathlib import Path
from dataclasses import dataclass
from geocoder import geocode_all, Location
from graph_builder import get_network, add_travel_times, nearest_node
from route_solver import build_distance_matrix, solve_tsp, reconstruct_full_route
from visualizer import build_map

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
                    datefmt="%H:%M:%S")
logger = logging.getLogger("main")

DEPOT_ADDRESS = "Piazza del Duomo, Milan, Italy"
CLIENT_ADDRESSES = [
    "Castello Sforzesco, Milan, Italy",
    "Navigli, Milan, Italy",
    "Brera, Milan, Italy",
    "Porta Venezia, Milan, Italy",
    "Stazione Centrale, Milan, Italy",
]
NETWORK_RADIUS_M = 4000
TSP_METHOD = "auto"
OUTPUT_MAP = Path("delivery_map.html")


@dataclass
class OptimizationResult:
    mode: str
    tsp_route_nodes: list
    full_route_nodes: list
    total_time_s: float
    total_time_hms: str


def _hms(s):
    if not math.isfinite(s): return "N/A"
    h, r = divmod(int(s), 3600); m, sc = divmod(r, 60)
    return f"{h}h {m:02d}m {sc:02d}s" if h else f"{m}m {sc:02d}s" if m else f"{sc}s"


def run_optimizer(depot_address=DEPOT_ADDRESS, client_addresses=None,
                  network_radius=NETWORK_RADIUS_M, tsp_method=TSP_METHOD,
                  output_map=OUTPUT_MAP):
    if client_addresses is None:
        client_addresses = CLIENT_ADDRESSES

    logger.info("STEP 1/5 - Geocoding %d addresses", 1 + len(client_addresses))
    locations = geocode_all([depot_address] + client_addresses)
    depot_loc, client_locs = locations[0], locations[1:]

    logger.info("STEP 2/5 - Downloading OSM street network")
    G_raw = get_network(depot_address, dist=network_radius)

    logger.info("STEP 3/5 - Snapping to nearest OSM nodes")
    depot_loc.node_id = nearest_node(G_raw, depot_loc.lat, depot_loc.lon)
    for loc in client_locs:
        loc.node_id = nearest_node(G_raw, loc.lat, loc.lon)
        logger.info("  '%s' -> node %d", loc.address[:50], loc.node_id)

    all_nodes = [depot_loc.node_id] + [c.node_id for c in client_locs]

    logger.info("STEP 4/5 - Computing travel-time graphs")
    mode_graphs = add_travel_times(G_raw)

    logger.info("STEP 5/5 - Solving TSP for each travel mode")
    results, mode_routes, mode_times = {}, {}, {}

    for mode, G_mode in mode_graphs.items():
        matrix = build_distance_matrix(G_mode, all_nodes, weight="travel_time")
        tsp_route, total_s = solve_tsp(all_nodes, matrix, method=tsp_method)
        full_route = reconstruct_full_route(G_mode, tsp_route, weight="travel_time")
        results[mode] = OptimizationResult(mode, tsp_route, full_route, total_s, _hms(total_s))
        mode_routes[mode] = full_route
        mode_times[mode] = total_s

    print("\n" + "=" * 55)
    print("  DELIVERY ROUTE OPTIMIZATION RESULTS")
    print("=" * 55)
    print(f"  Depot : {depot_address}")
    print(f"  Stops : {len(client_addresses)}")
    print("-" * 55)
    speeds = {"drive": "30 km/h", "bike": "15 km/h", "walk": "5 km/h"}
    for mode, res in results.items():
        print(f"  {mode:<8} | {speeds[mode]:>8} | {res.total_time_hms:>15}")
    print("=" * 55)

    ordered_stops = [n for n in results["drive"].tsp_route_nodes
                     if n != depot_loc.node_id]
    map_path = build_map(mode_graphs, mode_routes, mode_times,
                         depot_loc.node_id, ordered_stops, output_map)
    print(f"\n  Map saved -> {map_path.resolve()}\n")
    return results


if __name__ == "__main__":
    run_optimizer()
