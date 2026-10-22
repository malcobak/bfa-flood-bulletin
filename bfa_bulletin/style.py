"""Shared visual style: bundled Fira Sans font (close to the UK FGS typeface) and ink colour."""
from __future__ import annotations

from pathlib import Path

import matplotlib
from matplotlib import font_manager

FONT_DIR = Path(__file__).resolve().parent.parent / "assets" / "fonts"
FONT_FAMILY = "Fira Sans"
INK = "#000000"


def setup_matplotlib():
    for f in sorted(FONT_DIR.glob("*.ttf")):
        font_manager.fontManager.addfont(str(f))
    family = FONT_FAMILY if any(FONT_DIR.glob("*.ttf")) else "DejaVu Sans"
    matplotlib.rcParams.update({"font.family": family, "font.size": 8, "text.color": INK,
                                "axes.labelcolor": INK})


setup_matplotlib()
