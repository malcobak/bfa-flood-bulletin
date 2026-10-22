"""Hydrographs: simulated past discharge + multi-model forecast vs thresholds."""
from __future__ import annotations

import datetime as dt
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from .config import Config
from .style import INK

# Categorical model colours (validated palette; avoids the green/yellow/orange/red
# reserved for vigilance levels). Line styles give a second, colour-free encoding.
MODEL_STYLES = [("#2a78d6", "-"), ("#4a3aa7", "--"), ("#1baf7a", "-."), ("#e87ba4", (0, (5, 1.5, 1, 1.5, 1, 1.5))),
                ("#6b6b6b", ":")]
JOURS_COURTS = ["lun.", "mar.", "mer.", "jeu.", "ven.", "sam.", "dim."]
MUTED, GRID = "#444444", "#e6e6e6"


def model_style(cfg: Config, model: str):
    names = list(cfg["models"]["names"])
    i = names.index(model) if model in names else len(names)
    return MODEL_STYLES[min(i, len(MODEL_STYLES) - 1)]


def hydrograph(path: Path, df: pd.DataFrame, row: pd.Series, run: dt.datetime, days: list[dt.date],
               cfg: Config, title: str, figsize=(7.4, 2.9), compact=False):
    names = cfg["models"]["names"]
    colors = cfg["levels"]["colors"]
    t_run = pd.Timestamp(run)
    past_days = (cfg.get("plots") or {}).get("past_days")
    if past_days:  # shorter simulated period on the left of the run line
        df = df[df.index >= t_run - pd.Timedelta(days=float(past_days))]
    past = df[df.index <= t_run]
    fut = df[df.index >= t_run]

    fig, ax = plt.subplots(figsize=figsize)
    fig.subplots_adjust(left=0.085 if not compact else 0.15, right=0.86 if not compact else 0.78,
                        top=0.88, bottom=0.30 if not compact else 0.36)

    thr = [row.get("thr_alert"), row.get("thr_alarm"), row.get("thr_emergency")]
    qmax = np.nanmax(df.to_numpy()) if df.notna().any().any() else 1.0
    # y range: data + the next threshold above the peak (keep thresholds readable)
    above = [t for t in thr if t is not None and np.isfinite(t) and t > qmax]
    top = max(qmax * 1.12, (above[0] * 1.08) if above else qmax * 1.12, 1e-3)
    ax.set_ylim(0, top)

    # threshold bands (level colour, light) + labelled lines
    bounds = [t if (t is not None and np.isfinite(t)) else np.inf for t in thr] + [np.inf]
    for k in range(3):
        lo, hi = bounds[k], bounds[k + 1]
        if lo < top:
            ax.axhspan(lo, min(hi, top), color=colors[k + 1], alpha=0.13, lw=0, zorder=0)
            ax.axhline(lo, color=colors[k + 1], lw=1.1, zorder=1)
            ax.text(1.005, lo, f" {['Seuil jaune', 'Seuil orange', 'Seuil rouge'][k]}\n {lo:,.0f} m³/s".replace(",", " "),
                    transform=ax.get_yaxis_transform(), va="center", ha="left", fontsize=6.5, color=INK)

    # run line (day boundaries are drawn by the x grid)
    ax.axvline(t_run, color=INK, lw=0.8, ls="--", zorder=2)
    ax.text(t_run, 1.01, "Prévision →", transform=ax.get_xaxis_transform(), fontsize=6.5, color=MUTED, ha="left")
    ax.text(t_run, 1.01, "← Simulation ", transform=ax.get_xaxis_transform(), fontsize=6.5, color=MUTED, ha="right")

    # past: one simulated series (identical for all models before the run)
    if len(past):
        p = past.bfill(axis=1).iloc[:, 0]
        ax.plot(p.index, p.values, color=INK, lw=2, label="Simulation (forçage observé)", zorder=4)
    for m in fut.columns:
        c, ls = model_style(cfg, m)
        ax.plot(fut.index, fut[m].values, color=c, lw=2 if not compact else 1.6, ls=ls,
                label=f"Prévision {names.get(m, m)}", zorder=5)

    ax.set_xlim(df.index[0], pd.Timestamp(days[-1] + dt.timedelta(days=1)))
    ax.xaxis.set_major_locator(mdates.DayLocator())
    ax.xaxis.set_major_formatter(plt.FuncFormatter(
        lambda x, _: (lambda d: f"{JOURS_COURTS[d.weekday()]}\n{d:%d/%m}")(mdates.num2date(x))))
    ax.tick_params(labelsize=7, colors=MUTED, length=0)
    ax.grid(axis="both", color=GRID, lw=0.6)
    ax.set_axisbelow(True)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color("#bdbdbd")
    ax.set_ylabel("Débit (m³/s)", fontsize=7.5, color=INK)
    ax.set_title(title, loc="left", fontsize=9 if not compact else 8, weight="bold", color=INK, pad=14)
    # legend below the axis so it never hides the curves or the thresholds
    ax.legend(loc="upper left", bbox_to_anchor=(0, -0.2 if not compact else -0.24),
              ncol=3 if not compact else 2, fontsize=6.5, frameon=False, handlelength=2.8,
              columnspacing=1.5)
    fig.savefig(path, dpi=cfg["maps"]["dpi"])
    plt.close(fig)
    return path


def daily_table(df: pd.DataFrame, row: pd.Series, run: dt.datetime, days: list[dt.date], cfg: Config) -> list[dict]:
    """Daily maximum per model with its level, for the HTML table."""
    thr = [row.get("thr_alert"), row.get("thr_alarm"), row.get("thr_emergency")]
    fut = df[df.index >= pd.Timestamp(run)]
    out = []
    for m in fut.columns:
        cells = []
        for d in days:
            sl = fut[(fut.index >= pd.Timestamp(d)) & (fut.index < pd.Timestamp(d + dt.timedelta(days=1)))][m]
            q = sl.max() if sl.notna().sum() >= 12 else np.nan
            lvl = -1 if not np.isfinite(q) else int(sum(q >= t for t in thr if t is not None and np.isfinite(t)))
            cells.append({"q": q, "level": lvl})
        out.append({"model": cfg["models"]["names"].get(m, m), "cells": cells})
    return out
