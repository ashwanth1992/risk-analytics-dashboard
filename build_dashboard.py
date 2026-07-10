"""
build_dashboard.py — Layer 3: pipeline_output.json → final HTML dashboard
Run this for any UI/template-only changes. Typical runtime: 15-20 seconds at current build size.

Usage:
    python build_dashboard.py                          # uses defaults
    python build_dashboard.py --data my_data.json      # custom data file
    python build_dashboard.py --template my_tmpl.html  # custom template
    python build_dashboard.py --out output/dash.html   # custom output path
"""

import json
import logging
import argparse
import re
import sys
from pathlib import Path
from datetime import datetime

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s', datefmt="%H:%M:%S")
logger = logging.getLogger("Inject")

# ── DEFAULTS ────────────────────────────────────────────────────────────────
DEFAULT_DATA     = Path("pipeline_output.json")
DEFAULT_TEMPLATE = Path("dashboard_template.html")
DEFAULT_OUT_DIR  = Path("demo")
DEFAULT_OUT_NAME = "dashboard.html"

# Mapping: placeholder token → key in pipeline_output.json
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


def inject(data_path: Path, template_path: Path, out_path: Path, allow_missing: bool = False) -> None:
    t0 = datetime.now()

    # ── load data ───────────────────────────────────────────────────────────
    if not data_path.exists():
        raise FileNotFoundError(
            f"pipeline_output.json not found at {data_path.resolve()}\n"
            "Run process_data.py first to generate it."
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
    # A missing key used to be just a warning + exit 0, and the template's own
    # `typeof __TOKEN__!=="undefined"` guards would silently default that section to
    # empty — a "successful" build could ship with an invisibly empty data section.
    # Now it's a hard error by default (--allow-missing to opt back into the old,
    # permissive behavior for a deliberate partial build).
    missing = [key for _, key, _ in PLACEHOLDER_MAP if key not in data]
    if missing:
        msg = f"Keys missing from data file: {missing}"
        if allow_missing:
            logger.warning(msg + " — placeholders will be left unreplaced (--allow-missing set)")
        else:
            raise KeyError(msg + ". Re-run process_data.py, or pass --allow-missing to build anyway.")

    # Single-pass replace (was 17 sequential str.replace calls, each a full-string copy of a
    # ~450MB string — ~7+GB of transient churn) via one regex pass keyed on the placeholder map.
    replacements = {}
    for placeholder, key, do_dumps in PLACEHOLDER_MAP:
        if key not in data:
            continue
        replacements[placeholder] = json.dumps(data[key], ensure_ascii=False) if do_dumps else data[key]
    pattern = re.compile("|".join(re.escape(p) for p in replacements))
    html = pattern.sub(lambda m: replacements[m.group(0)], html)

    # A token surviving into the output means either a typo in PLACEHOLDER_MAP or a template
    # edit that introduced a new __TOKEN__ without a matching JSON key — catch it before ship
    # rather than let it render as literal text or an undefined JS identifier.
    leftover = sorted(set(re.findall(r"__[A-Z][A-Z0-9_]*__", html)))
    if leftover:
        raise ValueError(f"Placeholder token(s) survived injection (not in pipeline_output.json): {leftover}")

    # ── write output ─────────────────────────────────────────────────────────
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding='utf-8')
    elapsed = (datetime.now() - t0).total_seconds()
    size_mb = out_path.stat().st_size / 1_048_576
    logger.info(f"Done → {out_path.resolve()}  ({size_mb:.1f} MB, {elapsed:.1f}s)")


def main():
    parser = argparse.ArgumentParser(description="Inject pipeline_output.json into HTML template")
    parser.add_argument("--data",     type=Path, default=DEFAULT_DATA,
                        help=f"Path to pipeline_output.json (default: {DEFAULT_DATA})")
    parser.add_argument("--template", type=Path, default=DEFAULT_TEMPLATE,
                        help=f"Path to HTML template (default: {DEFAULT_TEMPLATE})")
    parser.add_argument("--out",      type=Path, default=DEFAULT_OUT_DIR / DEFAULT_OUT_NAME,
                        help=f"Output HTML path (default: {DEFAULT_OUT_DIR}/{DEFAULT_OUT_NAME})")
    parser.add_argument("--allow-missing", action="store_true",
                        help="Don't fail on missing pipeline_output.json keys — leave their placeholders unreplaced (old default behavior)")
    args = parser.parse_args()
    inject(args.data, args.template, args.out, allow_missing=args.allow_missing)


if __name__ == "__main__":
    main()