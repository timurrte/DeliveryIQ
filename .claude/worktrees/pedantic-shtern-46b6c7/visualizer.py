"""
visualizer.py
Renders an interactive Folium map for all three travel modes.
"""
from __future__ import annotations
import logging, math
from pathlib import Path
import folium
from folium.plugins import AntPath
import networkx as nx
from graph_builder import SPEED_KMH

logger = logging.getLogger(__name__)
MODE_COLOURS = {"drive": "#E74C3C", "bike": "#2ECC71", "walk": "#3498DB"}


def _nc(G, n):
    d = G.nodes[n]; return d["y"], d["x"]

def _hms(s):
    if not math.isfinite(s): return "N/A"
    h, r = divmod(int(s), 3600); m, sc = divmod(r, 60)
    return (f"{h}h {m:02d}m {sc:02d}s" if h else f"{m}m {sc:02d}s" if m else f"{sc}s")


def build_map(mode_graphs, mode_routes, mode_times, depot_node,
              stop_nodes, output_path="delivery_map.html", default_mode="drive"):
    op = Path(output_path)
    G = mode_graphs[default_mode]
    dlat, dlon = _nc(G, depot_node)
    fm = folium.Map(location=[dlat, dlon], zoom_start=14, tiles="CartoDB positron")

    for mode, route in mode_routes.items():
        coords = [_nc(G, n) for n in route]
        lg = folium.FeatureGroup(name=f"{mode.capitalize()} route", show=(mode == default_mode))
        AntPath(locations=coords, color=MODE_COLOURS[mode], weight=5, opacity=0.85,
                delay=800, dash_array=[20, 30], pulse_color="#FFFFFF",
                tooltip=f"{mode} - {_hms(mode_times[mode])}").add_to(lg)
        lg.add_to(fm)

    folium.Marker([dlat, dlon], tooltip="Depot",
                  icon=folium.Icon(color="darkblue", icon="home", prefix="fa")).add_to(fm)

    sl = folium.FeatureGroup(name="Stops", show=True)
    for i, node in enumerate(stop_nodes, 1):
        lat, lon = _nc(G, node)
        folium.Marker([lat, lon], tooltip=f"Stop #{i}",
                      icon=folium.DivIcon(
                          html=f'<div style="background:#E74C3C;color:white;border-radius:50%;'
                               f'width:28px;height:28px;line-height:28px;text-align:center;'
                               f'font-weight:bold;border:2px solid white">{i}</div>',
                          icon_size=(28, 28), icon_anchor=(14, 14))).add_to(sl)
    sl.add_to(fm)

    rows = "".join(
        f'<tr><td style="padding:4px 8px"><span style="color:{MODE_COLOURS[m]}">&#11044;</span>'
        f' <b>{m.capitalize()}</b></td>'
        f'<td style="padding:4px 8px;text-align:right">{SPEED_KMH[m]:.0f} km/h</td>'
        f'<td style="padding:4px 8px;text-align:right"><b>{_hms(mode_times.get(m,float("inf")))}</b></td></tr>'
        for m in ("drive", "bike", "walk"))
    panel = (
        '<div style="position:fixed;top:16px;right:16px;background:rgba(255,255,255,0.95);'
        'border-radius:10px;padding:14px 18px;box-shadow:0 2px 12px rgba(0,0,0,.25);z-index:9999;'
        'font-family:sans-serif;font-size:14px;min-width:240px">'
        f'<b style="font-size:16px">Delivery Summary</b><br>'
        f'<span style="font-size:12px;color:#666">{len(stop_nodes)} stop(s) - round-trip from depot</span>'
        '<table style="width:100%;border-collapse:collapse;margin-top:8px">'
        '<tr style="font-size:12px;color:#888"><th>Mode</th><th>Speed</th><th>Time</th></tr>'
        f'{rows}</table></div>')
    fm.get_root().html.add_child(folium.Element(panel))
    folium.LayerControl(collapsed=False).add_to(fm)
    fm.save(str(op))
    logger.info("Map saved -> %s", op.resolve())
    return op
