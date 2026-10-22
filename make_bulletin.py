#!/usr/bin/env python3
"""Bulletin de surveillance des inondations au Burkina Faso (bi-hebdomadaire).

Examples
--------
  python make_bulletin.py --prepare-geodata              # once (downloads background layers)
  python make_bulletin.py                                # bulletin of today
  python make_bulletin.py --date 2026-10-01 --focus Mouhoun_Dapola --focus Pendjari_Arly
  python make_bulletin.py --date 2026-10-01 --auto-focus
  python make_bulletin.py --date 2026-10-01 --ffgs demo   # preview with a synthetic FFGS background
  python make_bulletin.py --date 2026-10-01 --forecast-root ../exemple_files \\
                          --forecast-pattern "hydro*_{run:%Y%m%d%H%M}.json"
  python make_bulletin.py --atlas                         # static basin map of every reporting point
  python make_bulletin.py --list-sections

Cron (Monday and Thursday, 09:30):
  30 9 * * 1,4  cd /path/to/bulletin_bfa && python make_bulletin.py >> output/cron.log 2>&1
"""
from __future__ import annotations

import argparse
import datetime as dt
import logging
import sys
import warnings
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from bfa_bulletin.config import JOURS, load_config  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", help="config.yaml (default: next to this script)")
    ap.add_argument("--date", help="issue date YYYY-MM-DD (default: today)")
    ap.add_argument("--focus", action="append", metavar="SEC_NAME",
                    help="reporting point to zoom on (repeatable); added to focus_stations.yaml")
    ap.add_argument("--auto-focus", action="store_true", default=None,
                    help="also add the most threatened reporting points automatically")
    ap.add_argument("--ffgs", help="FFGS file (vector with 'level' field or raster), or 'demo'")
    ap.add_argument("--notes", help="forecaster text (YAML, see notes_template.yaml)")
    ap.add_argument("--number", help="bulletin number (default: computed from season_start)")
    ap.add_argument("--forecast-root", help="override paths.forecast_root")
    ap.add_argument("--forecast-pattern", help="override paths.forecast_pattern")
    ap.add_argument("--html-only", action="store_true", help="do not render the PDF")
    ap.add_argument("--prepare-geodata", action="store_true", help="download background layers and exit")
    ap.add_argument("--atlas", nargs="*", metavar="SEC_NAME",
                    help="static basin maps of all (or the given) reporting points and exit")
    ap.add_argument("--list-sections", action="store_true", help="print the reporting points and exit")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    for noisy in ("fontTools", "weasyprint", "pyogrio", "numexpr", "matplotlib"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    warnings.filterwarnings("ignore", category=UserWarning)
    cfg = load_config(a.config)

    if a.prepare_geodata:
        from bfa_bulletin.geodata import prepare_geodata
        prepare_geodata(cfg, force=True)
        return 0
    if a.list_sections:
        from bfa_bulletin.hydro import Hydro
        s = Hydro(cfg).sections
        cols = ["BASIN", "TYPE", "ADMIN_B_L1", "ADMIN_B_L2", "AREA"]
        print(s[cols].sort_values(["BASIN", "AREA"]).to_string())
        return 0
    if a.atlas is not None:
        from bfa_bulletin.report import build_atlas
        print(build_atlas(cfg, a.atlas or None))
        return 0

    issue = dt.date.fromisoformat(a.date) if a.date else dt.date.today()
    if issue.weekday() not in cfg["bulletin"]["issue_weekdays"]:
        logging.warning("%s is a %s: not a regular issue day", issue, JOURS[issue.weekday()])

    from bfa_bulletin.report import build_bulletin
    res = build_bulletin(cfg, issue, focus=a.focus, auto_focus=a.auto_focus, ffgs_file=a.ffgs,
                         notes_file=a.notes, number=a.number, forecast_root=a.forecast_root,
                         forecast_pattern=a.forecast_pattern, html_only=a.html_only)
    print(f"Bulletin : {res['pdf'] or res['html']}")
    print(f"Run FloodProofs : {res['run']:%Y-%m-%d %H:%M} - niveau max : {res['overall']} - zones : {res['zones']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
