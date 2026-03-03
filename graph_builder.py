"""
graph_builder.py
----------------
Retrieves and pre-processes OSM street-network data using OSMnx.
Produces one weighted graph per travel mode (drive / bike / walk).
"""

import logging
import osmnx as ox
import networkx as nx

logger = logging.getLogger(__name__)

# ── Average city speeds (km/h) ────────────────────────────────────────────────
SPEED_KMH: dict[str, float] = {
    "drive": 30.0,
    "bike":  15.0,
    "walk":   5.0,
}


# ─────────────────────────────────────────────────────────────────────────────
def get_network(location: str, dist: int = 3_000) -> nx.MultiDiGraph:
    """
    Download an OpenStreetMap 'all' (drive + bike + walk) network centred on
    *location* within *dist* metres.

    Parameters
    ----------
    location : str
        Any address or place name understood by the Nominatim geocoder.
    dist : int
        Buffer radius in metres around the geocoded point.

    Returns
    -------
    nx.MultiDiGraph
        Raw OSM graph with standard OSMnx edge attributes (length, etc.).
    """
    logger.info("Downloading OSM network for '%s'  (radius=%d m) …", location, dist)
    G = ox.graph_from_address(
        location,
        dist=dist,
        network_type="all",   # includes drive, bike, and walk edges
        simplify=True,
    )
    logger.info("  → %d nodes, %d edges downloaded.", G.number_of_nodes(), G.number_of_edges())
    return G


# ─────────────────────────────────────────────────────────────────────────────
def add_travel_times(G: nx.MultiDiGraph) -> dict[str, nx.MultiDiGraph]:
    """
    Build three independent copies of *G*, each with a ``travel_time`` edge
    attribute (seconds) computed from edge length ÷ mode speed.

    Parameters
    ----------
    G : nx.MultiDiGraph
        Base OSM graph (must have ``length`` attribute on every edge).

    Returns
    -------
    dict[str, nx.MultiDiGraph]
        Keys are ``"drive"``, ``"bike"``, ``"walk"``.
    """
    mode_graphs: dict[str, nx.MultiDiGraph] = {}

    for mode, speed_kmh in SPEED_KMH.items():
        speed_ms = speed_kmh * 1_000 / 3_600          # convert km/h → m/s
        H = G.copy()

        for u, v, key, data in H.edges(keys=True, data=True):
            length_m = float(data.get("length", 1.0))  # metres; default 1 m if missing
            H[u][v][key]["travel_time"] = length_m / speed_ms   # seconds

        mode_graphs[mode] = H
        logger.debug("  [%s] speed=%.1f km/h — travel_time stamped on all edges.", mode, speed_kmh)

    return mode_graphs


# ─────────────────────────────────────────────────────────────────────────────
def nearest_node(G: nx.MultiDiGraph, lat: float, lon: float) -> int:
    """Return the OSM node id of the graph node closest to (lat, lon)."""
    return ox.distance.nearest_nodes(G, X=lon, Y=lat)
