"""Static hydrographic layers: reporting points, river network and upstream
basins delineated from the Continuum drainage directions (PNT).

Continuum PNT coding (numeric keypad layout, 5 = outlet):
    7 8 9      NW N NE
    4 5 6  ->  W  .  E
    1 2 3      SW S SE
Section cells: HMC_X = row, HMC_Y = column, 1-based from the upper-left corner.
This reimplements the delineation of Continuum_PNT_delimitation_bassins_FR.ipynb
with numpy only (no pyflwdir dependency).
"""
from __future__ import annotations

import logging
from functools import cached_property
from pathlib import Path

import geopandas as gpd
import numpy as np
import rasterio
from rasterio.features import shapes
from shapely.geometry import shape
from shapely.ops import unary_union

from .config import Config

log = logging.getLogger(__name__)

PNT_OFFSETS = {7: (-1, -1), 8: (-1, 0), 9: (-1, 1), 4: (0, -1), 6: (0, 1),
               1: (1, -1), 2: (1, 0), 3: (1, 1)}


class Hydro:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.cache_dir = cfg.path("cache_dir")
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ layers
    @cached_property
    def sections(self) -> gpd.GeoDataFrame:
        s = gpd.read_file(self.cfg.static("sections"))
        if s.crs is None:
            s = s.set_crs("EPSG:4326")
        s = s.to_crs("EPSG:4326")
        # Place points at the centre of their model cell (as in the notebook)
        t = self.transform
        s["_row0"] = s["HMC_X"].astype(int) - 1
        s["_col0"] = s["HMC_Y"].astype(int) - 1
        xs, ys = rasterio.transform.xy(t, s["_row0"].to_numpy(), s["_col0"].to_numpy(), offset="center")
        s = s.set_geometry(gpd.points_from_xy(xs, ys), crs="EPSG:4326")
        return s.drop_duplicates("SEC_NAME").set_index("SEC_NAME", drop=False)

    @cached_property
    def rivers(self) -> gpd.GeoDataFrame:
        r = gpd.read_file(self.cfg.static("rivers"))
        if r.crs is None:
            r = r.set_crs("EPSG:4326")
        return r.to_crs("EPSG:4326")

    # -------------------------------------------------------------- PNT / D8
    @cached_property
    def _pnt(self):
        with rasterio.open(self.cfg.static("pnt")) as src:
            return src.read(1), src.transform

    @property
    def transform(self):
        return self._pnt[1]

    @cached_property
    def _upstream_index(self):
        """CSR-like structure: children (upstream cells) of every cell."""
        pnt, _ = self._pnt
        nr, nc = pnt.shape
        rows, cols = np.indices(pnt.shape)
        down = np.full(nr * nc, -1, dtype=np.int64)
        for code, (dr, dc) in PNT_OFFSETS.items():
            m = pnt == code
            rr, cc = rows[m] + dr, cols[m] + dc
            ok = (rr >= 0) & (rr < nr) & (cc >= 0) & (cc < nc)
            down[(rows[m] * nc + cols[m])[ok]] = (rr * nc + cc)[ok]
        src = np.nonzero(down >= 0)[0]
        tgt = down[src]
        order = np.argsort(tgt, kind="stable")
        src, tgt = src[order], tgt[order]
        cells = np.arange(nr * nc)
        return src, np.searchsorted(tgt, cells), np.searchsorted(tgt, cells, side="right")

    def upstream_mask(self, row0: int, col0: int) -> np.ndarray:
        pnt, _ = self._pnt
        nr, nc = pnt.shape
        src, start, end = self._upstream_index
        out = np.zeros(nr * nc, dtype=bool)
        stack = [row0 * nc + col0]
        while stack:
            i = stack.pop()
            out[i] = True
            stack.extend(src[start[i]:end[i]])
        return out.reshape(nr, nc)

    def basin_polygon(self, section: str):
        sec = self.sections.loc[section]
        mask = self.upstream_mask(int(sec["_row0"]), int(sec["_col0"]))
        geoms = [shape(g) for g, v in shapes(mask.astype(np.uint8), mask=mask, transform=self.transform) if v == 1]
        return unary_union(geoms), int(mask.sum())

    @cached_property
    def basins(self) -> gpd.GeoDataFrame:
        """Upstream basin of every section (cached in a GeoPackage)."""
        cache = self.cache_dir / "basins.gpkg"
        src_files = [self.cfg.static("pnt"), self.cfg.static("sections")]
        if cache.is_file() and cache.stat().st_mtime > max(f.stat().st_mtime for f in src_files):
            b = gpd.read_file(cache)
            if set(b["section"]) == set(self.sections.index):
                return b.set_index("section", drop=False)
        log.info("Delineating %d basins from %s", len(self.sections), self.cfg.static("pnt").name)
        recs = []
        for name in self.sections.index:
            geom, ncell = self.basin_polygon(name)
            recs.append({"section": name, "n_cells": ncell, "geometry": geom})
        b = gpd.GeoDataFrame(recs, crs="EPSG:4326")
        b.to_file(cache, driver="GPKG")
        return b.set_index("section", drop=False)

    def network_segments(self, min_cells: int, extent=None):
        """Drainage network from the PNT + area rasters: one segment from each
        cell centre (drained area >= min_cells) to its downstream cell centre.
        Returns (segments [n, 2, 2] lon/lat, drained cells [n])."""
        pnt, t = self._pnt
        with rasterio.open(self.cfg.static("area")) as src:
            area = src.read(1).astype(float)
        nr, nc = pnt.shape
        rows, cols = np.indices(pnt.shape)
        segs, vals = [], []
        for code, (dr, dc) in PNT_OFFSETS.items():
            m = (pnt == code) & (area >= min_cells)
            r0, c0 = rows[m], cols[m]
            r1, c1 = r0 + dr, c0 + dc
            ok = (r1 >= 0) & (r1 < nr) & (c1 >= 0) & (c1 < nc)
            r0, c0, r1, c1 = r0[ok], c0[ok], r1[ok], c1[ok]
            x0, y0 = t.c + (c0 + 0.5) * t.a, t.f + (r0 + 0.5) * t.e
            x1, y1 = t.c + (c1 + 0.5) * t.a, t.f + (r1 + 0.5) * t.e
            segs.append(np.stack([np.stack([x0, y0], 1), np.stack([x1, y1], 1)], 1))
            vals.append(area[r0, c0])
        segs, vals = np.concatenate(segs), np.concatenate(vals)
        if extent is not None:
            lon0, lat0, lon1, lat1 = extent
            x, y = segs[:, 0, 0], segs[:, 0, 1]
            k = (x >= lon0) & (x <= lon1) & (y >= lat0) & (y <= lat1)
            segs, vals = segs[k], vals[k]
        return segs, vals

    def sections_in(self, geom) -> list[str]:
        """Sections located inside a polygon (e.g. those upstream of an outlet)."""
        s = self.sections
        return list(s.index[s.within(geom.buffer(1e-6))])
