"""
app.py — Delivery Route Optimizer · Streamlit Dashboard
Run with:  streamlit run app.py
"""

from __future__ import annotations

import math
import sys
import os
import time
import datetime
import logging
from dataclasses import dataclass, field

import folium
import networkx as nx
import streamlit as st
from streamlit_folium import st_folium
from folium.plugins import AntPath

# ── ensure sibling modules are importable ─────────────────────────────────────
sys.path.insert(0, os.path.dirname(__file__))

from graph_builder import get_network, add_travel_times, nearest_node, SPEED_KMH
from geocoder import geocode_all, Location
from route_solver import (
    build_distance_matrix,
    solve_tsp,
    reconstruct_full_route,
    get_full_path,
)

logging.basicConfig(level=logging.INFO)

# ══════════════════════════════════════════════════════════════════════════════
#  PAGE CONFIG & GLOBAL STYLES
# ══════════════════════════════════════════════════════════════════════════════

st.set_page_config(
    page_title="DeliveryIQ · Route Optimizer",
    page_icon="📦",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
  @import url('https://fonts.googleapis.com/css2?family=DM+Mono:wght@400;500&family=DM+Sans:wght@300;400;500;600;700&display=swap');

  html, body, [class*="css"] {
    font-family: 'DM Sans', sans-serif;
  }

  /* ── Dark sidebar ── */
  section[data-testid="stSidebar"] {
    background: #0f1117;
    border-right: 1px solid #1e2130;
  }
  section[data-testid="stSidebar"] * {
    color: #e2e8f0 !important;
  }
  section[data-testid="stSidebar"] .stTextArea textarea,
  section[data-testid="stSidebar"] .stTextInput input,
  section[data-testid="stSidebar"] .stSelectbox select,
  section[data-testid="stSidebar"] .stSlider {
    background: #1a1f2e !important;
    border: 1px solid #2d3348 !important;
    border-radius: 8px !important;
    color: #e2e8f0 !important;
  }

  /* ── Main background ── */
  .main .block-container {
    background: #f8fafc;
    padding-top: 1.5rem;
    padding-bottom: 3rem;
    max-width: 1400px;
  }

  /* ── Header banner ── */
  .app-header {
    background: linear-gradient(135deg, #0f1117 0%, #1a2744 60%, #0f3460 100%);
    border-radius: 16px;
    padding: 28px 36px;
    margin-bottom: 24px;
    display: flex;
    align-items: center;
    gap: 18px;
    box-shadow: 0 4px 24px rgba(15,17,23,0.18);
  }
  .app-header h1 {
    color: #f0f6ff !important;
    font-size: 2rem !important;
    font-weight: 700 !important;
    margin: 0 !important;
    letter-spacing: -0.5px;
  }
  .app-header p {
    color: #7b9bc8 !important;
    margin: 4px 0 0 0 !important;
    font-size: 0.95rem;
  }

  /* ── Metric cards ── */
  .metric-card {
    background: white;
    border-radius: 14px;
    padding: 22px 24px;
    box-shadow: 0 2px 12px rgba(0,0,0,0.07);
    border: 1.5px solid #e8edf5;
    text-align: center;
    transition: transform 0.15s ease, box-shadow 0.15s ease;
    height: 100%;
  }
  .metric-card:hover {
    transform: translateY(-2px);
    box-shadow: 0 6px 20px rgba(0,0,0,0.11);
  }
  .metric-card.winner {
    border-color: #10b981;
    background: linear-gradient(135deg, #f0fdf8 0%, #ecfdf5 100%);
    box-shadow: 0 4px 20px rgba(16,185,129,0.15);
  }
  .metric-icon { font-size: 2rem; margin-bottom: 8px; }
  .metric-mode {
    font-size: 0.72rem;
    font-weight: 600;
    letter-spacing: 1.2px;
    text-transform: uppercase;
    color: #94a3b8;
    margin-bottom: 4px;
  }
  .metric-time {
    font-family: 'DM Mono', monospace;
    font-size: 1.75rem;
    font-weight: 500;
    color: #0f1117;
    line-height: 1.1;
  }
  .metric-sub {
    font-size: 0.78rem;
    color: #64748b;
    margin-top: 6px;
  }
  .winner-badge {
    display: inline-block;
    background: #10b981;
    color: white !important;
    font-size: 0.65rem;
    font-weight: 700;
    letter-spacing: 0.8px;
    text-transform: uppercase;
    padding: 3px 10px;
    border-radius: 20px;
    margin-top: 8px;
  }

  /* ── Section headers ── */
  .section-header {
    font-size: 0.7rem;
    font-weight: 700;
    letter-spacing: 1.5px;
    text-transform: uppercase;
    color: #94a3b8;
    margin: 28px 0 12px 0;
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .section-header::after {
    content: '';
    flex: 1;
    height: 1px;
    background: #e2e8f0;
  }

  /* ── Stop table ── */
  .stop-table {
    width: 100%;
    border-collapse: collapse;
    font-size: 0.875rem;
    background: white;
    border-radius: 12px;
    overflow: hidden;
    box-shadow: 0 2px 12px rgba(0,0,0,0.06);
  }
  .stop-table thead tr {
    background: #0f1117;
    color: #94a3b8 !important;
  }
  .stop-table thead th {
    padding: 12px 16px;
    font-size: 0.68rem;
    font-weight: 600;
    letter-spacing: 1px;
    text-transform: uppercase;
    text-align: left;
    color: #94a3b8;
  }
  .stop-table tbody tr {
    border-bottom: 1px solid #f1f5f9;
    transition: background 0.1s;
  }
  .stop-table tbody tr:hover { background: #f8faff; }
  .stop-table tbody td {
    padding: 12px 16px;
    color: #334155;
    vertical-align: middle;
  }
  .stop-num {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 26px; height: 26px;
    border-radius: 50%;
    font-weight: 700;
    font-size: 0.75rem;
    color: white;
    flex-shrink: 0;
  }
  .stop-depot { background: #1e3a5f; }
  .stop-client { background: #e74c3c; }
  .mono { font-family: 'DM Mono', monospace; font-size: 0.82rem; }
  .tag {
    display: inline-block;
    padding: 2px 10px;
    border-radius: 20px;
    font-size: 0.7rem;
    font-weight: 600;
  }
  .tag-depot { background: #dbeafe; color: #1d4ed8; }
  .tag-stop  { background: #fee2e2; color: #dc2626; }
  .tag-return { background: #f3f4f6; color: #6b7280; }

  /* ── Sidebar run button ── */
  div[data-testid="stSidebar"] .stButton > button {
    width: 100%;
    background: linear-gradient(135deg, #2563eb, #1d4ed8) !important;
    color: white !important;
    border: none !important;
    border-radius: 10px !important;
    padding: 14px !important;
    font-size: 0.95rem !important;
    font-weight: 600 !important;
    letter-spacing: 0.3px;
    box-shadow: 0 4px 14px rgba(37,99,235,0.35) !important;
    transition: all 0.2s !important;
    margin-top: 8px;
  }
  div[data-testid="stSidebar"] .stButton > button:hover {
    background: linear-gradient(135deg, #1d4ed8, #1e40af) !important;
    box-shadow: 0 6px 20px rgba(37,99,235,0.45) !important;
    transform: translateY(-1px);
  }

  /* ── Info / warning boxes ── */
  .info-box {
    background: #eff6ff;
    border: 1px solid #bfdbfe;
    border-left: 4px solid #2563eb;
    border-radius: 8px;
    padding: 14px 16px;
    font-size: 0.875rem;
    color: #1e3a5f;
    margin: 12px 0;
  }
  .warn-box {
    background: #fffbeb;
    border: 1px solid #fde68a;
    border-left: 4px solid #f59e0b;
    border-radius: 8px;
    padding: 14px 16px;
    font-size: 0.875rem;
    color: #78350f;
    margin: 12px 0;
  }

  /* ── Map container ── */
  .map-container {
    border-radius: 14px;
    overflow: hidden;
    box-shadow: 0 4px 20px rgba(0,0,0,0.1);
    border: 1.5px solid #e2e8f0;
  }

  /* ── Progress / status ── */
  .status-step {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 8px 0;
    font-size: 0.875rem;
    color: #334155;
  }
  .step-done  { color: #10b981; font-weight: 600; }
  .step-active { color: #2563eb; font-weight: 600; }

  /* hide streamlit branding */
  #MainMenu, footer, header { visibility: hidden; }
</style>
""", unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
#  DATA CLASSES
# ══════════════════════════════════════════════════════════════════════════════

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
    tsp_route: list[int]
    full_route: list[int]
    total_time_s: float
    legs: list[LegInfo] = field(default_factory=list)


# ══════════════════════════════════════════════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════════════════════════════════════════════

MODE_META = {
    "drive": {"icon": "🚗", "colour": "#e74c3c", "label": "Car",    "ant_colour": "#e74c3c"},
    "bike":  {"icon": "🚲", "colour": "#2ecc71", "label": "Bicycle","ant_colour": "#2ecc71"},
    "walk":  {"icon": "🚶", "colour": "#3498db", "label": "Walking","ant_colour": "#3498db"},
}

def hms(s: float) -> str:
    if not math.isfinite(s): return "N/A"
    h, r = divmod(int(s), 3600); m, sc = divmod(r, 60)
    return f"{h}h {m:02d}m {sc:02d}s" if h else f"{m}m {sc:02d}s" if m else f"{sc}s"

def node_coords(G: nx.MultiDiGraph, n: int) -> tuple[float, float]:
    d = G.nodes[n]; return d["y"], d["x"]

def leg_distance(G: nx.MultiDiGraph, path: list[int]) -> float:
    total = 0.0
    for i in range(len(path) - 1):
        edge_data = G.get_edge_data(path[i], path[i+1])
        if edge_data:
            lengths = [v.get("length", 0) for v in edge_data.values()]
            total += min(lengths)
    return total


# ══════════════════════════════════════════════════════════════════════════════
#  CORE PIPELINE  (cached aggressively)
# ══════════════════════════════════════════════════════════════════════════════

@st.cache_resource(show_spinner=False)
def cached_network(depot_address: str, radius: int):
    return get_network(depot_address, dist=radius)

@st.cache_resource(show_spinner=False)
def cached_mode_graphs(_G):
    return add_travel_times(_G)


def run_pipeline(
    depot_address: str,
    client_addresses: list[str],
    radius: int,
    tsp_method: str,
) -> tuple[dict[str, ModeResult], list[Location], dict]:
    """Full optimization pipeline. Returns (mode_results, locations, mode_graphs)."""

    status = st.empty()

    def log(msg: str, done: bool = False):
        icon = "✅" if done else "⏳"
        status.markdown(f'<div class="status-step">{icon} {msg}</div>', unsafe_allow_html=True)

    # Step 1 — Geocode
    log("Geocoding addresses…")
    all_addresses = [depot_address] + client_addresses
    locations = geocode_all(all_addresses)
    depot_loc = locations[0]
    client_locs = locations[1:]
    log(f"Geocoded {len(all_addresses)} addresses", done=True)

    # Step 2 — Network
    log("Downloading OSM street network (cached after first run)…")
    G_raw = cached_network(depot_address, radius)
    log(f"Network ready — {G_raw.number_of_nodes():,} nodes, {G_raw.number_of_edges():,} edges", done=True)

    # Step 3 — Snap nodes
    log("Snapping addresses to nearest OSM nodes…")
    depot_loc.node_id = nearest_node(G_raw, depot_loc.lat, depot_loc.lon)
    for loc in client_locs:
        loc.node_id = nearest_node(G_raw, loc.lat, loc.lon)
    log("All addresses snapped to graph nodes", done=True)

    all_nodes = [depot_loc.node_id] + [c.node_id for c in client_locs]

    # Step 4 — Weighted graphs
    log("Building travel-time graphs for all 3 modes…")
    mode_graphs = cached_mode_graphs(G_raw)
    log("Travel-time graphs ready", done=True)

    # Step 5 — TSP per mode
    results: dict[str, ModeResult] = {}
    for mode, G_mode in mode_graphs.items():
        log(f"Solving TSP [{mode}] via {tsp_method}…")
        matrix = build_distance_matrix(G_mode, all_nodes, weight="travel_time")
        tsp_route, total_s = solve_tsp(all_nodes, matrix, method=tsp_method)
        full_route = reconstruct_full_route(G_mode, tsp_route, weight="travel_time")

        # Build leg-by-leg breakdown
        legs: list[LegInfo] = []
        node_labels = {depot_loc.node_id: "Depot"}
        for i, cl in enumerate(client_locs, 1):
            node_labels[cl.node_id] = f"Stop #{i}"

        cum = 0.0
        for i in range(len(tsp_route) - 1):
            src, dst = tsp_route[i], tsp_route[i + 1]
            t = matrix.get((src, dst), math.inf)
            path = get_full_path(G_mode, src, dst, weight="travel_time")
            dist = leg_distance(G_mode, path)
            cum += t
            legs.append(LegInfo(
                from_label=node_labels.get(src, f"Node {src}"),
                to_label=node_labels.get(dst, f"Node {dst}"),
                distance_m=dist,
                travel_time_s=t,
                cumulative_time_s=cum,
            ))

        results[mode] = ModeResult(mode, tsp_route, full_route, total_s, legs)
        log(f"[{mode}] optimized — {hms(total_s)}", done=True)

    status.empty()
    return results, locations, mode_graphs


# ══════════════════════════════════════════════════════════════════════════════
#  MAP BUILDER
# ══════════════════════════════════════════════════════════════════════════════

def build_folium_map(
    mode_graphs: dict,
    results: dict[str, ModeResult],
    depot_loc: Location,
    client_locs: list[Location],
) -> folium.Map:
    G_ref = mode_graphs["drive"]
    dlat, dlon = depot_loc.lat, depot_loc.lon
    fmap = folium.Map(location=[dlat, dlon], zoom_start=14, tiles="CartoDB positron")

    # ── Routes ────────────────────────────────────────────────────────────────
    for mode, res in results.items():
        meta = MODE_META[mode]
        coords = [node_coords(G_ref, n) for n in res.full_route]
        layer = folium.FeatureGroup(
            name=f"{meta['icon']} {meta['label']} route — {hms(res.total_time_s)}",
            show=(mode == "drive"),
        )
        AntPath(
            locations=coords,
            color=meta["colour"],
            weight=5,
            opacity=0.85,
            delay=600,
            dash_array=[20, 35],
            pulse_color="#ffffff",
            tooltip=f"{meta['label']} — {hms(res.total_time_s)}",
        ).add_to(layer)
        layer.add_to(fmap)

    # ── Depot marker ──────────────────────────────────────────────────────────
    folium.Marker(
        location=[dlat, dlon],
        tooltip="<b>📦 Depot / Start & End</b>",
        popup=folium.Popup(
            f"<b>Depot</b><br>{depot_loc.address}<br>"
            f"<small>({dlat:.5f}, {dlon:.5f})</small>",
            max_width=250),
        icon=folium.Icon(color="darkblue", icon="home", prefix="fa"),
    ).add_to(fmap)

    # ── Stop markers ─────────────────────────────────────────────────────────
    stop_layer = folium.FeatureGroup(name="📍 Delivery stops", show=True)
    drive_route = results["drive"].tsp_route
    visit_order = [n for n in drive_route if n != results["drive"].tsp_route[0]]
    # deduplicate while preserving order
    seen = set()
    ordered_stops = []
    for n in drive_route[1:-1]:
        if n not in seen:
            seen.add(n)
            ordered_stops.append(n)

    node_to_loc = {cl.node_id: cl for cl in client_locs}

    for idx, node in enumerate(ordered_stops, 1):
        loc = node_to_loc.get(node)
        addr = loc.address if loc else f"OSM node {node}"
        lat = loc.lat if loc else node_coords(mode_graphs["drive"], node)[0]
        lon = loc.lon if loc else node_coords(mode_graphs["drive"], node)[1]

        folium.Marker(
            location=[lat, lon],
            tooltip=f"<b>Stop #{idx}</b><br><small>{addr[:60]}</small>",
            popup=folium.Popup(
                f"<b>Stop #{idx}</b><br>{addr}<br><small>({lat:.5f}, {lon:.5f})</small>",
                max_width=260),
            icon=folium.DivIcon(
                html=f"""
                <div style="background:#e74c3c;color:white;border-radius:50%;
                            width:30px;height:30px;line-height:30px;text-align:center;
                            font-weight:700;font-size:13px;border:2.5px solid white;
                            box-shadow:0 2px 6px rgba(0,0,0,0.35)">{idx}</div>""",
                icon_size=(30, 30), icon_anchor=(15, 15)),
        ).add_to(stop_layer)
    stop_layer.add_to(fmap)

    folium.LayerControl(collapsed=False, position="topleft").add_to(fmap)
    return fmap


# ══════════════════════════════════════════════════════════════════════════════
#  STOP TABLE RENDERER
# ══════════════════════════════════════════════════════════════════════════════

def render_stop_table(result: ModeResult) -> str:
    rows = ""
    departure = datetime.datetime.now().replace(second=0, microsecond=0)

    for i, leg in enumerate(result.legs):
        is_depot_start = i == 0
        is_return = leg.to_label == "Depot"
        arrival_dt = departure + datetime.timedelta(seconds=leg.cumulative_time_s)

        if is_depot_start:
            from_badge = '<span class="stop-num stop-depot">D</span>'
            from_tag = '<span class="tag tag-depot">Depot</span>'
        else:
            from_badge = f'<span class="stop-num stop-client">{i}</span>'
            from_tag = f'<span class="tag tag-stop">Stop #{i}</span>'

        if is_return:
            to_badge = '<span class="stop-num stop-depot">D</span>'
            to_tag = '<span class="tag tag-return">Return</span>'
        else:
            to_badge = f'<span class="stop-num stop-client">{i+1}</span>'
            to_tag = f'<span class="tag tag-stop">Stop #{i+1}</span>'

        dist_str = f"{leg.distance_m/1000:.2f} km" if leg.distance_m >= 100 else f"{leg.distance_m:.0f} m"
        leg_t = hms(leg.travel_time_s)
        arr_str = arrival_dt.strftime("%H:%M")
        cum_str = hms(leg.cumulative_time_s)

        rows += f"""
        <tr>
          <td>{from_badge}&nbsp; {from_tag}</td>
          <td>{to_badge}&nbsp; {to_tag}</td>
          <td class="mono">{dist_str}</td>
          <td class="mono">{leg_t}</td>
          <td class="mono">{arr_str}</td>
          <td class="mono">{cum_str}</td>
        </tr>"""

    return f"""
    <table class="stop-table">
      <thead>
        <tr>
          <th>From</th><th>To</th><th>Distance</th>
          <th>Leg Time</th><th>Est. Arrival</th><th>Elapsed</th>
        </tr>
      </thead>
      <tbody>{rows}</tbody>
    </table>"""


# ══════════════════════════════════════════════════════════════════════════════
#  SIDEBAR
# ══════════════════════════════════════════════════════════════════════════════

with st.sidebar:
    st.markdown("""
    <div style="padding:20px 0 8px 0">
      <div style="font-size:1.4rem;font-weight:700;color:#f0f6ff">📦 DeliveryIQ</div>
      <div style="font-size:0.75rem;color:#4a5568;letter-spacing:0.5px">Route Optimizer · v2.0</div>
    </div>
    <hr style="border-color:#1e2130;margin:8px 0 20px 0">
    """, unsafe_allow_html=True)

    st.markdown('<div style="font-size:0.7rem;font-weight:600;letter-spacing:1px;color:#4a5568;text-transform:uppercase;margin-bottom:6px">🏢 Depot Address</div>', unsafe_allow_html=True)
    depot_input = st.text_input(
        label="depot",
        value="Piazza del Duomo, Milan, Italy",
        label_visibility="collapsed",
        placeholder="e.g. 10 Downing Street, London",
    )

    st.markdown('<div style="font-size:0.7rem;font-weight:600;letter-spacing:1px;color:#4a5568;text-transform:uppercase;margin:14px 0 6px 0">📍 Delivery Addresses</div>', unsafe_allow_html=True)
    stops_input = st.text_area(
        label="stops",
        value=(
            "Castello Sforzesco, Milan, Italy\n"
            "Navigli, Milan, Italy\n"
            "Brera, Milan, Italy\n"
            "Porta Venezia, Milan, Italy\n"
            "Stazione Centrale, Milan, Italy"
        ),
        label_visibility="collapsed",
        placeholder="One address per line…",
        height=180,
    )

    st.markdown('<div style="font-size:0.7rem;font-weight:600;letter-spacing:1px;color:#4a5568;text-transform:uppercase;margin:14px 0 6px 0">⚙️ Settings</div>', unsafe_allow_html=True)

    radius = st.slider("Network radius (m)", 2000, 8000, 4000, step=500,
                        help="OSM download radius around the depot")

    tsp_method = st.selectbox(
        "TSP solver",
        options=["auto", "christofides", "2opt", "genetic", "nn"],
        index=0,
        help="auto = best method chosen by stop count",
    )

    st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)
    run_btn = st.button("🚀  Run Optimization", use_container_width=True)

    st.markdown("""
    <hr style="border-color:#1e2130;margin:20px 0 12px 0">
    <div style="font-size:0.72rem;color:#374151;line-height:1.7">
      <b style="color:#6b7280">Algorithms used</b><br>
      Dijkstra · Christofides · 2-opt · Genetic
      <br><br>
      <b style="color:#6b7280">Data source</b><br>
      OpenStreetMap via OSMnx
    </div>
    """, unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN CONTENT
# ══════════════════════════════════════════════════════════════════════════════

st.markdown("""
<div class="app-header">
  <div style="font-size:2.8rem">📦</div>
  <div>
    <h1>DeliveryIQ · Route Optimizer</h1>
    <p>Multi-modal delivery routing powered by OSM street data, Dijkstra shortest-paths &amp; TSP heuristics</p>
  </div>
</div>
""", unsafe_allow_html=True)

# ── Idle state ────────────────────────────────────────────────────────────────
if not run_btn and "results" not in st.session_state:
    st.markdown("""
    <div class="info-box">
      👈 &nbsp;Configure your <b>depot</b> and <b>delivery stops</b> in the sidebar, then press
      <b>Run Optimization</b> to calculate the fastest routes for Car, Bike, and Walking simultaneously.
    </div>
    """, unsafe_allow_html=True)

    col1, col2, col3 = st.columns(3)
    with col1:
        st.markdown("""
        <div class="metric-card" style="text-align:left">
          <div style="font-size:1.8rem">🗺️</div>
          <div style="font-weight:600;margin:8px 0 4px">Real Street Data</div>
          <div style="font-size:0.82rem;color:#64748b">Uses OpenStreetMap via OSMnx — real roads, one-ways, bike paths, and footways.</div>
        </div>""", unsafe_allow_html=True)
    with col2:
        st.markdown("""
        <div class="metric-card" style="text-align:left">
          <div style="font-size:1.8rem">🧬</div>
          <div style="font-weight:600;margin:8px 0 4px">Smart TSP Solvers</div>
          <div style="font-size:0.82rem;color:#64748b">Christofides, 2-opt refinement, and Genetic Algorithm — auto-selected by problem size.</div>
        </div>""", unsafe_allow_html=True)
    with col3:
        st.markdown("""
        <div class="metric-card" style="text-align:left">
          <div style="font-size:1.8rem">⚡</div>
          <div style="font-weight:600;margin:8px 0 4px">3 Modes at Once</div>
          <div style="font-size:0.82rem;color:#64748b">Car, Bike, and Walking routes calculated simultaneously. Each mode uses its own optimal path.</div>
        </div>""", unsafe_allow_html=True)
    st.stop()


# ── Run pipeline ──────────────────────────────────────────────────────────────
if run_btn:
    client_addresses = [a.strip() for a in stops_input.strip().splitlines() if a.strip()]

    if not depot_input.strip():
        st.error("Please enter a depot address.")
        st.stop()
    if len(client_addresses) < 1:
        st.error("Please enter at least one delivery address.")
        st.stop()

    with st.spinner(""):
        progress_container = st.container()
        with progress_container:
            st.markdown('<div class="section-header">⏳ Optimization in progress</div>', unsafe_allow_html=True)
            try:
                results, locations, mode_graphs = run_pipeline(
                    depot_input.strip(), client_addresses, radius, tsp_method
                )
                st.session_state["results"] = results
                st.session_state["locations"] = locations
                st.session_state["mode_graphs"] = mode_graphs
            except Exception as e:
                st.error(f"❌ Optimization failed: {e}")
                st.exception(e)
                st.stop()


# ── Render results ────────────────────────────────────────────────────────────
if "results" in st.session_state:
    results: dict[str, ModeResult] = st.session_state["results"]
    locations: list[Location] = st.session_state["locations"]
    mode_graphs: dict = st.session_state["mode_graphs"]

    depot_loc = locations[0]
    client_locs = locations[1:]

    best_mode = min(results, key=lambda m: results[m].total_time_s)

    # ── Metric row ────────────────────────────────────────────────────────────
    st.markdown('<div class="section-header">📊 Mode Comparison</div>', unsafe_allow_html=True)
    cols = st.columns(3)
    for col, mode in zip(cols, ["drive", "bike", "walk"]):
        res = results[mode]
        meta = MODE_META[mode]
        is_winner = (mode == best_mode)
        winner_html = '<div class="winner-badge">⚡ Most Efficient</div>' if is_winner else ""
        card_cls = "metric-card winner" if is_winner else "metric-card"

        stops_count = len(client_locs)
        total_dist = sum(leg.distance_m for leg in res.legs)
        dist_str = f"{total_dist/1000:.1f} km"

        col.markdown(f"""
        <div class="{card_cls}">
          <div class="metric-icon">{meta['icon']}</div>
          <div class="metric-mode">{meta['label']}</div>
          <div class="metric-time">{hms(res.total_time_s)}</div>
          <div class="metric-sub">{dist_str} &nbsp;·&nbsp; {SPEED_KMH[mode]:.0f} km/h avg &nbsp;·&nbsp; {stops_count} stop{"s" if stops_count != 1 else ""}</div>
          {winner_html}
        </div>
        """, unsafe_allow_html=True)

    # ── Map ───────────────────────────────────────────────────────────────────
    st.markdown('<div class="section-header">🗺️ Interactive Route Map</div>', unsafe_allow_html=True)
    st.markdown("""
    <div class="info-box" style="margin-bottom:10px">
      Use the <b>layer control</b> (top-left of map) to toggle visibility between Car, Bike, and Walking routes.
      Click any marker for stop details.
    </div>
    """, unsafe_allow_html=True)

    fmap = build_folium_map(mode_graphs, results, depot_loc, client_locs)
    with st.container():
        st_folium(fmap, width="100%", height=520, returned_objects=[])

    # ── Stop tables ───────────────────────────────────────────────────────────
    st.markdown('<div class="section-header">📋 Detailed Stop Breakdown</div>', unsafe_allow_html=True)

    tab_drive, tab_bike, tab_walk = st.tabs(["🚗  Car Route", "🚲  Bike Route", "🚶  Walk Route"])

    for tab, mode in zip([tab_drive, tab_bike, tab_walk], ["drive", "bike", "walk"]):
        with tab:
            res = results[mode]
            meta = MODE_META[mode]
            total_dist = sum(leg.distance_m for leg in res.legs)

            c1, c2, c3 = st.columns(3)
            c1.metric("Total Time", hms(res.total_time_s))
            c2.metric("Total Distance", f"{total_dist/1000:.2f} km")
            c3.metric("Avg Speed", f"{SPEED_KMH[mode]:.0f} km/h")

            st.markdown(render_stop_table(res), unsafe_allow_html=True)

    # ── Re-run nudge ──────────────────────────────────────────────────────────
    st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)
    st.markdown("""
    <div class="warn-box">
      🔄 &nbsp;Change addresses or settings in the sidebar and press <b>Run Optimization</b> again to recalculate.
      The street network is cached — only the TSP is re-run on address changes.
    </div>
    """, unsafe_allow_html=True)
