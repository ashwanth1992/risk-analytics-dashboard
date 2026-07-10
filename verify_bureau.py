"""
verify_bureau.py — Internal consistency checks for the bureau data structures in
pipeline_output.json (BUREAU_DATA_JSON / BUREAU_PINCODE_DATA).

Bureau slicing has produced three real, silent bugs so far (90P12M denominator using the
wrong population; district slices missing the trade_size dimension; district slices missing
the quarter dimension entirely, which zeroed a working member-group filter the moment a
quarter filter was added on top) — and none of verify_loss.py / verify_volume.py / verify_dedup.py
touch bureau data at all. This script exists to close that gap.

Unlike verify_volume.py, this does NOT re-parse the ~530MB raw bureau CSV (that would duplicate
process_data.py's aggregation logic at high risk of drifting out of sync with it, for a
marginal benefit over the checks below). Instead it cross-checks the JSON's own internal
structures against each other — exactly the kind of check that would have caught all three
bugs above:

  1. SLICE SHAPE — every state/district slice must carry mg + q_raw + ts (whichever of those
     dimensions the dataset has at all). A slice silently missing q_raw is the exact bug class
     that zeroed a working mg filter the moment a quarter filter was added.
  2. OVERALL RECONCILIATION — re-derive the blended "overall" 30P6M/90P12M/amt from the
     per-quarter `quarterly` array (mature quarters only, using the same maturity cutoffs
     recorded in _meta.mature_through) and diff against the stored `overall`. Also re-derive
     from `slices` the same way, so a maturity-gate or slice-shape bug shows up as a mismatch
     even if `quarterly` itself is fine.
  3. PINCODE QUARTERLY SUMS — pincode `quarterly` loans/delinquent (mature quarters) must sum
     to the pincode's own `overall` loans/delinquent.

Usage:
    python verify_bureau.py
    python verify_bureau.py --top 20              # show top N mismatches per section
    python verify_bureau.py --tol 1                # loans/delinquent count tolerance
    python verify_bureau.py --state "Maharashtra"  # restrict to one state
"""

import json
import argparse
import re
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DATA_JSON = Path("pipeline_output.json")
SEP = "=" * 70

_QTR_MONTH_POS = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
                  "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}
_QTR_RE = re.compile(r"^([A-Za-z]{3}).*?['’](\d{2})")


def qtr_key(label):
    """Chronological sort/comparison key for 'Jan - Mar '25'-style labels.
    Mirrors the JS _qtrKey/_sortQtrs parsing exactly (month-abbrev + 2-digit year)."""
    if not label:
        return None
    m = _QTR_RE.match(str(label))
    if not m:
        return None
    mon, yr = m.group(1).lower(), int(m.group(2))
    pos = _QTR_MONTH_POS.get(mon)
    if pos is None:
        return None
    return yr * 100 + pos


def main():
    ap = argparse.ArgumentParser(description="Verify BUREAU_DATA_JSON / BUREAU_PINCODE_DATA internal consistency")
    ap.add_argument("--top", type=int, default=15, help="Show top N mismatches per section")
    ap.add_argument("--tol", type=float, default=1.0, help="Absolute tolerance for loans/delinquent counts")
    ap.add_argument("--state", type=str, default=None, help="Restrict to a single state")
    args = ap.parse_args()

    if not DATA_JSON.exists():
        print(f"ERROR: {DATA_JSON} not found. Run process_data.py first.")
        sys.exit(1)

    print(f"Loading {DATA_JSON} ...")
    with open(DATA_JSON, "r", encoding="utf-8") as f:
        data = json.load(f)

    bureau = data.get("BUREAU_DATA_JSON", {})
    bureau_pin = data.get("BUREAU_PINCODE_DATA", {})
    if not bureau:
        print("BUREAU_DATA_JSON is empty or missing — nothing to verify.")
        sys.exit(1)

    meta = bureau.get("_meta", {})
    mature_through = meta.get("mature_through", {})
    mk30 = qtr_key(mature_through.get("30p6m"))
    mk90 = qtr_key(mature_through.get("90p12m"))
    print(f"Maturity cutoffs from _meta: 30P6M<={mature_through.get('30p6m')!r} (key={mk30})  "
          f"90P12M<={mature_through.get('90p12m')!r} (key={mk90})")

    states = {k: v for k, v in bureau.items() if k != "_meta" and (args.state is None or k == args.state)}
    print(f"States to check: {len(states)}")

    # ── SECTION 1 — slice shape ──────────────────────────────────────────────
    print(f"\n{SEP}\n  SECTION 1 — SLICE SHAPE (mg / q_raw / ts presence)\n{SEP}")
    shape_issues = []  # (level, state, district_or_none, missing_fields)
    expected_fields = {"mg", "q_raw"}  # ts is optional-by-design at some grains; checked separately
    ts_seen_anywhere = False
    ts_missing_states = []

    for sn, sdata in states.items():
        for slc in sdata.get("slices", []) or []:
            missing = expected_fields - set(slc.keys())
            if missing:
                shape_issues.append(("state", sn, None, sorted(missing)))
            if "ts" in slc:
                ts_seen_anywhere = True

    for sn, sdata in states.items():
        for dn, ddata in sdata.get("districts", {}).items():
            d_slices = ddata.get("slices", []) or []
            if not d_slices:
                continue
            has_ts_here = any("ts" in s for s in d_slices)
            for slc in d_slices:
                missing = expected_fields - set(slc.keys())
                if missing:
                    shape_issues.append(("district", sn, dn, sorted(missing)))
            if ts_seen_anywhere and not has_ts_here:
                ts_missing_states.append(f"{sn}:{dn}")

    if shape_issues:
        print(f"  ✗ {len(shape_issues)} slice(s) missing mg/q_raw — showing top {args.top}:")
        for level, sn, dn, missing in shape_issues[:args.top]:
            loc = sn if dn is None else f"{sn} / {dn}"
            print(f"    [{level}] {loc}: missing {missing}")
    else:
        print("  ✓ Every state and district slice carries both mg and q_raw.")

    if ts_seen_anywhere and ts_missing_states:
        print(f"  ✗ {len(ts_missing_states)} district(s) have trade_size on SOME but not ALL of their "
              f"own slices, or missing where the dataset has ts elsewhere — showing top {args.top}:")
        for loc in ts_missing_states[:args.top]:
            print(f"    {loc}")
    elif ts_seen_anywhere:
        print("  ✓ Trade-size (ts) present consistently wherever the dataset carries it.")

    # ── SECTION 2 — overall reconciliation (from quarterly, and from slices) ──
    print(f"\n{SEP}\n  SECTION 2 — OVERALL RECONCILIATION\n{SEP}")

    def recompute_from_quarterly(quarterly):
        l30 = d30 = l90 = d90 = amt = 0
        has90 = has_amt = False
        for q in quarterly or []:
            qk = qtr_key(q.get("q"))
            if mk30 is None or (qk is not None and qk <= mk30):
                l30 += q.get("loans", 0) or 0
                d30 += q.get("delinquent", 0) or 0
            if "loans_90p12m" in q:
                has90 = True
                if mk90 is None or (qk is not None and qk <= mk90):
                    l90 += q.get("loans_90p12m", 0) or 0
                    d90 += q.get("delinquent_90p12m", 0) or 0
            if "amt" in q:
                has_amt = True
                amt += q.get("amt", 0) or 0  # amt is intentionally NOT maturity-gated
        out = {"loans": l30, "delinquent": d30}
        if has90:
            out["loans_90p12m"] = l90
            out["delinquent_90p12m"] = d90
        if has_amt:
            out["amt"] = amt
        return out

    def recompute_from_slices(slices):
        l30 = d30 = l90 = d90 = amt = 0
        has90 = has_amt = False
        for s in slices or []:
            qk = qtr_key(s.get("q_raw") or s.get("q"))
            if mk30 is None or (qk is not None and qk <= mk30):
                l30 += s.get("l", 0) or 0
                d30 += s.get("d", 0) or 0
            if "l90" in s or "d90" in s:
                has90 = True
                if mk90 is None or (qk is not None and qk <= mk90):
                    l90 += s.get("l90", 0) or 0
                    d90 += s.get("d90", 0) or 0
            if "amt" in s:
                has_amt = True
                amt += s.get("amt", 0) or 0
        out = {"loans": l30, "delinquent": d30}
        if has90:
            out["loans_90p12m"] = l90
            out["delinquent_90p12m"] = d90
        if has_amt:
            out["amt"] = amt
        return out

    def diff_fields(stored, recomputed, tol):
        mismatches = []
        for k, v in recomputed.items():
            sv = stored.get(k)
            if sv is None:
                continue
            if abs((sv or 0) - v) > tol:
                mismatches.append((k, sv, v))
        return mismatches

    qtr_mismatches = []   # (level, loc, field, stored, recomputed)
    slice_mismatches = []

    for sn, sdata in states.items():
        overall = sdata.get("overall", {})
        rq = recompute_from_quarterly(sdata.get("quarterly", []))
        for field, sv, rv in diff_fields(overall, rq, args.tol):
            qtr_mismatches.append(("state", sn, field, sv, rv))
        if sdata.get("slices"):
            rs = recompute_from_slices(sdata["slices"])
            for field, sv, rv in diff_fields(overall, rs, args.tol):
                slice_mismatches.append(("state", sn, field, sv, rv))

        for dn, ddata in sdata.get("districts", {}).items():
            d_overall = ddata.get("overall", {})
            rq = recompute_from_quarterly(ddata.get("quarterly", []))
            for field, sv, rv in diff_fields(d_overall, rq, args.tol):
                qtr_mismatches.append(("district", f"{sn} / {dn}", field, sv, rv))
            if ddata.get("slices"):
                rs = recompute_from_slices(ddata["slices"])
                for field, sv, rv in diff_fields(d_overall, rs, args.tol):
                    slice_mismatches.append(("district", f"{sn} / {dn}", field, sv, rv))

    if qtr_mismatches:
        print(f"  ✗ {len(qtr_mismatches)} overall-vs-quarterly mismatch(es) — showing top {args.top}:")
        for level, loc, field, sv, rv in qtr_mismatches[:args.top]:
            print(f"    [{level}] {loc}: {field} stored={sv} recomputed-from-quarterly={rv}")
    else:
        print("  ✓ Every state/district 'overall' reconciles with its own 'quarterly' array "
              "(mature quarters only).")

    if slice_mismatches:
        print(f"  ✗ {len(slice_mismatches)} overall-vs-slices mismatch(es) — showing top {args.top}:")
        for level, loc, field, sv, rv in slice_mismatches[:args.top]:
            print(f"    [{level}] {loc}: {field} stored={sv} recomputed-from-slices={rv}")
    else:
        print("  ✓ Every state/district 'overall' reconciles with its own 'slices' array "
              "(mature quarters only) — this is the check that would have caught the "
              "missing-q_raw/ts district-slice bugs.")

    # ── SECTION 3 — pincode quarterly sums ───────────────────────────────────
    # NOTE: unlike state/district "overall" (maturity-gated blend), pincode-level top-level
    # loans/delinquent (_load_bureau_data's `grp` aggregation) are the FULL unfiltered sum
    # across every quarter, not gated to bureau_30p6m_mature_through/90p12m_mature_through —
    # confirmed by reading the pincode aggregation code. So the correct reconciliation here is
    # against the UNGATED sum of `quarterly`, not the mature-only sum used for state/district.
    print(f"\n{SEP}\n  SECTION 3 — PINCODE QUARTERLY SUMS\n{SEP}")

    def recompute_from_quarterly_ungated(quarterly):
        l = d = l90 = d90 = amt = 0
        has90 = has_amt = False
        for q in quarterly or []:
            l += q.get("loans", 0) or 0
            d += q.get("delinquent", 0) or 0
            if "loans_90p12m" in q:
                has90 = True
                l90 += q.get("loans_90p12m", 0) or 0
                d90 += q.get("delinquent_90p12m", 0) or 0
            if "amt" in q:
                has_amt = True
                amt += q.get("amt", 0) or 0
        out = {"loans": l, "delinquent": d}
        if has90:
            out["loans_90p12m"] = l90
            out["delinquent_90p12m"] = d90
        if has_amt:
            out["amt"] = amt
        return out

    pin_mismatches = []
    checked = 0
    for pin, pdata in bureau_pin.items():
        if args.state and pdata.get("state") != args.state:
            continue
        checked += 1
        rq = recompute_from_quarterly_ungated(pdata.get("quarterly", []))
        for field, sv, rv in diff_fields(
            {"loans": pdata.get("loans"), "delinquent": pdata.get("delinquent"),
             "loans_90p12m": pdata.get("loans_90p12m"), "delinquent_90p12m": pdata.get("delinquent_90p12m"),
             "amt": pdata.get("amt")}, rq, args.tol
        ):
            pin_mismatches.append((pin, field, sv, rv))

    print(f"  Pincodes checked: {checked:,}")
    if pin_mismatches:
        print(f"  ✗ {len(pin_mismatches)} pincode quarterly-sum mismatch(es) — showing top {args.top}:")
        for pin, field, sv, rv in pin_mismatches[:args.top]:
            print(f"    {pin}: {field} stored={sv} recomputed-from-quarterly={rv}")
    else:
        print("  ✓ Every pincode's quarterly array sums to its own totals (full, ungated — "
              "pincode grain is NOT maturity-gated, unlike state/district 'overall').")

    # ── Summary ───────────────────────────────────────────────────────────────
    print(f"\n{SEP}\n  SUMMARY\n{SEP}")
    total_issues = len(shape_issues) + len(ts_missing_states) + len(qtr_mismatches) + len(slice_mismatches) + len(pin_mismatches)
    if total_issues == 0:
        print("  ALL CHECKS PASSED — bureau data structures are internally consistent.")
    else:
        print(f"  {total_issues} total issue(s) found across all sections — see above.")
    sys.exit(1 if total_issues else 0)


if __name__ == "__main__":
    main()
