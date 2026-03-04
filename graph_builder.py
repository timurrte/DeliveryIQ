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
  ox.config(useful_tags_way=[…])                ox.settings.useful_tags_way = […]

Dnipro / 'all' network type notes
───────────────────────────────────
The 'all' network type mixes drive, bike, and pedestrian edges in the
same graph.  Some pedestrian sub-graphs in Dnipro (riverside paths,
park alleys, etc.) are *weakly* connected — they can be entered on
foot but have no valid exit for a car.  We MUST reduce to the Largest
Strongly Connected Component (LSCC) before running Dijkstra or the
TSP solver will see 0-cost or infinite-cost paths between some pairs.

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

# Log the active OSMnx version once at import time
_OSMNX_VERSION: str = getattr(ox, "__version__", "unknown")
logger.info("graph_builder loaded — OSMnx version: %s", _OSMNX_VERSION)


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
# OSMnx only preserves tags listed in useful_tags_way.  Any tag absent from
# this list is silently dropped during graph construction.  We extend the
# default set with every tag the modal filter reads.
_REQUIRED_TAGS: list[str] = [
    # already in most OSMnx defaults
    "access",
    "highway",
    "junction",
    "lanes",
    "maxspeed",
    "name",
    "oneway",
    "service",
    "width",
    # modal tags we specifically need
    "bicycle",          # bicycle access: yes/no/designated/permissive/dismount
    "cycleway",         # cycling facility; "opposite*" = contra-flow allowed
    "cycleway:left",    # contra-flow lane/track on left side of road
    "cycleway:right",   # contra-flow lane/track on right side
    "cycleway:both",    # contra-flow on both sides
    "foot",             # pedestrian access override
    "motor_vehicle",    # motor-vehicle access: no/private/destination/yes
    "oneway:bicycle",   # "no" = bikes may go against oneway restriction
    "bicycle:oneway",   # alternate key order for same meaning
    "surface",          # road surface (future use: speed penalty for gravel)
]


# ══════════════════════════════════════════════════════════════════════════════
#  PRIVATE HELPERS  — OSM tag reading
# ══════════════════════════════════════════════════════════════════════════════

def _osm_tag(data: dict, key: str, default: str = "") -> str:
    """
    Read one OSM tag value from an edge data dict, returning a normalised
    lowercase string.

    OSMnx may store tag values as:
      * str          — the normal case after simplification
      * list[str]    — when a way carries multiple values for the same key
                       (e.g. highway=["footway","path"] at a merged junction)
      * None / absent— tag was not in useful_tags_way or absent from OSM

    For lists, returns the FIRST non-empty element.  Use _osm_tag_in() when
    you need to test membership across all values of a multi-value tag.
    """
    val = data.get(key, default)
    if val is None or val == "":
        return default
    if isinstance(val, list):
        items = [str(v).strip().lower() for v in val if v is not None and v != ""]
        return items[0] if items else default
    return str(val).strip().lower()


def _osm_tag_in(data: dict, key: str, values: frozenset) -> bool:
    """
    True if ANY value of an OSM tag (scalar or list) is a member of *values*.

    This is the correct way to test multi-value tags, for example:
        highway = ["footway", "path"]
        _osm_tag_in(data, "highway", {"footway"})  ->  True
    """
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

    OSM uses several overlapping tag schemes for this:

      * oneway:bicycle = no           modern primary scheme
      * bicycle:oneway = no           alternate key order (less common)
      * cycleway = opposite           deprecated but widespread
      * cycleway = opposite_lane      physical contra-flow lane exists
      * cycleway = opposite_track     physical contra-flow track exists
      * cycleway:left = opposite_lane lane only on left side
      * cycleway:right = opposite_lane lane only on right side
      * cycleway:both = opposite_lane lanes on both sides

    Any single one of these is sufficient — OSM is not perfectly consistent
    and real-world data uses different schemes for the same situation.
    """
    _OPPOSITE = frozenset({"opposite", "opposite_lane", "opposite_track"})
    return (
        _osm_tag(data, "oneway:bicycle")  == "no"       or
        _osm_tag(data, "bicycle:oneway")  == "no"       or
        _osm_tag_in(data, "cycleway",       _OPPOSITE)  or
        _osm_tag_in(data, "cycleway:left",  _OPPOSITE)  or
        _osm_tag_in(data, "cycleway:right", _OPPOSITE)  or
        _osm_tag_in(data, "cycleway:both",  _OPPOSITE)
    )


# ══════════════════════════════════════════════════════════════════════════════
#  PRIVATE HELPERS  — per-mode travel time
# ══════════════════════════════════════════════════════════════════════════════

# Highway values where cars are physically/legally blocked.
# These are never accessible by motor vehicles regardless of access= tags.
_DRIVE_BLOCKED_HW: frozenset = frozenset({
    "footway",      # pavement / pedestrian path
    "path",         # unmaintained generic path; cars require explicit access=yes
    "pedestrian",   # pedestrianised street or zone
    "steps",        # staircase
    "corridor",     # indoor corridor
    "elevator",     # vertical lift
    "escalator",    # moving staircase
    "bridleway",    # horse path; cars prohibited by default
})

# motor_vehicle / access values that prohibit car travel.
# "destination" blocks through-traffic; impassable for routing through it.
_MOTOR_NO: frozenset = frozenset({"no", "private", "destination"})

# bicycle tag values that explicitly ALLOW cycling.
_BIKE_ALLOWED: frozenset = frozenset({
    "yes", "designated", "permissive", "official", "use_sidepath",
})

# bicycle tag values that explicitly FORBID cycling.
_BIKE_FORBIDDEN: frozenset = frozenset({"no", "dismount"})

# access / foot values that prohibit walking.
_FOOT_NO: frozenset = frozenset({"no", "private"})

# Steps walk-speed multiplier: climbing/descending stairs is roughly
# half the pace of walking on flat ground.
_STEPS_WALK_MULTIPLIER: float = 0.5


def _compute_travel_time(
    data: dict,
    mode: str,
    speed_ms: float,
) -> float:
    """
    Return the travel time in seconds for ONE directed edge under *mode*.

    Returns PENALTY (1e9 s) when the mode is legally or physically blocked.
    Never returns 0.0 for a real edge — length is floored at 1 m.

    Parameters
    ----------
    data     : OSMnx edge attribute dict  (from G.edges(data=True))
    mode     : "drive" | "bike" | "walk"
    speed_ms : base travel speed for this mode in metres/second

    Returns
    -------
    float  travel_time in seconds, or PENALTY if impassable
    """
    # Physical length — floor at 1 m to prevent division by zero
    raw_len  = data.get("length", None)
    length_m = max(float(raw_len) if raw_len is not None else 1.0, 1.0)

    # OSM tag reads (all normalised to lowercase str via _osm_tag)
    hw      = _osm_tag(data, "highway")
    access  = _osm_tag(data, "access")
    bicycle = _osm_tag(data, "bicycle")
    mv      = _osm_tag(data, "motor_vehicle")
    foot    = _osm_tag(data, "foot")
    oneway  = _osm_tag(data, "oneway") in {"yes", "true", "1", "-1"}

    # OSMnx adds `reversed=True` on synthetic edges it creates to model
    # the reverse direction of a one-way road for non-car modes.
    is_reversed = bool(data.get("reversed", False))

    # ── DRIVE (motor vehicles) ────────────────────────────────────────────────
    if mode == "drive":
        # 1. Highway type physically prevents cars (stairs, footpaths, etc.)
        if _osm_tag_in(data, "highway", _DRIVE_BLOCKED_HW):
            return PENALTY
        # 2. motor_vehicle tag explicitly forbids cars
        if mv in _MOTOR_NO:
            return PENALTY
        # 3. Generic access restriction — cars inherit access=no unless
        #    motor_vehicle tag explicitly overrides it with yes/permissive.
        if access in _MOTOR_NO and mv not in {"yes", "permissive", "designated"}:
            return PENALTY
        return length_m / speed_ms

    # ── BIKE (bicycles) ───────────────────────────────────────────────────────
    elif mode == "bike":
        # 1. Explicit bicycle prohibition always blocks cycling.
        if bicycle in _BIKE_FORBIDDEN:
            return PENALTY
        # 2. Dedicated cycling infrastructure — unconditionally accessible.
        #    Cycleways are bidirectional by default in OSM unless
        #    oneway:bicycle=yes is explicitly set (handled by graph topology).
        if hw == "cycleway" or bicycle in _BIKE_ALLOWED:
            return length_m / speed_ms
        # 3. Paths and bridleways are accessible to bikes by default
        #    (bicycle=no already handled above in rule 1).
        if hw in {"path", "bridleway"}:
            return length_m / speed_ms
        # 4. Steps are physically impassable for bikes.
        if hw == "steps":
            return PENALTY
        # 5. One-way reversed synthetic edge.
        #    OSMnx adds reversed=True on edges representing the forbidden
        #    direction of a one-way road so pedestrians can still traverse it.
        #    Bikes may only use this reversed edge if a contra-flow exception
        #    is explicitly tagged (oneway:bicycle=no, cycleway=opposite, etc.).
        if oneway and is_reversed and not _bike_contraflow_allowed(data):
            return PENALTY
        return length_m / speed_ms

    # ── WALK (pedestrians) ────────────────────────────────────────────────────
    elif mode == "walk":
        # 1. Explicit "no pedestrians" via foot= tag.
        if foot in _FOOT_NO:
            return PENALTY
        # 2. Generic access=no / private blocks walking UNLESS foot= overrides.
        if access in _FOOT_NO and foot not in {"yes", "designated", "permissive"}:
            return PENALTY
        # 3. Steps: physically walkable but significantly slower.
        #    50% speed penalty — climbing stairs is ~half the pace of flat walking.
        if hw == "steps":
            return length_m / (speed_ms * _STEPS_WALK_MULTIPLIER)
        # All other highway types are walkable at normal speed.
        return length_m / speed_ms

    raise ValueError(
        f"Unknown mode {mode!r}. Expected one of: 'drive', 'bike', 'walk'."
    )


# ══════════════════════════════════════════════════════════════════════════════
#  PRIVATE HELPERS  — version-shim layer
# ══════════════════════════════════════════════════════════════════════════════

def _configure_osmnx_tags() -> None:
    """
    Tell OSMnx to retain all tags the modal filter needs.

    OSMnx only preserves tags listed in useful_tags_way on the final edge
    attributes — any tag absent from this list is silently dropped during
    graph construction and simplification.  By default, 'bicycle',
    'motor_vehicle', 'foot', and the cycleway:* sub-keys are NOT kept.

    This function extends the current useful_tags_way list with every tag
    in _REQUIRED_TAGS, then applies the result using whichever API the
    installed OSMnx version exposes:

      OSMnx 2.x:  ox.settings.useful_tags_way = [...]
      OSMnx 1.x:  ox.config(useful_tags_way=[...])

    It is safe to call multiple times — deduplication is applied.
    It MUST be called before any graph_from_* download call.
    """
    # Collect the current default list so we extend rather than replace it.
    current: list[str] = []
    if hasattr(ox, "settings"):
        current = list(getattr(ox.settings, "useful_tags_way", None) or [])

    merged = list(dict.fromkeys(current + _REQUIRED_TAGS))  # dedup, order-stable

    # Try OSMnx 2.x API first
    if hasattr(ox, "settings"):
        try:
            ox.settings.useful_tags_way = merged
            logger.debug(
                "  Tag config via ox.settings.useful_tags_way (OSMnx %s) — "
                "%d tags retained.",
                _OSMNX_VERSION, len(merged),
            )
            return
        except AttributeError:
            pass
        except Exception as exc:
            logger.warning(
                "  ox.settings.useful_tags_way assignment raised %s: %s — "
                "falling back to ox.config().",
                type(exc).__name__, exc,
            )

    # Fall back to OSMnx 1.x API
    if hasattr(ox, "config"):
        try:
            ox.config(useful_tags_way=merged)
            logger.debug(
                "  Tag config via ox.config(useful_tags_way=…) (OSMnx %s) — "
                "%d tags retained.",
                _OSMNX_VERSION, len(merged),
            )
            return
        except Exception as exc:
            logger.warning(
                "  ox.config(useful_tags_way=…) raised %s: %s.  "
                "OSM tags may be incomplete — modal filtering may be less accurate.",
                type(exc).__name__, exc,
            )
            return

    logger.warning(
        "  Could not configure useful_tags_way (OSMnx %s has neither "
        "ox.settings nor ox.config).  Modal tag filtering may be degraded.",
        _OSMNX_VERSION,
    )


def _largest_strongly_connected_component(
    G: nx.MultiDiGraph,
) -> nx.MultiDiGraph:
    """
    Return the subgraph induced by the largest strongly connected component
    of *G*, trying every known OSMnx API variant before falling back to
    pure NetworkX.

    Attempt order
    ─────────────
    1. ox.truncate.largest_component(G, strongly=True)  — OSMnx 2.0+
    2. ox.utils_graph.get_largest_component(G, strongly=True)  — OSMnx ≤ 1.9
    3. Pure NetworkX fallback (always works, version-independent)
    """
    raw_n = G.number_of_nodes()
    raw_e = G.number_of_edges()

    # Attempt 1: OSMnx 2.0+
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
            "trying ox.utils_graph fallback.", _OSMNX_VERSION,
        )
    except Exception as exc:
        logger.warning(
            "  ox.truncate.largest_component raised %s: %s (OSMnx %s) — "
            "trying next fallback.", type(exc).__name__, exc, _OSMNX_VERSION,
        )

    # Attempt 2: OSMnx <= 1.9
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
            "falling back to pure NetworkX.", _OSMNX_VERSION,
        )
    except Exception as exc:
        logger.warning(
            "  ox.utils_graph.get_largest_component raised %s: %s (OSMnx %s) — "
            "falling back to pure NetworkX.", type(exc).__name__, exc, _OSMNX_VERSION,
        )

    # Attempt 3: Pure NetworkX (always available)
    logger.info(
        "  SCC via pure NetworkX nx.strongly_connected_components "
        "(OSMnx %s — both OSMnx wrappers unavailable).", _OSMNX_VERSION,
    )
    sccs = list(nx.strongly_connected_components(G))
    if not sccs:
        raise RuntimeError(
            "Graph has no strongly connected components at all.  "
            "The download area may be too small or the location returned an empty graph."
        )
    largest_scc_nodes = max(sccs, key=len)
    G_lscc = nx.MultiDiGraph(G.subgraph(largest_scc_nodes))
    G_lscc.graph.update(G.graph)  # preserve CRS and other graph-level attrs
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
            "(%d->%d nodes, %d->%d edges). "
            "Dropped nodes were in weakly-connected sub-graphs with no "
            "bidirectional path to the main network (common with 'all' "
            "network type in cities like Dnipro).",
            dropped_n, dropped_e, raw_n, lscc_n, raw_e, lscc_e,
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

    * retain_all  removed in OSMnx 2.0; passing it raises TypeError in 2.x.
    * simplify    still accepted in both 1.x and 2.x.

    Tries the modern 2.0+ signature first; falls back to 1.x on TypeError.
    """
    try:
        G = ox.graph_from_address(
            location, dist=dist, network_type=network_type, simplify=True,
        )
        logger.debug("  graph_from_address: used 2.0+ signature.")
        return G
    except TypeError as exc:
        msg = str(exc).lower()
        if "simplify" not in msg and "retain_all" not in msg:
            raise
        logger.debug(
            "  graph_from_address 2.0+ raised TypeError (%s); "
            "retrying with legacy 1.x signature.", exc,
        )

    # Legacy 1.x fallback
    G = ox.graph_from_address(
        location, dist=dist, network_type=network_type,
        simplify=True, retain_all=False,
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

    ox.nearest_nodes  — top-level alias added in OSMnx 1.1+; preferred in 2.0+
    ox.distance.nearest_nodes  — legacy submodule path, works in all 1.x
    """
    try:
        return ox.nearest_nodes(G, X=lon, Y=lat)
    except AttributeError:
        pass

    try:
        return ox.distance.nearest_nodes(G, X=lon, Y=lat)
    except AttributeError:
        pass

    raise RuntimeError(
        f"Could not find a nearest_nodes function in OSMnx {_OSMNX_VERSION}.  "
        "Please update OSMnx: pip install --upgrade osmnx"
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
    'all' networks in dense CIS cities like Dnipro often contain weakly-
    connected sub-graphs that Dijkstra can enter but not exit.  LSCC
    pruning removes them completely before any routing is attempted.

    Tag retention
    ─────────────
    _configure_osmnx_tags() is called before each download to ensure that
    all modal-routing tags (bicycle, motor_vehicle, cycleway:*, etc.) are
    preserved by OSMnx on every edge.  Without this step those tags are
    silently dropped during graph construction.

    Parameters
    ----------
    location : str   Nominatim-compatible address or place name.
    dist     : int   Download radius in metres (default 3 000).

    Returns
    -------
    nx.MultiDiGraph  — LSCC-pruned, ready for add_travel_times().
    """
    # Step 0: configure tag retention BEFORE the download
    _configure_osmnx_tags()

    logger.info(
        "get_network: downloading OSM 'all' graph — location='%s', dist=%d m, "
        "OSMnx=%s", location, dist, _OSMNX_VERSION,
    )

    # Step 1: Download raw graph
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
        G_raw.number_of_nodes(), G_raw.number_of_edges(),
    )

    # Step 2: Sanity check — non-empty graph
    if G_raw.number_of_nodes() == 0:
        raise RuntimeError(
            f"OSMnx returned an empty graph for '{location}' at dist={dist} m.  "
            "The address may be outside OSM coverage, or the radius is too small."
        )

    # Step 3: LSCC pruning
    try:
        G = _largest_strongly_connected_component(G_raw)
    except Exception as exc:
        logger.error(
            "LSCC extraction failed (OSMnx %s): %s — returning raw graph.  "
            "Route quality may be degraded.", _OSMNX_VERSION, exc,
        )
        G = G_raw  # last-resort: reachability audit will surface bad nodes

    # Step 4: Final validation
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


def add_travel_times(G: nx.MultiDiGraph) -> dict[str, nx.MultiDiGraph]:
    """
    Build three independent mode-specific copies of *G* and stamp every edge
    with a ``travel_time`` attribute (seconds) that encodes both speed and
    modal accessibility.

    Modal logic summary
    ────────────────────
    Each mode has a base speed (SPEED_KMH) and OSM tag rules that either
    block the edge entirely (travel_time = PENALTY = 1e9 s) or apply a
    speed multiplier.  Full rule table:

    Edge characteristic            drive        bike         walk
    ─────────────────────────────  ───────────  ───────────  ────────────
    highway=footway                PENALTY      passable     passable
    highway=path                   PENALTY      passable*    passable
    highway=pedestrian             PENALTY      passable     passable
    highway=steps                  PENALTY      PENALTY      x0.5 speed
    highway=cycleway               passable     passable     passable
    highway=bridleway              PENALTY      passable     passable
    motor_vehicle=no               PENALTY      passable     passable
    motor_vehicle=destination      PENALTY      passable     passable
    access=private (no mv ovrd)    PENALTY      passable     PENALTY
    bicycle=no / dismount          —            PENALTY      —
    bicycle=yes/designated/…       —            passable     —
    foot=no / private              —            —            PENALTY
    oneway reversed edge (bike)    —            PENALTY**    passable

    *  path + bicycle=no -> PENALTY for bike
    ** unless oneway:bicycle=no / cycleway=opposite* is tagged

    Sentinel value
    ──────────────
    PENALTY (1e9 s) is used rather than 999 999 because route_solver's
    audit_reachability() flags pairs whose cost is >= PENALTY.  Using
    999 999 would allow multi-hop paths through "impassable" edges to
    accumulate without triggering any warning.

    Returns
    -------
    dict[str, nx.MultiDiGraph]   keys: "drive", "bike", "walk"
        Three independent deep copies of G, each with modal travel_time
        stamped on every edge.
    """
    mode_graphs: dict[str, nx.MultiDiGraph] = {}

    for mode, speed_kmh in SPEED_KMH.items():
        speed_ms = speed_kmh * 1_000.0 / 3_600.0   # km/h -> m/s
        H = G.copy()

        n_passable   = 0
        n_impassable = 0
        n_penalised  = 0   # steps (walk) or blocked contra-flow (bike)

        for u, v, key, data in H.edges(keys=True, data=True):
            tt = _compute_travel_time(data, mode, speed_ms)

            # Categorise for the summary log
            if tt >= PENALTY:
                n_impassable += 1
            elif mode == "walk" and _osm_tag(data, "highway") == "steps":
                n_penalised += 1
            elif mode == "bike" and bool(data.get("reversed", False)):
                # Reversed edge that passed the contra-flow check
                n_penalised += 1
            else:
                n_passable += 1

            H[u][v][key]["travel_time"] = tt

        logger.info(
            "  [%s] %.1f km/h — edges: %d passable, %d penalised, %d impassable",
            mode, speed_kmh, n_passable, n_penalised, n_impassable,
        )

        # Warn if ALL edges ended up impassable (indicates missing tag coverage)
        total = H.number_of_edges()
        if n_impassable == total and total > 0:
            logger.warning(
                "  [%s] ALL %d edges are impassable — graph may lack modal tags.  "
                "Check that _configure_osmnx_tags() ran before get_network().",
                mode, total,
            )

        mode_graphs[mode] = H

    return mode_graphs


def nearest_node(G: nx.MultiDiGraph, lat: float, lon: float) -> int:
    """
    Snap a geocoded (lat, lon) coordinate to the nearest OSM node in *G*.

    Use for forward-geocoded addresses (always in-bounds).
    For map-click coordinates use nearest_node_safe() instead.
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
    Bounding-box-validated node snapping for map-click coordinates.

    Raises ValueError if (lat, lon) is more than tolerance_m outside the
    graph bbox, so the Streamlit UI can show a clear warning instead of
    silently snapping to a node hundreds of kilometres away.

    Parameters
    ----------
    G           : LSCC-pruned OSM graph (output of get_network).
    lat, lon    : Coordinates of the map-click event.
    tolerance_m : Extra buffer outside the strict bbox (default 500 m).

    Returns
    -------
    int  OSM node id

    Raises
    ------
    ValueError  if (lat, lon) is outside bbox + tolerance_m.
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
        "nodes":              G.number_of_nodes(),
        "edges":              G.number_of_edges(),
        "min_lat":            min(node_lats),
        "max_lat":            max(node_lats),
        "min_lon":            min(node_lons),
        "max_lon":            max(node_lons),
        "strongly_connected": nx.is_strongly_connected(G),
        "osmnx_version":      _OSMNX_VERSION,
    }
