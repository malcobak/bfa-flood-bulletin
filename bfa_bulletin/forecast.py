"""Read FloodProofs hydrograph JSON files and compute daily vigilance levels.

One JSON per section and run. Relevant keys:
    section_name, section_discharge_thr_{alert,alarm,emergency}, time_run,
    time_period (comma separated, hourly), time_series_discharge_simulated-<model>
Missing values are -9999. Before `time_run` all models share the same
simulation (forced by observations); after it they differ (NWP forcing).
"""
from __future__ import annotations

import datetime as dt
import glob
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from .config import Config

log = logging.getLogger(__name__)

THR_KEYS = ["alert", "alarm", "emergency"]
SERIES_PREFIX = "time_series_discharge_simulated-"
NODATA = -9000.0  # anything below is missing
MIN_HOURS = 12    # minimum number of hourly values for a forecast day


@dataclass
class Forecast:
    run: dt.datetime                  # FloodProofs run time
    issue: dt.date                    # bulletin issue date (= first forecast day)
    days: list[dt.date]               # forecast days shown on the bulletin
    table: pd.DataFrame               # one row per section (index = SEC_NAME)
    series: dict[str, pd.DataFrame] = field(repr=False, default_factory=dict)
    models: list[str] = field(default_factory=list)   # models with data in this run

    @property
    def day_cols(self) -> list[str]:
        return [f"d{k}" for k in range(len(self.days))]

    def level_counts(self, col: str) -> dict[int, int]:
        v = self.table[col]
        return {lvl: int((v == lvl).sum()) for lvl in range(4)}


def _split(s: str) -> list[str]:
    return [x for x in str(s).strip().strip(",").split(",") if x != ""]


def read_json(path: Path) -> tuple[dict, pd.DataFrame]:
    """Return (metadata, hourly series DataFrame with one column per model)."""
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    times = pd.to_datetime(_split(d["time_period"]), format="%Y-%m-%d %H:%M")
    cols = {}
    for k, v in d.items():
        if not k.startswith(SERIES_PREFIX):
            continue
        vals = np.array([float(x) for x in _split(v)], dtype=float)
        if len(vals) != len(times):  # truncated series: pad / cut
            vals = np.resize(np.append(vals, [np.nan] * len(times)), len(times))
        vals[vals < NODATA] = np.nan
        if np.isfinite(vals).any():
            cols[k[len(SERIES_PREFIX):]] = vals
    meta = {k: v for k, v in d.items() if not k.startswith("time_series") and k != "time_period"}
    return meta, pd.DataFrame(cols, index=times)


def find_run_files(cfg: Config, issue: dt.date, root: Path | None = None,
                   pattern: str | None = None) -> tuple[dt.datetime, list[Path]]:
    """Most recent run (<= issue date) having files, looking back N days."""
    root = Path(root) if root else cfg.path("forecast_root")
    pattern = pattern or cfg["paths"]["forecast_pattern"]
    hour = int(cfg["bulletin"].get("run_hour", 0))
    forced = cfg["bulletin"].get("force_run")  # run date chosen by hand (web app)
    if forced:
        forced = dt.date.fromisoformat(str(forced))
        backs = [(issue - forced).days]
    else:
        backs = range(int(cfg["bulletin"].get("max_run_lookback_days", 3)) + 1)
    for back in backs:
        run = dt.datetime.combine(issue - dt.timedelta(days=back), dt.time(hour))
        # Expand {run:%fmt} placeholders
        pat = re.sub(r"\{run:([^}]*)\}", lambda m: run.strftime(m.group(1)), pattern)
        files = sorted(Path(p) for p in glob.glob(str(root / pat)))
        if files:
            if back:
                log.warning("Run of %s not found, using run %s", issue, run)
            return run, files
        log.info("No file for run %s (%s)", run, root / pat)
    raise FileNotFoundError(f"No FloodProofs output found for {issue} in {root} ({pattern})")


def _level(q: float, thr: tuple[float, float, float]) -> int:
    if not np.isfinite(q):
        return -1
    return int(sum(q >= t for t in thr if np.isfinite(t)))


def _aggregate(levels: list[int], min_models: int) -> int:
    """Highest level reached by at least `min_models` models."""
    levels = [x for x in levels if x >= 0]
    if not levels:
        return -1
    k = max(1, min(min_models, len(levels)))
    return int(sorted(levels, reverse=True)[k - 1])


def load_forecast(cfg: Config, issue: dt.date, sections: pd.DataFrame | None = None,
                  root: Path | None = None, pattern: str | None = None) -> Forecast:
    run, files = find_run_files(cfg, issue, root, pattern)
    n_days = int(cfg["bulletin"]["n_days"])
    days = [issue + dt.timedelta(days=k) for k in range(n_days)]
    min_models = int(cfg["models"].get("min_models", 1))
    model_order = list(cfg["models"]["names"])

    rows, series, seen_models = [], {}, set()
    for path in files:
        try:
            meta, df = read_json(path)
        except Exception as exc:  # corrupt file: skip, keep the bulletin going
            log.error("Cannot read %s: %s", path, exc)
            continue
        name = meta["section_name"]
        enabled = cfg["models"].get("enabled")  # subset of models chosen by hand (web app)
        if enabled:
            df = df[[c for c in df.columns if c in enabled]]
        thr = []
        for k in THR_KEYS:
            try:
                thr.append(float(meta[f"section_discharge_thr_{k}"]))
            except (KeyError, ValueError):
                thr.append(np.nan)
        if sections is not None and name in sections.index and not np.isfinite(thr).all():
            thr = [float(sections.loc[name, c]) for c in ("Q_THR1", "Q_THR2", "Q_THR3")]
        thr = tuple(thr)
        t_run = pd.to_datetime(meta.get("time_run", run.strftime("%Y%m%d%H%M")), format="%Y%m%d%H%M")
        fc = df[df.index >= t_run]
        seen_models.update(df.columns)

        row = {"section": name, "thr_alert": thr[0], "thr_alarm": thr[1],
               "thr_emergency": thr[2], "n_models": df.shape[1],
               "domain": meta.get("section_domain"),
               "drained_area": float(meta.get("section_drained_area", np.nan))}
        for k, day in enumerate(days):
            sl = fc[(fc.index >= pd.Timestamp(day)) & (fc.index < pd.Timestamp(day + dt.timedelta(days=1)))]
            # a day needs at least MIN_HOURS hourly values (avoid partial last day)
            per_model = [_level(sl[m].max(), thr) for m in sl.columns] if len(sl) >= MIN_HOURS else []
            row[f"d{k}"] = _aggregate(per_model, min_models)
            row[f"q{k}"] = float(sl.max().max()) if len(sl) >= MIN_HOURS and sl.notna().any().any() else np.nan
        # Peak over the bulletin window
        win = fc[(fc.index >= pd.Timestamp(days[0])) & (fc.index < pd.Timestamp(days[-1] + dt.timedelta(days=1)))]
        if len(win) and win.notna().any().any():
            m_peak = win.max().idxmax()
            row.update(peak_q=float(win[m_peak].max()), peak_time=win[m_peak].idxmax(),
                       peak_model=m_peak,
                       models_alert=int((win.max() >= thr[0]).sum()))
        else:
            row.update(peak_q=np.nan, peak_time=pd.NaT, peak_model=None, models_alert=0)
        rows.append(row)
        series[name] = df[[m for m in model_order if m in df.columns]
                          + [m for m in df.columns if m not in model_order]]

    table = pd.DataFrame(rows).drop_duplicates("section").set_index("section")
    day_cols = [f"d{k}" for k in range(n_days)]
    table["level_max"] = table[day_cols].max(axis=1)
    # First day with the max level
    table["first_day"] = [next((k for k, c in enumerate(day_cols) if r[c] == r["level_max"]), -1)
                          for _, r in table.iterrows()]
    if sections is not None:
        keep = [c for c in ("BASIN", "TYPE", "SEC_RS", "ADMIN_B_L1", "ADMIN_B_L2", "ADMIN_B_L3",
                            "AREA", "LON", "LAT") if c in sections.columns]
        table = table.join(sections[keep], how="left")
    models = [m for m in model_order if m in seen_models] + sorted(seen_models - set(model_order))
    log.info("Run %s: %d sections, models %s", run, len(table), models)
    return Forecast(run=run, issue=issue, days=days, table=table, series=series, models=models)


def available_runs(cfg: Config, root: Path | None = None, pattern: str | None = None) -> list[dt.datetime]:
    """Run datetimes that have files under forecast_root (most recent first)."""
    root = Path(root) if root else cfg.path("forecast_root")
    pattern = pattern or cfg["paths"]["forecast_pattern"]
    pat = re.sub(r"\{run:[^}]*\}", "*", pattern)
    runs = set()
    for p in glob.glob(str(root / pat)):
        m = re.search(r"(\d{12})\.json$", p)
        if m:
            runs.add(dt.datetime.strptime(m.group(1), "%Y%m%d%H%M"))
    return sorted(runs, reverse=True)
