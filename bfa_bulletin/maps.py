"""Static maps (matplotlib): national vigilance map, daily maps, zoom maps."""
from __future__ import annotations

import logging
from pathlib import Path

import geopandas as gpd
import matplotlib

matplotlib.use("Agg")
import matplotlib.patheffects as pe
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.collections import LineCollection
from matplotlib.colors import ListedColormap
from matplotlib.lines import Line2D
from matplotlib.patches import Patch, Rectangle
from shapely.geometry import box

from .config import Config
from .geodata import GeoLayers
from .hydro import Hydro
from .style import INK

log = logging.getLogger(__name__)

NATIONAL_EXTENT = (-5.75, 9.25, 2.55, 15.25)  # lon0, lat0, lon1, lat1
HALO = [pe.withStroke(linewidth=2.2, foreground="white")]


# ---------------------------------------------------------------- helpers
def level_color(cfg: Config, lvl) -> str:
    try:
        lvl = int(lvl)
    except (TypeError, ValueError):
        lvl = -1
    return cfg["levels"]["colors"][lvl] if 0 <= lvl <= 3 else cfg["levels"]["nodata_color"]


def _setup_ax(ax, extent):
    lon0, lat0, lon1, lat1 = extent
    ax.set_xlim(lon0, lon1)
    ax.set_ylim(lat0, lat1)
    ax.set_aspect(1.0 / np.cos(np.radians((lat0 + lat1) / 2)))
    ax.set_xticks([])
    ax.set_yticks([])
    for s in ax.spines.values():
        s.set_visible(False)


def fit_extent(bounds, aspect: float, margin: float):
    """Expand (minx, miny, maxx, maxy) so that width/height (in km) == aspect."""
    minx, miny, maxx, maxy = bounds
    minx, miny, maxx, maxy = minx - margin, miny - margin, maxx + margin, maxy + margin
    k = np.cos(np.radians((miny + maxy) / 2))
    w, h = (maxx - minx) * k, maxy - miny
    cx, cy = (minx + maxx) / 2, (miny + maxy) / 2
    if w / h < aspect:
        w = h * aspect
    else:
        h = w / aspect
    return (cx - w / k / 2, cy - h / 2, cx + w / k / 2, cy + h / 2)


def _save(fig, path, cfg: Config, **kw):
    # recent geopandas label the axes with the CRS name: maps have no axis labels
    for ax in fig.axes + [c for a in fig.axes for c in a.child_axes]:
        ax.set_xlabel("")
        ax.set_ylabel("")
    fig.savefig(path, dpi=cfg["maps"]["dpi"], **kw)
    plt.close(fig)


def land_color(cfg: Config) -> str:
    return cfg["maps"].get("land_color", "#b9bcb6")


def draw_background(ax, geo: GeoLayers, cfg: Config, extent, fill=True, provinces=False,
                    region_width=0.8):
    """Burkina Faso alone, 'floating' on the page: filled territory, region borders
    in white, no neighbouring countries, no frame."""
    clip = box(*extent).buffer(1)
    ax.set_facecolor("none")
    if len(geo.burkina) and fill:
        geo.burkina.plot(aspect=None, ax=ax, color=land_color(cfg), edgecolor="none", zorder=1.1)
    if provinces and len(geo.provinces):
        geo.provinces.clip(geo.burkina).boundary.plot(aspect=None, ax=ax, color="white",
                                                      linewidth=region_width * 0.4, zorder=1.6)
    if len(geo.regions):
        geo.regions.boundary.plot(aspect=None, ax=ax, color="white", linewidth=region_width, zorder=1.7)
    lakes = geo.lakes.clip(clip) if len(geo.lakes) else geo.lakes
    if len(lakes):
        lakes.plot(aspect=None, ax=ax, color="#bcd7ea", edgecolor="none", zorder=2.5)


def draw_rivers(ax, hydro: Hydro, levels: pd.Series, cfg: Config, width=1.0,
                within=None, faded_outside=False, min_order=0):
    """Rivers coloured by the level of the section of each reach."""
    r = hydro.rivers.copy()
    r["level"] = r["section"].map(levels).fillna(-1).astype(int)
    r = r[r["log_ups"] >= min_order]
    r["lw"] = width * (0.25 + 0.17 * (r["log_ups"] - r["log_ups"].min()))
    r["color"] = [level_color(cfg, v) for v in r["level"]]
    if within is not None:
        inside = r.intersects(within)
        if faded_outside:
            out = r[~inside]
            if len(out):
                out.plot(aspect=None, ax=ax, color=out["color"].tolist(), linewidth=(out["lw"] * 0.7).tolist(),
                         alpha=0.35, zorder=3)
        r = r[inside]
    # draw low levels first so that red reaches are on top
    for lvl in [-1, 0, 1, 2, 3]:
        sub = r[r["level"] == lvl]
        if len(sub):
            sub.plot(aspect=None, ax=ax, color=sub["color"].tolist(), linewidth=sub["lw"].tolist(), zorder=3 + 0.1 * (lvl + 1),
                     capstyle="round", joinstyle="round")


def draw_network(ax, hydro: Hydro, cfg: Config, extent, width=1.0):
    """Thin blue drainage network derived from the rasters (background)."""
    min_cells = int(cfg["maps"].get("network_min_cells", 15))
    if extent[2] - extent[0] < 2:  # small zoom: show finer tributaries
        min_cells = max(3, min_cells // 3)
    try:
        segs, area = hydro.network_segments(min_cells, extent)
    except Exception as exc:  # area raster missing: skip
        log.warning("Drainage network not drawn: %s", exc)
        return
    if not len(segs):
        return
    lw = width * (0.25 + 0.22 * np.log10(area / area.min() + 1) ** 1.3)
    ax.add_collection(LineCollection(segs, linewidths=lw, colors="#6aa3d5", alpha=0.85,
                                     capstyle="round", zorder=2.8))


def draw_alert_points(ax, hydro: Hydro, levels: pd.Series, cfg: Config, size=9):
    """Coloured markers on reporting points in vigilance (>= yellow)."""
    s = hydro.sections
    lv = levels.reindex(s.index).fillna(-1)
    for lvl in (1, 2, 3):
        sub = s[lv == lvl]
        if len(sub):
            ax.scatter(sub.geometry.x, sub.geometry.y, s=size * (0.8 + 0.4 * lvl), c=level_color(cfg, lvl),
                       edgecolors="#2b2b2b", linewidths=0.35, zorder=6 + lvl)


def river_legend_handles(cfg: Config, with_nodata=True):
    h = [Line2D([], [], color=c, lw=2.2, label=l)
         for c, l in zip(cfg["levels"]["colors"], cfg["levels"]["labels"])]
    if with_nodata:
        h.append(Line2D([], [], color=cfg["levels"]["nodata_color"], lw=2.2, label="Pas de données"))
    return h


# ------------------------------------------------------------------- FFGS
def load_ffgs(cfg: Config, geo: GeoLayers, forecast=None, file=None):
    """Return ('vector', GeoDataFrame[level]) / ('raster', (array, extent)) / None."""
    fcfg = cfg.get("ffgs", {})
    file = file or fcfg.get("file")
    if not (fcfg.get("enabled") or file):
        return None
    if not file:
        log.warning("FFGS enabled but no file configured: background not drawn")
        return None
    if str(file).lower() == "demo":
        return "vector", demo_ffgs(geo, forecast)
    path = Path(file)
    if not path.is_absolute():
        path = cfg.base_dir / path
    if not path.exists():
        log.warning("FFGS file %s not found: background not drawn", path)
        return None
    if path.suffix.lower() in (".tif", ".tiff", ".asc", ".txt", ".nc"):
        import rasterio
        with rasterio.open(path) as src:
            arr = src.read(1, masked=True)
            b = src.bounds
        return "raster", (arr, (b.left, b.right, b.bottom, b.top))
    g = gpd.read_file(path).to_crs("EPSG:4326")
    field = fcfg.get("level_field", "level")
    g["level"] = pd.to_numeric(g[field], errors="coerce").fillna(0).clip(0, 3).astype(int)
    return "vector", g


def demo_ffgs(geo: GeoLayers, forecast=None) -> gpd.GeoDataFrame:
    """Synthetic FFGS layer (provinces) only to preview the layout."""
    p = geo.provinces if len(geo.provinces) else geo.admin1
    rng = np.random.default_rng(int(forecast.run.strftime("%Y%m%d")) if forecast else 0)
    lv = rng.choice([0, 0, 0, 0, 1, 1, 2, 3], size=len(p))
    return gpd.GeoDataFrame({"level": lv}, geometry=p.geometry.values, crs=p.crs)


def draw_ffgs(ax, ffgs, cfg: Config, geo: GeoLayers):
    kind, data = ffgs
    colors = cfg["ffgs"]["colors"]
    if kind == "vector":
        g = data.clip(geo.burkina) if len(geo.burkina) else data
        for lvl in range(4):
            sub = g[g["level"] == lvl]
            if len(sub):
                sub.plot(aspect=None, ax=ax, color=colors[lvl], edgecolor="white", linewidth=0.3, zorder=1.2)
    else:
        arr, ext = data
        im = ax.imshow(np.ma.masked_less(arr, 0), extent=ext, cmap=ListedColormap(colors),
                       vmin=-0.5, vmax=3.5, interpolation="nearest", zorder=1.2)
        if len(geo.burkina):
            from matplotlib.patches import PathPatch
            from matplotlib.path import Path as MPath
            geom = geo.burkina.geometry.iloc[0]
            polys = geom.geoms if geom.geom_type == "MultiPolygon" else [geom]
            verts, codes = [], []
            for poly in polys:
                xy = np.asarray(poly.exterior.coords)
                verts += list(xy)
                codes += [MPath.MOVETO] + [MPath.LINETO] * (len(xy) - 2) + [MPath.CLOSEPOLY]
            patch = PathPatch(MPath(verts, codes), transform=ax.transData, fc="none", ec="none")
            ax.add_patch(patch)
            im.set_clip_path(patch)


# ------------------------------------------------------------ national maps
def national_map(path: Path, levels: pd.Series, hydro: Hydro, geo: GeoLayers, cfg: Config,
                 ffgs=None, focus: list[dict] | None = None, demo_ffgs_flag=False):
    fig = plt.figure(figsize=(7.4, 5.4))
    ax = fig.add_axes([0, 0, 1, 1])
    ext = NATIONAL_EXTENT
    _setup_ax(ax, ext)
    draw_background(ax, geo, cfg, ext, fill=not ffgs)
    if ffgs:
        draw_ffgs(ax, ffgs, cfg, geo)
    draw_rivers(ax, hydro, levels, cfg, width=1.0)

    towns = geo.main_towns(cfg["maps"].get("label_cities_national", 12))
    if len(towns):
        for _, t in towns.iterrows():
            cap = t["fcode"] == "PPLC"
            ax.plot(t.geometry.x, t.geometry.y, "s" if cap else "o", ms=4.5 if cap else 3.2,
                    mfc="white", mec=INK, mew=0.8, zorder=7)
            ax.text(t.geometry.x + 0.06, t.geometry.y + 0.05, t["name"], fontsize=7.5 if cap else 6.6,
                    weight="bold" if cap else "normal", color=INK, path_effects=HALO, zorder=7)

    # focus zones: basin outline + letter
    for z in focus or []:
        b = hydro.basins.loc[z["section"]].geometry
        gpd.GeoSeries([b], crs="EPSG:4326").boundary.plot(aspect=None, ax=ax, color=INK, lw=0.9, ls="--", zorder=5)
        p = hydro.sections.loc[z["section"]].geometry
        ax.text(p.x, p.y, z["letter"], ha="center", va="center", fontsize=8, weight="bold", zorder=9,
                bbox=dict(boxstyle="circle,pad=0.25", fc="white", ec=INK, lw=1.0))

    # legend (upper-left corner, over Mali, as in the mock-up)
    has_nodata = bool((hydro.rivers["section"].map(levels).fillna(-1) < 0).any())
    handles = [Patch(color="none", label="Crues fluviales")] + river_legend_handles(cfg, has_nodata)
    if ffgs:
        handles += [Patch(color="none", label="Crues soudaines (FFGS)" + (" - DÉMO" if demo_ffgs_flag else ""))]
        handles += [Patch(fc=c, ec="white", label=l) for c, l in zip(cfg["ffgs"]["colors"], cfg["ffgs"]["labels"])]
    leg = ax.legend(handles=handles, loc="upper left", fontsize=7.5, frameon=False,
                    handlelength=1.8, borderpad=0.7, labelspacing=0.35)
    for t in leg.get_texts():
        if t.get_text().startswith(("Crues fluviales", "Crues soudaines")):
            t.set_weight("bold")
            t.set_x(-28)
    if demo_ffgs_flag:
        ax.text(0.99, 0.01, "Fond FFGS : données de démonstration", transform=ax.transAxes,
                ha="right", va="bottom", fontsize=7, color="#a33", style="italic")
    _save(fig, path, cfg, transparent=True)
    return path


def day_map(path: Path, levels: pd.Series, hydro: Hydro, geo: GeoLayers, cfg: Config):
    fig = plt.figure(figsize=(2.3, 1.62))
    ax = fig.add_axes([0, 0, 1, 1])
    ext = (-5.65, 9.35, 2.45, 15.15)
    _setup_ax(ax, ext)
    draw_background(ax, geo, cfg, ext, region_width=0.45)
    draw_rivers(ax, hydro, levels, cfg, width=0.55, min_order=8)
    draw_alert_points(ax, hydro, levels, cfg, size=7)
    towns = geo.main_towns(1)
    if len(towns):
        t = towns.iloc[0]
        ax.plot(t.geometry.x, t.geometry.y, "s", ms=2.5, mfc="white", mec=INK, mew=0.6, zorder=7)
    _save(fig, path, cfg, transparent=True)
    return path


# ---------------------------------------------------------------- zoom map
def zoom_map(path: Path, zone: dict, levels: pd.Series, hydro: Hydro, geo: GeoLayers, cfg: Config,
             fc_table: pd.DataFrame | None = None, atlas: bool = False):
    section = zone["section"]
    basin = hydro.basins.loc[section].geometry
    margin = float(zone.get("margin_deg", cfg["maps"].get("zoom_margin_deg", 0.25)))
    figsize = (7.4, float(cfg["maps"].get("zoom_height", 3.95)))
    ext = fit_extent(basin.bounds, figsize[0] / figsize[1], margin)
    fig = plt.figure(figsize=figsize)
    ax = fig.add_axes([0, 0, 1, 1])
    _setup_ax(ax, ext)
    draw_background(ax, geo, cfg, ext, provinces=True, region_width=1.2)
    gpd.GeoSeries([basin], crs="EPSG:4326").plot(aspect=None, ax=ax, color="#dce9f5", alpha=0.55, zorder=2)
    gpd.GeoSeries([basin], crs="EPSG:4326").boundary.plot(aspect=None, ax=ax, color="#1f5f99", lw=1.3, zorder=4.5)

    big = (ext[2] - ext[0]) > 4  # very large basin: thinner network
    draw_network(ax, hydro, cfg, ext, width=0.7 if big else 1.0)
    draw_rivers(ax, hydro, levels, cfg, width=1.0 if big else 1.5, within=box(*ext), min_order=0)

    # province names (small italic grey)
    if len(geo.provinces) and "shapeName" in geo.provinces.columns and (ext[2] - ext[0]) < 3.5:
        pv = geo.provinces[geo.provinces.intersects(box(*ext))]
        for _, p in pv.iterrows():
            rp = p.geometry.representative_point()
            if ext[0] < rp.x < ext[2] and ext[1] < rp.y < ext[3]:
                ax.text(rp.x, rp.y, p["shapeName"], fontsize=6, color="#5f625c", style="italic",
                        ha="center", va="center", zorder=2.6, alpha=0.9)

    # villages and towns
    places = geo.places
    if len(places):
        inext = places.cx[ext[0]:ext[2], ext[1]:ext[3]]
        if cfg["maps"].get("show_villages_zoom", True):
            vil = inext[inext.within(basin)]
            ax.scatter(vil.geometry.x, vil.geometry.y, s=0.6, color="#6f726c", lw=0, zorder=2.7)
        lab = inext.sort_values(["rank", "population"], ascending=[True, False])
        min_d = (ext[2] - ext[0]) * 0.07
        chosen = []
        for _, p in lab.iterrows():
            if all((p.geometry.x - q.x) ** 2 + (p.geometry.y - q.y) ** 2 > min_d ** 2 for q in chosen):
                chosen.append(p.geometry)
                big_town = p["rank"] <= 1 or p["population"] > 50000
                ax.plot(p.geometry.x, p.geometry.y, "s" if big_town else "o", ms=3.6 if big_town else 2.6,
                        mfc="white", mec=INK, mew=0.7, zorder=7)
                ax.text(p.geometry.x, p.geometry.y + (ext[3] - ext[1]) * 0.012, p["name"],
                        fontsize=7 if big_town else 6.2, weight="bold" if big_town else "normal",
                        ha="center", va="bottom", color=INK, path_effects=HALO, zorder=7)
            if len(chosen) >= cfg["maps"].get("label_places_zoom", 25):
                break

    # main reporting point + stations of the additional hydrographs
    secs = hydro.sections
    extras = [n for n in zone.get("extra_hydrographs", []) if n in secs.index and n != section]
    for name in extras:
        s = secs.loc[name]
        ax.plot(s.geometry.x, s.geometry.y, "o", ms=5, mfc=level_color(cfg, levels.get(name, -1)),
                mec=INK, mew=0.7, zorder=8)
        ax.text(s.geometry.x + (ext[2] - ext[0]) * 0.008, s.geometry.y, station_label(s), fontsize=6.5,
                color=INK, style="italic", va="center", path_effects=HALO, zorder=8)
    o = secs.loc[section].geometry
    ax.plot(o.x, o.y, "*", ms=14, mfc=level_color(cfg, levels.get(section, -1)), mec=INK, mew=0.9, zorder=9)
    ax.text(o.x, o.y - (ext[3] - ext[1]) * 0.03, zone["station_label"], ha="center", va="top",
            fontsize=7.5, weight="bold", color=INK, path_effects=HALO, zorder=9)

    # locator inset
    ins = ax.inset_axes([0.79, 0.69, 0.2, 0.3])
    ins.set_zorder(15)
    _setup_ax(ins, NATIONAL_EXTENT)
    ins.set_facecolor("white")
    for sp in ins.spines.values():  # hairline so the inset reads apart from the territory behind
        sp.set_visible(True)
        sp.set_edgecolor("#9a9a9a")
        sp.set_linewidth(0.5)
    if len(geo.burkina):
        geo.burkina.plot(aspect=None, ax=ins, color=land_color(cfg), edgecolor="none")
    gpd.GeoSeries([basin], crs="EPSG:4326").plot(aspect=None, ax=ins, color="#1f5f99", alpha=0.6)
    ins.add_patch(Rectangle((ext[0], ext[1]), ext[2] - ext[0], ext[3] - ext[1], fill=False, ec="#c0392b", lw=0.8))

    rivers_h = ([Line2D([], [], color=cfg["levels"]["nodata_color"], lw=2, label="Cours d'eau (points de rapport)")]
                if atlas else river_legend_handles(cfg, with_nodata=False))
    handles = rivers_h + [
        Line2D([], [], color="#6aa3d5", lw=1.2, label="Réseau de drainage (modèle)"),
        Patch(fc="#dce9f5", ec="#1f5f99", label="Bassin versant"),
        Line2D([], [], marker="*", ls="", ms=10, mfc="white", mec=INK, label="Point de rapport principal"),
    ] + ([Line2D([], [], marker="o", ls="", ms=5, mfc="white", mec=INK, label="Hydrogrammes complémentaires")]
         if extras else []) + [
        Line2D([], [], marker="s", ls="", ms=4, mfc="white", mec=INK, label="Ville / chef-lieu"),
    ]
    leg = ax.legend(handles=handles, loc="lower right", fontsize=6.5, framealpha=0.95, edgecolor="none",
                    handlelength=1.6, labelspacing=0.3)
    leg.set_zorder(20)
    _save(fig, path, cfg, transparent=True)
    return path


def atlas_map(path: Path, section: str, hydro: Hydro, geo: GeoLayers, cfg: Config):
    """Static basin map of one reporting point (rivers in blue, no forecast)."""
    levels = pd.Series(dtype=float)
    cfg_blue = dict(cfg)
    cfg_blue["levels"] = dict(cfg["levels"], nodata_color="#2f7fc1")
    s = hydro.sections.loc[section]
    zone = {"section": section, "station_label": station_label(s)}
    return zoom_map(path, zone, levels, hydro, geo, cfg_blue, atlas=True)


def station_label(sec) -> str:
    """'Mouhoun à Dapola' from SEC_NAME 'Mouhoun_Dapola'."""
    name = sec["SEC_NAME"] if "SEC_NAME" in sec else str(sec.name)
    river, _, place = str(name).partition("_")
    place = place.replace("-", " ")
    if place.lower().startswith("confluence"):
        return f"{river.replace('-', ' ')} - {place}"
    return f"{river.replace('-', ' ')} à {place}" if place else river
