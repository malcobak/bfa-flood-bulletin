"""Automatic French text of the bulletin and trend since the previous bulletin."""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pandas as pd

from .config import JOURS, Config
from .forecast import Forecast

RISK_WORDS = ["TRÈS FAIBLE", "FAIBLE", "MODÉRÉ", "ÉLEVÉ"]
COLOR_WORDS = ["verte", "jaune", "orange", "rouge"]


def river_name(section: str, row: pd.Series | None = None) -> str:
    return section.split("_", 1)[0].replace("-", " ")


def place_name(section: str) -> str:
    return section.split("_", 1)[1].replace("-", " ") if "_" in section else ""


def _enum(items: list[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " et " + items[-1]


def overall_level(fc: Forecast) -> int:
    v = fc.table["level_max"]
    return int(v.max()) if (v >= 0).any() else -1


def summary_text(fc: Forecast, max_items: int = 6) -> tuple[str, list[str]]:
    """(headline, one sentence per vigilance level)."""
    t = fc.table
    lvl = overall_level(fc)
    horizon = f"{len(fc.days)} prochains jours"
    if lvl < 0:
        return "Aucune donnée de prévision exploitable n'est disponible pour cette échéance.", []
    if lvl == 0:
        return (f"Aucun dépassement des seuils de vigilance n'est prévu sur les cours d'eau suivis pour les "
                f"{horizon}. Le risque de crue fluviale est {RISK_WORDS[0]}."), []
    parts = []
    for level in (3, 2, 1):
        sub = t[t["level_max"] == level].copy()
        if sub.empty:
            continue
        sub = sub.sort_values(["first_day", "AREA"], ascending=[True, False])
        # group by river so that a river is cited once
        items, seen = [], set()
        for name, r in sub.iterrows():
            riv = river_name(name)
            if riv in seen:
                continue
            seen.add(riv)
            region = r.get("ADMIN_B_L1")
            day = JOURS[fc.days[int(r["first_day"])].weekday()] if r["first_day"] >= 0 else ""
            where = f" ({region})" if isinstance(region, str) and region not in ("-", "NA") else ""
            items.append(f"{riv}{where} dès {day}")
        more = len(items) - max_items
        txt = _enum(items[:max_items] + ([f"{more} autre(s) cours d'eau"] if more > 0 else []))
        n = len(sub)
        parts.append(f"Vigilance {COLOR_WORDS[level]} : {n} point(s) de rapport - {txt}.")
    head = {
        1: "Des montées des eaux avec possibles débordements localisés sont prévues.",
        2: "Des crues importantes avec débordements sont possibles sur certains cours d'eau.",
        3: "Des crues majeures sont prévues sur certains cours d'eau.",
    }[lvl]
    return (f"{head} Le risque global de crue fluviale est {RISK_WORDS[lvl]} pour les {horizon}.",
            parts)


def day_cards(fc: Forecast, previous: dict | None, cfg: Config) -> list[dict]:
    cards = []
    for k, d in enumerate(fc.days):
        col = f"d{k}"
        v = fc.table[col]
        lvl = int(v.max()) if (v >= 0).any() else -1
        n_alert = int((v >= 1).sum())
        trend, arrow = "—", ""
        if previous and d.isoformat() in previous.get("days", {}):
            p = previous["days"][d.isoformat()]
            now, before = (lvl, n_alert), (p["max"], p["n_alert"])
            trend, arrow = (("En hausse", "↑") if now > before else
                            ("En baisse", "↓") if now < before else ("Stable", "→"))
        elif previous:
            trend = "Nouvelle échéance"
        cards.append({"date": d, "col": col, "level": lvl, "n_alert": n_alert,
                      "counts": fc.level_counts(col), "trend": trend, "arrow": arrow})
    return cards


def save_summary(path: Path, fc: Forecast, number: str):
    data = {"issue": fc.issue.isoformat(), "run": fc.run.isoformat(), "number": number,
            "days": {}}
    for k, d in enumerate(fc.days):
        v = fc.table[f"d{k}"]
        data["days"][d.isoformat()] = {"max": int(v.max()) if (v >= 0).any() else -1,
                                       "n_alert": int((v >= 1).sum())}
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def load_previous_summary(output_dir: Path, issue: dt.date) -> dict | None:
    """Summary of the most recent bulletin issued before `issue`."""
    best = None
    for f in output_dir.glob("*/summary.json"):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            di = dt.date.fromisoformat(d["issue"])
        except Exception:
            continue
        if di < issue and (best is None or di > dt.date.fromisoformat(best["issue"])):
            best = d
    return best
