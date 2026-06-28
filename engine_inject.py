"""
engine_inject.py — Layer 3: dashboard_data.json → final HTML dashboard
Run this for any UI/template-only changes. Typical runtime: 2-5 seconds.

Usage:
    python engine_inject.py                          # uses defaults
    python engine_inject.py --data my_data.json      # custom data file
    python engine_inject.py --template my_tmpl.html  # custom template
    python engine_inject.py --out output/dash.html   # custom output path
"""

import json
import logging
import argparse
from pathlib import Path
from datetime import datetime

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s', datefmt="%H:%M:%S")
logger = logging.getLogger("Inject")

# ── DEFAULTS ────────────────────────────────────────────────────────────────
DEFAULT_DATA     = Path("dashboard_data.json")
DEFAULT_TEMPLATE = Path("template_Finalized.html")
DEFAULT_OUT_DIR  = Path("Dashboard_Build")
DEFAULT_OUT_NAME = "Phase3_Dashboard_Finalized.html"

# Mapping: placeholder token → key in dashboard_data.json
# GEOJSON_DATA is a raw string (not JSON-encoded twice); everything else gets json.dumps()
PLACEHOLDER_MAP = [
    ("__PORTFOLIO_STATS__",   "PORTFOLIO_STATS",   True),
    ("__REGION_DATA__",       "REGION_DATA",       True),
    ("__GEOJSON_DATA__",      "GEOJSON_DATA",      False),   # already a JSON string
    ("__BUREAU_DATA_JSON__",  "BUREAU_DATA_JSON",  True),
    ("__ATS_DATA_JSON__",     "ATS_DATA_JSON",     True),
    ("__WINDOW_CONFIG__",     "WINDOW_CONFIG",     True),
    ("__CORRIDOR_DATA__",     "CORRIDOR_DATA",     True),
    ("__LEAD_ARRAY__",        "LEAD_ARRAY",        True),
    ("__REJECTION_DATA__",    "REJECTION_DATA",    True),
    ("__PINCODE_RISK_DATA__", "PINCODE_RISK_DATA", True),
    ("__BUREAU_PINCODE_DATA__","BUREAU_PINCODE_DATA",True),
    ("__PINCODE_MAP_DATA__",  "PINCODE_MAP_DATA",  True),
    ("__GREEN_PINCODE_STATS__","GREEN_PINCODE_STATS",True),
    ("__PINCODE_COORDS__",    "PINCODE_COORDS",    True),
    ("__D1_PINCODE_VOLUME__",       "D1_PINCODE_VOLUME",       True),   # Part 3
    ("__CORRIDOR_D1_VOLUME__",      "CORRIDOR_D1_VOLUME",      True),   # Part 3b
    ("__MONTHLY_DISBURSAL_BASE__",  "MONTHLY_DISBURSAL_BASE",  True),   # rupees/month for % normalisation
]


def inject(data_path: Path, template_path: Path, out_path: Path) -> None:
    t0 = datetime.now()

    # ── load data ───────────────────────────────────────────────────────────
    if not data_path.exists():
        raise FileNotFoundError(
            f"dashboard_data.json not found at {data_path.resolve()}\n"
            "Run engine_data.py first to generate it."
        )
    logger.info(f"Loading {data_path} …")
    with open(data_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    logger.info(f"  {data_path.stat().st_size / 1_048_576:.1f} MB loaded")

    # ── load template ────────────────────────────────────────────────────────
    if not template_path.exists():
        raise FileNotFoundError(f"Template not found: {template_path.resolve()}")
    logger.info(f"Loading template …")
    html = template_path.read_text(encoding='utf-8')

    # ── inject ───────────────────────────────────────────────────────────────
    missing = []
    for placeholder, key, do_dumps in PLACEHOLDER_MAP:
        if key not in data:
            missing.append(key)
            continue
        value = json.dumps(data[key], ensure_ascii=False) if do_dumps else data[key]
        html = html.replace(placeholder, value)

    if missing:
        logger.warning(f"Keys missing from data file (placeholders left unreplaced): {missing}")

    # ── write output ─────────────────────────────────────────────────────────
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding='utf-8')
    elapsed = (datetime.now() - t0).total_seconds()
    size_mb = out_path.stat().st_size / 1_048_576
    logger.info(f"Done → {out_path.resolve()}  ({size_mb:.1f} MB, {elapsed:.1f}s)")


def main():
    parser = argparse.ArgumentParser(description="Inject dashboard_data.json into HTML template")
    parser.add_argument("--data",     type=Path, default=DEFAULT_DATA,
                        help=f"Path to dashboard_data.json (default: {DEFAULT_DATA})")
    parser.add_argument("--template", type=Path, default=DEFAULT_TEMPLATE,
                        help=f"Path to HTML template (default: {DEFAULT_TEMPLATE})")
    parser.add_argument("--out",      type=Path, default=DEFAULT_OUT_DIR / DEFAULT_OUT_NAME,
                        help=f"Output HTML path (default: {DEFAULT_OUT_DIR}/{DEFAULT_OUT_NAME})")
    args = parser.parse_args()
    inject(args.data, args.template, args.out)


if __name__ == "__main__":
    main()