"""
graph_builder.py
----------------
Retrieves and pre-processes OSM street-network data using OSMnx.

Key fixes vs v2
───────────────
1. Strongest Connected Component (SCC) pruning
   After downloading the raw 'all' graph we immediately extract the
   *largest strongly connected component*.  This eliminates dead-end
   sub-graphs whose nodes exist in OSM but have no bidirectional path
   to the rest of the city — the root cause of the
   "Connectivity is undefined for the null graph" crash and of the
   0.0 s route cost.

2. Bounding-box guard for click-to-add
   `nearest_node_safe()` replaces the bare `nearest_node()` call
   whenever a coordinate comes from a map click.  It checks that the
   clicked point lies within the bounding box of the already-downloaded
   graph and raises a descriptive ValueError if it does not, so the
   Streamlit UI can surface a clean warning instead of silently
   snapping to a far-away node.

3. Travel-time completeness guarantee
   Every edge is guaranteed to have a positive 'travel_time' attribute
   after `add_travel_times()`.  An explicit post-copy assertion logs
   any edge that still has length=0 so it can be investigated.
"""

from __future__ import annotations

import logging
import math

import osmnx as ox
import networkx as nx

logger = logging.getLogger(__name__)

# ── Mode speeds ───────────────────────────────────────────────────────────────
SPEED_KMH: dict[str, float] = {
    "drive": 30.0,
    "bike":  15.0,
    "walk":   5.0,
}

# Penalty weight used for unreachable pairs in the distance matrix
# (large but finite so the TSP solver can still form a valid tour)
PENALTY: float = 1e9


# ══════════════════════════════════════════════════════════════════════════════
#  GRAPH DOWNLOAD + SCC PRUNING
# ══════════════════════════════════════════════════════════════════════════════

def get_network(location: str, dist: int = 3_000) -> nx.MultiDiGraph:
    """
    Download an OSM 'all' network and return ONLY its largest strongly
    connected component (LSCC).

    Why LSCC?
    ─────────
    OSM contains many nodes that are reachable in one direction only
    (e.g. a dead-end alley, a ferry terminal with no return road, a
    one-way link with a missing reverse stub).  Dijkstra on an
    undirected or weakly-connected graph may still produce a *path*
    between two such nodes, but that path may not respect turn
    restrictions or one-way streets — which means the 'travel_time'
    along it is meaningless and often comes out as 0.0.

    Restricting to the LSCC guarantees:
      • Every node can reach every other node while obeying edge
        directions (i.e. real street direction / one-way rules).
      • `nx.shortest_path_length` will never raise `NetworkXNoPath`
        for node pairs that are both inside the LSCC.
      • Christofides / 2-opt receive a truly connected distance
        matrix and will never see a 'null graph'.

    Parameters
    ----------
    location : str
        Any address or place name understood by the Nominatim geocoder.
    dist : int
        Buffer radius in metres around the geocoded point.

    Returns
    -------
    nx.MultiDiGraph
        LSCC of the downloaded OSM graph.
    """
    logger.info("Downloading OSM network for '%s'  (radius=%d m)…", location, dist)
    G_raw = ox.graph_from_address(
        location,
        dist=dist,
        network_type="all",
        simplify=True,
        retain_all=False,   # OSMnx already simplifies, but be explicit
    )
    raw_n = G_raw.number_of_nodes()
    raw_e = G_raw.number_of_edges()
    logger.info("  Raw graph: %d nodes, %d edges", raw_n, raw_e)

    # ── Extract the largest STRONGLY connected component ─────────────────────
    #
    # ox.utils_graph.get_largest_component(G, strongly=True) is the
    # canonical OSMnx way to do this.  It internally calls
    # nx.strongly_connected_components and keeps the biggest one.
    #
    G = ox.utils_graph.get_largest_component(G_raw, strongly=True)
    lscc_n = G.number_of_nodes()
    lscc_e = G.number_of_edges()
    dropped = raw_n - lscc_n

    if dropped > 0:
        logger.warning(
            "  SCC pruning removed %d node(s) (%d → %d) and %d → %d edges. "
            "These were isolated sub-graphs with no strongly-connected path "
            "to the rest of the network.",
            dropped, raw_n, lscc_n, raw_e, lscc_e,
        )
    else:
        logger.info("  Graph is already strongly connected — no nodes removed.")

    logger.info("  Final LSCC: %d nodes, %d edges", lscc_n, lscc_e)
    return G


# ══════════════════════════════════════════════════════════════════════════════
#  TRAVEL-TIME WEIGHTING
# ══════════════════════════════════════════════════════════════════════════════

def add_travel_times(G: nx.MultiDiGraph) -> dict[str, nx.MultiDiGraph]:
    """
    Build three independent copies of *G* (one per travel mode) and stamp
    every edge with a positive ``travel_time`` attribute (seconds).

    Edge length comes from the OSM ``length`` attribute (metres).
    If an edge somehow has ``length`` ≤ 0 we fall back to 1 m so
    ``travel_time`` is always strictly positive — a 0-length edge
    would make Dijkstra treat the two nodes as co-located and produce
    a 0 s cost, which is the second cause of the 0.0 s route bug.

    Returns
    -------
    dict[str, nx.MultiDiGraph]
        Keys: ``"drive"``, ``"bike"``, ``"walk"``
    """
    mode_graphs: dict[str, nx.MultiDiGraph] = {}

    for mode, speed_kmh in SPEED_KMH.items():
        speed_ms = speed_kmh * 1_000.0 / 3_600.0   # km/h → m/s

        H = G.copy()
        zero_length_count = 0

        for u, v, key, data in H.edges(keys=True, data=True):
            raw_length = data.get("length", None)

            # Guard 1: missing length attribute
            if raw_length is None:
                length_m = 1.0
                logger.debug("  Edge (%s→%s) key=%s has no 'length'; defaulting to 1 m", u, v, key)
            else:
                length_m = float(raw_length)

            # Guard 2: zero / negative length
            if length_m <= 0.0:
                zero_length_count += 1
                length_m = 1.0   # treat as 1 m so travel_time > 0

            H[u][v][key]["travel_time"] = length_m / speed_ms   # seconds

        if zero_length_count:
            logger.warning(
                "  [%s] %d edge(s) had length ≤ 0; corrected to 1 m "
                "to prevent 0-second travel times.",
                mode, zero_length_count,
            )

        mode_graphs[mode] = H
        logger.debug(
            "  [%s] speed=%.1f km/h — travel_time stamped on all %d edges.",
            mode, speed_kmh, H.number_of_edges(),
        )

    return mode_graphs


# ══════════════════════════════════════════════════════════════════════════════
#  NODE SNAPPING
# ══════════════════════════════════════════════════════════════════════════════

def nearest_node(G: nx.MultiDiGraph, lat: float, lon: float) -> int:
    """
    Return the OSM node id of the graph node closest to (lat, lon).

    This is the original fast version — use it for addresses that were
    forward-geocoded (they are already city-scoped, so they will always
    be inside the graph bounds).
    """
    return ox.distance.nearest_nodes(G, X=lon, Y=lat)


def nearest_node_safe(
    G: nx.MultiDiGraph,
    lat: float,
    lon: float,
    *,
    tolerance_m: float = 500.0,
) -> int:
    """
    Bounding-box–validated version of ``nearest_node``.

    Use this for coordinates that come from a **map click** rather than
    from a forward geocode, because the user could click anywhere on the
    Folium map — including areas outside the downloaded OSM graph radius.

    Algorithm
    ─────────
    1. Compute the axis-aligned bounding box of all nodes in *G*
       (min/max lat/lon).
    2. Expand the bounding box outward by ``tolerance_m`` metres
       (≈ 0.0045° at mid-latitudes) to allow for slight overhang.
    3. If the clicked point falls outside the expanded box, raise
       ``ValueError`` with a human-readable message that the Streamlit
       UI can display directly.
    4. If the point is inside, delegate to the standard
       ``ox.distance.nearest_nodes``.

    Parameters
    ----------
    G           : already-downloaded OSM MultiDiGraph (LSCC)
    lat, lon    : coordinates of the clicked map point
    tolerance_m : how far outside the strict bbox (in metres) is still
                  considered acceptable.  Default 500 m.

    Returns
    -------
    int  OSM node id

    Raises
    ------
    ValueError
        If (lat, lon) is more than *tolerance_m* outside the graph bbox.
    """
    # Collect all node latitudes and longitudes
    node_lats = [data["y"] for _, data in G.nodes(data=True)]
    node_lons = [data["x"] for _, data in G.nodes(data=True)]

    min_lat, max_lat = min(node_lats), max(node_lats)
    min_lon, max_lon = min(node_lons), max(node_lons)

    # Convert tolerance from metres to approximate degrees
    # 1° latitude  ≈ 111_000 m  (constant)
    # 1° longitude ≈ 111_000 * cos(lat) m  (varies with latitude)
    tol_lat = tolerance_m / 111_000.0
    tol_lon = tolerance_m / (111_000.0 * math.cos(math.radians(lat)))

    lat_ok = (min_lat - tol_lat) <= lat <= (max_lat + tol_lat)
    lon_ok = (min_lon - tol_lon) <= lon <= (max_lon + tol_lon)

    if not (lat_ok and lon_ok):
        # Build a readable description of the graph boundary
        centre_lat = (min_lat + max_lat) / 2
        centre_lon = (min_lon + max_lon) / 2
        raise ValueError(
            f"Clicked point ({lat:.5f}, {lon:.5f}) is outside the downloaded "
            f"street-network graph.  The graph covers roughly "
            f"({min_lat:.4f}–{max_lat:.4f} lat, "
            f"{min_lon:.4f}–{max_lon:.4f} lon) centred at "
            f"({centre_lat:.4f}, {centre_lon:.4f}).  "
            f"Try increasing the network radius in Settings, or click "
            f"closer to the depot area."
        )

    return ox.distance.nearest_nodes(G, X=lon, Y=lat)


# ══════════════════════════════════════════════════════════════════════════════
#  DIAGNOSTIC HELPER
# ══════════════════════════════════════════════════════════════════════════════

def graph_summary(G: nx.MultiDiGraph) -> dict:
    """
    Return a dictionary of key graph statistics useful for debugging.
    Called by the Streamlit UI to display graph health in the sidebar.
    """
    node_lats = [d["y"] for _, d in G.nodes(data=True)]
    node_lons = [d["x"] for _, d in G.nodes(data=True)]
    return {
        "nodes":   G.number_of_nodes(),
        "edges":   G.number_of_edges(),
        "min_lat": min(node_lats),
        "max_lat": max(node_lats),
        "min_lon": min(node_lons),
        "max_lon": max(node_lons),
        "strongly_connected": nx.is_strongly_connected(G),
    }
