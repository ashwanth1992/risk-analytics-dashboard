"""
verify_dedup.py — Verifies that no D1 disbursal is double-counted across
region and corridor loss calculations.

Two layers of checking:

  LAYER 1 — Lead-level (LEAD_ARRAY)
    Each portfolio lead has one identity: either it is an inter-state migrant
    (perm_state != curr_state) or it is not. The simulation JS already prevents
    a single lead from being claimed by both region AND corridor via the
    `if (isCorrSelected && maxSev === 0)` guard. This layer confirms no lead ID
    appears more than once in LEAD_ARRAY.

  LAYER 2 — D1 volume-level (the financial risk)
    An inter-state disbursal (perm != curr) is counted in BOTH:
      - D1_PINCODE_VOLUME[pin][tier]   (used if region is selected)
      - CORRIDOR_D1_VOLUME[corr][tier] (used if corridor is selected)
    If a user selects BOTH a regional geography AND a corridor that overlap,
    those shared disbursals inflate the total loss figure.
    This layer quantifies exactly which disbursals are "dual-counted" and
    under what conditions double-counting would occur.

Usage:
    python verify_dedup.py
    python verify_dedup.py --corridor "Maharashtra -> Gujarat"  # single corridor detail
    python verify_dedup.py --top 15
"""

import json
import sys
import argparse
import pandas as pd
from pathlib import Path
from collections import defaultdict

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ARROW = " → "   # Unicode right arrow used in corridor keys

DATA_JSON      = Path("pipeline_output.json")
D1_FILE        = Path("sample_data/disbursement_tracker.csv")
SUBSTAGE_COL   = "mx_lead_substage"
SUBSTAGE_VAL   = "Disbursed"
DATE_COL       = "mx_lender_disbursal_date"
RISK_COL       = "mx_risk_category"
PIN_COL        = "mx_current_address_zip"
PERM_STATE_COL = "Perm Address State"
CURR_STATE_COL = "Curr Address State"
D1_START       = "2026-03-01"
D1_END         = "2026-04-30"
CANONICAL      = {"Low", "Medium", "High", "Very High"}
SEP = "=" * 68


def fmt_pin(v) -> str:
    try:
        return str(int(float(v))).zfill(6)
    except Exception:
        return str(v).strip()


# ── Data loaders ──────────────────────────────────────────────────────────────

def load_json() -> dict:
    print(f"Loading {DATA_JSON} ...")
    with open(DATA_JSON, encoding="utf-8") as f:
        return json.load(f)


def load_d1() -> pd.DataFrame:
    print(f"Loading {D1_FILE} ...")
    df = pd.read_csv(D1_FILE, low_memory=False)
    df.rename(columns=lambda x: x.strip() if isinstance(x, str) else x, inplace=True)
    df = df[df[SUBSTAGE_COL].astype(str).str.strip() == SUBSTAGE_VAL].copy()
    df[DATE_COL] = pd.to_datetime(df[DATE_COL], dayfirst=True, errors="coerce")
    df = df[df[DATE_COL].notna()]
    start = pd.Timestamp(D1_START)
    end   = pd.Timestamp(D1_END) + pd.Timedelta(days=1) - pd.Timedelta(seconds=1)
    df = df[(df[DATE_COL] >= start) & (df[DATE_COL] <= end)]
    df[RISK_COL] = df[RISK_COL].astype(str).str.title().str.strip()
    df["_pin"]   = df[PIN_COL].apply(fmt_pin)
    df = df[df[RISK_COL].isin(CANONICAL) & df["_pin"].notna() & (df["_pin"] != "")]
    print(f"  Disbursals in window ({D1_START} -> {D1_END}): {len(df):,}")

    if PERM_STATE_COL in df.columns and CURR_STATE_COL in df.columns:
        df["_ps"] = df[PERM_STATE_COL].astype(str).str.strip()
        df["_cs"] = df[CURR_STATE_COL].astype(str).str.strip()
        bad = {"", "nan", "none", "na"}
        df["_is_inter"] = (
            ~df["_ps"].str.lower().isin(bad) &
            ~df["_cs"].str.lower().isin(bad) &
            (df["_ps"] != df["_cs"])
        )
        df["_ck"] = df.apply(
            lambda r: r["_ps"] + ARROW + r["_cs"] if r["_is_inter"] else "", axis=1
        )
        print(f"  Inter-state disbursals (perm != curr): {df['_is_inter'].sum():,}")
        print(f"  Intra-state disbursals (perm == curr): {(~df['_is_inter']).sum():,}")
    else:
        print(f"  [WARN] State columns not found — corridor overlap check skipped")
        df["_is_inter"] = False
        df["_ck"]       = ""
    return df


# ── Layer 1: LEAD_ARRAY dedup ─────────────────────────────────────────────────

def check_lead_array(lead_array: list):
    print(f"\n{SEP}")
    print("  LAYER 1 — LEAD_ARRAY: Duplicate Lead IDs")
    print(SEP)

    total = len(lead_array)
    ids   = [str(lead.get("id", "")).strip() for lead in lead_array]
    blank = sum(1 for i in ids if not i)
    nonempty = [i for i in ids if i]

    print(f"  Total leads in LEAD_ARRAY : {total:,}")
    print(f"  Leads with no ID field    : {blank:,}")
    print(f"  Leads with an ID          : {len(nonempty):,}")

    from collections import Counter
    counts = Counter(nonempty)
    dupes  = {k: v for k, v in counts.items() if v > 1}

    if dupes:
        print(f"\n  !! DUPLICATES FOUND: {len(dupes):,} IDs appear more than once")
        for lead_id, cnt in sorted(dupes.items(), key=lambda x: -x[1])[:10]:
            print(f"     ID={lead_id!r}  appears {cnt}x")
    else:
        print(f"\n  OK — no duplicate lead IDs (each ID appears exactly once)")

    # Also check for duplicate (pin, tier, month) combos, which are valid
    # (multiple leads at same pin+tier+month) but good to know
    combos = [(fmt_pin(l.get("pin","")), l.get("r",""), l.get("mo","")) for l in lead_array]
    from collections import Counter as C2
    combo_counts = C2(combos)
    repeated = sum(1 for v in combo_counts.values() if v > 1)
    print(f"\n  Unique (pin, tier, month) combos : {len(combo_counts):,}")
    print(f"  Combos with multiple leads       : {repeated:,}  (expected — many leads share same pin+tier+month)")


# ── Layer 2: D1 volume overlap ────────────────────────────────────────────────

def check_volume_overlap(df: pd.DataFrame, stored_pin: dict, stored_corr: dict,
                         ats_data: dict, top: int, focus_corr: str = None):
    print(f"\n{SEP}")
    print("  LAYER 2 — D1 VOLUME OVERLAP (financial double-count risk)")
    print(SEP)

    if not df["_is_inter"].any():
        print("  No inter-state records — no overlap possible.")
        return

    inter = df[df["_is_inter"]].copy()
    intra = df[~df["_is_inter"]].copy()

    print(f"  Intra-state disbursals  : {len(intra):,}  → appear ONLY in D1_PINCODE_VOLUME")
    print(f"  Inter-state disbursals  : {len(inter):,}  → appear in BOTH D1_PINCODE_VOLUME AND CORRIDOR_D1_VOLUME")
    print()
    print("  Inter-state disbursals are counted in TWO structures:")
    print("    D1_PINCODE_VOLUME[pin][tier]   — used when a REGION is selected")
    print("    CORRIDOR_D1_VOLUME[corr][tier] — used when a CORRIDOR is selected")
    print()
    print("  Double-counting occurs ONLY when BOTH the pin's region AND its")
    print("  corridor are selected in the same simulation run.")

    # Per-corridor: how many disbursals are in pincodes that also exist in D1_PINCODE_VOLUME
    pins_in_vol = set(stored_pin.keys())

    corridor_stats = []
    for ck, tier_vols in stored_corr.items():
        corr_rows = inter[inter["_ck"] == ck]
        if corr_rows.empty:
            continue
        total_corr = len(corr_rows)
        # Disbursals that are in a pincode present in D1_PINCODE_VOLUME
        overlap_rows = corr_rows[corr_rows["_pin"].isin(pins_in_vol)]
        overlap_cnt  = len(overlap_rows)
        overlap_pct  = 100 * overlap_cnt / total_corr if total_corr else 0

        # Max financial double-count: overlap disbursals / num_months * ATS summed over tiers
        num_months = max(1, (pd.Timestamp(D1_END).year - pd.Timestamp(D1_START).year)*12
                         + (pd.Timestamp(D1_END).month - pd.Timestamp(D1_START).month) + 1)
        max_double = 0.0
        for tier in CANONICAL:
            ov_tier = len(overlap_rows[overlap_rows[RISK_COL] == tier])
            ats     = ats_data.get(tier, {}).get("ats", 0)
            max_double += (ov_tier / num_months) * ats

        corridor_stats.append({
            "corridor"    : ck,
            "total_d1"    : total_corr,
            "overlap_cnt" : overlap_cnt,
            "overlap_pct" : overlap_pct,
            "max_double_L": max_double / 100_000,   # lakhs
        })

    if not corridor_stats:
        print("\n  No corridor overlap data computable.")
        return

    corridor_stats.sort(key=lambda x: -x["overlap_cnt"])

    print(f"\n  Corridors with overlap (disbursals in both PINCODE and CORRIDOR volumes):")
    print(f"  {'Corridor':<35} {'D1 total':>9} {'Overlap':>9} {'Overlap%':>9} {'Max double-cnt (Rs.L)':>22}")
    print(f"  {'-'*35} {'-'*9} {'-'*9} {'-'*9} {'-'*22}")

    n_show = top if not focus_corr else len(corridor_stats)
    shown = 0
    for row in corridor_stats:
        if focus_corr and focus_corr.lower() not in row["corridor"].lower():
            continue
        mark = " !" if row["overlap_pct"] > 50 else "  "
        print(f"{mark} {row['corridor']:<35} {row['total_d1']:>9,} {row['overlap_cnt']:>9,} "
              f"{row['overlap_pct']:>8.1f}% {row['max_double_L']:>22.2f}")
        shown += 1
        if not focus_corr and shown >= n_show:
            break

    total_overlap_disbursals = sum(r["overlap_cnt"] for r in corridor_stats)
    total_inter              = len(inter)
    total_max_double         = sum(r["max_double_L"] for r in corridor_stats)

    print()
    print(f"  SUMMARY")
    print(f"  Total inter-state disbursals            : {total_inter:,}")
    print(f"  Disbursals in BOTH pincode + corridor   : {total_overlap_disbursals:,}")
    print(f"  As % of all D1 disbursals in window     : {100*total_overlap_disbursals/len(df):.1f}%")
    print(f"  Max financial double-count (if all both): Rs.{total_max_double:.2f}L / mo")
    print()
    print("  INTERPRETATION")
    print("  - 'Overlap' = disbursals that are IN BOTH a pincode volume and a corridor volume.")
    print("  - The JS simulation prevents LEAD double-counting (maxSev===0 guard).")
    print("  - Financial double-counting only occurs if the USER selects BOTH:")
    print("      a region/district (triggering pincode volume) AND")
    print("      a corridor whose geography overlaps with that region.")
    print("  - If only REGION or only CORRIDOR is selected, there is zero double-count.")


# ── Focus on a single corridor ────────────────────────────────────────────────

def drill_corridor(ck: str, df: pd.DataFrame, stored_pin: dict,
                   stored_corr: dict, ats_data: dict):
    print(f"\n{SEP}")
    print(f"  CORRIDOR DRILL-DOWN: {ck}")
    print(SEP)
    corr_rows = df[df["_is_inter"] & (df["_ck"] == ck)]
    if corr_rows.empty:
        # try loose match
        matches = df[df["_ck"].str.lower().str.contains(ck.lower())]
        if matches.empty:
            print(f"  No disbursals found for corridor key: {ck!r}")
            return
        unique_ks = matches["_ck"].unique()
        print(f"  Exact key not found. Close matches: {list(unique_ks)[:5]}")
        return

    num_months = max(1, (pd.Timestamp(D1_END).year - pd.Timestamp(D1_START).year)*12
                     + (pd.Timestamp(D1_END).month - pd.Timestamp(D1_START).month) + 1)

    pins_in_vol = set(stored_pin.keys())
    corr_json   = stored_corr.get(ck, {})

    print(f"  Total D1 disbursals in corridor : {len(corr_rows):,}")
    print()
    print(f"  {'Tier':<12} {'D1 count':>10} {'Vol/mo (CSV)':>14} {'Vol/mo (JSON)':>14} "
          f"{'Overlap count':>14} {'Overlap%':>9}")
    print(f"  {'-'*12} {'-'*10} {'-'*14} {'-'*14} {'-'*14} {'-'*9}")

    for tier in list(CANONICAL):
        tier_rows = corr_rows[corr_rows[RISK_COL] == tier]
        cnt       = len(tier_rows)
        csv_vol   = round(cnt / num_months, 4)
        jsn_vol   = corr_json.get(tier, 0)
        ov        = len(tier_rows[tier_rows["_pin"].isin(pins_in_vol)])
        ov_pct    = 100 * ov / cnt if cnt else 0
        print(f"  {tier:<12} {cnt:>10,} {csv_vol:>14.4f} {jsn_vol:>14.4f} "
              f"{ov:>14,} {ov_pct:>8.1f}%")

    print()
    # Show which pincodes in this corridor are also in D1_PINCODE_VOLUME
    pin_overlap = corr_rows[corr_rows["_pin"].isin(pins_in_vol)].groupby("_pin")[RISK_COL].value_counts()
    if not pin_overlap.empty:
        print(f"  Pincodes in corridor that are ALSO in D1_PINCODE_VOLUME (top 10):")
        print(f"  {'Pincode':<10} {'Tier':<12} {'D1 Count':>10} {'Corr Vol/mo (from JSON)':>24} {'Pin Vol/mo (from JSON)':>22}")
        print(f"  {'-'*10} {'-'*12} {'-'*10} {'-'*24} {'-'*22}")
        for (pin, tier), cnt in list(pin_overlap.items())[:10]:
            corr_v = corr_json.get(tier, 0)
            pin_v  = stored_pin.get(pin, {}).get(tier, 0)
            print(f"  {pin:<10} {tier:<12} {cnt:>10,} {corr_v:>24.4f} {pin_v:>22.4f}")
        print()
        print("  When this corridor AND a region containing any of the above pincodes")
        print("  are BOTH selected, those pin-tier disbursals appear in both loss figures.")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Verify no D1 disbursal is double-counted across region and corridor")
    parser.add_argument("--top",      type=int, default=20, help="Number of corridors to show in overlap table")
    parser.add_argument("--corridor", type=str, default=None,
                        help="Drill into a corridor key, e.g. 'Maharashtra -> Gujarat'")
    args = parser.parse_args()

    focus_corr = None
    if args.corridor:
        # Accept ASCII -> as shorthand for Unicode arrow
        focus_corr = args.corridor.replace("->", ARROW.strip()).strip()

    data        = load_json()
    stored_pin  = data.get("D1_PINCODE_VOLUME", {})
    stored_corr = data.get("CORRIDOR_D1_VOLUME", {})
    lead_array  = data.get("LEAD_ARRAY", [])
    ats_data    = data.get("ATS_DATA_JSON", {})

    d1_df = load_d1()

    check_lead_array(lead_array)
    check_volume_overlap(d1_df, stored_pin, stored_corr, ats_data, args.top, focus_corr)

    if focus_corr:
        drill_corridor(focus_corr, d1_df, stored_pin, stored_corr, ats_data)

    print(f"\n{SEP}")
    print("  DONE")
    print(SEP)


if __name__ == "__main__":
    main()
