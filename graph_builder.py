"""
graph_builder.py
----------------
OSM street-network download and pre-processing for DeliveryIQ.

OSMnx version compatibility
────────────────────────────
This file is intentionally written to work against BOTH the legacy
1.x API and the breaking 2.0+ API.  Every call that changed between
major versions is wrapped in a try/except with a clearly-labelled
fallback, and the active OSMnx version is logged at startup so you
can always tell which branch ran.

Known breaking changes in OSMnx 2.0
─────────────────────────────────────
  Old (≤ 1.9)                                  New (≥ 2.0)
  ─────────────────────────────────────────    ───────────────────────────────
  ox.utils_graph.get_largest_component(G, …)   ox.truncate.largest_component(G, …)
  graph_from_address(…, retain_all=False)       retain_all kwarg removed entirely
  graph_from_address(…, simplify=True)          simplify kwarg still accepted
  ox.distance.nearest_nodes(…)                  still works; also ox.nearest_nodes(…)

Dnipro / 'all' network type notes
───────────────────────────────────
The 'all' network type mixes drive, bike, and pedestrian edges in the
same graph.  Some pedestrian sub-graphs in Dnipro (riverside paths,
park alleys, etc.) are *weakly* connected — they can be entered on
foot but have no valid exit for a car.  We MUST reduce to the Largest
Strongly Connected Component (LSCC) before running Dijkstra or the
TSP solver will see 0-cost or infinite-cost paths between some pairs.
"""

from __future__ import annotations

import logging
import math
from typing import Optional

import networkx as nx
import osmnx as ox

logger = logging.getLogger(__name__)

# ── Log the active OSMnx version once at import time ─────────────────────────
_OSMNX_VERSION: str = getattr(ox, "__version__", "unknown")
logger.info("graph_builder loaded — OSMnx version: %s", _OSMNX_VERSION)


# ── Mode speeds (km/h) ────────────────────────────────────────────────────────
SPEED_KMH: dict[str, float] = {
    "drive": 30.0,
    "bike":  15.0,
    "walk":   5.0,
}

# Sentinel for unreachable pairs  (large but finite → TSP still works)
PENALTY: float = 1e9


# ══════════════════════════════════════════════════════════════════════════════
#  PRIVATE HELPERS  — version-shim layer
# ══════════════════════════════════════════════════════════════════════════════

def _largest_strongly_connected_component(
    G: nx.MultiDiGraph,
) -> nx.MultiDiGraph:
    """
    Return the subgraph induced by the largest strongly connected component
    of *G*, trying every known OSMnx API variant before falling back to
    pure NetworkX.

    Attempt order
    ─────────────
    1. ox.truncate.largest_component(G, strongly=True)
       — OSMnx 2.0+, the canonical new location.

    2. ox.utils_graph.get_largest_component(G, strongly=True)
       — OSMnx ≤ 1.9, the old location.

    3. Pure NetworkX fallback (version-independent):
           scc  = max(nx.strongly_connected_components(G), key=len)
           view = G.subgraph(scc)
           G    = nx.MultiDiGraph(view)   ← materialise into a new graph
       This always works regardless of OSMnx version and is the reference
       implementation that both OSMnx wrappers ultimately delegate to.

    All three paths produce an identical result.  The first one that
    succeeds without raising AttributeError / TypeError is used, and the
    chosen path is logged so you can see exactly which branch ran.

    Parameters
    ----------
    G : raw OSM MultiDiGraph (output of graph_from_address / graph_from_place)

    Returns
    -------
    nx.MultiDiGraph  — largest SCC, as a new independent graph object
    """
    raw_n = G.number_of_nodes()
    raw_e = G.number_of_edges()

    # ── Attempt 1: OSMnx 2.0+ ────────────────────────────────────────────────
    try:
        G_lscc = ox.truncate.largest_component(G, strongly=True)
        logger.info(
            "  SCC via ox.truncate.largest_component (OSMnx %s)", _OSMNX_VERSION
        )
        _log_scc_stats(raw_n, raw_e, G_lscc)
        return G_lscc
    except AttributeError:
        logger.debug(
            "  ox.truncate.largest_component not found (OSMnx %s) — "
            "trying ox.utils_graph fallback.",
            _OSMNX_VERSION,
        )
    except Exception as exc:
        logger.warning(
            "  ox.truncate.largest_component raised %s: %s "
            "(OSMnx %s) — trying next fallback.",
            type(exc).__name__, exc, _OSMNX_VERSION,
        )

    # ── Attempt 2: OSMnx ≤ 1.9 ───────────────────────────────────────────────
    try:
        G_lscc = ox.utils_graph.get_largest_component(G, strongly=True)
        logger.info(
            "  SCC via ox.utils_graph.get_largest_component (OSMnx %s)",
            _OSMNX_VERSION,
        )
        _log_scc_stats(raw_n, raw_e, G_lscc)
        return G_lscc
    except AttributeError:
        logger.debug(
            "  ox.utils_graph.get_largest_component not found (OSMnx %s) — "
            "falling back to pure NetworkX.",
            _OSMNX_VERSION,
        )
    except Exception as exc:
        logger.warning(
            "  ox.utils_graph.get_largest_component raised %s: %s "
            "(OSMnx %s) — falling back to pure NetworkX.",
            type(exc).__name__, exc, _OSMNX_VERSION,
        )

    # ── Attempt 3: Pure NetworkX (always available) ───────────────────────────
    logger.info(
        "  SCC via pure NetworkX nx.strongly_connected_components "
        "(OSMnx %s — both OSMnx wrappers unavailable).",
        _OSMNX_VERSION,
    )
    sccs = list(nx.strongly_connected_components(G))
    if not sccs:
        raise RuntimeError(
            f"Graph has no strongly connected components at all.  "
            f"This likely means the download area (radius) is too small or "
            f"the location '{_OSMNX_VERSION}' returned an empty graph."
        )

    largest_scc_nodes = max(sccs, key=len)

    # G.subgraph() returns a *frozen view* — we materialise it into an
    # independent MultiDiGraph so callers can freely add attributes later.
    G_lscc = nx.MultiDiGraph(G.subgraph(largest_scc_nodes))

    # Copy graph-level attributes (CRS, etc.) that OSMnx stores in G.graph{}
    G_lscc.graph.update(G.graph)

    _log_scc_stats(raw_n, raw_e, G_lscc)
    return G_lscc


def _log_scc_stats(raw_n: int, raw_e: int, G_lscc: nx.MultiDiGraph) -> None:
    """Log a concise before/after summary of the SCC pruning step."""
    lscc_n = G_lscc.number_of_nodes()
    lscc_e = G_lscc.number_of_edges()
    dropped_n = raw_n - lscc_n
    dropped_e = raw_e - lscc_e

    if dropped_n > 0:
        logger.warning(
            "  SCC pruning: removed %d node(s) and %d edge(s) "
            "(%d→%d nodes, %d→%d edges). "
            "Dropped nodes were in weakly-connected sub-graphs with no "
            "bidirectional path to the main network (common with 'all' "
            "network type in cities like Dnipro).",
            dropped_n, dropped_e,
            raw_n, lscc_n,
            raw_e, lscc_e,
        )
    else:
        logger.info(
            "  SCC pruning: graph was already strongly connected — "
            "no nodes removed (%d nodes, %d edges).",
            lscc_n, lscc_e,
        )


def _graph_from_address_compat(
    location: str,
    dist: int,
    network_type: str,
) -> nx.MultiDiGraph:
    """
    Call ox.graph_from_address with a version-safe parameter set.

    Changes between OSMnx versions
    ───────────────────────────────
    • retain_all  — removed in 2.0; passing it raises TypeError.
    • simplify    — still accepted in both 1.x and 2.x.
    • dist         — unchanged.
    • network_type — unchanged.

    We try the modern (2.0+) signature first.  If it raises TypeError
    (unexpected keyword argument 'retain_all') we know we're on 1.x and
    retry without retain_all.  If the first call itself fails with
    TypeError for a different reason we re-raise immediately so the
    original error is not swallowed.
    """
    # Modern 2.0+ call — no retain_all
    try:
        G = ox.graph_from_address(
            location,
            dist=dist,
            network_type=network_type,
            simplify=True,
        )
        logger.debug("  graph_from_address: used 2.0+ signature.")
        return G
    except TypeError as exc:
        # If the error mentions something OTHER than simplify/retain_all
        # it is a genuine caller error — re-raise.
        msg = str(exc).lower()
        if "simplify" not in msg and "retain_all" not in msg:
            raise

        logger.debug(
            "  graph_from_address 2.0+ signature raised TypeError (%s); "
            "retrying with legacy 1.x signature.",
            exc,
        )

    # Legacy 1.x fallback — include retain_all=False
    G = ox.graph_from_address(
        location,
        dist=dist,
        network_type=network_type,
        simplify=True,
        retain_all=False,
    )
    logger.debug("  graph_from_address: used legacy 1.x signature.")
    return G


def _nearest_nodes_compat(
    G: nx.MultiDiGraph,
    lon: float,
    lat: float,
) -> int:
    """
    Call the nearest-nodes function using whichever API shape is available.

    Both calls are identical in semantics — the top-level alias
    ox.nearest_nodes was added in 1.1 as a convenience and is the
    preferred form in 2.0+, while ox.distance.nearest_nodes still works
    in 2.0 but may be deprecated in a future release.
    """
    # Preferred: top-level alias (works in 1.1+ and 2.0+)
    try:
        return ox.nearest_nodes(G, X=lon, Y=lat)
    except AttributeError:
        pass

    # Legacy submodule path (works in all 1.x)
    try:
        return ox.distance.nearest_nodes(G, X=lon, Y=lat)
    except AttributeError:
        pass

    raise RuntimeError(
        f"Could not find a nearest_nodes function in OSMnx {_OSMNX_VERSION}.  "
        f"Please update OSMnx: pip install --upgrade osmnx"
    )


# ══════════════════════════════════════════════════════════════════════════════
#  PUBLIC API
# ══════════════════════════════════════════════════════════════════════════════

def get_network(location: str, dist: int = 3_000) -> nx.MultiDiGraph:
    """
    Download an OSM 'all' network centred on *location* and return ONLY
    its Largest Strongly Connected Component (LSCC).

    Why 'all' + LSCC?
    ──────────────────
    'all' captures every traversable path (roads, bike lanes, footpaths)
    which is essential for the bike and walk routing modes.  However,
    'all' networks in dense CIS cities like Dnipro often contain:
      • Riverside pedestrian paths with no road-exit
      • Partial highway onramps that OSM has tagged in only one direction
      • Dead-end courtyards (дворы) with no turning space modelled

    These form weakly-connected islands.  Dijkstra can navigate INTO
    them but not OUT, producing 0.0 s or infinite-cost paths and
    breaking the TSP solver with "Connectivity undefined for null graph".
    LSCC pruning removes them completely.

    Parameters
    ----------
    location : str   Nominatim-compatible address or place name.
    dist     : int   Download radius in metres (default 3 000).

    Returns
    -------
    nx.MultiDiGraph  — LSCC-pruned, ready for add_travel_times().
    """
    logger.info(
        "get_network: downloading OSM 'all' graph — location='%s', dist=%d m, "
        "OSMnx=%s",
        location, dist, _OSMNX_VERSION,
    )

    # ── Step 1: Download raw graph ────────────────────────────────────────────
    try:
        G_raw = _graph_from_address_compat(location, dist, network_type="all")
    except Exception as exc:
        logger.error(
            "graph_from_address failed for '%s' (OSMnx %s): %s",
            location, _OSMNX_VERSION, exc,
        )
        raise

    logger.info(
        "  Raw graph: %d nodes, %d edges",
        G_raw.number_of_nodes(),
        G_raw.number_of_edges(),
    )

    # ── Step 2: Sanity check — non-empty graph ────────────────────────────────
    if G_raw.number_of_nodes() == 0:
        raise RuntimeError(
            f"OSMnx returned an empty graph for '{location}' at dist={dist} m.  "
            f"The address may be outside OSM coverage, or the radius is too small.  "
            f"Try increasing the network radius."
        )

    # ── Step 3: LSCC pruning ──────────────────────────────────────────────────
    try:
        G = _largest_strongly_connected_component(G_raw)
    except Exception as exc:
        logger.error(
            "LSCC extraction failed (OSMnx %s): %s — returning raw graph.  "
            "Route quality may be degraded.",
            _OSMNX_VERSION, exc,
        )
        # Last-resort: return the raw graph so the app doesn't crash entirely.
        # The reachability audit in route_solver.py will surface any bad nodes.
        G = G_raw

    # ── Step 4: Final validation ──────────────────────────────────────────────
    if G.number_of_nodes() == 0:
        raise RuntimeError(
            f"After LSCC pruning the graph for '{location}' is empty.  "
            f"This can happen if the entire downloaded area consists of "
            f"one-way streets with no return paths (unusual).  "
            f"Try a different depot address or a larger radius."
        )

    logger.info(
        "  Final graph: %d nodes, %d edges (strongly_connected=%s)",
        G.number_of_nodes(),
        G.number_of_edges(),
        nx.is_strongly_connected(G),
    )
    return G


def add_travel_times(G: nx.MultiDiGraph) -> dict[str, nx.MultiDiGraph]:
    """
    Build three independent copies of *G* (one per travel mode) and stamp
    every edge with a strictly positive ``travel_time`` attribute (seconds).

    Robustness guarantees
    ──────────────────────
    • Missing ``length`` attribute  → treated as 1 m (logged at DEBUG).
    • ``length`` ≤ 0               → corrected to 1 m (logged at WARNING).
    • ``travel_time`` is always > 0 after this function returns.

    A 0-second travel time between two different nodes is physically
    impossible and is the direct cause of the "route cost = 0.0 s" bug:
    Dijkstra assigns cost 0 to the edge, the TSP accumulates 0+0+0=0,
    and the UI displays "0m 00s" for the entire route.

    Returns
    -------
    dict[str, nx.MultiDiGraph]   keys: "drive", "bike", "walk"
    """
    mode_graphs: dict[str, nx.MultiDiGraph] = {}

    for mode, speed_kmh in SPEED_KMH.items():
        speed_ms = speed_kmh * 1_000.0 / 3_600.0   # km/h → m/s
        H = G.copy()
        zero_length_count = 0
        missing_length_count = 0

        for u, v, key, data in H.edges(keys=True, data=True):
            raw_length = data.get("length", None)

            if raw_length is None:
                missing_length_count += 1
                length_m = 1.0
            else:
                length_m = float(raw_length)

            if length_m <= 0.0:
                zero_length_count += 1
                length_m = 1.0

            H[u][v][key]["travel_time"] = length_m / speed_ms

        if missing_length_count:
            logger.debug(
                "  [%s] %d edge(s) had no 'length' attribute — defaulted to 1 m.",
                mode, missing_length_count,
            )
        if zero_length_count:
            logger.warning(
                "  [%s] %d edge(s) had length ≤ 0 — corrected to 1 m "
                "to prevent 0-second travel times.",
                mode, zero_length_count,
            )

        mode_graphs[mode] = H
        logger.debug(
            "  [%s] %.1f km/h — travel_time stamped on all %d edges.",
            mode, speed_kmh, H.number_of_edges(),
        )

    return mode_graphs


def nearest_node(G: nx.MultiDiGraph, lat: float, lon: float) -> int:
    """
    Snap a geocoded (lat, lon) coordinate to the nearest OSM node in *G*.

    Use this for addresses obtained via forward-geocoding (they are always
    city-scoped and will always fall inside the graph bounds).
    For map-click coordinates use ``nearest_node_safe`` instead.
    """
    return _nearest_nodes_compat(G, lon, lat)


def nearest_node_safe(
    G: nx.MultiDiGraph,
    lat: float,
    lon: float,
    *,
    tolerance_m: float = 500.0,
) -> int:
    """
    Bounding-box–validated node snapping for map-click coordinates.

    The user can click anywhere on a Folium map, including areas that lie
    outside the downloaded OSM graph radius.  This function checks that
    the clicked point is within (bbox + tolerance_m) before snapping and
    raises a descriptive ValueError if it is not, so the Streamlit UI can
    display a clear warning instead of silently snapping to a node that is
    hundreds of kilometres away.

    Parameters
    ----------
    G           : LSCC-pruned OSM graph (output of get_network).
    lat, lon    : Coordinates of the map-click event.
    tolerance_m : Extra buffer outside the strict bbox that is still
                  accepted (default 500 m).

    Returns
    -------
    int  OSM node id

    Raises
    ------
    ValueError
        If (lat, lon) is more than *tolerance_m* outside the graph bbox.
    """
    node_lats = [d["y"] for _, d in G.nodes(data=True)]
    node_lons = [d["x"] for _, d in G.nodes(data=True)]

    min_lat, max_lat = min(node_lats), max(node_lats)
    min_lon, max_lon = min(node_lons), max(node_lons)

    # Convert metre tolerance to approximate degrees
    tol_lat = tolerance_m / 111_000.0
    tol_lon = tolerance_m / (111_000.0 * math.cos(math.radians(lat)))

    if not ((min_lat - tol_lat) <= lat <= (max_lat + tol_lat)):
        raise ValueError(
            f"Clicked latitude {lat:.5f} is outside the graph bounds "
            f"({min_lat:.4f} – {max_lat:.4f}).  "
            f"Try increasing the network radius or clicking closer to the depot."
        )
    if not ((min_lon - tol_lon) <= lon <= (max_lon + tol_lon)):
        raise ValueError(
            f"Clicked longitude {lon:.5f} is outside the graph bounds "
            f"({min_lon:.4f} – {max_lon:.4f}).  "
            f"Try increasing the network radius or clicking closer to the depot."
        )

    return _nearest_nodes_compat(G, lon, lat)


def graph_summary(G: nx.MultiDiGraph) -> dict:
    """
    Return a dict of key graph statistics for debugging / sidebar display.

    Keys: nodes, edges, min_lat, max_lat, min_lon, max_lon,
          strongly_connected, osmnx_version
    """
    node_lats = [d["y"] for _, d in G.nodes(data=True)]
    node_lons = [d["x"] for _, d in G.nodes(data=True)]
    return {
        "nodes":             G.number_of_nodes(),
        "edges":             G.number_of_edges(),
        "min_lat":           min(node_lats),
        "max_lat":           max(node_lats),
        "min_lon":           min(node_lons),
        "max_lon":           max(node_lons),
        "strongly_connected": nx.is_strongly_connected(G),
        "osmnx_version":     _OSMNX_VERSION,
    }
