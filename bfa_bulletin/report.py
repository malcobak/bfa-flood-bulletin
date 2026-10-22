"""Build the bulletin: figures + HTML (Jinja2) + PDF (WeasyPrint).

A bulletin is described by a *draft* (plain JSON-serialisable dict): issue date,
options overriding config.yaml, focus zones, text overrides, layout options and
extra images. The command line builds a default draft and exports it at once;
the web application (webapp/) lets forecasters edit the draft before exporting.

Figures are content-addressed (file name = hash of their inputs), so editing a
draft only redraws the figures whose inputs changed.
"""
from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
import logging
import string
import threading
from pathlib import Path

import numpy as np
import pandas as pd
from jinja2 import Environment, FileSystemLoader
from markupsafe import Markup, escape

from . import maps, plots, text
from .config import (JOURS, Config, bulletin_number, fr_date, load_yaml, next_issue)
from .forecast import Forecast, load_forecast
from .geodata import GeoLayers
from .hydro import Hydro

log = logging.getLogger(__name__)
TEMPLATES = Path(__file__).parent / "templates"

DEFAULT_LAYOUT = {
    "main_map_width": 168,      # mm (page content width = 186 mm)
    "show_parts": True,         # one bullet per vigilance level under the headline
    "show_days": True,          # 5-day strip of page 1
    "show_alert_table": True,
    "alert_max_rows": 0,        # 0 = all
    "show_zone_list": True,
    "zone_layout": "map_first",  # map_first | hydro_first
    "zone_map_height": 3.95,    # inches (figure width = 7.4 in)
    "hydro_height": 2.6,
    "show_zone_table": True,
    "show_extra_hydros": True,
    "show_guide": True,
}

# Options of config.yaml that can be overridden per bulletin: draft["options"][key] -> cfg path
OPTION_PATHS = {
    "min_models": ("models", "min_models"),
    "models": ("models", "enabled"),
    "force_run": ("bulletin", "force_run"),
    "issue_time": ("bulletin", "issue_time"),
    "contact": ("bulletin", "contact"),
    "label_cities_national": ("maps", "label_cities_national"),
    "label_places_zoom": ("maps", "label_places_zoom"),
    "show_villages_zoom": ("maps", "show_villages_zoom"),
    "network_min_cells": ("maps", "network_min_cells"),
    "zoom_margin_deg": ("maps", "zoom_margin_deg"),
    "past_days": ("plots", "past_days"),
}

FIG_VERSION = 3  # bump when the drawing code changes, to redraw cached figures
IMAGE_PLACES = ("page1", "page2", "annex")  # + "zone:<SEC_NAME>"


# ------------------------------------------------------------------ drafts
def new_draft(cfg: Config, issue: dt.date, focus: list[str] | None = None, auto_focus: bool | None = None,
              ffgs_file: str | None = None, notes_file: str | None = None, number: str | None = None,
              forecast_root: str | None = None, forecast_pattern: str | None = None) -> dict:
    """Default draft from config.yaml, focus_stations.yaml, notes and command line."""
    focus_cfg = load_yaml(cfg.base_dir / cfg.get("focus_file", "focus_stations.yaml"))
    notes = load_yaml(notes_file)
    zones = [dict(z) for z in (focus_cfg.get("zones") or [])]
    zones += [{"section": s} for s in (focus or [])]
    for z in zones:
        extra = (notes.get("zones") or {}).get(z["section"])
        if extra:
            z["comment"] = " ".join(x for x in [z.get("comment"), extra] if x)
    auto_cfg = dict(focus_cfg.get("auto") or {})
    texts = {}
    if notes.get("summary"):
        texts["headline"] = notes["summary"]
    for k_note, k_text in (("additional", "additional"), ("flash_floods", "flash")):
        if notes.get(k_note):
            texts[k_text] = notes[k_note]
    options = {}
    if forecast_root:
        options["forecast_root"] = forecast_root
    if forecast_pattern:
        options["forecast_pattern"] = forecast_pattern
    ffgs = ffgs_file or cfg["ffgs"].get("file") if (ffgs_file or cfg["ffgs"].get("enabled")) else None
    return {
        "issue": issue.isoformat(),
        "number": number or bulletin_number(issue, cfg),
        "options": options,
        "ffgs": ffgs,
        "zones": zones,
        "auto": {"enabled": bool(auto_focus if auto_focus is not None else auto_cfg.get("enabled", False)),
                 "min_level": int(auto_cfg.get("min_level", 2)),
                 "max_zones": int(auto_cfg.get("max_zones", 4)),
                 "done": False},
        "texts": texts,
        "layout": dict(DEFAULT_LAYOUT, show_parts=not notes.get("summary")),
        "images": [],
        "rev": 0,
    }


def effective_config(cfg: Config, draft: dict) -> Config:
    """config.yaml + the options of the draft."""
    data = copy.deepcopy(dict(cfg))
    opts = draft.get("options") or {}
    for key, (sec, sub) in OPTION_PATHS.items():
        if opts.get(key) not in (None, "", []):
            data.setdefault(sec, {})[sub] = opts[key]
    for key in ("forecast_root", "forecast_pattern"):
        if opts.get(key):
            data["paths"][key] = opts[key]
    lay = {**DEFAULT_LAYOUT, **(draft.get("layout") or {})}
    data["maps"]["zoom_height"] = float(lay["zone_map_height"])
    return Config(data, cfg.base_dir)


def _hash(obj) -> str:
    return hashlib.sha1(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()[:12]


# --------------------------------------------------------------- helpers
def _fmt_q(q) -> str:
    if q is None or not np.isfinite(q):
        return "-"
    return f"{q:,.0f}".replace(",", " ") if q >= 10 else f"{q:.1f}".replace(".", ",")


def _str(v) -> str:
    return "" if v is None or (isinstance(v, float) and np.isnan(v)) or str(v) in ("-", "NA", "nan") else str(v)


def _levels_key(s: pd.Series) -> dict:
    return {k: int(v) for k, v in s.fillna(-1).items()}


def auto_zones(fc: Forecast, hydro: Hydro, min_level: int = 2, max_zones: int = 4,
               exclude: list[str] | None = None) -> list[str]:
    """Most threatened reporting points: one per basin, from downstream to upstream."""
    exclude = list(exclude or [])
    cand = fc.table[fc.table["level_max"] >= min_level].sort_values(
        ["level_max", "drained_area"], ascending=[False, False])
    picked = []
    for name in cand.index:
        if len(picked) >= max_zones:
            break
        if name in exclude:
            continue
        # skip a point already inside the basin of a selected (downstream) zone
        if any(hydro.sections.loc[name].geometry.within(hydro.basins.loc[p].geometry.buffer(0.01))
               for p in picked + exclude if p in hydro.basins.index):
            continue
        picked.append(name)
    return picked


def resolve_zones(zones: list[dict], fc: Forecast, hydro: Hydro) -> list[dict]:
    out, seen = [], set()
    for z in zones:
        z = dict(z)
        s = z.get("section")
        if not s or s in seen:
            continue
        if s not in hydro.sections.index:
            log.error("Focus section %s unknown (see --list-sections): skipped", s)
            continue
        seen.add(s)
        z["letter"] = string.ascii_uppercase[len(out) % 26]
        z["station_label"] = maps.station_label(hydro.sections.loc[s])
        if not z.get("title"):
            z["title"] = z["station_label"]
        z["extra_hydrographs"] = [e for e in (z.get("extra_hydrographs") or [])
                                  if e in fc.table.index and e != s][:2]
        z["comment"] = z.get("comment") or ""
        out.append(z)
    return out


def _alert_rows(fc: Forecast) -> list[dict]:
    t = fc.table[fc.table["level_max"] >= 1].sort_values(
        ["level_max", "first_day", "drained_area"], ascending=[False, True, False])
    rows = []
    for name, r in t.iterrows():
        thr_idx = int(r["level_max"]) - 1
        thr_val = r[["thr_alert", "thr_alarm", "thr_emergency"][thr_idx]]
        rows.append({
            "section": name,
            "river": text.river_name(name), "place": text.place_name(name),
            "region": _str(r.get("ADMIN_B_L1")), "province": _str(r.get("ADMIN_B_L2")),
            "area": f"{float(r.get('AREA', np.nan)):,.0f}".replace(",", " "),
            "days": [int(r[c]) for c in fc.day_cols], "level": int(r["level_max"]),
            "peak_q": _fmt_q(r["peak_q"]), "thr": _fmt_q(thr_val),
            "models": f"{int(r['models_alert'])}/{int(r['n_models'])}",
        })
    return rows


def _nl2br(v):
    if isinstance(v, Markup):
        return v
    return Markup("<br>".join(escape(x) for x in str(v).split("\n")))


# ================================================================== engine
class Engine:
    """Heavy objects (sections, basins, background layers, forecasts) kept in memory
    and shared between bulletins. Figure drawing is serialised by a lock
    (matplotlib is not thread-safe)."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.hydro = Hydro(cfg)
        self.geo = GeoLayers(cfg)
        self.lock = threading.RLock()
        self._fc_cache: dict[str, Forecast] = {}
        self.env = Environment(loader=FileSystemLoader(TEMPLATES), autoescape=True,
                               trim_blocks=True, lstrip_blocks=True)
        self.env.filters["q"] = _fmt_q
        self.env.filters["nl2br"] = _nl2br

    # ---------------------------------------------------------------- data
    def outdir(self, issue: dt.date) -> Path:
        return self.cfg.path("output_dir") / f"bulletin_{issue:%Y%m%d}"

    def forecast(self, ecfg: Config, issue: dt.date) -> Forecast:
        key = _hash([issue, ecfg["paths"]["forecast_root"], ecfg["paths"]["forecast_pattern"],
                     ecfg["models"], ecfg["bulletin"].get("force_run"), ecfg["bulletin"]["n_days"],
                     ecfg["bulletin"].get("run_hour"), ecfg["bulletin"].get("max_run_lookback_days")])
        with self.lock:
            if key not in self._fc_cache:
                if len(self._fc_cache) > 12:
                    self._fc_cache.pop(next(iter(self._fc_cache)))
                self._fc_cache[key] = load_forecast(ecfg, issue, self.hydro.sections)
            return self._fc_cache[key]

    def clear_cache(self):
        with self.lock:
            self._fc_cache.clear()

    def suggest_zones(self, draft: dict) -> list[str]:
        ecfg = effective_config(self.cfg, draft)
        fc = self.forecast(ecfg, dt.date.fromisoformat(draft["issue"]))
        a = draft.get("auto") or {}
        return auto_zones(fc, self.hydro, int(a.get("min_level", 2)), int(a.get("max_zones", 4)),
                          exclude=[z["section"] for z in draft.get("zones") or []])

    def materialize_auto(self, draft: dict) -> dict:
        """Add the automatic zones to the draft once (they become ordinary editable zones)."""
        a = draft.get("auto") or {}
        if a.get("enabled") and not a.get("done"):
            draft["zones"] = list(draft.get("zones") or []) + [
                {"section": s, "auto": True} for s in self.suggest_zones(draft)]
            a["done"] = True
        return draft

    # ------------------------------------------------------------- figures
    def _fig(self, figdir: Path, kind: str, key, draw) -> str:
        name = f"{kind}_{_hash(key)}.png"
        path = figdir / name
        if not path.is_file():
            with self.lock:
                tmp = path.with_suffix(".tmp.png")
                draw(tmp)
                tmp.replace(path)
        return name

    # ------------------------------------------------------------- context
    def build(self, draft: dict) -> tuple[dict, Forecast]:
        """Figures + template context of a draft."""
        cfg = effective_config(self.cfg, draft)
        issue = dt.date.fromisoformat(draft["issue"])
        hydro, geo = self.hydro, self.geo
        fc = self.forecast(cfg, issue)
        lay = {**DEFAULT_LAYOUT, **(draft.get("layout") or {})}
        texts = dict(draft.get("texts") or {})
        outdir = self.outdir(issue)
        figdir = outdir / "figures"
        figdir.mkdir(parents=True, exist_ok=True)
        used = []

        zones = resolve_zones(draft.get("zones") or [], fc, hydro)
        previous = text.load_previous_summary(cfg.path("output_dir"), issue)
        style_key = [cfg["maps"], cfg["levels"]["colors"], cfg["levels"]["nodata_color"],
                     cfg["levels"]["labels"], cfg["ffgs"]["colors"], cfg["ffgs"]["labels"], FIG_VERSION]

        # ---- FFGS background
        ffgs_file = draft.get("ffgs")
        ffgs, ffgs_key = None, None
        if ffgs_file:
            p = Path(ffgs_file)
            p = p if p.is_absolute() or str(ffgs_file).lower() == "demo" else cfg.base_dir / p
            ffgs_key = [str(p), p.stat().st_mtime if p.is_file() else None]
            ffgs = maps.load_ffgs(cfg, geo, fc, str(ffgs_file))
        is_demo = str(ffgs_file or "").lower() == "demo"

        # ---- national + daily maps
        lv_max = fc.table["level_max"]
        national = self._fig(
            figdir, "national",
            ["nat", fc.run, _levels_key(lv_max), ffgs_key, [(z["section"], z["letter"]) for z in zones], style_key],
            lambda f: maps.national_map(f, lv_max, hydro, geo, cfg, ffgs=ffgs, focus=zones, demo_ffgs_flag=is_demo))
        used.append(national)
        cards = text.day_cards(fc, previous, cfg)
        for c in cards:
            lv = fc.table[c["col"]]
            c["map"] = self._fig(figdir, f"day_{c['col']}", ["day", _levels_key(lv), style_key],
                                 lambda f, lv=lv: maps.day_map(f, lv, hydro, geo, cfg))
            used.append(c["map"])
            c["label"] = JOURS[c["date"].weekday()].capitalize()
            c["date_str"] = f"{c['date']:%d/%m/%Y}"

        zone_ctx = [self._zone_context(z, fc, cfg, figdir, lay, style_key, used) for z in zones]

        # ---- automatic texts (defaults of the editable blocks)
        headline, parts = text.summary_text(fc)
        overall = text.overall_level(fc)
        risk_word = text.RISK_WORDS[overall] if overall >= 0 else "INDÉTERMINÉ"
        n = len(cards)
        models = [cfg["models"]["names"].get(m, m) for m in fc.models]
        run_str = f"{fc.run:%d/%m/%Y %H:%M} UTC"
        min_models = cfg["models"].get("min_models", 1)
        defaults = {
            "subtitle": f"Vigilance inondations pour les {n} prochains jours",
            "headline": headline,
            "additional": "",
            "river_risk": Markup(f"Le risque de crue fluviale est <span class=\"risk\">{escape(risk_word)}</span>"
                                 f" pour les {n} prochains jours."),
            "flash": "",
            "ffgs_text": ("Le fond de la carte nationale représente la vigilance aux crues soudaines issue du "
                          "système FFGS." if ffgs else "Produit FFGS non disponible pour ce bulletin."),
            "alert_note": ("Q max : débit maximal prévu sur la période (tous modèles). Seuil : seuil du niveau de "
                           "vigilance maximal atteint. Modèles : nombre de modèles dépassant le seuil jaune / "
                           "modèles disponibles."),
            "no_alert": f"Aucun point de rapport n'atteint le seuil de vigilance jaune pour les {n} prochains jours.",
            "zones_note": "Voir les cartes détaillées dans les pages suivantes.",
            "method": (f"Les débits sont simulés par le modèle hydrologique Continuum de la chaîne FloodProofs "
                       f"(Fondation CIMA), forcé par les observations jusqu'à l'heure du run puis par les prévisions "
                       f"des modèles météorologiques ({', '.join(models)}). Pour chaque point de rapport et chaque "
                       f"jour, le débit maximal prévu par chaque modèle est comparé aux trois seuils de la section ; "
                       f"le niveau retenu est le plus élevé atteint par au moins {min_models} modèle(s). La carte "
                       f"nationale représente le niveau maximal sur les {n} jours ; chaque tronçon de rivière prend "
                       f"la couleur du point de rapport situé à son aval."),
            "trend_note": ("La tendance compare, pour chaque jour, le niveau maximal et le nombre de points en "
                           "vigilance avec le bulletin précédent"
                           + (f" (numéro {previous['number']} du {previous['issue']})." if previous else ".")),
            "flash_note": ("Les crues soudaines locales (orages intenses, ruissellement urbain) peuvent survenir "
                           "hors des cours d'eau surveillés et ne sont pas représentées par les niveaux de "
                           "vigilance fluviale."),
            "next_issue": f"{fr_date(next_issue(issue, cfg)).capitalize()} à {cfg['bulletin']['issue_time']} (heure locale).",
            "contact": f"{cfg['bulletin']['organisation']}\n{cfg['bulletin']['contact']}",
        }
        for i, p in enumerate(parts):
            defaults[f"part.{i}"] = p
        for i, imp in enumerate(cfg["levels"]["impacts"]):
            defaults[f"impact.{i}"] = imp

        def txt(key):
            v = texts.get(key)
            return defaults.get(key, "") if v is None else v

        # ---- extra images
        images = {}
        for im in draft.get("images") or []:
            images.setdefault(im.get("place", "annex"), []).append(im)

        alert_rows = _alert_rows(fc)
        ctx = {
            "issue": issue, "cfg": cfg, "b": cfg["bulletin"], "levels": cfg["levels"], "number": draft.get("number"),
            "issue_str": fr_date(issue).capitalize(), "issue_time": cfg["bulletin"]["issue_time"],
            "valid_from": f"{fc.days[0]:%d/%m/%Y}", "valid_to": f"{fc.days[-1]:%d/%m/%Y}",
            "run_str": run_str, "run_late": fc.run.date() < issue,
            "models": models, "overall": overall, "risk_word": risk_word,
            "n_parts": len(parts) if lay["show_parts"] else 0,
            "ffgs": bool(ffgs), "ffgs_demo": is_demo,
            "national": national, "cards": cards, "zones": zone_ctx,
            "alert_rows": alert_rows[:int(lay["alert_max_rows"])] if int(lay["alert_max_rows"] or 0) else alert_rows,
            "n_alert_rows": len(alert_rows),
            "n_sections": len(fc.table), "previous": previous,
            "generated": dt.datetime.now().strftime("%d/%m/%Y %H:%M"),
            "L": lay, "txt": txt, "images": images,
            "_used": used,
        }
        return ctx, fc

    def _zone_context(self, z: dict, fc: Forecast, cfg: Config, figdir: Path, lay: dict,
                      style_key, used: list) -> dict:
        hydro = self.hydro
        s = z["section"]
        row = fc.table.loc[s] if s in fc.table.index else None
        sec = hydro.sections.loc[s]
        levels = fc.table["level_max"]
        zmap = self._fig(
            figdir, "zoom",
            ["zoom", s, z["station_label"], z["extra_hydrographs"], z.get("margin_deg"),
             _levels_key(levels), style_key],
            lambda f: maps.zoom_map(f, z, levels, hydro, self.geo, cfg, fc.table))
        used.append(zmap)
        hydros = []
        for name in [s] + z["extra_hydrographs"]:
            if name not in fc.series:
                continue
            r = fc.table.loc[name]
            compact = name != s
            title = f"Hydrogramme - {maps.station_label(hydro.sections.loc[name])}"
            figsize = (7.4, float(lay["hydro_height"])) if not compact else (3.65, 2.5)
            f = self._fig(
                figdir, "hydro",
                ["hydro", name, fc.run, fc.days, list(fc.series[name].columns),
                 [r["thr_alert"], r["thr_alarm"], r["thr_emergency"]], title, figsize, compact,
                 cfg["models"]["names"], cfg.get("plots"), cfg["maps"]["dpi"], cfg["levels"]["colors"]],
                lambda p, name=name, r=r, title=title, figsize=figsize, compact=compact: plots.hydrograph(
                    p, fc.series[name], r, fc.run, fc.days, cfg, title=title, figsize=figsize, compact=compact))
            used.append(f)
            hydros.append({"file": f, "compact": compact,
                           "table": plots.daily_table(fc.series[name], r, fc.run, fc.days, cfg)
                           if not compact else None})
        lvl = int(row["level_max"]) if row is not None else -1
        up = hydro.sections_in(hydro.basins.loc[s].geometry)
        up_levels = fc.table.loc[[u for u in up if u in fc.table.index], "level_max"]
        return {
            **z,
            "map": zmap,
            "hydros": hydros,
            "level": lvl,
            "river": text.river_name(s),
            "basin": _str(sec.get("BASIN")),
            "region": _str(sec.get("ADMIN_B_L1")),
            "province": _str(sec.get("ADMIN_B_L2")),
            "commune": _str(sec.get("ADMIN_B_L3")),
            "station_type": "Station hydrométrique" if _str(sec.get("TYPE")) == "Hydrometre" else "Point de rapport",
            "area": f"{float(sec.get('AREA', np.nan)):,.0f}".replace(",", " "),
            "thr": [_fmt_q(row[c]) for c in ("thr_alert", "thr_alarm", "thr_emergency")] if row is not None else [],
            "peak_q": _fmt_q(row["peak_q"]) if row is not None else "-",
            "peak_time": (f"{JOURS[row['peak_time'].weekday()]} {row['peak_time']:%d/%m %Hh}"
                          if row is not None and pd.notna(row["peak_time"]) else "-"),
            "peak_model": cfg["models"]["names"].get(row["peak_model"], row["peak_model"])
            if row is not None and row["peak_model"] else "",
            "models_alert": int(row["models_alert"]) if row is not None else 0,
            "n_models": int(row["n_models"]) if row is not None else 0,
            "n_up": len(up_levels),
            "n_up_alert": int((up_levels >= 1).sum()),
            "days": [int(row[c]) for c in fc.day_cols] if row is not None else [],
        }

    # -------------------------------------------------------------- render
    def render(self, ctx: dict, urls: dict | None = None, edit: bool = False) -> str:
        """HTML of the bulletin. `urls` = base URLs of css/logos/figures/uploads
        (default: local file:// URIs, used for the PDF)."""
        cfg = ctx["cfg"]
        outdir = self.outdir(ctx["issue"])
        urls = urls or {
            "css": (TEMPLATES / "bulletin.css").as_uri(),
            "logos": cfg.path("logos_dir").as_uri(),
            "fig": (outdir / "figures").as_uri(),
            "up": (outdir / "uploads").as_uri(),
        }
        env = self.env
        env.filters["lc"] = lambda lvl: maps.level_color(cfg, lvl)
        return env.get_template("bulletin.html.j2").render(**ctx, **urls, edit=edit)

    def preview(self, draft: dict, urls: dict) -> str:
        ctx, _ = self.build(draft)
        return self.render(ctx, urls, edit=True)

    # -------------------------------------------------------------- export
    def export(self, draft: dict, html_only: bool = False) -> dict:
        ctx, fc = self.build(draft)
        issue = dt.date.fromisoformat(draft["issue"])
        outdir = self.outdir(issue)
        html = self.render(ctx)
        stem = f"bulletin_inondations_BFA_{issue:%Y%m%d}"
        html_path = outdir / f"{stem}.html"
        html_path.write_text(html, encoding="utf-8")
        pdf_path = None
        if not html_only:
            from weasyprint import HTML
            pdf_path = outdir / f"{stem}.pdf"
            with self.lock:
                HTML(string=html, base_url=str(outdir)).write_pdf(pdf_path)
            log.info("PDF written: %s", pdf_path)

        # ---- side products
        text.save_summary(outdir / "summary.json", fc, draft.get("number"))
        cols = ["BASIN", "ADMIN_B_L1", "ADMIN_B_L2", "thr_alert", "thr_alarm", "thr_emergency",
                *fc.day_cols, "level_max", "peak_q", "peak_time", "peak_model", "models_alert", "n_models"]
        csv_path = outdir / f"niveaux_vigilance_{issue:%Y%m%d}.csv"
        fc.table[[c for c in cols if c in fc.table.columns]].to_csv(csv_path)
        # drop figures of previous versions of the draft
        used = set(ctx["_used"])
        for f in (outdir / "figures").glob("*.png"):
            if f.name not in used:
                f.unlink(missing_ok=True)
        return {"pdf": pdf_path, "html": html_path, "csv": csv_path, "outdir": outdir,
                "zones": [z["section"] for z in ctx["zones"]], "overall": ctx["overall"], "run": fc.run}


# --------------------------------------------------------- command line API
def build_bulletin(cfg: Config, issue: dt.date, focus: list[str] | None = None, auto_focus: bool | None = None,
                   ffgs_file: str | None = None, notes_file: str | None = None, number: str | None = None,
                   forecast_root: str | None = None, forecast_pattern: str | None = None,
                   html_only: bool = False) -> dict:
    engine = Engine(cfg)
    draft = new_draft(cfg, issue, focus, auto_focus, ffgs_file, notes_file, number,
                      forecast_root, forecast_pattern)
    engine.materialize_auto(draft)
    return engine.export(draft, html_only=html_only)


def build_atlas(cfg: Config, sections: list[str] | None = None) -> Path:
    """Static basin map of every reporting point (or of the given ones)."""
    hydro, geo = Hydro(cfg), GeoLayers(cfg)
    out = cfg.path("output_dir") / "atlas_bassins"
    out.mkdir(parents=True, exist_ok=True)
    for name in sections or list(hydro.sections.index):
        safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in name)
        maps.atlas_map(out / f"bassin_{safe}.png", name, hydro, geo, cfg)
        log.info("Atlas: %s", name)
    hydro.basins.reset_index(drop=True).to_file(out / "bassins_points_de_rapport.gpkg", driver="GPKG")
    return out
