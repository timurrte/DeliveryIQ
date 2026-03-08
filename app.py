"""
app.py — DeliveryIQ · Route Optimizer v3.0
==========================================
New in v3:
  • City-locked address search  — every geocode is scoped to one city
  • Click-to-Add on the map     — reverse geocodes click coords to an address
  • Live 'Selected Deliveries' list with per-item remove buttons
  • Green depot / red numbered client markers
  • Full multi-modal AntPath route rendering after optimisation

Run:  streamlit run app.py
"""

from __future__ import annotations

import math
import sys
import os
import datetime
import logging
from dataclasses import dataclass, field
from typing import Optional

import folium
import networkx as nx
import streamlit as st
from streamlit_folium import st_folium
from folium.plugins import AntPath
from geopy.geocoders import Nominatim
from geopy.exc import GeocoderTimedOut

sys.path.insert(0, os.path.dirname(__file__))

from graph_builder import (
    get_network, get_network_from_point, add_travel_times,
    nearest_node, nearest_node_safe,
    distance_m_between_nodes,
    nearest_car_accessible_node,
    graph_summary, SPEED_KMH,
)
from route_solver import (
    build_distance_matrix,
    build_drive_matrix_hybrid,
    build_drive_matrix_mapbox,
    solve_tsp,
    reconstruct_full_route,
    get_full_path,
    audit_reachability,
    PENALTY as ROUTE_PENALTY,
)
from vrp_solver import Vehicle, VehicleRoute, solve_vrp, VEHICLE_COLORS

logging.basicConfig(level=logging.INFO)

MAPBOX_API_KEY: str = os.getenv("MAPBOX_API_KEY", "")

# ══════════════════════════════════════════════════════════════════════════════
#  CITY LOCK  — change this one line to re-scope the whole app
# ══════════════════════════════════════════════════════════════════════════════
DEFAULT_CITY = "Dnipro, Ukraine"

CITY_CENTRES: dict[str, tuple[float, float]] = {
    "Dnipro, Ukraine": (48.4647, 35.0462),
    "Kyiv, Ukraine":   (50.4501, 30.5234),
    "Lviv, Ukraine":   (49.8397, 24.0297),
    "Odesa, Ukraine":  (46.4825, 30.7233),
}

_geolocator = Nominatim(user_agent="deliveryiq_v3", timeout=10)

# ══════════════════════════════════════════════════════════════════════════════
#  PAGE CONFIG
# ══════════════════════════════════════════════════════════════════════════════
st.set_page_config(
    page_title="DeliveryIQ · Route Optimizer",
    page_icon="📦",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ══════════════════════════════════════════════════════════════════════════════
#  GLOBAL CSS
# ══════════════════════════════════════════════════════════════════════════════
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Mono:wght@400;500&family=Outfit:wght@300;400;500;600;700;800&display=swap');

html,body,[class*="css"]{ font-family:'Outfit',sans-serif; }

/* sidebar */
section[data-testid="stSidebar"]{background:#080c14!important;border-right:1px solid #161d2e;}
section[data-testid="stSidebar"] *{color:#cbd5e1!important;}
section[data-testid="stSidebar"] input,
section[data-testid="stSidebar"] textarea,
section[data-testid="stSidebar"] .stSelectbox>div>div{
  background:#0f1623!important;border:1px solid #1e2d45!important;
  border-radius:8px!important;color:#e2e8f0!important;}
section[data-testid="stSidebar"] .stButton>button{
  width:100%;background:linear-gradient(135deg,#1d4ed8,#2563eb)!important;
  color:white!important;border:none!important;border-radius:10px!important;
  padding:13px!important;font-size:.88rem!important;font-weight:600!important;
  box-shadow:0 4px 14px rgba(37,99,235,.4)!important;transition:all .2s!important;}
section[data-testid="stSidebar"] .stButton>button:hover{
  background:linear-gradient(135deg,#1e40af,#1d4ed8)!important;transform:translateY(-1px);}

/* main */
.main .block-container{background:#f1f5f9;padding-top:1.2rem;padding-bottom:3rem;max-width:1440px;}

/* header */
.page-header{
  background:linear-gradient(120deg,#080c14 0%,#0f2044 55%,#0c3d6b 100%);
  border-radius:18px;padding:26px 36px;margin-bottom:20px;
  display:flex;align-items:center;gap:20px;
  box-shadow:0 6px 30px rgba(8,12,20,.25);}
.page-header h1{color:#f0f9ff!important;font-size:1.9rem!important;
  font-weight:800!important;margin:0!important;letter-spacing:-.5px;}
.page-header p{color:#7eb3d8!important;margin:5px 0 0!important;font-size:.88rem;}
.city-pill{
  display:inline-block;background:rgba(37,99,235,.25);
  border:1px solid rgba(96,165,250,.4);color:#93c5fd!important;
  font-size:.72rem;font-weight:600;padding:4px 14px;
  border-radius:20px;margin-top:8px;letter-spacing:.4px;}

/* metric cards */
.metric-card{
  background:white;border-radius:16px;padding:22px 20px;text-align:center;
  box-shadow:0 2px 16px rgba(0,0,0,.06);border:1.5px solid #e2e8f0;height:100%;
  transition:transform .15s,box-shadow .15s;}
.metric-card:hover{transform:translateY(-2px);box-shadow:0 6px 24px rgba(0,0,0,.1);}
.metric-card.winner{
  border-color:#10b981;background:linear-gradient(150deg,#f0fdf8,#ecfdf5);
  box-shadow:0 4px 20px rgba(16,185,129,.16);}
.m-icon{font-size:1.9rem;margin-bottom:6px;}
.m-mode{font-size:.63rem;font-weight:700;letter-spacing:1.3px;text-transform:uppercase;color:#94a3b8;}
.m-time{font-family:'DM Mono',monospace;font-size:1.65rem;font-weight:500;color:#0f172a;line-height:1.1;}
.m-sub{font-size:.72rem;color:#64748b;margin-top:5px;}
.winner-badge{
  display:inline-block;background:#10b981;color:white!important;
  font-size:.6rem;font-weight:700;letter-spacing:.8px;text-transform:uppercase;
  padding:3px 10px;border-radius:20px;margin-top:7px;}

/* section headers */
.sec-head{
  font-size:.63rem;font-weight:700;letter-spacing:1.6px;text-transform:uppercase;
  color:#94a3b8;margin:24px 0 10px;display:flex;align-items:center;gap:8px;}
.sec-head::after{content:'';flex:1;height:1px;background:#e2e8f0;}

/* sidebar delivery list */
.delivery-item{
  display:flex;align-items:flex-start;gap:8px;
  background:#0f1623;border:1px solid #1e2d45;
  border-radius:10px;padding:9px 12px;margin-bottom:6px;
  font-size:.76rem;line-height:1.4;}
.delivery-item.depot-item{border-color:#166534;background:#052e16;}
.di-num{
  flex-shrink:0;width:22px;height:22px;border-radius:50%;
  background:#dc2626;color:white;font-weight:700;font-size:.68rem;
  display:flex;align-items:center;justify-content:center;}
.di-num.depot{background:#16a34a;}
.di-addr{flex:1;color:#cbd5e1!important;word-break:break-word;}
.di-coords{font-size:.63rem;color:#475569!important;font-family:'DM Mono',monospace;}

/* click mode banner */
.click-active{
  background:linear-gradient(90deg,#0c4a6e,#075985);border:1px solid #0284c7;
  border-radius:10px;padding:12px 16px;color:#bae6fd!important;
  font-size:.82rem;font-weight:500;text-align:center;
  animation:pulse-b 2s infinite;}
@keyframes pulse-b{
  0%,100%{box-shadow:0 0 0 0 rgba(14,165,233,.4);}
  50%{box-shadow:0 0 0 6px rgba(14,165,233,0);}}

/* info/warn boxes */
.info-box{background:#eff6ff;border:1px solid #bfdbfe;border-left:4px solid #2563eb;
  border-radius:8px;padding:12px 16px;font-size:.83rem;color:#1e3a5f;margin:10px 0;}
.warn-box{background:#fffbeb;border:1px solid #fde68a;border-left:4px solid #f59e0b;
  border-radius:8px;padding:12px 16px;font-size:.83rem;color:#78350f;margin:10px 0;}

/* stop table */
.stop-table{
  width:100%;border-collapse:collapse;font-size:.83rem;
  background:white;border-radius:14px;overflow:hidden;
  box-shadow:0 2px 16px rgba(0,0,0,.06);}
.stop-table thead tr{background:#0f172a;}
.stop-table thead th{
  padding:12px 16px;font-size:.61rem;font-weight:700;
  letter-spacing:1.1px;text-transform:uppercase;color:#94a3b8;text-align:left;}
.stop-table tbody tr{border-bottom:1px solid #f1f5f9;transition:background .1s;}
.stop-table tbody tr:hover{background:#f8faff;}
.stop-table tbody td{padding:11px 16px;color:#334155;vertical-align:middle;}
.snum{display:inline-flex;align-items:center;justify-content:center;
  width:24px;height:24px;border-radius:50%;font-weight:700;font-size:.7rem;color:white;}
.snum-depot{background:#16a34a;} .snum-stop{background:#dc2626;}
.tag{display:inline-block;padding:2px 9px;border-radius:20px;font-size:.65rem;font-weight:600;}
.tag-depot{background:#dcfce7;color:#15803d;}
.tag-stop{background:#fee2e2;color:#dc2626;}
.tag-return{background:#f3f4f6;color:#6b7280;}
.mono{font-family:'DM Mono',monospace;font-size:.78rem;}

#MainMenu,footer,header{visibility:hidden;}
</style>
""", unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
#  DATA CLASSES
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class DeliveryStop:
    address: str
    lat: float
    lon: float
    source: str = "typed"        # "typed" | "map_click"
    node_id: Optional[int] = None


@dataclass
class LegInfo:
    from_label: str
    to_label: str
    distance_m: float
    travel_time_s: float
    cumulative_time_s: float


@dataclass
class ModeResult:
    mode: str
    tsp_route: list
    full_route: list
    total_time_s: float
    legs: list = field(default_factory=list)
    # For drive (hybrid last-meter): stops in visit order for map markers.
    stop_visit_order: Optional[list] = None


# ══════════════════════════════════════════════════════════════════════════════
#  SESSION STATE
# ══════════════════════════════════════════════════════════════════════════════
#
#  All interactive state is stored here so that Streamlit re-runs (triggered
#  by any widget interaction) always have consistent data.
#
#  Key                Type                  Purpose
#  ─────────────────────────────────────────────────────────────────────────
#  city               str                   Active city lock
#  depot              DeliveryStop | None   The warehouse / start point
#  stops              list[DeliveryStop]    Growing list of delivery stops
#  click_mode         bool                  True = map captures clicks
#  last_click         tuple | None          Dedup: last processed click coords
#  opt_results        dict | None           TSP results (all 3 modes)
#  opt_graphs         dict | None           Weighted nx graphs (all 3 modes)
#
def _init():
    defs = {
        "city": DEFAULT_CITY,
        "depot": None,
        "stops": [],
        "click_mode": False,
        "last_click": None,
        "opt_results": None,
        "opt_graphs": None,
        "opt_warnings": [],
        "opt_car_unreachable": [],
        # Multi-vehicle fleet (VRP)
        "fleet": [Vehicle("Vehicle 1", "drive", 50, VEHICLE_COLORS[0])],
        "opt_vrp_results": None,
        "opt_vrp_warnings": [],
    }
    for k, v in defs.items():
        if k not in st.session_state:
            st.session_state[k] = v

_init()


# ══════════════════════════════════════════════════════════════════════════════
#  CITY-LOCKED GEOCODING
# ══════════════════════════════════════════════════════════════════════════════

def _lock(address: str, city: str) -> str:
    """
    Append the city suffix if not already present.
    This single function is the city-lock gate for ALL forward geocoding.
    Example:  "Navigli"  →  "Navigli, Milan, Italy"
    """
    city_stem = city.lower().split(",")[0].strip()
    if city_stem not in address.lower():
        return f"{address.strip()}, {city}"
    return address.strip()


def forward_geocode(raw: str, city: str) -> Optional[DeliveryStop]:
    """Forward geocode with city lock. Returns None on failure."""
    query = _lock(raw, city)
    try:
        r = _geolocator.geocode(query, timeout=10)
        if r is None:
            return None
        return DeliveryStop(address=r.address, lat=r.latitude, lon=r.longitude, source="typed")
    except GeocoderTimedOut:
        return None


def reverse_geocode(lat: float, lon: float) -> Optional[DeliveryStop]:
    """Reverse geocode (lat, lon) → DeliveryStop. Falls back to coord string."""
    try:
        r = _geolocator.reverse((lat, lon), language="en", timeout=10)
        addr = r.address if r else f"{lat:.5f}, {lon:.5f}"
    except GeocoderTimedOut:
        addr = f"{lat:.5f}, {lon:.5f}"
    return DeliveryStop(address=addr, lat=lat, lon=lon, source="map_click")


# ══════════════════════════════════════════════════════════════════════════════
#  OSM / ROUTING
# ══════════════════════════════════════════════════════════════════════════════

@st.cache_resource(show_spinner=False)
def cached_network(city: str, radius: int):
    return get_network(city, dist=radius)


@st.cache_resource(show_spinner=False)
def cached_network_at(lat: float, lon: float, radius: int):
    """Network centred on explicit coordinates (used during optimization)."""
    return get_network_from_point(lat, lon, dist=radius)


@st.cache_resource(show_spinner=False)
def cached_mode_graphs(city: str, radius: int):
    """
    Build travel-time graphs for all three modes, keyed by (city, radius).

    WHY not cached_mode_graphs(_G):
    Streamlit excludes underscore-prefixed parameters from the cache hash.
    The old `_G` signature meant this function was called ONCE ever — all
    subsequent calls returned stale mode-graphs built from the first graph
    object regardless of city/radius changes.  That caused every address to
    snap to the same node as the original graph's centre.
    """
    G = cached_network(city, radius)
    return add_travel_times(G)


@st.cache_resource(show_spinner=False)
def cached_mode_graphs_at(lat: float, lon: float, radius: int):
    """Mode graphs centred on explicit coordinates (used during optimization)."""
    G = cached_network_at(lat, lon, radius)
    return add_travel_times(G)


def leg_dist(G, path):
    d = 0.0
    for i in range(len(path) - 1):
        ed = G.get_edge_data(path[i], path[i+1])
        if ed:
            d += min(v.get("length", 0) for v in ed.values())
    return d


def n_coords(G, n):
    return G.nodes[n]["y"], G.nodes[n]["x"]


# Hybrid last-meter: max walking distance from parked car to delivery (metres).
LAST_METER_THRESHOLD_M: float = 100.0


def run_optimization(depot, stops, city, radius, tsp_method):
    """
    Full optimization pipeline — v3.2.

    Fixes applied in this version
    ──────────────────────────────
    1. SNAP LOGGING  — every address, its (lat, lon), and its resolved
       node ID are printed to the Python logger at INFO level before any
       matrix work begins.  This makes it immediately visible in the
       terminal when two addresses collapse to the same node.

    2. DEDUPLICATION — after snapping, duplicate node IDs are removed
       from all_nodes using dict.fromkeys() which preserves insertion
       order (depot stays at index 0).  The labels dict is rebuilt so
       colliding stops share a single merged label.  The UI receives a
       named warning for every collision.

    3. FIXED MODE-GRAPH CACHE — cached_mode_graphs() is now keyed by
       (city, radius) instead of the graph object.  The old `_G` parameter
       name bypassed Streamlit's cache hash so stale mode-graphs from a
       previous city/radius were silently reused, causing every nearest-node
       call to return the same node from the old graph's bounding area.

    Returns (results, mode_graphs, warnings_list, car_unreachable_notes).
    car_unreachable_notes: addresses > 100 m from nearest car-accessible road.
    """
    prog = st.empty()
    warnings_out: list[str] = []

    def status(msg: str, done: bool = False) -> None:
        icon = "✅" if done else "⏳"
        prog.markdown(
            f'<p style="color:#64748b;font-size:.83rem">{icon} {msg}</p>',
            unsafe_allow_html=True,
        )

    # ── Step 1: Download OSM + LSCC pruning ──────────────────────────────────
    # Download centred on the depot's geocoded coordinates, not the city-name
    # string.  Nominatim may place a city label far from the actual depot
    # location, which causes the entire graph to miss the delivery area.
    status("Downloading OSM network (largest strongly-connected component)…")
    G_raw = cached_network_at(depot.lat, depot.lon, radius)
    summary = graph_summary(G_raw)
    status(
        f"Network ready — {summary['nodes']:,} nodes, {summary['edges']:,} edges "
        f"({'✅ strongly connected' if summary['strongly_connected'] else '⚠️ not fully connected'})",
        done=True,
    )

    # ── Step 2: Snap every address to its nearest OSM node ───────────────────
    # Snapping happens AFTER get_network() so every node_id is guaranteed
    # to exist in the LSCC-pruned graph.
    # nearest_node()      — for typed/geocoded addresses (always in-bounds)
    # nearest_node_safe() — for map-click coordinates (may be out-of-bounds)
    status("Snapping addresses to road nodes…")

    def _snap(obj, label: str) -> None:
        """
        Snap a DeliveryStop to its nearest OSM node.
        For map-click sources, try bbox-validated snapping first; if the click
        landed slightly outside the downloaded area, fall back to unchecked
        nearest_node() and emit a warning rather than failing outright.
        """
        if obj.source == "map_click":
            try:
                obj.node_id = nearest_node_safe(G_raw, obj.lat, obj.lon)
            except ValueError:
                obj.node_id = nearest_node(G_raw, obj.lat, obj.lon)
                warnings_out.append(
                    f"⚠️ {label} was clicked slightly outside the downloaded "
                    f"network area — snapped to the nearest road node."
                )
        else:
            obj.node_id = nearest_node(G_raw, obj.lat, obj.lon)

    _snap(depot, "Depot")
    for idx, stop in enumerate(stops, 1):
        _snap(stop, f"Stop #{idx}")

    # ── Step 2a: DEBUG — log every snap result to the terminal ───────────────
    # This is the primary diagnostic tool when all addresses map to the same
    # node: run `streamlit run app.py` in a terminal and watch the output.
    logging.getLogger(__name__).info(
        "=== NODE SNAP REPORT (city=%s, radius=%d m) ===", city, radius
    )
    logging.getLogger(__name__).info(
        "  DEPOT  lat=%.6f  lon=%.6f  →  node_id=%s  [%s]",
        depot.lat, depot.lon, depot.node_id, depot.address[:60],
    )
    for idx, stop in enumerate(stops, 1):
        logging.getLogger(__name__).info(
            "  STOP #%d  lat=%.6f  lon=%.6f  →  node_id=%s  [%s]",
            idx, stop.lat, stop.lon, stop.node_id, stop.address[:60],
        )

    # Print to stdout as well so it is visible even when the log level
    # is set above INFO (e.g. in production deployments).
    print("\n" + "=" * 60)
    print(f"NODE SNAP REPORT  city={city!r}  radius={radius}m")
    print("=" * 60)
    print(f"  DEPOT  ({depot.lat:.6f}, {depot.lon:.6f})  →  {depot.node_id}  [{depot.address[:55]}]")
    for idx, stop in enumerate(stops, 1):
        marker = "⚠️ NONE" if stop.node_id is None else str(stop.node_id)
        print(f"  STOP #{idx}  ({stop.lat:.6f}, {stop.lon:.6f})  →  {marker}  [{stop.address[:55]}]")
    print("=" * 60 + "\n")

    # ── Step 2b: Filter unsnappable stops ────────────────────────────────────
    valid_stops = [s for s in stops if s.node_id is not None]
    if len(valid_stops) < len(stops):
        skipped = len(stops) - len(valid_stops)
        warnings_out.append(
            f"⚠️ {skipped} stop(s) could not be snapped to the road network "
            f"and will be skipped."
        )

    if depot.node_id is None:
        raise RuntimeError(
            "Depot could not be snapped to the road network.  "
            f"It may be outside the {radius} m download radius — "
            "try increasing the network radius in Settings."
        )
    if not valid_stops:
        raise RuntimeError(
            "No delivery stops could be snapped to the road network.  "
            "All stops are outside the downloaded graph area."
        )

    # ── Step 2c: Build raw node list and detect duplicate snaps ──────────────
    # Two addresses are "co-located" when Nominatim geocodes them to the same
    # (lat, lon) — e.g. a vague query returns the city centroid — and both
    # therefore snap to the same OSM node.  We:
    #   a) warn the user by name for each collision
    #   b) deduplicate all_nodes so build_distance_matrix never sees
    #      src==dst for what should be two different stops (which produces
    #      a 0.0 s cost and makes the whole route cost 0.0 s)
    #   c) merge their labels so the leg table still shows both addresses

    # Map: node_id → list of human labels that share it
    node_label_map: dict[int, list[str]] = {}
    node_label_map[depot.node_id] = ["Depot"]
    for idx, stop in enumerate(valid_stops, 1):
        short = stop.address[:30]
        lbl = f"Stop #{idx} ({short})"
        node_label_map.setdefault(stop.node_id, []).append(lbl)

    # Report collisions
    for node_id, lbls in node_label_map.items():
        if len(lbls) > 1:
            collision_str = " + ".join(lbls)
            msg = (
                f"⚠️ Node collision: {collision_str} all snap to OSM node "
                f"{node_id}.  These addresses are too close together to be "
                f"distinguished on the road network (or Nominatim returned "
                f"the same coordinates for both).  They will be treated as a "
                f"single stop."
            )
            warnings_out.append(msg)
            logging.getLogger(__name__).warning(
                "NODE COLLISION: node=%d  labels=%s", node_id, lbls
            )
            print(f"⚠️  {msg}")

    # Deduplicated node list — dict.fromkeys preserves insertion order,
    # guaranteeing depot stays at index 0.
    raw_nodes  = [depot.node_id] + [s.node_id for s in valid_stops]
    unique_nodes: list[int] = list(dict.fromkeys(raw_nodes))

    # Merged labels: if two stops share a node, join their names
    labels: dict[int, str] = {
        node_id: " & ".join(lbls)
        for node_id, lbls in node_label_map.items()
    }

    n_unique = len(unique_nodes)
    n_raw    = len(raw_nodes)
    status(
        f"Snapped {n_raw} addresses → {n_unique} unique node(s)"
        + (f" ({n_raw - n_unique} collision(s) merged)" if n_raw != n_unique else ""),
        done=True,
    )

    if n_unique < 2:
        raise RuntimeError(
            f"After deduplication only {n_unique} unique road node(s) remain.  "
            f"All addresses resolve to the same location — please use more "
            f"specific street addresses, or increase the network radius."
        )

    # ── Step 3: Build mode graphs ─────────────────────────────────────────────
    status("Building travel-time graphs (drive / bike / walk)…")
    mode_graphs = cached_mode_graphs_at(depot.lat, depot.lon, radius)
    status("Mode graphs ready", done=True)

    # ── Step 3b: Hybrid last-meter — dual-node mapping for car ─────────────────
    # N_ped = current node_id (nearest in full graph). N_car = nearest car-accessible.
    # d = distance(N_ped, N_car). If d > 100 m → car unreachable; else use N_car + walk time.
    G_drive = mode_graphs["drive"]
    walk_speed_ms = SPEED_KMH["walk"] * 1_000.0 / 3_600.0
    car_unreachable_notes: list[str] = []
    car_reachable: list[tuple[int, float, object, str]] = []  # (n_car, walk_time_s, stop, label)

    for idx, stop in enumerate(valid_stops, 1):
        n_ped = stop.node_id
        n_car = nearest_car_accessible_node(G_drive, stop.lat, stop.lon)
        d_m = distance_m_between_nodes(G_drive, n_ped, n_car)
        short = stop.address[:30]
        label = f"Stop #{idx} ({short})"
        if d_m > LAST_METER_THRESHOLD_M:
            car_unreachable_notes.append(
                f"Address [{stop.address[:50]}{'…' if len(stop.address) > 50 else ''}] "
                f"is {d_m:.0f} m from the nearest road and is inaccessible for vehicle delivery."
            )
        else:
            walk_time_s = d_m / walk_speed_ms
            car_reachable.append((n_car, walk_time_s, stop, label))

    # Build drive labels (merge when multiple stops share same n_car)
    labels_drive: dict[int, str] = {depot.node_id: "Depot"}
    for (n_car, _wt, _stop, lbl) in car_reachable:
        labels_drive[n_car] = labels_drive.get(n_car, "") + (" & " + lbl if n_car in labels_drive else lbl)
    node_to_stops_drive: dict[int, list] = {}
    for (n_car, _wt, stop, _lbl) in car_reachable:
        node_to_stops_drive.setdefault(n_car, []).append(stop)

    # ── Step 4: Distance matrix + TSP per mode ─────────────────────────────────
    results: dict[str, ModeResult] = {}

    for mode, G_mode in mode_graphs.items():
        if mode == "drive":
            # Hybrid last-meter: only car-reachable stops; cost = drive to N_car + walk d.
            if len(car_reachable) < 1:
                status("[drive] No car-reachable stops (all beyond 100 m) — skipping TSP")
                results["drive"] = ModeResult(
                    "drive",
                    tsp_route=[depot.node_id, depot.node_id],
                    full_route=[(depot.node_id, depot.node_id)],
                    total_time_s=0.0,
                    legs=[],
                    stop_visit_order=[],
                )
                status("[drive] done — 0 stops", done=True)
                continue
            car_stops = [(n_car, wt) for (n_car, wt, _s, _l) in car_reachable]
            n_drive = 1 + len(car_stops)

            if MAPBOX_API_KEY:
                drive_latlons = (
                    [(depot.lat, depot.lon)]
                    + [(s.lat, s.lon) for (_n, _w, s, _l) in car_reachable]
                )
                status(
                    f"[drive] Fetching traffic-aware matrix from Mapbox "
                    f"({n_drive} stops)…"
                )
                try:
                    matrix_drive_idx = build_drive_matrix_mapbox(
                        drive_latlons, MAPBOX_API_KEY
                    )
                    nodes_drive = [depot.node_id] + [
                        n_car for (n_car, _w, _s, _l) in car_reachable
                    ]
                except Exception as exc:
                    warnings_out.append(
                        f"⚠️ Mapbox API error ({exc}) — "
                        f"falling back to static travel times."
                    )
                    matrix_drive_idx, nodes_drive = build_drive_matrix_hybrid(
                        G_drive, depot.node_id, car_stops, weight="travel_time"
                    )
            else:
                status(
                    f"[drive] Building static matrix "
                    f"(depot + {len(car_stops)} stops, last-meter walk)…"
                )
                matrix_drive_idx, nodes_drive = build_drive_matrix_hybrid(
                    G_drive, depot.node_id, car_stops, weight="travel_time"
                )
            indices_drive = list(range(n_drive))
            labels_drive_list = ["Depot"] + [lbl for (_n, _w, _s, lbl) in car_reachable]
            status(f"[drive] Solving TSP ({tsp_method}, {n_drive} nodes)…")
            tsp_route_indices, total_s_d = solve_tsp(
                indices_drive, matrix_drive_idx, method=tsp_method
            )
            tsp_route_d = [nodes_drive[i] for i in tsp_route_indices]
            if total_s_d >= ROUTE_PENALTY:
                warnings_out.append(
                    "⚠️ [Car] Route cost is ≥ PENALTY — at least one stop unreachable."
                )
            full_route_d = reconstruct_full_route(G_drive, tsp_route_d, weight="travel_time")
            legs_d = []
            cum = 0.0
            for leg_idx in range(len(tsp_route_d) - 1):
                i, j = tsp_route_indices[leg_idx], tsp_route_indices[leg_idx + 1]
                t = matrix_drive_idx.get((i, j), ROUTE_PENALTY)
                path = get_full_path(
                    G_drive, tsp_route_d[leg_idx], tsp_route_d[leg_idx + 1],
                    weight="travel_time",
                )
                dist = leg_dist(G_drive, path)
                cum += t
                legs_d.append(LegInfo(
                    labels_drive_list[i],
                    labels_drive_list[j],
                    dist, t, cum,
                ))
            # Route indices: 0 = depot, 1..n_drive-1 = stops; car_reachable[k] = (k+1)-th stop
            stop_visit_order_d = [
                car_reachable[i - 1][2] for i in tsp_route_indices[1:-1]
            ]
            results["drive"] = ModeResult(
                "drive", tsp_route_d, full_route_d, total_s_d, legs_d,
                stop_visit_order=stop_visit_order_d,
            )
            status(f"[drive] done — {hms(total_s_d)}", done=True)
            continue

        # Bike and Walk: same node set for all stops (matrix consistency).
        status(f"[{mode}] Building distance matrix ({n_unique}×{n_unique})…")
        matrix = build_distance_matrix(G_mode, unique_nodes, weight="travel_time")
        problems = audit_reachability(matrix, unique_nodes, labels)
        for problem in problems:
            msg = (
                f"⚠️ [{mode}] {problem.label} has connectivity issues — "
                + problem.summary().split("] ", 1)[-1]
            )
            if msg not in warnings_out:
                warnings_out.append(msg)
        status(f"[{mode}] Solving TSP ({tsp_method}, {n_unique} nodes)…")
        tsp_route, total_s = solve_tsp(unique_nodes, matrix, method=tsp_method)
        if total_s >= ROUTE_PENALTY:
            warnings_out.append(
                f"⚠️ [{mode}] Route cost is ≥ PENALTY — "
                f"at least one stop is unreachable via this mode."
            )
        full_route = reconstruct_full_route(G_mode, tsp_route, weight="travel_time")
        legs = []
        cum = 0.0
        for leg_idx in range(len(tsp_route) - 1):
            src, dst = tsp_route[leg_idx], tsp_route[leg_idx + 1]
            t = matrix.get((src, dst), ROUTE_PENALTY)
            path = get_full_path(G_mode, src, dst, weight="travel_time")
            dist = leg_dist(G_mode, path)
            cum += t
            legs.append(LegInfo(
                labels.get(src, str(src)),
                labels.get(dst, str(dst)),
                dist, t, cum,
            ))
        results[mode] = ModeResult(mode, tsp_route, full_route, total_s, legs)
        status(f"[{mode}] done — {hms(total_s)}", done=True)

    prog.empty()
    return results, mode_graphs, warnings_out, car_unreachable_notes


def run_vrp_optimization(depot, stops, fleet, radius, tsp_method):
    """
    Multi-vehicle routing pipeline (VRP).

    Downloads the OSM network centred on the depot, snaps every stop,
    builds modal graphs, then calls solve_vrp() to partition stops across
    the fleet and find the optimal route per vehicle.

    Returns (vrp_routes, mode_graphs, warnings).
    """
    prog = st.empty()
    warnings_out: list[str] = []

    def status(msg: str, done: bool = False) -> None:
        icon = "✅" if done else "⏳"
        prog.markdown(
            f'<p style="color:#64748b;font-size:.83rem">{icon} {msg}</p>',
            unsafe_allow_html=True,
        )

    # ── Step 1: OSM network ───────────────────────────────────────────────────
    status("Downloading OSM network…")
    G_raw = cached_network_at(depot.lat, depot.lon, radius)
    summary = graph_summary(G_raw)
    status(
        f"Network ready — {summary['nodes']:,} nodes, {summary['edges']:,} edges",
        done=True,
    )

    # ── Step 2: Snap addresses to road nodes ─────────────────────────────────
    status("Snapping addresses to road nodes…")

    def _snap(obj, label: str) -> None:
        if obj.source == "map_click":
            try:
                obj.node_id = nearest_node_safe(G_raw, obj.lat, obj.lon)
            except ValueError:
                obj.node_id = nearest_node(G_raw, obj.lat, obj.lon)
                warnings_out.append(
                    f"⚠️ {label} was clicked slightly outside the network — "
                    f"snapped to the nearest road node."
                )
        else:
            obj.node_id = nearest_node(G_raw, obj.lat, obj.lon)

    _snap(depot, "Depot")
    for idx, stop in enumerate(stops, 1):
        _snap(stop, f"Stop #{idx}")

    if depot.node_id is None:
        raise RuntimeError("Depot could not be snapped to the road network.")

    valid_stops = [s for s in stops if s.node_id is not None]
    if len(valid_stops) < len(stops):
        warnings_out.append(
            f"⚠️ {len(stops) - len(valid_stops)} stop(s) could not be snapped "
            f"and will be skipped."
        )
    if not valid_stops:
        raise RuntimeError("No delivery stops could be snapped to the road network.")

    status(f"Snapped {len(valid_stops)} stop(s)", done=True)

    # ── Step 3: Modal graphs ──────────────────────────────────────────────────
    status("Building modal travel-time graphs…")
    mode_graphs = cached_mode_graphs_at(depot.lat, depot.lon, radius)
    status("Modal graphs ready", done=True)

    # ── Step 4: Total fleet capacity check ───────────────────────────────────
    total_cap = sum(v.capacity for v in fleet)
    if total_cap < len(valid_stops):
        raise ValueError(
            f"Total fleet capacity ({total_cap}) is less than the number of stops "
            f"({len(valid_stops)}). Increase vehicle capacities or add more vehicles."
        )

    # ── Step 5: Solve VRP ─────────────────────────────────────────────────────
    status(f"Running VRP for {len(fleet)} vehicle(s), {len(valid_stops)} stop(s)…")
    vrp_routes, vrp_warnings = solve_vrp(
        valid_stops, depot, fleet, mode_graphs, tsp_method
    )
    warnings_out.extend(vrp_warnings)

    # ── Step 6: Build leg info for each vehicle route ────────────────────────
    for vr in vrp_routes:
        G = mode_graphs[vr.vehicle.mode]
        label_map = {depot.node_id: "Depot"}
        for idx, s in enumerate(vr.stops, 1):
            label_map[s.node_id] = f"Stop #{idx}"
        legs = []
        cum = 0.0
        for i in range(len(vr.tsp_route) - 1):
            src, dst = vr.tsp_route[i], vr.tsp_route[i + 1]
            try:
                t = nx.shortest_path_length(G, src, dst, weight="travel_time")
            except (nx.NetworkXNoPath, nx.NodeNotFound):
                t = ROUTE_PENALTY
            path = get_full_path(G, src, dst, weight="travel_time")
            dist = leg_dist(G, path)
            cum += t
            legs.append(LegInfo(
                label_map.get(src, f"Node {src}"),
                label_map.get(dst, f"Node {dst}"),
                dist, t, cum,
            ))
        vr.legs = legs

    n_active = len(vrp_routes)
    status(f"VRP solved — {n_active} active vehicle(s)", done=True)
    prog.empty()
    return vrp_routes, mode_graphs, warnings_out


# ══════════════════════════════════════════════════════════════════════════════
#  MAP BUILDERS
# ══════════════════════════════════════════════════════════════════════════════

MODE_META = {
    "drive": {"icon": "🚗", "colour": "#e74c3c", "label": "Car"},
    "bike":  {"icon": "🚲", "colour": "#2ecc71", "label": "Bicycle"},
    "walk":  {"icon": "🚶", "colour": "#3b82f6", "label": "Walking"},
}


def _stop_div_icon(idx: int) -> folium.DivIcon:
    return folium.DivIcon(
        html=(f'<div style="background:#dc2626;color:white;border-radius:50%;'
              f'width:32px;height:32px;line-height:32px;text-align:center;'
              f'font-weight:700;font-size:13px;font-family:Outfit,sans-serif;'
              f'border:3px solid white;box-shadow:0 2px 8px rgba(0,0,0,.4)">'
              f'{idx}</div>'),
        icon_size=(32, 32), icon_anchor=(16, 16),
    )


def build_selection_map(city, depot, stops) -> folium.Map:
    """Pre-optimisation map: green depot + red numbered stops."""
    centre = CITY_CENTRES.get(city, (48.8566, 2.3522))
    zoom = 13
    if depot:
        centre = (depot.lat, depot.lon)
        zoom = 14

    fmap = folium.Map(location=list(centre), zoom_start=zoom, tiles="CartoDB positron")

    if depot:
        folium.Marker(
            location=[depot.lat, depot.lon],
            tooltip="<b>📦 Depot</b>",
            popup=folium.Popup(f"<b>Depot</b><br><small>{depot.address}</small>", max_width=240),
            icon=folium.Icon(color="green", icon="home", prefix="fa"),
        ).add_to(fmap)

    for i, stop in enumerate(stops, 1):
        src_icon = "🖱" if stop.source == "map_click" else "✏️"
        folium.Marker(
            location=[stop.lat, stop.lon],
            tooltip=f"<b>Stop #{i}</b> {src_icon}",
            popup=folium.Popup(f"<b>Stop #{i}</b><br><small>{stop.address}</small>", max_width=260),
            icon=_stop_div_icon(i),
        ).add_to(fmap)

    return fmap


def build_result_map(mode_graphs, results, depot, stops) -> folium.Map:
    """Post-optimisation map: animated routes + coloured markers."""
    G_ref = mode_graphs["drive"]
    fmap = folium.Map(location=[depot.lat, depot.lon], zoom_start=14, tiles="CartoDB positron")

    for mode, res in results.items():
        meta = MODE_META[mode]
        coords = [n_coords(G_ref, n) for n in res.full_route]
        layer = folium.FeatureGroup(
            name=f"{meta['icon']} {meta['label']} — {hms(res.total_time_s)}",
            show=(mode == "drive"),
        )
        AntPath(locations=coords, color=meta["colour"], weight=5, opacity=0.85,
                delay=600, dash_array=[20, 35], pulse_color="#fff",
                tooltip=f"{meta['label']} · {hms(res.total_time_s)}").add_to(layer)
        layer.add_to(fmap)

    # Depot — GREEN home icon
    folium.Marker(
        location=[depot.lat, depot.lon],
        tooltip="<b>📦 Depot (Start &amp; End)</b>",
        popup=folium.Popup(f"<b>Depot</b><br><small>{depot.address}</small>", max_width=260),
        icon=folium.Icon(color="green", icon="home", prefix="fa"),
    ).add_to(fmap)

    # Stops in route order — RED numbered (drive uses stop_visit_order when set)
    drive_res = results["drive"]
    stop_layer = folium.FeatureGroup(name="📍 Delivery stops", show=True)
    if getattr(drive_res, "stop_visit_order", None):
        for idx, s in enumerate(drive_res.stop_visit_order, 1):
            folium.Marker(
                location=[s.lat, s.lon],
                tooltip=f"<b>Stop #{idx}</b>",
                popup=folium.Popup(f"<b>Stop #{idx}</b><br>{s.address}", max_width=270),
                icon=_stop_div_icon(idx),
            ).add_to(stop_layer)
    else:
        drive_route = drive_res.tsp_route
        seen, ordered = set(), []
        for n in drive_route[1:-1]:
            if n not in seen:
                seen.add(n)
                ordered.append(n)
        node_to_stop = {s.node_id: s for s in stops}
        for idx, node in enumerate(ordered, 1):
            s = node_to_stop.get(node)
            lat = s.lat if s else n_coords(G_ref, node)[0]
            lon = s.lon if s else n_coords(G_ref, node)[1]
            addr = s.address if s else f"Node {node}"
            folium.Marker(
                location=[lat, lon],
                tooltip=f"<b>Stop #{idx}</b>",
                popup=folium.Popup(f"<b>Stop #{idx}</b><br>{addr}", max_width=270),
                icon=_stop_div_icon(idx),
            ).add_to(stop_layer)
    stop_layer.add_to(fmap)

    folium.LayerControl(collapsed=False, position="topleft").add_to(fmap)
    return fmap


def build_vrp_result_map(mode_graphs, vrp_routes, depot) -> folium.Map:
    """Post-VRP map: one colour-coded animated route per vehicle."""
    fmap = folium.Map(location=[depot.lat, depot.lon], zoom_start=14, tiles="CartoDB positron")

    mode_icons = {"drive": "🚗", "bike": "🚲", "walk": "🚶"}

    for vr in vrp_routes:
        # Use the vehicle's own modal graph for coordinate lookup — bike/walk routes
        # may include nodes absent from the drive graph copy.
        G_veh = mode_graphs[vr.vehicle.mode]
        coords = [n_coords(G_veh, n) for n in vr.full_route]
        icon = mode_icons.get(vr.vehicle.mode, "🚛")
        total_time_label = hms(vr.total_time_s)
        layer = folium.FeatureGroup(
            name=f"{icon} {vr.vehicle.name} — {total_time_label}",
            show=True,
        )
        AntPath(
            locations=coords,
            color=vr.vehicle.color,
            weight=5,
            opacity=0.85,
            delay=600,
            dash_array=[20, 35],
            pulse_color="#fff",
            tooltip=f"{vr.vehicle.name} · {vr.vehicle.mode} · {total_time_label}",
        ).add_to(layer)

        # Stop markers in this vehicle's colour
        stop_layer = folium.FeatureGroup(
            name=f"📍 {vr.vehicle.name} stops",
            show=True,
        )
        for idx, s in enumerate(vr.stops, 1):
            marker_html = (
                f'<div style="background:{vr.vehicle.color};color:white;border-radius:50%;'
                f'width:32px;height:32px;line-height:32px;text-align:center;'
                f'font-weight:700;font-size:13px;font-family:Outfit,sans-serif;'
                f'border:3px solid white;box-shadow:0 2px 8px rgba(0,0,0,.4)">'
                f'{idx}</div>'
            )
            folium.Marker(
                location=[s.lat, s.lon],
                tooltip=f"<b>[{vr.vehicle.name}] Stop #{idx}</b>",
                popup=folium.Popup(
                    f"<b>[{vr.vehicle.name}] Stop #{idx}</b><br>{s.address}",
                    max_width=270,
                ),
                icon=folium.DivIcon(
                    html=marker_html,
                    icon_size=(32, 32),
                    icon_anchor=(16, 16),
                ),
            ).add_to(stop_layer)
        stop_layer.add_to(fmap)
        layer.add_to(fmap)

    # Depot marker — always visible
    folium.Marker(
        location=[depot.lat, depot.lon],
        tooltip="<b>📦 Depot (Start &amp; End)</b>",
        popup=folium.Popup(f"<b>Depot</b><br><small>{depot.address}</small>", max_width=260),
        icon=folium.Icon(color="green", icon="home", prefix="fa"),
    ).add_to(fmap)

    folium.LayerControl(collapsed=False, position="topleft").add_to(fmap)
    return fmap


# ══════════════════════════════════════════════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def hms(s: float) -> str:
    if not math.isfinite(s): return "N/A"
    h, r = divmod(int(s), 3600); m, sc = divmod(r, 60)
    return f"{h}h {m:02d}m {sc:02d}s" if h else f"{m}m {sc:02d}s" if m else f"{sc}s"


def render_stop_table(result: ModeResult) -> str:
    rows = ""
    dep_time = datetime.datetime.now().replace(second=0, microsecond=0)
    for leg in result.legs:
        arr = dep_time + datetime.timedelta(seconds=leg.cumulative_time_s)
        ret = leg.to_label == "Depot"

        if leg.from_label == "Depot":
            fb = '<span class="snum snum-depot">D</span>'
            ft = '<span class="tag tag-depot">Depot</span>'
        else:
            n = leg.from_label.replace("Stop #","")
            fb = f'<span class="snum snum-stop">{n}</span>'
            ft = f'<span class="tag tag-stop">{leg.from_label}</span>'

        if ret:
            tb = '<span class="snum snum-depot">D</span>'
            tt = '<span class="tag tag-return">Return</span>'
        else:
            n = leg.to_label.replace("Stop #","")
            tb = f'<span class="snum snum-stop">{n}</span>'
            tt = f'<span class="tag tag-stop">{leg.to_label}</span>'

        d = f"{leg.distance_m/1000:.2f} km" if leg.distance_m >= 100 else f"{leg.distance_m:.0f} m"
        rows += (f"<tr>"
                 f"<td>{fb}&nbsp;{ft}</td><td>{tb}&nbsp;{tt}</td>"
                 f'<td class="mono">{d}</td>'
                 f'<td class="mono">{hms(leg.travel_time_s)}</td>'
                 f'<td class="mono">{arr.strftime("%H:%M")}</td>'
                 f'<td class="mono">{hms(leg.cumulative_time_s)}</td>'
                 f"</tr>")

    return (f'<table class="stop-table"><thead><tr>'
            f'<th>From</th><th>To</th><th>Distance</th>'
            f'<th>Leg Time</th><th>Est. Arrival</th><th>Elapsed</th>'
            f'</tr></thead><tbody>{rows}</tbody></table>')


# ══════════════════════════════════════════════════════════════════════════════
#  SIDEBAR
# ══════════════════════════════════════════════════════════════════════════════

with st.sidebar:
    st.markdown("""
    <div style="padding:18px 0 6px">
      <div style="font-size:1.3rem;font-weight:800;color:#f0f9ff">📦 DeliveryIQ</div>
      <div style="font-size:.68rem;color:#334155;letter-spacing:.5px">Route Optimizer · v3.0</div>
    </div>
    <hr style="border-color:#161d2e;margin:6px 0 14px">
    """, unsafe_allow_html=True)

    # ── City lock ─────────────────────────────────────────────────────────────
    st.markdown('<div style="font-size:.61rem;font-weight:700;letter-spacing:1.2px;color:#475569;text-transform:uppercase;margin-bottom:5px">🌆 City Lock</div>', unsafe_allow_html=True)
    city_opts = list(CITY_CENTRES.keys()) + ["Custom…"]
    sel_city = st.selectbox("city_sel", city_opts,
                            index=city_opts.index(st.session_state.city)
                            if st.session_state.city in city_opts else 0,
                            label_visibility="collapsed")
    if sel_city == "Custom…":
        custom = st.text_input("custom_city", placeholder="e.g. Barcelona, Spain",
                               label_visibility="collapsed")
        if custom.strip():
            sel_city = custom.strip()
    if sel_city != "Custom…" and sel_city != st.session_state.city:
        st.session_state.update(city=sel_city, depot=None, stops=[],
                                opt_results=None, opt_graphs=None, last_click=None,
                                opt_vrp_results=None, opt_vrp_warnings=[])
        st.rerun()

    city = st.session_state.city

    # ── Network settings ──────────────────────────────────────────────────────
    st.markdown('<div style="font-size:.61rem;font-weight:700;letter-spacing:1.2px;color:#475569;text-transform:uppercase;margin:12px 0 5px">⚙️ Settings</div>', unsafe_allow_html=True)
    radius = st.slider("Radius (m)", 2000, 8000, 4000, 500)
    tsp_method = st.selectbox("TSP solver",
                              ["auto", "christofides", "2opt", "genetic", "nn"])

    st.markdown('<hr style="border-color:#161d2e;margin:14px 0">', unsafe_allow_html=True)

    # ── Depot ─────────────────────────────────────────────────────────────────
    st.markdown('<div style="font-size:.61rem;font-weight:700;letter-spacing:1.2px;color:#475569;text-transform:uppercase;margin-bottom:5px">🏢 Depot Address</div>', unsafe_allow_html=True)
    dc1, dc2 = st.columns([4, 1])
    with dc1:
        depot_txt = st.text_input("dep_txt",
                                  placeholder=f"Street in {city.split(',')[0]}…",
                                  label_visibility="collapsed")
    with dc2:
        dep_go = st.button("➜", key="dep_go")
    if dep_go and depot_txt.strip():
        with st.spinner("Geocoding…"):
            r = forward_geocode(depot_txt.strip(), city)
        if r:
            st.session_state.depot = r
            st.session_state.opt_results = None
            st.session_state.opt_vrp_results = None
            st.rerun()
        else:
            st.error(f"Not found in {city}")

    if st.session_state.depot:
        dep = st.session_state.depot
        st.markdown(f"""
        <div class="delivery-item depot-item">
          <div class="di-num depot">D</div>
          <div>
            <div class="di-addr">{dep.address[:65]}{"…" if len(dep.address)>65 else ""}</div>
            <div class="di-coords">{dep.lat:.5f}, {dep.lon:.5f}</div>
          </div>
        </div>""", unsafe_allow_html=True)
        if st.button("✕ Clear depot", key="clr_dep"):
            st.session_state.depot = None
            st.session_state.opt_results = None
            st.session_state.opt_vrp_results = None
            st.rerun()

    st.markdown('<hr style="border-color:#161d2e;margin:12px 0">', unsafe_allow_html=True)

    # ── Add stop by typing ────────────────────────────────────────────────────
    st.markdown('<div style="font-size:.61rem;font-weight:700;letter-spacing:1.2px;color:#475569;text-transform:uppercase;margin-bottom:5px">📍 Add Delivery Stop</div>', unsafe_allow_html=True)
    sc1, sc2 = st.columns([4, 1])
    with sc1:
        stop_txt = st.text_input("stop_txt",
                                 placeholder=f"Address in {city.split(',')[0]}…",
                                 label_visibility="collapsed")
    with sc2:
        stop_go = st.button("➜", key="stop_go")
    if stop_go and stop_txt.strip():
        with st.spinner("Geocoding…"):
            r = forward_geocode(stop_txt.strip(), city)
        if r:
            st.session_state.stops.append(r)
            st.session_state.opt_results = None
            st.session_state.opt_vrp_results = None
            st.rerun()
        else:
            st.error(f"Not found in {city}")

    # ── Click-to-add toggle ───────────────────────────────────────────────────
    click_label = "🖱 Disable Map Click" if st.session_state.click_mode else "🖱 Enable Map Click"
    if st.button(click_label, key="click_tog", use_container_width=True):
        st.session_state.click_mode = not st.session_state.click_mode
        st.rerun()

    if st.session_state.click_mode:
        target = "depot" if st.session_state.depot is None else "stop"
        st.markdown(f"""
        <div class="click-active">
          🖱 Click Mode <b>ON</b><br>
          <span style="font-size:.75rem">Next click adds a <b>{target}</b></span>
        </div>""", unsafe_allow_html=True)

    st.markdown('<hr style="border-color:#161d2e;margin:12px 0">', unsafe_allow_html=True)

    # ── Selected deliveries list ──────────────────────────────────────────────
    n_stops = len(st.session_state.stops)
    st.markdown(
        f'<div style="font-size:.61rem;font-weight:700;letter-spacing:1.2px;'
        f'color:#475569;text-transform:uppercase;margin-bottom:7px">'
        f'📋 Selected Deliveries ({n_stops})</div>',
        unsafe_allow_html=True)

    if n_stops == 0:
        st.markdown('<div style="font-size:.74rem;color:#374151;padding:4px 0">No stops yet.</div>',
                    unsafe_allow_html=True)
    else:
        for i, stop in enumerate(st.session_state.stops):
            src = "🖱" if stop.source == "map_click" else "✏️"
            short = stop.address[:52] + ("…" if len(stop.address) > 52 else "")
            st.markdown(f"""
            <div class="delivery-item">
              <div class="di-num">{i+1}</div>
              <div style="flex:1">
                <div class="di-addr">{src} {short}</div>
                <div class="di-coords">{stop.lat:.5f}, {stop.lon:.5f}</div>
              </div>
            </div>""", unsafe_allow_html=True)
            if st.button("✕", key=f"rm_{i}", help=f"Remove stop #{i+1}"):
                st.session_state.stops.pop(i)
                st.session_state.opt_results = None
                st.session_state.opt_vrp_results = None
                st.rerun()

    st.markdown('<hr style="border-color:#161d2e;margin:12px 0">', unsafe_allow_html=True)

    # ── Run button ────────────────────────────────────────────────────────────
    can_run = st.session_state.depot is not None and len(st.session_state.stops) >= 1
    run_btn = st.button(
        "🚀  Optimize Routes",
        disabled=not can_run,
        use_container_width=True,
        help="Add a depot + at least 1 stop first" if not can_run else "Run route optimization",
    )

    st.markdown("""
    <div style="font-size:.63rem;color:#1e293b;line-height:1.8;margin-top:8px">
      <b style="color:#334155">Algorithms</b><br>Dijkstra · Christofides · 2-opt · Genetic<br>
      <b style="color:#334155">Geocoder</b><br>Nominatim (city-locked)<br>
      <b style="color:#334155">Data</b><br>OpenStreetMap / OSMnx
    </div>""", unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN CONTENT
# ══════════════════════════════════════════════════════════════════════════════

city  = st.session_state.city
depot = st.session_state.depot
stops = st.session_state.stops
fleet = st.session_state.fleet

st.markdown(f"""
<div class="page-header">
  <div style="font-size:2.5rem">📦</div>
  <div>
    <h1>DeliveryIQ · Route Optimizer</h1>
    <p>City-locked routing · Click-to-Add markers · Real OSM street data · 3 travel modes</p>
    <div class="city-pill">📍 Locked to: {city}</div>
  </div>
</div>""", unsafe_allow_html=True)

map_tab, fleet_tab = st.tabs(["🗺️ Map & Optimize", "🚛 Fleet Settings"])

# ══════════════════════════════════════════════════════════════════════════════
#  MAP & OPTIMIZE TAB
# ══════════════════════════════════════════════════════════════════════════════

with map_tab:

    # ── Coordinator map (shown when no results yet) ───────────────────────────
    no_results = (
        st.session_state.opt_results is None
        and st.session_state.opt_vrp_results is None
    )
    if no_results:
        st.markdown('<div class="sec-head">🗺️ Coordinator Map — Build Your Delivery List</div>',
                    unsafe_allow_html=True)

        if st.session_state.click_mode:
            target_hint = "depot location" if depot is None else "delivery stop"
            st.markdown(f"""
            <div class="click-active" style="text-align:left;padding:10px 18px;margin-bottom:10px">
              🖱 <b>Click Mode Active</b> — click the map to place a
              <b>{target_hint}</b>. Coordinates are reverse-geocoded automatically.
            </div>""", unsafe_allow_html=True)
        elif depot is None:
            st.markdown("""
            <div class="info-box">
              👈 Set a <b>depot</b> in the sidebar or enable <b>Map Click</b> to place it
              on the map, then add delivery stops to begin.
            </div>""", unsafe_allow_html=True)

        sel_map = build_selection_map(city, depot, stops)
        map_output = st_folium(
            sel_map,
            width="100%",
            height=510,
            returned_objects=["last_clicked"],
            key="coord_map",
        )

        if (
            st.session_state.click_mode
            and map_output
            and map_output.get("last_clicked")
        ):
            raw = map_output["last_clicked"]
            ck = (round(raw["lat"], 5), round(raw["lng"], 5))
            if ck != st.session_state.last_click:
                st.session_state.last_click = ck
                with st.spinner("Reverse geocoding…"):
                    new = reverse_geocode(raw["lat"], raw["lng"])
                if new:
                    if st.session_state.depot is None:
                        new.source = "map_click"
                        st.session_state.depot = new
                    else:
                        st.session_state.stops.append(new)
                    st.session_state.opt_results = None
                    st.session_state.opt_vrp_results = None
                    st.rerun()

    # ── Run optimisation ──────────────────────────────────────────────────────
    if run_btn and can_run:
        st.markdown('<div class="sec-head">⏳ Running Optimization</div>', unsafe_allow_html=True)
        if len(st.session_state.fleet) >= 2:
            # VRP mode — multi-vehicle fleet
            try:
                vrp_routes, mg, vrp_warnings = run_vrp_optimization(
                    depot, stops, st.session_state.fleet, radius, tsp_method
                )
                st.session_state.opt_vrp_results = vrp_routes
                st.session_state.opt_graphs      = mg
                st.session_state.opt_vrp_warnings = vrp_warnings
                st.session_state.opt_results     = None
                st.rerun()
            except ValueError as e:
                st.error(f"Fleet configuration error: {e}")
            except Exception as e:
                st.error(f"VRP optimization failed: {e}")
                st.exception(e)
        else:
            # Single-vehicle mode — existing 3-mode comparison
            try:
                res, mg, warnings, car_unreachable = run_optimization(
                    depot, stops, city, radius, tsp_method
                )
                st.session_state.opt_results         = res
                st.session_state.opt_graphs          = mg
                st.session_state.opt_warnings        = warnings
                st.session_state.opt_car_unreachable = car_unreachable
                st.session_state.opt_vrp_results     = None
                st.rerun()
            except Exception as e:
                st.error(f"Optimization failed: {e}")
                st.exception(e)

    # ── Single-vehicle results dashboard ─────────────────────────────────────
    if st.session_state.opt_results is not None:
        results     = st.session_state.opt_results
        mode_graphs = st.session_state.opt_graphs
        warnings    = st.session_state.get("opt_warnings", [])
        car_unreachable_notes = st.session_state.get("opt_car_unreachable", [])
        best_mode   = min(results, key=lambda m: results[m].total_time_s)

        if warnings:
            st.markdown('<div class="sec-head">⚠️ Connectivity Warnings</div>',
                        unsafe_allow_html=True)
            for w in warnings:
                st.markdown(f'<div class="warn-box">{w}</div>', unsafe_allow_html=True)

        st.markdown('<div class="sec-head">📊 Mode Comparison</div>', unsafe_allow_html=True)
        cols = st.columns(3)
        for col, mode in zip(cols, ["drive", "bike", "walk"]):
            res  = results[mode]
            meta = MODE_META[mode]
            win  = mode == best_mode
            tdist = sum(l.distance_m for l in res.legs)
            badge = '<div class="winner-badge">⚡ Most Efficient</div>' if win else ""
            col.markdown(f"""
            <div class="metric-card {'winner' if win else ''}">
              <div class="m-icon">{meta['icon']}</div>
              <div class="m-mode">{meta['label']}</div>
              <div class="m-time">{hms(res.total_time_s)}</div>
              <div class="m-sub">{tdist/1000:.1f} km · {SPEED_KMH[mode]:.0f} km/h · {len(stops)} stop{"s" if len(stops)!=1 else ""}</div>
              {badge}
            </div>""", unsafe_allow_html=True)

        st.markdown('<div class="sec-head">🗺️ Optimized Routes</div>', unsafe_allow_html=True)
        st.markdown("""
        <div class="info-box">
          <b>Layer control</b> (top-left) toggles Car / Bike / Walk routes.
          🟢 Green = Depot &nbsp;·&nbsp; 🔴 Red numbers = Delivery stops in optimized order.
        </div>""", unsafe_allow_html=True)
        rmap = build_result_map(mode_graphs, results, depot, stops)
        st_folium(rmap, width="100%", height=530, returned_objects=[], key="result_map")

        st.markdown('<div class="sec-head">📋 Detailed Stop Breakdown</div>',
                    unsafe_allow_html=True)
        t1, t2, t3 = st.tabs(["🚗  Car", "🚲  Bike", "🚶  Walk"])
        for tab, mode in zip([t1, t2, t3], ["drive", "bike", "walk"]):
            with tab:
                res = results[mode]
                if mode == "drive" and car_unreachable_notes:
                    st.markdown(
                        '<div class="warn-box"><strong>⚠️ Unreachable by car (last-meter)</strong><br>'
                        + "<br>".join(car_unreachable_notes)
                        + "</div>",
                        unsafe_allow_html=True,
                    )
                tdist = sum(l.distance_m for l in res.legs)
                c1, c2, c3 = st.columns(3)
                c1.metric("Total Time",     hms(res.total_time_s))
                c2.metric("Total Distance", f"{tdist/1000:.2f} km")
                c3.metric("Avg Speed",      f"{SPEED_KMH[mode]:.0f} km/h")
                st.markdown(render_stop_table(res), unsafe_allow_html=True)

        st.markdown('<div style="height:14px"></div>', unsafe_allow_html=True)
        ca, cb = st.columns([3, 1])
        with ca:
            st.markdown("""
            <div class="warn-box">
              🔄 Add more stops via the sidebar or map click, then press
              <b>Optimize Routes</b> again. The OSM network is cached automatically.
            </div>""", unsafe_allow_html=True)
        with cb:
            if st.button("🗑 Clear & Restart", key="clear_single", use_container_width=True):
                st.session_state.update(
                    opt_results=None, opt_graphs=None, opt_warnings=[],
                    opt_car_unreachable=[], opt_vrp_results=None, opt_vrp_warnings=[],
                )
                st.rerun()

    # ── VRP results dashboard ─────────────────────────────────────────────────
    elif st.session_state.opt_vrp_results is not None:
        vrp_routes  = st.session_state.opt_vrp_results
        mode_graphs = st.session_state.opt_graphs
        vrp_warnings = st.session_state.get("opt_vrp_warnings", [])

        if vrp_warnings:
            st.markdown('<div class="sec-head">⚠️ Routing Warnings</div>',
                        unsafe_allow_html=True)
            for w in vrp_warnings:
                st.markdown(f'<div class="warn-box">{w}</div>', unsafe_allow_html=True)

        # Per-vehicle summary cards
        st.markdown('<div class="sec-head">🚛 Fleet Summary</div>', unsafe_allow_html=True)
        mode_icons = {"drive": "🚗", "bike": "🚲", "walk": "🚶"}
        n_cols = min(len(vrp_routes), 4)
        vcols = st.columns(n_cols)
        for col, vr in zip(vcols, vrp_routes):
            icon = mode_icons.get(vr.vehicle.mode, "🚛")
            n_stops = len(vr.stops)
            col.markdown(f"""
            <div class="metric-card" style="border-top:4px solid {vr.vehicle.color}">
              <div class="m-icon">{icon}</div>
              <div class="m-mode">{vr.vehicle.name}</div>
              <div class="m-time">{hms(vr.total_time_s)}</div>
              <div class="m-sub">
                {vr.total_dist_m/1000:.1f} km · {n_stops} stop{"s" if n_stops != 1 else ""}
                · {vr.vehicle.mode}
              </div>
            </div>""", unsafe_allow_html=True)

        # Multi-vehicle map
        st.markdown('<div class="sec-head">🗺️ Vehicle Routes</div>', unsafe_allow_html=True)
        st.markdown("""
        <div class="info-box">
          <b>Layer control</b> (top-left) toggles individual vehicle routes and stop markers.
          🟢 Green = Depot &nbsp;·&nbsp; Numbered markers show stop visit order per vehicle.
        </div>""", unsafe_allow_html=True)
        vmap = build_vrp_result_map(mode_graphs, vrp_routes, depot)
        st_folium(vmap, width="100%", height=560, returned_objects=[], key="vrp_result_map")

        # Per-vehicle stop breakdown expanders
        st.markdown('<div class="sec-head">📋 Per-Vehicle Stop Breakdown</div>',
                    unsafe_allow_html=True)
        for vr in vrp_routes:
            icon = mode_icons.get(vr.vehicle.mode, "🚛")
            n_s = len(vr.stops)
            with st.expander(
                f"{icon} {vr.vehicle.name} — {n_s} stop{'s' if n_s != 1 else ''} · {hms(vr.total_time_s)}",
                expanded=False,
            ):
                tdist = vr.total_dist_m
                c1, c2, c3 = st.columns(3)
                c1.metric("Total Time",     hms(vr.total_time_s))
                c2.metric("Total Distance", f"{tdist/1000:.2f} km")
                c3.metric("Mode",           vr.vehicle.mode.capitalize())
                if vr.legs:
                    # Reuse existing render_stop_table by wrapping in a ModeResult-like object
                    pseudo = ModeResult(vr.vehicle.mode, vr.tsp_route, vr.full_route,
                                        vr.total_time_s, vr.legs)
                    st.markdown(render_stop_table(pseudo), unsafe_allow_html=True)

        st.markdown('<div style="height:14px"></div>', unsafe_allow_html=True)
        ca, cb = st.columns([3, 1])
        with ca:
            st.markdown("""
            <div class="warn-box">
              🔄 Add more stops via the sidebar or adjust the fleet in
              <b>Fleet Settings</b>, then press <b>Optimize Routes</b> again.
            </div>""", unsafe_allow_html=True)
        with cb:
            if st.button("🗑 Clear & Restart", key="clear_vrp", use_container_width=True):
                st.session_state.update(
                    opt_results=None, opt_graphs=None, opt_warnings=[],
                    opt_car_unreachable=[], opt_vrp_results=None, opt_vrp_warnings=[],
                )
                st.rerun()

# ══════════════════════════════════════════════════════════════════════════════
#  FLEET SETTINGS TAB
# ══════════════════════════════════════════════════════════════════════════════

with fleet_tab:
    st.markdown('<div class="sec-head">🚛 Fleet Configuration</div>', unsafe_allow_html=True)

    n_vehicles = len(fleet)
    mode_badge = {"drive": "🚗 Drive", "bike": "🚲 Bike", "walk": "🚶 Walk"}
    if n_vehicles >= 2:
        st.markdown(
            f'<div class="info-box">Fleet has <b>{n_vehicles} vehicles</b> — '
            f'<b>Optimize Routes</b> will use <b>multi-vehicle VRP mode</b>.</div>',
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            '<div class="info-box">Fleet has <b>1 vehicle</b> — '
            '<b>Optimize Routes</b> uses single-vehicle mode (3-mode comparison). '
            'Add a second vehicle to enable VRP.</div>',
            unsafe_allow_html=True,
        )

    # Edit existing vehicles
    st.markdown("**Edit vehicles:**")
    updated_fleet = list(fleet)
    changed = False
    for i, v in enumerate(fleet):
        c1, c2, c3, c4 = st.columns([2, 2, 1, 0.4])
        with c1:
            new_name = st.text_input("Name", value=v.name, key=f"v_name_{i}",
                                     label_visibility="collapsed")
        with c2:
            mode_opts = ["drive", "bike", "walk"]
            new_mode = st.selectbox("Mode", mode_opts,
                                    index=mode_opts.index(v.mode),
                                    key=f"v_mode_{i}",
                                    label_visibility="collapsed")
        with c3:
            new_cap = st.number_input("Cap", min_value=1, max_value=500,
                                      value=v.capacity, step=1,
                                      key=f"v_cap_{i}",
                                      label_visibility="collapsed")
        with c4:
            rm = st.button("✕", key=f"v_rm_{i}",
                           disabled=(len(fleet) <= 1),
                           help="Remove this vehicle")

        if rm:
            updated_fleet.pop(i)
            # Reassign colours in order
            for j, u in enumerate(updated_fleet):
                updated_fleet[j] = Vehicle(u.name, u.mode, u.capacity,
                                           VEHICLE_COLORS[j % len(VEHICLE_COLORS)])
            st.session_state.fleet = updated_fleet
            st.session_state.opt_vrp_results = None
            st.session_state.opt_results = None
            st.rerun()

        if new_name != v.name or new_mode != v.mode or int(new_cap) != v.capacity:
            updated_fleet[i] = Vehicle(new_name, new_mode, int(new_cap),
                                       VEHICLE_COLORS[i % len(VEHICLE_COLORS)])
            changed = True

    if changed:
        st.session_state.fleet = updated_fleet
        st.session_state.opt_vrp_results = None
        st.session_state.opt_results = None

    st.markdown('<div style="height:8px"></div>', unsafe_allow_html=True)
    col_add, _ = st.columns([1, 3])
    with col_add:
        if st.button("＋ Add Vehicle", use_container_width=True):
            new_idx = len(st.session_state.fleet)
            st.session_state.fleet.append(
                Vehicle(f"Vehicle {new_idx + 1}", "drive", 50,
                        VEHICLE_COLORS[new_idx % len(VEHICLE_COLORS)])
            )
            st.session_state.opt_vrp_results = None
            st.rerun()

    # Fleet summary table
    if fleet:
        st.markdown('<div style="height:16px"></div>', unsafe_allow_html=True)
        st.markdown("**Current fleet:**")
        header = "| # | Vehicle | Mode | Max Stops | Colour |"
        sep    = "|---|---|---|---|---|"
        rows = [header, sep]
        for i, v in enumerate(st.session_state.fleet, 1):
            swatch = f'<span style="color:{v.color}">■</span>'
            rows.append(f"| {i} | {v.name} | {mode_badge.get(v.mode, v.mode)} | {v.capacity} | {swatch} |")
        st.markdown("\n".join(rows), unsafe_allow_html=True)
