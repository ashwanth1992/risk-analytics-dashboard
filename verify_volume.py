"""
verify_volume.py — Cross-checks D1_PINCODE_VOLUME and CORRIDOR_D1_VOLUME stored in
pipeline_output.json against the raw D1_Tracker CSV, using the exact same pipeline
logic as process_data.py.

Three verification sections:
  1. PINCODE VOLUME  — re-computes monthly count per (pin, tier) and compares to JSON
  2. CORRIDOR VOLUME — same for (perm_state ->curr_state, tier)
  3. LEAD ARRAY INFO — shows overlap between risk portfolio leads and D1 disbursals
                        (different populations, informational only)

Usage:
    python verify_volume.py
    python verify_volume.py --top 20          # show top N mismatches
    python verify_volume.py --tol 0.05        # looser float tolerance
    python verify_volume.py --no-lead         # skip lead array section
    python verify_volume.py --pin 380001      # drill into a specific pincode
    python verify_volume.py --corridor "Maharashtra ->Gujarat"
"""

import json
import argparse
import sys
import pandas as pd
from pathlib import Path

# Force UTF-8 output so Unicode arrows and currency symbols render on Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ARROW = " → "  # Unicode right arrow — matches corridor keys in pipeline_output.json

# ── File paths ────────────────────────────────────────────────────────────────
DATA_JSON = Path("pipeline_output.json")
D1_FILE   = Path("sample_data/disbursement_tracker.csv")

# ── Config mirrors Phase2Config defaults ──────────────────────────────────────
SUBSTAGE_COL    = "mx_lead_substage"
SUBSTAGE_VAL    = "Disbursed"
DATE_COL        = "mx_lender_disbursal_date"   # dayfirst=True
RISK_COL        = "mx_risk_category"           # will be .str.title().str.strip()
PIN_COL         = "mx_current_address_zip"
PERM_STATE_COL  = "Perm Address State"
CURR_STATE_COL  = "Curr Address State"
D1_START        = "2026-03-01"
D1_END          = "2026-04-30"
CANONICAL_TIERS = ["Low", "Medium", "High", "Very High"]

# Mirrors process_data.py's _build_corridor_d1_volume normalization (added 2026-07-10) — without
# this, a raw D1-tracker spelling ("dadra and nagar haveli") won't match the title-cased +
# aliased corridor keys in CORRIDOR_DATA/CORRIDOR_D1_VOLUME, and this script would report
# spurious "missing" corridors that are actually present under the normalized name.
STATE_MAPPING = {
    "Nct Of Delhi": "Delhi", "Orissa": "Odisha", "Chattisgarh": "Chhattisgarh",
    "Tamilnadu": "Tamil Nadu", "Jammu & Kashmir": "Jammu and Kashmir",
    "Pondicherry": "Puducherry",
    "Dadra & Nagar Haveli And Daman & Diu": "Dadra and Nagar Haveli and Daman and Diu",
    "Andaman & Nicobar Islands": "Andaman and Nicobar Islands",
}

SEP = "=" * 64


def fmt_pin(v) -> str:
    """Exact replica of DataEngine._fmt_pin."""
    try:
        return str(int(float(v))).zfill(6)
    except Exception:
        return str(v).strip()


# ── Loaders ───────────────────────────────────────────────────────────────────

def load_dashboard() -> dict:
    print(f"Loading {DATA_JSON} …")
    with open(DATA_JSON, encoding="utf-8") as f:
        data = json.load(f)
    print(f"  Keys: {list(data.keys())}")
    return data


def load_and_filter_d1() -> tuple:
    """
    Returns (filtered_df, num_months) applying the exact same steps as
    _build_d1_pincode_volume in process_data.py.
    """
    print(f"\nLoading {D1_FILE} …")
    df = pd.read_csv(D1_FILE, low_memory=False)
    df.rename(columns=lambda x: x.strip() if isinstance(x, str) else x, inplace=True)
    print(f"  Raw rows : {len(df):,}")
    print(f"  Columns  : {list(df.columns[:12])}")

    # Check required columns
    needed = [SUBSTAGE_COL, DATE_COL, RISK_COL, PIN_COL]
    missing = [c for c in needed if c not in df.columns]
    if missing:
        raise ValueError(f"D1 tracker missing required columns: {missing}\n"
                         f"Available: {list(df.columns)}")

    # Step 1 — substage filter
    before = len(df)
    df = df[df[SUBSTAGE_COL].astype(str).str.strip() == SUBSTAGE_VAL].copy()
    print(f"  After substage='{SUBSTAGE_VAL}': {len(df):,} of {before:,}")

    # Step 2 — date parse + window filter
    df[DATE_COL] = pd.to_datetime(df[DATE_COL], dayfirst=True, errors="coerce")
    df = df[df[DATE_COL].notna()]
    start = pd.Timestamp(D1_START)
    end   = pd.Timestamp(D1_END) + pd.Timedelta(days=1) - pd.Timedelta(seconds=1)
    before2 = len(df)
    df = df[(df[DATE_COL] >= start) & (df[DATE_COL] <= end)]
    print(f"  After date window {D1_START} ->{D1_END}: {len(df):,} of {before2:,}")

    if df.empty:
        raise ValueError("Zero rows remain after date filter — check D1_START / D1_END dates")

    # Step 3 — tier normalisation + pin format
    df[RISK_COL] = df[RISK_COL].astype(str).str.title().str.strip()
    df["_pin"]   = df[PIN_COL].apply(fmt_pin)
    before3 = len(df)
    df = df[df[RISK_COL].isin(CANONICAL_TIERS) & df["_pin"].notna() & (df["_pin"] != "")]
    print(f"  After tier/pin filter: {len(df):,} of {before3:,}")

    if df.empty:
        raise ValueError("Zero rows remain after tier filter — check RISK_COL values")

    num_months = max(1, (end.year - start.year) * 12 + (end.month - start.month) + 1)
    print(f"  Window months (num_months) : {num_months}")
    print(f"  Unique pincodes in window  : {df['_pin'].nunique():,}")
    return df, num_months


# ── Recompute helpers ─────────────────────────────────────────────────────────

def recompute_pincode_volume(df: pd.DataFrame, num_months: int) -> dict:
    """Exact replica of the groupby in _build_d1_pincode_volume."""
    grp = df.groupby(["_pin", RISK_COL]).size().reset_index(name="cnt")
    result = {}
    for _, row in grp.iterrows():
        result.setdefault(row["_pin"], {})[row[RISK_COL]] = round(row["cnt"] / num_months, 4)
    return result


def recompute_corridor_volume(df: pd.DataFrame, num_months: int) -> dict:
    """Exact replica of _build_corridor_d1_volume."""
    if PERM_STATE_COL not in df.columns or CURR_STATE_COL not in df.columns:
        print(f"  [WARN] State columns ({PERM_STATE_COL!r}, {CURR_STATE_COL!r}) not in D1 data — skipping corridor check")
        return {}

    d = df.copy()
    d["_ps"] = d[PERM_STATE_COL].astype(str).str.title().str.strip().replace(STATE_MAPPING)
    d["_cs"] = d[CURR_STATE_COL].astype(str).str.title().str.strip().replace(STATE_MAPPING)
    bad = {"", "nan", "none", "na"}
    d = d[
        d["_ps"].str.lower().map(lambda x: x not in bad) &
        d["_cs"].str.lower().map(lambda x: x not in bad) &
        (d["_ps"] != d["_cs"])
    ]
    if d.empty:
        print("  [WARN] No inter-state records found for corridor volume")
        return {}

    d["_ck"] = d["_ps"] + ARROW + d["_cs"]
    grp = d.groupby(["_ck", RISK_COL]).size().reset_index(name="cnt")
    result = {}
    for _, row in grp.iterrows():
        result.setdefault(row["_ck"], {})[row[RISK_COL]] = round(row["cnt"] / num_months, 4)
    print(f"  Corridor records used  : {len(d):,}")
    print(f"  Unique corridors found : {len(result):,}")
    return result


# ── Comparison ────────────────────────────────────────────────────────────────

def compare_volumes(label: str, computed: dict, stored: dict, tol: float, top: int):
    print(f"\n{SEP}")
    print(f"  {label}")
    print(SEP)
    print(f"  Recomputed from D1 CSV : {len(computed):,} keys")
    print(f"  Stored in JSON         : {len(stored):,} keys")

    only_computed = set(computed) - set(stored)
    only_stored   = set(stored)   - set(computed)
    common        = set(computed) & set(stored)

    print(f"  In D1 CSV only (not in JSON)  : {len(only_computed):,}")
    print(f"  In JSON only (not in D1 CSV)  : {len(only_stored):,}")
    print(f"  Common keys                   : {len(common):,}")

    # Value-level mismatches on common keys
    mismatches = []
    exact_matches = 0
    for key in common:
        for tier in CANONICAL_TIERS:
            cv = computed.get(key, {}).get(tier)
            sv = stored.get(key,   {}).get(tier)
            if cv is None and sv is None:
                continue
            if cv is None or sv is None:
                delta = abs((cv or 0) - (sv or 0))
                mismatches.append((key, tier, cv, sv, delta, "ONE_MISSING"))
            elif abs(cv - sv) > tol:
                mismatches.append((key, tier, cv, sv, abs(cv - sv), "VALUE_DIFF"))
            else:
                exact_matches += 1

    print(f"  Value matches (within tol={tol}) : {exact_matches:,}")
    print(f"  Value mismatches                : {len(mismatches):,}")

    if mismatches:
        mismatches.sort(key=lambda x: -x[4])
        n = min(top, len(mismatches))
        print(f"\n  TOP {n} MISMATCHES (sorted by delta):")
        print(f"  {'Key':<30} {'Tier':<12} {'CSV':>10} {'JSON':>10} {'Delta':>9} {'Type'}")
        print(f"  {'-'*30} {'-'*12} {'-'*10} {'-'*10} {'-'*9} {'-'*12}")
        for key, tier, cv, sv, delta, mtype in mismatches[:n]:
            cv_s = f"{cv:.4f}" if cv is not None else "—"
            sv_s = f"{sv:.4f}" if sv is not None else "—"
            print(f"  {str(key):<30} {tier:<12} {cv_s:>10} {sv_s:>10} {delta:>9.4f} {mtype}")
    else:
        print("  ✓ All values match within tolerance")

    if only_computed:
        s = sorted(only_computed)[:5]
        print(f"\n  Sample: in D1 CSV but missing from JSON ->{s}")
    if only_stored:
        s = sorted(only_stored)[:5]
        print(f"  Sample: in JSON but missing from D1 CSV ->{s}")


# ── Drill-down: single pincode ────────────────────────────────────────────────

def drill_pincode(pin: str, df: pd.DataFrame, num_months: int,
                  stored_vol: dict, ats_data: dict):
    print(f"\n{SEP}")
    print(f"  PINCODE DRILL-DOWN: {pin}")
    print(SEP)
    sub = df[df["_pin"] == pin]
    if sub.empty:
        print(f"  No disbursals found for pin {pin} in D1 window.")
        return

    print(f"  Raw disbursals in window : {len(sub):,}")
    print(f"  Date range               : {sub[DATE_COL].min().date()} ->{sub[DATE_COL].max().date()}")
    print()
    print(f"  {'Tier':<12} {'D1 Count':>10} {'÷ months':>8} {'CSV vol':>10} {'JSON vol':>10} {'Δ':>8} {'ATS':>12} {'Loss/mo':>12}")
    print(f"  {'-'*12} {'-'*10} {'-'*8} {'-'*10} {'-'*10} {'-'*8} {'-'*12} {'-'*12}")

    tier_counts = sub.groupby(RISK_COL).size()
    for tier in CANONICAL_TIERS:
        cnt     = tier_counts.get(tier, 0)
        csv_vol = round(cnt / num_months, 4) if cnt > 0 else 0
        jsn_vol = stored_vol.get(pin, {}).get(tier)
        ats     = ats_data.get(tier, {}).get("ats", 0)
        loss    = (jsn_vol or 0) * ats / 100000  # in lakhs
        delta   = (csv_vol - (jsn_vol or 0)) if jsn_vol is not None else None
        jsn_s   = f"{jsn_vol:.4f}" if jsn_vol is not None else "—"
        delta_s = f"{delta:+.4f}" if delta is not None else "—"
        loss_s  = f"Rs.{loss:.2f}L" if jsn_vol else "—"
        print(f"  {tier:<12} {cnt:>10,} {cnt/num_months:>8.4f} {csv_vol:>10.4f} {jsn_s:>10} {delta_s:>8} {ats:>12,.0f} {loss_s:>12}")


# ── Drill-down: single corridor ───────────────────────────────────────────────

def drill_corridor(ck: str, df: pd.DataFrame, num_months: int,
                   stored_vol: dict, ats_data: dict):
    print(f"\n{SEP}")
    print(f"  CORRIDOR DRILL-DOWN: {ck}")
    print(SEP)

    if PERM_STATE_COL not in df.columns or CURR_STATE_COL not in df.columns:
        print("  State columns not available in D1 file.")
        return

    parts = [p.strip() for p in ck.split(ARROW.strip())]
    if len(parts) != 2:
        print(f"  Invalid corridor key format (expected 'A ->B'): {ck!r}")
        return

    perm, curr = parts
    d = df.copy()
    d["_ps"] = d[PERM_STATE_COL].astype(str).str.title().str.strip().replace(STATE_MAPPING)
    d["_cs"] = d[CURR_STATE_COL].astype(str).str.title().str.strip().replace(STATE_MAPPING)
    sub = d[(d["_ps"] == perm) & (d["_cs"] == curr)]

    if sub.empty:
        print(f"  No disbursals found for corridor {ck!r} in D1 window.")
        return

    print(f"  Raw inter-state disbursals : {len(sub):,}")
    print()
    print(f"  {'Tier':<12} {'D1 Count':>10} {'÷ months':>8} {'CSV vol':>10} {'JSON vol':>10} {'Δ':>8} {'ATS':>12} {'Loss/mo':>12}")
    print(f"  {'-'*12} {'-'*10} {'-'*8} {'-'*10} {'-'*10} {'-'*8} {'-'*12} {'-'*12}")

    tier_counts = sub.groupby(RISK_COL).size()
    for tier in CANONICAL_TIERS:
        cnt     = tier_counts.get(tier, 0)
        csv_vol = round(cnt / num_months, 4) if cnt > 0 else 0
        jsn_vol = stored_vol.get(ck, {}).get(tier)
        ats     = ats_data.get(tier, {}).get("ats", 0)
        loss    = (jsn_vol or 0) * ats / 100000
        delta   = (csv_vol - (jsn_vol or 0)) if jsn_vol is not None else None
        jsn_s   = f"{jsn_vol:.4f}" if jsn_vol is not None else "—"
        delta_s = f"{delta:+.4f}" if delta is not None else "—"
        loss_s  = f"Rs.{loss:.2f}L" if jsn_vol else "—"
        print(f"  {tier:<12} {cnt:>10,} {cnt/num_months:>8.4f} {csv_vol:>10.4f} {jsn_s:>10} {delta_s:>8} {ats:>12,.0f} {loss_s:>12}")


# ── Lead array informational check ────────────────────────────────────────────

def verify_lead_array(lead_array: list, d1_df: pd.DataFrame):
    print(f"\n{SEP}")
    print("  LEAD ARRAY  vs  D1 TRACKER  —  informational")
    print(SEP)
    print("  LEAD_ARRAY = risk portfolio (historical bounced/active loans)")
    print("  D1 filtered = recent NEW disbursals in the D1 window")
    print("  These are DIFFERENT populations. Overlap shows where both exist.\n")

    # Count portfolio leads by pin+tier
    la_by_pin = {}
    for lead in lead_array:
        pin  = fmt_pin(lead.get("pin", ""))
        tier = str(lead.get("r", "")).strip()
        if pin and tier in CANONICAL_TIERS:
            la_by_pin.setdefault(pin, {}).setdefault(tier, 0)
            la_by_pin[pin][tier] += 1

    total_la_leads = sum(sum(t.values()) for t in la_by_pin.values())
    print(f"  Portfolio (LEAD_ARRAY)")
    print(f"    Total leads   : {total_la_leads:,}")
    print(f"    Unique pins   : {len(la_by_pin):,}")

    # Count D1 disbursals by pin+tier (raw count, not monthly)
    d1_by_pin = {}
    for _, row in d1_df.iterrows():
        pin  = row["_pin"]
        tier = row[RISK_COL]
        if pin and tier in CANONICAL_TIERS:
            d1_by_pin.setdefault(pin, {}).setdefault(tier, 0)
            d1_by_pin[pin][tier] += 1

    total_d1 = sum(sum(t.values()) for t in d1_by_pin.values())
    print(f"\n  D1 Tracker (in window {D1_START} ->{D1_END})")
    print(f"    Total disbursals : {total_d1:,}")
    print(f"    Unique pins      : {len(d1_by_pin):,}")

    overlap   = set(la_by_pin) & set(d1_by_pin)
    only_la   = set(la_by_pin) - set(d1_by_pin)
    only_d1   = set(d1_by_pin) - set(la_by_pin)

    print(f"\n  Overlap (pin in BOTH portfolio and D1 window) : {len(overlap):,}")
    print(f"  Portfolio pincodes with NO recent D1 disbursal : {len(only_la):,}  ← these become Zero-D1 pins")
    print(f"  D1 pincodes not yet in portfolio               : {len(only_d1):,}")

    # Top pincodes by portfolio size that have zero D1 activity
    zero_d1_pins = []
    for pin in only_la:
        total = sum(la_by_pin[pin].values())
        zero_d1_pins.append((pin, total))
    zero_d1_pins.sort(key=lambda x: -x[1])

    if zero_d1_pins:
        print(f"\n  Top 10 portfolio pincodes with ZERO D1 disbursal (highest lead count first):")
        print(f"  {'Pincode':<10} {'Portfolio leads':>16}")
        print(f"  {'-'*10} {'-'*16}")
        for pin, cnt in zero_d1_pins[:10]:
            tiers = ", ".join(f"{t}:{v}" for t, v in la_by_pin[pin].items())
            print(f"  {pin:<10} {cnt:>16,}  [{tiers}]")


# ── ATS summary ───────────────────────────────────────────────────────────────

def print_ats(ats_data: dict):
    print(f"\n{SEP}")
    print("  ATS (Average Ticket Size) from JSON  —  used for loss calculation")
    print(SEP)
    print(f"  {'Tier':<12} {'ATS (Rs)':>14} {'Count':>10}")
    print(f"  {'-'*12} {'-'*14} {'-'*10}")
    for tier in CANONICAL_TIERS + ["Total"]:
        a = ats_data.get(tier, {})
        print(f"  {tier:<12} {a.get('ats',0):>14,.0f} {a.get('count',0):>10,}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Verify D1 volume data in pipeline_output.json")
    parser.add_argument("--top",       type=int,   default=15,   help="Top N mismatches to show per section")
    parser.add_argument("--tol",       type=float, default=0.01, help="Float tolerance for volume comparison")
    parser.add_argument("--no-lead",   action="store_true",      help="Skip lead array section")
    parser.add_argument("--pin",       type=str,   default=None, help="Drill into a specific pincode (6 digits)")
    parser.add_argument("--corridor",  type=str,   default=None, help="Drill into a specific corridor key, e.g. 'Maharashtra ->Gujarat'")
    args = parser.parse_args()

    data        = load_dashboard()
    stored_pin  = data.get("D1_PINCODE_VOLUME",  {})
    stored_corr = data.get("CORRIDOR_D1_VOLUME", {})
    lead_array  = data.get("LEAD_ARRAY", [])
    ats_data    = data.get("ATS_DATA_JSON", {})

    print_ats(ats_data)

    d1_df, num_months = load_and_filter_d1()

    # ── Section 1: Pincode volume ─────────────────────────────────────
    pin_computed = recompute_pincode_volume(d1_df, num_months)
    compare_volumes("PINCODE D1 VOLUME  (D1_PINCODE_VOLUME in JSON)",
                    pin_computed, stored_pin, args.tol, args.top)

    # ── Section 2: Corridor volume ────────────────────────────────────
    corr_computed = recompute_corridor_volume(d1_df, num_months)
    compare_volumes("CORRIDOR D1 VOLUME  (CORRIDOR_D1_VOLUME in JSON)",
                    corr_computed, stored_corr, args.tol, args.top)

    # ── Drill-downs ───────────────────────────────────────────────────
    if args.pin:
        drill_pincode(args.pin.zfill(6), d1_df, num_months, stored_pin, ats_data)

    if args.corridor:
        drill_corridor(args.corridor, d1_df, num_months, stored_corr, ats_data)

    # ── Section 3: Lead array ─────────────────────────────────────────
    if not args.no_lead:
        verify_lead_array(lead_array, d1_df)

    print(f"\n{SEP}")
    print("  DONE")
    print(SEP)


if __name__ == "__main__":
    main()
