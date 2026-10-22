"""Background geographic layers (borders, regions, lakes, towns and villages).

Downloaded once with `python make_bulletin.py --prepare-geodata` and stored,
clipped to the model domain, in paths.geodata_dir. The bulletin runs offline
afterwards; a missing layer is simply not drawn.

Sources: Natural Earth 10m (public domain), GeoNames (CC-BY 4.0),
geoBoundaries (CC-BY 4.0) for Burkina Faso provinces.
"""
from __future__ import annotations

import io
import json
import logging
import urllib.request
import zipfile
from functools import cached_property
from pathlib import Path

import geopandas as gpd
import pandas as pd
from shapely.geometry import box

from .config import Config

log = logging.getLogger(__name__)

# Model domain (PNT grid) with a small margin
DOMAIN = (-6.2, 8.8, 3.3, 16.4)

NE = "https://naciscdn.org/naturalearth/10m/{kind}/ne_10m_{name}.zip"
NE_LAYERS = {
    "countries": ("cultural", "admin_0_countries"),
    "admin1": ("cultural", "admin_1_states_provinces"),
    "lakes": ("physical", "lakes"),
}
GEONAMES = "https://download.geonames.org/export/dump/{cc}.zip"
GEONAMES_COUNTRIES = ["BF", "ML", "NE", "GH", "CI", "TG", "BJ"]
GEONAMES_COLS = ["geonameid", "name", "asciiname", "alternatenames", "lat", "lon",
                 "fclass", "fcode", "cc", "cc2", "adm1", "adm2", "adm3", "adm4",
                 "population", "elevation", "dem", "tz", "moddate"]
GEOBOUNDARIES = "https://www.geoboundaries.org/api/current/gbOpen/BFA/ADM2/"


def _get(url: str, timeout: int = 120) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "bfa-flood-bulletin/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def prepare_geodata(cfg: Config, force: bool = False) -> None:
    out = cfg.path("geodata_dir")
    out.mkdir(parents=True, exist_ok=True)
    dom = box(*DOMAIN)

    for key, (kind, name) in NE_LAYERS.items():
        dst = out / f"{key}.gpkg"
        if dst.is_file() and not force:
            continue
        log.info("Downloading Natural Earth %s", name)
        tmp = out / f"_{name}.zip"
        tmp.write_bytes(_get(NE.format(kind=kind, name=name)))
        g = gpd.read_file(f"zip://{tmp}").to_crs("EPSG:4326")
        g = g[g.intersects(dom)].copy()
        if key in ("countries", "admin1"):
            # keep the full polygons of the countries touching the domain
            keep = ["NAME", "NAME_FR", "ADM0_A3", "name", "name_fr", "adm0_a3", "region", "geometry"]
            g = g[[c for c in keep if c in g.columns]]
        else:
            g = g[[c for c in ("name", "geometry") if c in g.columns]]
        g.to_file(dst, driver="GPKG")
        tmp.unlink()

    dst = out / "places.gpkg"
    if not dst.is_file() or force:
        frames = []
        for cc in GEONAMES_COUNTRIES:
            log.info("Downloading GeoNames %s", cc)
            z = zipfile.ZipFile(io.BytesIO(_get(GEONAMES.format(cc=cc))))
            with z.open(f"{cc}.txt") as f:
                df = pd.read_csv(f, sep="\t", header=None, names=GEONAMES_COLS, dtype=str,
                                 quoting=3, keep_default_na=False)
            df = df[df["fclass"] == "P"]
            frames.append(df)
        df = pd.concat(frames)
        df["lat"], df["lon"] = df["lat"].astype(float), df["lon"].astype(float)
        df["population"] = pd.to_numeric(df["population"], errors="coerce").fillna(0).astype(int)
        df = df[(df.lon > DOMAIN[0]) & (df.lon < DOMAIN[2]) & (df.lat > DOMAIN[1]) & (df.lat < DOMAIN[3])]
        g = gpd.GeoDataFrame(df[["name", "fcode", "cc", "population"]],
                             geometry=gpd.points_from_xy(df.lon, df.lat), crs="EPSG:4326")
        g.to_file(dst, driver="GPKG")

    dst = out / "provinces.gpkg"
    if not dst.is_file() or force:
        try:
            meta = json.loads(_get(GEOBOUNDARIES))
            url = meta.get("simplifiedGeometryGeoJSON") or meta["gjDownloadURL"]
            g = gpd.read_file(io.BytesIO(_get(url))).to_crs("EPSG:4326")
            g[[c for c in ("shapeName", "geometry") if c in g.columns]].to_file(dst, driver="GPKG")
        except Exception as exc:
            log.warning("Provinces (geoBoundaries) not downloaded: %s", exc)
    log.info("Geodata ready in %s", out)


class GeoLayers:
    """Lazy access to the background layers (empty GeoDataFrame if missing)."""

    def __init__(self, cfg: Config):
        self.dir = cfg.path("geodata_dir")

    def _read(self, name: str) -> gpd.GeoDataFrame:
        f = self.dir / f"{name}.gpkg"
        if not f.is_file():
            log.warning("Geodata layer %s missing (run --prepare-geodata)", f.name)
            return gpd.GeoDataFrame(geometry=[], crs="EPSG:4326")
        return gpd.read_file(f)

    @cached_property
    def countries(self):
        g = self._read("countries")
        if len(g):
            code = "ADM0_A3" if "ADM0_A3" in g.columns else "adm0_a3"
            g["iso3"] = g[code]
        return g

    @cached_property
    def burkina(self):
        c = self.countries
        return c[c["iso3"] == "BFA"] if len(c) else c

    @cached_property
    def admin1(self):
        g = self._read("admin1")
        if len(g):
            code = "adm0_a3" if "adm0_a3" in g.columns else "ADM0_A3"
            g = g[g[code] == "BFA"]
        return g

    @cached_property
    def regions(self):
        """BF regions = admin1 provinces dissolved on the Natural Earth 'region' field."""
        a = self.admin1
        if not len(a) or "region" not in a.columns:
            return a
        return a.dissolve(by="region", as_index=False)

    @cached_property
    def provinces(self):
        return self._read("provinces")

    @cached_property
    def lakes(self):
        return self._read("lakes")

    @cached_property
    def places(self):
        g = self._read("places")
        if len(g):
            rank = {"PPLC": 0, "PPLA": 1, "PPLA2": 2, "PPLA3": 3}
            g["rank"] = g["fcode"].map(rank).fillna(5)
        return g

    def main_towns(self, n: int):
        """Capital + largest towns of Burkina Faso."""
        p = self.places
        if not len(p):
            return p
        p = p[p["cc"] == "BF"].assign(_r=lambda d: d["rank"].clip(upper=2))
        return p.sort_values(["_r", "population"], ascending=[True, False]) \
                .drop_duplicates("name").head(n).drop(columns="_r")
