"""Web application to prepare the flood bulletin interactively.

  ./run_webapp.sh                     # http://localhost:8000
  BULLETIN_PORT=8080 ./run_webapp.sh

The forecaster opens a bulletin (issue date), the application proposes the
automatic bulletin and the forecaster adjusts options, focus zones, texts,
layout and images. The draft is saved in output/bulletin_YYYYMMDD/draft.json
after every change, so several people can work on it; "Générer le PDF" writes
the final PDF / HTML / CSV in the same folder as the command line does.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
import re
import sys
import threading
import warnings
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fastapi import Body, FastAPI, File, Form, HTTPException, Request, UploadFile  # noqa: E402
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

from bfa_bulletin.config import load_config  # noqa: E402
from bfa_bulletin.forecast import available_runs  # noqa: E402
from bfa_bulletin.report import (DEFAULT_LAYOUT, IMAGE_PLACES, Engine, effective_config,  # noqa: E402
                                 new_draft)
from bfa_bulletin import maps, text  # noqa: E402
from webapp import users as accounts  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
for noisy in ("fontTools", "weasyprint", "pyogrio", "numexpr", "matplotlib", "PIL"):
    logging.getLogger(noisy).setLevel(logging.WARNING)
warnings.filterwarnings("ignore", category=UserWarning)
log = logging.getLogger("webapp")

cfg = load_config(os.environ.get("BULLETIN_CONFIG"))
engine = Engine(cfg)
STATIC = Path(__file__).parent / "static"
store_lock = threading.Lock()
SAFE_NAME = re.compile(r"^[\w.\-]+$")

app = FastAPI(title="Bulletin inondations BFA", docs_url=None, redoc_url=None)


# ------------------------------------------------------------------ auth
@app.middleware("http")
async def no_cache_static(request: Request, call_next):
    """The UI files change with the application: always revalidate them."""
    resp = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static/"):
        resp.headers["Cache-Control"] = "no-cache"
    return resp


@app.middleware("http")
async def basic_auth(request: Request, call_next):
    users = accounts.load_users()
    if users:
        name = accounts.check_basic(request.headers.get("authorization"), users)
        if not name:
            return Response("Authentification requise", status_code=401,
                            headers={"WWW-Authenticate": 'Basic realm="Bulletin inondations", charset="UTF-8"'})
        request.state.user = name
    else:
        request.state.user = "local"
    return await call_next(request)


app.mount("/static", StaticFiles(directory=STATIC), name="static")
app.mount("/pkg/bfa_bulletin/templates", StaticFiles(directory=ROOT / "bfa_bulletin" / "templates"), name="tpl")
app.mount("/pkg/assets", StaticFiles(directory=ROOT / "assets"), name="assets")


# ---------------------------------------------------------------- drafts
def _issue(issue: str) -> dt.date:
    try:
        return dt.date.fromisoformat(issue)
    except ValueError:
        raise HTTPException(400, f"Date invalide : {issue}")


def _draft_path(issue: dt.date) -> Path:
    return engine.outdir(issue) / "draft.json"


def load_draft(issue: dt.date) -> dict:
    p = _draft_path(issue)
    if not p.is_file():
        raise HTTPException(404, "Bulletin non créé")
    return json.loads(p.read_text(encoding="utf-8"))


def save_draft(draft: dict, user: str) -> dict:
    draft["rev"] = int(draft.get("rev", 0)) + 1
    draft["updated"] = dt.datetime.now().isoformat(timespec="seconds")
    draft["updated_by"] = user
    p = _draft_path(dt.date.fromisoformat(draft["issue"]))
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(draft, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
    tmp.replace(p)
    return draft


def _urls(issue: dt.date) -> dict:
    base = f"/api/bulletins/{issue.isoformat()}"
    return {"css": "/pkg/bfa_bulletin/templates/bulletin.css", "logos": "/pkg/assets/logos",
            "fig": f"{base}/fig", "up": f"{base}/uploads"}


def _list_bulletins() -> list[dict]:
    out = []
    for p in sorted(cfg.path("output_dir").glob("bulletin_*/"), reverse=True):
        m = re.match(r"bulletin_(\d{8})$", p.name)
        if not m:
            continue
        issue = dt.datetime.strptime(m.group(1), "%Y%m%d").date()
        d = {"issue": issue.isoformat(), "draft": False, "pdf": None}
        f = p / "draft.json"
        if f.is_file():
            dr = json.loads(f.read_text(encoding="utf-8"))
            d.update(draft=True, number=dr.get("number"), rev=dr.get("rev"), updated=dr.get("updated"),
                     updated_by=dr.get("updated_by"), exported=dr.get("exported"))
        pdf = next(p.glob("*.pdf"), None)
        d["pdf"] = pdf.name if pdf else None
        out.append(d)
    return out


# ------------------------------------------------------------------ api
@app.get("/", response_class=HTMLResponse)
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/meta")
def meta(request: Request):
    runs = available_runs(cfg)
    return {
        "user": request.state.user,
        "today": dt.date.today().isoformat(),
        "title": cfg["bulletin"]["title"],
        "models": cfg["models"]["names"],
        "levels": {"colors": cfg["levels"]["colors"], "labels": cfg["levels"]["labels"],
                   "nodata": cfg["levels"]["nodata_color"]},
        "defaults": {
            "min_models": cfg["models"].get("min_models", 1),
            "issue_time": cfg["bulletin"]["issue_time"],
            "contact": cfg["bulletin"]["contact"],
            "label_cities_national": cfg["maps"].get("label_cities_national", 12),
            "label_places_zoom": cfg["maps"].get("label_places_zoom", 25),
            "show_villages_zoom": cfg["maps"].get("show_villages_zoom", True),
            "network_min_cells": cfg["maps"].get("network_min_cells", 15),
            "zoom_margin_deg": cfg["maps"].get("zoom_margin_deg", 0.25),
            "past_days": None,
        },
        "layout": DEFAULT_LAYOUT,
        "image_places": IMAGE_PLACES,
        "runs": [r.isoformat() for r in runs],
        "bulletins": _list_bulletins(),
        "issue_weekdays": cfg["bulletin"]["issue_weekdays"],
    }


@app.post("/api/bulletins/{issue}")
def create(issue: str, request: Request, reset: bool = False):
    d = _issue(issue)
    with store_lock:
        if _draft_path(d).is_file() and not reset:
            return load_draft(d)
        old_rev = load_draft(d).get("rev", 0) if _draft_path(d).is_file() else 0
        draft = new_draft(cfg, d)
        draft["rev"] = old_rev
        try:
            engine.materialize_auto(draft)
        except FileNotFoundError as exc:
            raise HTTPException(404, str(exc))
        return save_draft(draft, request.state.user)


@app.get("/api/bulletins/{issue}")
def get_draft(issue: str):
    return load_draft(_issue(issue))


MERGE_KEYS = ("options", "layout", "auto")
REPLACE_KEYS = ("zones", "images", "number", "ffgs")


@app.patch("/api/bulletins/{issue}")
def patch(issue: str, request: Request, body: dict = Body(...)):
    """Partial update. options/layout/auto/texts are merged (None removes a key,
    i.e. back to config.yaml / automatic text); zones/images/number/ffgs are replaced.
    texts["zone.<SEC_NAME>.<title|comment>"] edits a focus zone."""
    d = _issue(issue)
    with store_lock:
        draft = load_draft(d)
        for k in MERGE_KEYS:
            if k in body:
                cur = draft.setdefault(k, {})
                for kk, v in (body[k] or {}).items():
                    if v is None:
                        cur.pop(kk, None)
                    else:
                        cur[kk] = v
        for k in REPLACE_KEYS:
            if k in body:
                draft[k] = body[k]
        for key, v in (body.get("texts") or {}).items():
            if key.startswith("zone."):
                _, sec, field = key.split(".", 2)
                for z in draft.get("zones") or []:
                    if z["section"] == sec and field in ("title", "comment"):
                        z[field] = v or ""
            elif v is None:
                draft.setdefault("texts", {}).pop(key, None)
            else:
                draft.setdefault("texts", {})[key] = v
        return save_draft(draft, request.state.user)


def _error_page(msg: str) -> str:
    return (f"<html><body style='font-family:sans-serif;padding:40px;color:#a3261b'>"
            f"<h2>Aperçu impossible</h2><p>{msg}</p></body></html>")


@app.get("/api/bulletins/{issue}/preview", response_class=HTMLResponse)
def preview(issue: str):
    d = _issue(issue)
    draft = load_draft(d)
    try:
        html = engine.preview(draft, _urls(d))
    except FileNotFoundError as exc:
        return HTMLResponse(_error_page(str(exc)), status_code=200)
    except Exception as exc:  # show the error in the preview pane rather than a blank page
        log.exception("Preview failed")
        return HTMLResponse(_error_page(f"{type(exc).__name__} : {exc}"), status_code=200)
    return html.replace("</head>", '<link rel="stylesheet" href="/static/preview.css">\n</head>')


def _file(folder: Path, name: str) -> FileResponse:
    if not SAFE_NAME.match(name) or not (folder / name).is_file():
        raise HTTPException(404)
    return FileResponse(folder / name)


@app.get("/api/bulletins/{issue}/fig/{name}")
def figure(issue: str, name: str):
    r = _file(engine.outdir(_issue(issue)) / "figures", name)
    r.headers["Cache-Control"] = "public, max-age=31536000, immutable"  # content-addressed
    return r


@app.get("/api/bulletins/{issue}/uploads/{name}")
def uploaded(issue: str, name: str):
    return _file(engine.outdir(_issue(issue)) / "uploads", name)


@app.get("/api/bulletins/{issue}/files/{name}")
def download(issue: str, name: str):
    if not name.endswith((".pdf", ".csv", ".html")):
        raise HTTPException(404)
    return _file(engine.outdir(_issue(issue)), name)


@app.get("/api/bulletins/{issue}/sections")
def sections(issue: str):
    """Reporting points with their levels (GeoJSON) for the selection map."""
    d = _issue(issue)
    draft = load_draft(d)
    ecfg = effective_config(cfg, draft)
    try:
        fc = engine.forecast(ecfg, d)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc))
    sec = engine.hydro.sections
    t = fc.table
    feats = []
    for name, s in sec.iterrows():
        r = t.loc[name] if name in t.index else None
        f = lambda v: None if v is None or v != v else round(float(v), 1)  # noqa: E731
        props = {
            "name": name, "label": maps.station_label(s), "river": text.river_name(name),
            "basin": str(s.get("BASIN") or ""), "region": str(s.get("ADMIN_B_L1") or ""),
            "province": str(s.get("ADMIN_B_L2") or ""), "area": f(s.get("AREA")),
            "type": str(s.get("TYPE") or ""),
            "level": int(r["level_max"]) if r is not None else -1,
            "days": [int(r[c]) for c in fc.day_cols] if r is not None else [],
            "peak_q": f(r["peak_q"]) if r is not None else None,
            "thr": [f(r[c]) for c in ("thr_alert", "thr_alarm", "thr_emergency")] if r is not None else [],
            "models": f"{int(r['models_alert'])}/{int(r['n_models'])}" if r is not None else "",
        }
        feats.append({"type": "Feature", "properties": props,
                      "geometry": {"type": "Point", "coordinates": [s.geometry.x, s.geometry.y]}})
    return {"type": "FeatureCollection", "features": feats,
            "run": fc.run.isoformat(), "days": [x.isoformat() for x in fc.days],
            "models": fc.models, "overall": text.overall_level(fc),
            "counts": {str(k): int((t["level_max"] == k).sum()) for k in range(-1, 4)}}


_rivers_cache: dict = {}


@app.get("/api/rivers")
def rivers():
    if "data" not in _rivers_cache:
        r = engine.hydro.rivers[["section", "log_ups", "geometry"]].copy()
        r["geometry"] = r.geometry.simplify(0.005)
        _rivers_cache["data"] = json.loads(r.to_json())
    return JSONResponse(_rivers_cache["data"], headers={"Cache-Control": "max-age=86400"})


@app.get("/api/basin/{section}")
def basin(section: str):
    b = engine.hydro.basins
    if section not in b.index:
        raise HTTPException(404)
    g = b.loc[[section]].geometry.simplify(0.01)
    return JSONResponse(json.loads(g.to_json()), headers={"Cache-Control": "max-age=86400"})


@app.post("/api/bulletins/{issue}/suggest")
def suggest(issue: str, body: dict = Body(default={})):
    draft = load_draft(_issue(issue))
    draft["auto"] = {**(draft.get("auto") or {}), **body}
    return {"sections": engine.suggest_zones(draft)}


@app.post("/api/bulletins/{issue}/upload")
async def upload(issue: str, request: Request, file: UploadFile = File(...), kind: str = Form("image"),
                 place: str = Form("annex"), caption: str = Form(""), width: int = Form(100)):
    d = _issue(issue)
    ext = Path(file.filename or "").suffix.lower()
    allowed = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"} if kind == "image" else \
              {".gpkg", ".geojson", ".json", ".tif", ".tiff", ".zip"}
    if ext not in allowed:
        raise HTTPException(400, f"Type de fichier non accepté ({ext}) : {', '.join(sorted(allowed))}")
    folder = engine.outdir(d) / "uploads"
    folder.mkdir(parents=True, exist_ok=True)
    stem = re.sub(r"[^\w\-]+", "_", Path(file.filename).stem)[:40] or "fichier"
    name = f"{kind}_{dt.datetime.now():%H%M%S}_{stem}{ext}"
    (folder / name).write_bytes(await file.read())
    with store_lock:
        draft = load_draft(d)
        if kind == "image":
            draft.setdefault("images", []).append({
                "id": name, "file": name, "caption": caption,
                "place": place if place in IMAGE_PLACES or place.startswith("zone:") else "annex",
                "width": max(20, min(100, int(width)))})
        else:
            draft["ffgs"] = str(folder / name)
        return save_draft(draft, request.state.user)


@app.post("/api/bulletins/{issue}/export")
def export(issue: str, request: Request):
    d = _issue(issue)
    draft = load_draft(d)
    try:
        res = engine.export(draft)
    except Exception as exc:
        log.exception("Export failed")
        raise HTTPException(500, f"{type(exc).__name__} : {exc}")
    with store_lock:
        draft = load_draft(d)
        draft["exported"] = {"at": dt.datetime.now().isoformat(timespec="seconds"), "by": request.state.user,
                             "rev": int(draft.get("rev", 0)) + 1,  # rev after this save
                             "files": [p.name for p in (res["pdf"], res["html"], res["csv"]) if p]}
        return save_draft(draft, request.state.user)


@app.post("/api/reload")
def reload_data():
    """Forget forecasts held in memory (new FloodProofs files mirrored)."""
    engine.clear_cache()
    return {"ok": True}
