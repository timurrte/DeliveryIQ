"""
graph_builder.py
----------------
OSM street-network download and pre-processing for DeliveryIQ.

Requires OSMnx >= 2.1.

Modal routing tag model
────────────────────────
Every edge in the 'all' graph carries OSM tags that describe which
transport modes may legally and physically use it.  The key tags are:

  highway         — road/path type; the primary modal discriminator
  access          — generic access restriction (inherits to all modes)
  motor_vehicle   — car/motorcycle access override
  bicycle         — cycling access override
  foot            — pedestrian access override
  oneway          — one-directional restriction (cars)
  oneway:bicycle  — one-directional override for bikes (no = contra-flow OK)
  cycleway        — cycling facility type; "opposite*" = contra-flow allowed
  reversed        — OSMnx synthetic flag on edges added to model non-oneway
                    travel in the reverse direction for non-car modes

The impassable sentinel (PENALTY = 1e9 s) is used instead of 999 999 s
because route_solver.py's audit_reachability() flags pairs whose matrix
cost is >= PENALTY.  Using a smaller value like 999 999 would silently
allow multi-hop paths through "impassable" edges without triggering any
warning.  1e9 s ~= 31 years - Dijkstra will always prefer any real detour.
"""

from __future__ import annotations

import logging
import math
from typing import Optional

import networkx as nx
import osmnx as ox

logger = logging.getLogger(__name__)
logger.info("graph_builder loaded — OSMnx version: %s", ox.__version__)


# ── Mode speeds (km/h) ────────────────────────────────────────────────────────
SPEED_KMH: dict[str, float] = {
    "drive": 30.0,
    "bike":  15.0,
    "walk":   5.0,
}

# Sentinel for impassable edges AND unreachable matrix pairs.
# Must match route_solver.PENALTY so audit_reachability() triggers correctly.
PENALTY: float = 1e9


# ── OSM tags we must retain on every edge ────────────────────────────────────
_REQUIRED_TAGS: list[str] = [
    "access",
    "highway",
    "junction",
    "lanes",
    "maxspeed",
    "name",
    "oneway",
    "service",
    "width",
    "bicycle",
    "cycleway",
    "cycleway:left",
    "cycleway:right",
    "cycleway:both",
    "foot",
    "motor_vehicle",
    "oneway:bicycle",
    "bicycle:oneway",
    "surface",
]


# ══════════════════════════════════════════════════════════════════════════════
#  PRIVATE HELPERS  — OSM tag reading
# ══════════════════════════════════════════════════════════════════════════════

def _osm_tag(data: dict, key: str, default: str = "") -> str:
    """
    Read one OSM tag value from an edge data dict, returning a normalised
    lowercase string.  For list values, returns the first non-empty element.
    """
    val = data.get(key, default)
    if val is None or val == "":
        return default
    if isinstance(val, list):
        items = [str(v).strip().lower() for v in val if v is not None and v != ""]
        return items[0] if items else default
    return str(val).strip().lower()


def _osm_tag_in(data: dict, key: str, values: frozenset) -> bool:
    """True if ANY value of an OSM tag (scalar or list) is a member of *values*."""
    raw = data.get(key)
    if raw is None:
        return False
    if isinstance(raw, list):
        return any(
            str(v).strip().lower() in values
            for v in raw
            if v is not None
        )
    return str(raw).strip().lower() in values


def _bike_contraflow_allowed(data: dict) -> bool:
    """
    True if a bicycle is explicitly permitted to travel AGAINST a one-way
    car restriction on this edge.
    """
    _OPPOSITE = frozenset({"opposite", "opposite_lane", "opposite_track"})
    return (
        _osm_tag(data, "oneway:bicycle")  == "no"       or
        _osm_tag(data, "bicycle")         == "two_way"  or
        _osm_tag(data, "bicycle:oneway")  == "no"       or
        _osm_tag_in(data, "cycleway",       _OPPOSITE)  or
        _osm_tag_in(data, "cycleway:left",  _OPPOSITE)  or
        _osm_tag_in(data, "cycleway:right", _OPPOSITE)  or
        _osm_tag_in(data, "cycleway:both",  _OPPOSITE)
    )


# ══════════════════════════════════════════════════════════════════════════════
#  PRIVATE HELPERS  — per-mode travel time
# ══════════════════════════════════════════════════════════════════════════════

_DRIVE_BLOCKED_HW: frozenset = frozenset({
    "footway", "path", "pedestrian", "steps", "corridor", "elevator", "escalator", "bridleway",
})
_MOTOR_NO: frozenset = frozenset({"no", "private", "destination"})
_BIKE_ALLOWED: frozenset = frozenset({"yes", "designated", "permissive", "official", "use_sidepath"})
_BIKE_FORBIDDEN: frozenset = frozenset({"no", "dismount"})
_FOOT_NO: frozenset = frozenset({"no", "private"})
_STEPS_WALK_MULTIPLIER: float = 0.5


def _compute_travel_time(data: dict, mode: str, speed_ms: float) -> float:
    """
    Return the travel time in seconds for ONE directed edge under *mode*.
    Returns PENALTY (1e9 s) when the mode is legally or physically blocked.
    """
    raw_len  = data.get("length", None)
    length_m = max(float(raw_len) if raw_len is not None else 1.0, 1.0)

    hw      = _osm_tag(data, "highway")
    access  = _osm_tag(data, "access")
    bicycle = _osm_tag(data, "bicycle")
    mv      = _osm_tag(data, "motor_vehicle")
    foot    = _osm_tag(data, "foot")
    oneway  = _osm_tag(data, "oneway") in {"yes", "true", "1", "-1"}
    is_reversed = bool(data.get("reversed", False))

    if mode == "drive":
        if _osm_tag_in(data, "highway", _DRIVE_BLOCKED_HW):
            return PENALTY
        if mv in _MOTOR_NO:
            return PENALTY
        if access in _MOTOR_NO and mv not in {"yes", "permissive", "designated"}:
            return PENALTY
        return length_m / speed_ms

    elif mode == "bike":
        if bicycle in _BIKE_FORBIDDEN:
            return PENALTY
        if hw == "cycleway" or bicycle in _BIKE_ALLOWED:
            return length_m / speed_ms
        if hw in {"path", "bridleway"}:
            return length_m / speed_ms
        if hw == "steps":
            return PENALTY
        if oneway and is_reversed and not _bike_contraflow_allowed(data):
            return PENALTY
        return length_m / speed_ms

    elif mode == "walk":
        if foot in _FOOT_NO:
            return PENALTY
        if access in _FOOT_NO and foot not in {"yes", "designated", "permissive"}:
            return PENALTY
        if hw == "steps":
            return length_m / (speed_ms * _STEPS_WALK_MULTIPLIER)
        return length_m / speed_ms

    raise ValueError(f"Unknown mode {mode!r}. Expected one of: 'drive', 'bike', 'walk'.")


# ══════════════════════════════════════════════════════════════════════════════
#  PRIVATE HELPERS  — OSMnx 2.x wrappers
# ══════════════════════════════════════════════════════════════════════════════

def _configure_osmnx_tags() -> None:
    """
    Tell OSMnx to retain all tags the modal filter needs.
    Must be called before any ox.graph_from_* download.
    """
    current: list[str] = list(ox.settings.useful_tags_way or [])
    merged = list(dict.fromkeys(current + _REQUIRED_TAGS))
    ox.settings.useful_tags_way = merged
    logger.debug("Tag config: %d tags retained in useful_tags_way.", len(merged))


def _largest_strongly_connected_component(G: nx.MultiDiGraph) -> nx.MultiDiGraph:
    """Return the subgraph of the largest strongly connected component."""
    raw_n = G.number_of_nodes()
    raw_e = G.number_of_edges()

    G_lscc = ox.truncate.largest_component(G, strongly=True)

    lscc_n = G_lscc.number_of_nodes()
    lscc_e = G_lscc.number_of_edges()
    dropped_n = raw_n - lscc_n
    dropped_e = raw_e - lscc_e

    if dropped_n > 0:
        logger.warning(
            "SCC pruning: removed %d node(s) and %d edge(s) (%d->%d nodes, %d->%d edges).",
            dropped_n, dropped_e, raw_n, lscc_n, raw_e, lscc_e,
        )
    else:
        logger.info(
            "SCC pruning: graph already strongly connected (%d nodes, %d edges).",
            lscc_n, lscc_e,
        )
    return G_lscc


# ══════════════════════════════════════════════════════════════════════════════
#  PUBLIC API
# ══════════════════════════════════════════════════════════════════════════════

def get_network_from_point(lat: float, lon: float, dist: int = 3_000) -> nx.MultiDiGraph:
    """
    Download an OSM 'all' network centred on (lat, lon) and return its LSCC.

    Use this in preference to get_network() when you have explicit coordinates
    (e.g. a geocoded depot address), so the download area is guaranteed to be
    centred on the actual location rather than wherever Nominatim places a
    city-name label.

    Parameters
    ----------
    lat, lon : float  WGS-84 coordinates of the centre point.
    dist     : int    Download radius in metres (default 3 000).

    Returns
    -------
    nx.MultiDiGraph  — LSCC-pruned, ready for add_travel_times().
    """
    _configure_osmnx_tags()
    logger.info(
        "get_network_from_point: downloading OSM 'all' graph — "
        "lat=%.6f, lon=%.6f, dist=%d m", lat, lon, dist,
    )

    G_raw = ox.graph_from_point((lat, lon), dist=dist, network_type="all", simplify=True)

    logger.info("  Raw graph: %d nodes, %d edges", G_raw.number_of_nodes(), G_raw.number_of_edges())

    if G_raw.number_of_nodes() == 0:
        raise RuntimeError(
            f"OSMnx returned an empty graph for ({lat}, {lon}) at dist={dist} m."
        )

    G = _largest_strongly_connected_component(G_raw)

    if G.number_of_nodes() == 0:
        raise RuntimeError(
            f"After LSCC pruning the graph for ({lat}, {lon}) is empty.  "
            "Try a larger radius."
        )

    logger.info(
        "  Final graph: %d nodes, %d edges (strongly_connected=%s)",
        G.number_of_nodes(), G.number_of_edges(), nx.is_strongly_connected(G),
    )
    return G


def get_network(location: str, dist: int = 3_000) -> nx.MultiDiGraph:
    """
    Download an OSM 'all' network centred on *location* and return ONLY
    its Largest Strongly Connected Component (LSCC).

    Parameters
    ----------
    location : str   Nominatim-compatible address or place name.
    dist     : int   Download radius in metres (default 3 000).

    Returns
    -------
    nx.MultiDiGraph  — LSCC-pruned, ready for add_travel_times().
    """
    _configure_osmnx_tags()
    logger.info("get_network: downloading OSM 'all' graph — location='%s', dist=%d m", location, dist)

    G_raw = ox.graph_from_address(location, dist=dist, network_type="all", simplify=True)

    logger.info("  Raw graph: %d nodes, %d edges", G_raw.number_of_nodes(), G_raw.number_of_edges())

    if G_raw.number_of_nodes() == 0:
        raise RuntimeError(
            f"OSMnx returned an empty graph for '{location}' at dist={dist} m.  "
            "The address may be outside OSM coverage, or the radius is too small."
        )

    G = _largest_strongly_connected_component(G_raw)

    if G.number_of_nodes() == 0:
        raise RuntimeError(
            f"After LSCC pruning the graph for '{location}' is empty.  "
            "Try a different depot address or a larger radius."
        )

    logger.info(
        "  Final graph: %d nodes, %d edges (strongly_connected=%s)",
        G.number_of_nodes(), G.number_of_edges(), nx.is_strongly_connected(G),
    )
    return G


def get_network_from_place(
    place: str,
    which_result: Optional[int] = None,
) -> nx.MultiDiGraph:
    """
    Download an OSM 'all' network for a named *place* and return its LSCC.

    Parameters
    ----------
    place         : str   Nominatim query string (place name, city, region).
    which_result  : int, optional  Which Nominatim result to use (1-based).

    Returns
    -------
    nx.MultiDiGraph  — LSCC-pruned, ready for add_travel_times().
    """
    _configure_osmnx_tags()
    logger.info("get_network_from_place: downloading OSM 'all' graph — place='%s'", place)

    kwargs: dict = {"network_type": "all", "simplify": True}
    if which_result is not None:
        kwargs["which_result"] = which_result

    G_raw = ox.graph_from_place(place, **kwargs)

    logger.info("  Raw graph: %d nodes, %d edges", G_raw.number_of_nodes(), G_raw.number_of_edges())

    if G_raw.number_of_nodes() == 0:
        raise RuntimeError(
            f"OSMnx returned an empty graph for '{place}'.  "
            "Try a different place name or check Nominatim coverage."
        )

    G = _largest_strongly_connected_component(G_raw)

    if G.number_of_nodes() == 0:
        raise RuntimeError(f"After LSCC pruning the graph for '{place}' is empty.")

    logger.info(
        "  Final graph: %d nodes, %d edges (strongly_connected=%s)",
        G.number_of_nodes(), G.number_of_edges(), nx.is_strongly_connected(G),
    )
    return G


def add_travel_times(G: nx.MultiDiGraph) -> dict[str, nx.MultiDiGraph]:
    """
    Build three independent mode-specific copies of *G* and stamp every edge
    with a ``travel_time`` attribute (seconds) encoding modal accessibility.

    Returns
    -------
    dict[str, nx.MultiDiGraph]   keys: "drive", "bike", "walk"
    """
    mode_graphs: dict[str, nx.MultiDiGraph] = {}

    for mode, speed_kmh in SPEED_KMH.items():
        speed_ms = speed_kmh * 1_000.0 / 3_600.0
        H = G.copy()

        n_passable   = 0
        n_impassable = 0
        n_penalised  = 0

        for u, v, key, data in H.edges(keys=True, data=True):
            tt = _compute_travel_time(data, mode, speed_ms)

            if tt >= PENALTY:
                n_impassable += 1
            elif mode == "walk" and _osm_tag(data, "highway") == "steps":
                n_penalised += 1
            elif mode == "bike" and bool(data.get("reversed", False)):
                n_penalised += 1
            else:
                n_passable += 1

            H[u][v][key]["travel_time"] = tt

        logger.info(
            "  [%s] %.1f km/h — edges: %d passable, %d penalised, %d impassable",
            mode, speed_kmh, n_passable, n_penalised, n_impassable,
        )

        total = H.number_of_edges()
        if n_impassable == total and total > 0:
            logger.warning(
                "  [%s] ALL %d edges are impassable — check that _configure_osmnx_tags() "
                "ran before get_network().", mode, total,
            )

        mode_graphs[mode] = H

    return mode_graphs


def add_travel_times_to_single_graph(G: nx.MultiDiGraph) -> nx.MultiDiGraph:
    """
    Add three mode-specific travel-time attributes to every edge of *G*
    (travel_time_drive, travel_time_bike, travel_time_walk).
    """
    H = G.copy()
    speed_ms = {
        "drive": SPEED_KMH["drive"] * 1_000.0 / 3_600.0,
        "bike":  SPEED_KMH["bike"]  * 1_000.0 / 3_600.0,
        "walk":  SPEED_KMH["walk"]  * 1_000.0 / 3_600.0,
    }
    for u, v, key, data in H.edges(keys=True, data=True):
        H[u][v][key]["travel_time_drive"] = _compute_travel_time(data, "drive", speed_ms["drive"])
        H[u][v][key]["travel_time_bike"]  = _compute_travel_time(data, "bike",  speed_ms["bike"])
        H[u][v][key]["travel_time_walk"]  = _compute_travel_time(data, "walk",  speed_ms["walk"])
    logger.info(
        "  Single graph: added travel_time_drive/bike/walk to %d edges.", H.number_of_edges()
    )
    return H


def distance_m_between_nodes(
    G: nx.MultiDiGraph,
    node_a: int,
    node_b: int,
) -> float:
    """Return the great-circle distance in metres between two graph nodes."""
    try:
        y1, x1 = G.nodes[node_a]["y"], G.nodes[node_a]["x"]
        y2, x2 = G.nodes[node_b]["y"], G.nodes[node_b]["x"]
    except KeyError as e:
        raise ValueError(f"Node missing x/y: {e}") from e

    try:
        return float(ox.distance.great_circle(y1, x1, y2, x2))
    except (AttributeError, TypeError, ValueError):
        pass

    # Haversine fallback
    R = 6_371_000.0
    phi1, phi2 = math.radians(y1), math.radians(y2)
    dphi = math.radians(y2 - y1)
    dlam = math.radians(x2 - x1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return 2 * R * math.asin(math.sqrt(min(1.0, a)))


def _node_car_accessible(
    G: nx.MultiDiGraph,
    node: int,
    *,
    weight: str = "travel_time",
    penalty: float = PENALTY,
) -> bool:
    """True if node has at least one incident edge with weight < penalty."""
    for u, v, key, data in G.edges(node, keys=True, data=True):
        if u != node and v != node:
            continue
        w = data.get(weight)
        if w is not None and isinstance(w, (int, float)) and float(w) < penalty:
            return True
    for u, v, key, data in G.in_edges(node, keys=True, data=True):
        w = data.get(weight)
        if w is not None and isinstance(w, (int, float)) and float(w) < penalty:
            return True
    return False


def nearest_car_accessible_node(
    G_drive: nx.MultiDiGraph,
    lat: float,
    lon: float,
    *,
    weight: str = "travel_time",
    penalty: float = PENALTY,
) -> int:
    """
    Return the nearest node to (lat, lon) that is car-accessible (has at least
    one incident edge with travel_time < penalty).
    """
    nn = ox.nearest_nodes(G_drive, X=lon, Y=lat)
    if _node_car_accessible(G_drive, nn, weight=weight, penalty=penalty):
        return nn

    # Fallback: scan car-accessible nodes and minimise haversine distance
    best_node: Optional[int] = None
    best_dist: float = float("inf")

    for node in G_drive.nodes():
        if not _node_car_accessible(G_drive, node, weight=weight, penalty=penalty):
            continue
        try:
            ny, nx_ = G_drive.nodes[node]["y"], G_drive.nodes[node]["x"]
        except KeyError:
            continue
        try:
            d = float(ox.distance.great_circle(lat, lon, ny, nx_))
        except (AttributeError, TypeError, ValueError):
            R = 6_371_000.0
            phi1, phi2 = math.radians(lat), math.radians(ny)
            dphi = math.radians(ny - lat)
            dlam = math.radians(lon - nx_)
            a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
            d = 2 * R * math.asin(math.sqrt(min(1.0, a)))
        if d < best_dist:
            best_dist = d
            best_node = node

    if best_node is None:
        return ox.nearest_nodes(G_drive, X=lon, Y=lat)
    return best_node


def nearest_node(G: nx.MultiDiGraph, lat: float, lon: float) -> int:
    """Snap a geocoded (lat, lon) to the nearest OSM node in *G*."""
    return ox.nearest_nodes(G, X=lon, Y=lat)


def nearest_node_safe(
    G: nx.MultiDiGraph,
    lat: float,
    lon: float,
    *,
    tolerance_m: float = 500.0,
) -> int:
    """
    Bounding-box-validated node snapping for map-click coordinates.

    Raises ValueError if (lat, lon) is more than tolerance_m outside the
    graph bbox, so the Streamlit UI can show a clear warning.
    """
    node_lats = [d["y"] for _, d in G.nodes(data=True)]
    node_lons = [d["x"] for _, d in G.nodes(data=True)]

    min_lat, max_lat = min(node_lats), max(node_lats)
    min_lon, max_lon = min(node_lons), max(node_lons)

    tol_lat = tolerance_m / 111_000.0
    tol_lon = tolerance_m / (111_000.0 * math.cos(math.radians(lat)))

    if not ((min_lat - tol_lat) <= lat <= (max_lat + tol_lat)):
        raise ValueError(
            f"Clicked latitude {lat:.5f} is outside the graph bounds "
            f"({min_lat:.4f} - {max_lat:.4f}).  "
            "Try increasing the network radius or clicking closer to the depot."
        )
    if not ((min_lon - tol_lon) <= lon <= (max_lon + tol_lon)):
        raise ValueError(
            f"Clicked longitude {lon:.5f} is outside the graph bounds "
            f"({min_lon:.4f} - {max_lon:.4f}).  "
            "Try increasing the network radius or clicking closer to the depot."
        )

    return ox.nearest_nodes(G, X=lon, Y=lat)


def graph_summary(G: nx.MultiDiGraph) -> dict:
    """Return key graph statistics for debugging / sidebar display."""
    node_lats = [d["y"] for _, d in G.nodes(data=True)]
    node_lons = [d["x"] for _, d in G.nodes(data=True)]
    return {
        "nodes":              G.number_of_nodes(),
        "edges":              G.number_of_edges(),
        "min_lat":            min(node_lats),
        "max_lat":            max(node_lats),
        "min_lon":            min(node_lons),
        "max_lon":            max(node_lons),
        "strongly_connected": nx.is_strongly_connected(G),
        "osmnx_version":      ox.__version__,
    }
