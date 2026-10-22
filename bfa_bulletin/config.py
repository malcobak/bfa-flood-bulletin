"""Configuration loading and small shared helpers."""
from __future__ import annotations

import datetime as dt
from pathlib import Path

import yaml

PACKAGE_ROOT = Path(__file__).resolve().parent.parent

JOURS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
MOIS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août",
        "septembre", "octobre", "novembre", "décembre"]


class Config(dict):
    """Dict with attribute access to top-level sections and path resolution."""

    def __init__(self, data: dict, base_dir: Path):
        super().__init__(data)
        self.base_dir = base_dir

    def path(self, key: str) -> Path:
        """Resolve paths.<key> relative to the config folder."""
        p = Path(self["paths"][key])
        return p if p.is_absolute() else (self.base_dir / p).resolve()

    def static(self, key: str) -> Path:
        """Path of a file of the static data folder (sections, rivers, pnt)."""
        return self.path("static_data") / self["paths"][key]


def _merge(base: dict, over: dict) -> dict:
    """Recursive update of `base` with `over` (dicts merged, other values replaced)."""
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _merge(base[k], v)
        else:
            base[k] = v
    return base


def load_config(path: str | Path | None = None) -> Config:
    """config.yaml, updated by config.local.yaml (same folder) when it exists.

    config.local.yaml holds the settings of one installation (paths of the
    FloodProofs outputs, contact...) and is not versioned."""
    path = Path(path) if path else PACKAGE_ROOT / "config.yaml"
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    local = path.with_name("config.local.yaml")
    if local.is_file():
        with open(local, encoding="utf-8") as f:
            _merge(data, yaml.safe_load(f) or {})
    return Config(data, path.resolve().parent)


def load_yaml(path: str | Path | None) -> dict:
    if not path:
        return {}
    path = Path(path)
    if not path.is_file():
        return {}
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def fr_date(d: dt.date, weekday: bool = True, year: bool = True) -> str:
    """'lundi 5 octobre 2026'."""
    s = f"{d.day} {MOIS[d.month - 1]}"
    if year:
        s += f" {d.year}"
    if weekday:
        s = f"{JOURS[d.weekday()]} {s}"
    return s


def bulletin_number(issue: dt.date, cfg: Config) -> str:
    """n-th issue day (Mon/Thu by default) since the season start."""
    start = cfg["bulletin"].get("season_start") or dt.date(issue.year, 1, 1)
    if isinstance(start, str):
        start = dt.date.fromisoformat(start)
    days = set(cfg["bulletin"]["issue_weekdays"])
    n = sum(1 for k in range((issue - start).days + 1)
            if (start + dt.timedelta(days=k)).weekday() in days)
    return f"{issue.year}-{max(n, 1):02d}"


def next_issue(issue: dt.date, cfg: Config) -> dt.date:
    days = set(cfg["bulletin"]["issue_weekdays"])
    d = issue + dt.timedelta(days=1)
    while d.weekday() not in days:
        d += dt.timedelta(days=1)
    return d
