"""
generate_sample_data.py — Creates synthetic demo data in sample_data/

Generates realistic but entirely fictional lending portfolio data for Indian
states and districts. Run once before process_data.py to populate sample_data/.

Usage:
    python generate_sample_data.py

Output:
    sample_data/portfolio_risk_data.xlsx
    sample_data/disbursement_tracker.csv
    sample_data/bureau_market_data.csv
    sample_data/application_rejections.csv
    sample_data/pincode_mapping.csv
"""

import random
import string
from pathlib import Path
from datetime import date, timedelta

import pandas as pd

random.seed(42)
OUT = Path("sample_data")
OUT.mkdir(exist_ok=True)

# ── Geography ────────────────────────────────────────────────────────────────
# Real state/district names so GeoJSON mapping works in process_data.py

GEO = {
    "Maharashtra": {
        "prefix": 40,
        "districts": ["Mumbai", "Pune", "Nagpur", "Thane", "Nashik", "Aurangabad", "Solapur", "Kolhapur"],
    },
    "Karnataka": {
        "prefix": 56,
        "districts": ["Bengaluru Urban", "Mysuru", "Dharwad", "Dakshina Kannada", "Belagavi", "Davanagere", "Shivamogga"],
    },
    "Tamil Nadu": {
        "prefix": 60,
        "districts": ["Chennai", "Coimbatore", "Madurai", "Salem", "Tiruchirappalli", "Erode", "Vellore"],
    },
    "Delhi": {
        "prefix": 11,
        "districts": ["Delhi", "Delhi", "Delhi", "Delhi", "Delhi", "Delhi"],
    },
    "Gujarat": {
        "prefix": 38,
        "districts": ["Ahmedabad", "Surat", "Vadodara", "Rajkot", "Gandhinagar", "Bharuch", "Anand"],
    },
    "Uttar Pradesh": {
        "prefix": 20,
        "districts": ["Lucknow", "Kanpur Nagar", "Agra", "Varanasi", "Allahabad", "Meerut", "Ghaziabad"],
    },
    "Rajasthan": {
        "prefix": 30,
        "districts": ["Jaipur", "Jodhpur", "Kota", "Ajmer", "Bikaner", "Udaipur", "Alwar"],
    },
    "West Bengal": {
        "prefix": 70,
        "districts": ["Kolkata", "North 24 Parganas", "South 24 Parganas", "Howrah", "Hooghly", "Paschim Bardhaman"],
    },
    "Telangana": {
        "prefix": 50,
        "districts": ["Hyderabad", "Ranga Reddy", "Medchal Malkajgiri", "Warangal", "Karimnagar", "Nizamabad"],
    },
    "Madhya Pradesh": {
        "prefix": 46,
        "districts": ["Bhopal", "Indore", "Jabalpur", "Gwalior", "Ujjain", "Sagar", "Rewa"],
    },
}

# Migration source states (perm address) — create corridor data
MIGRATION_SOURCES = ["Bihar", "Uttar Pradesh", "Rajasthan", "Madhya Pradesh", "Odisha"]

RISK_TIERS    = ["Low", "Medium", "High", "Very High"]
TIER_WEIGHTS  = [0.40, 0.35, 0.18, 0.07]
# Bad rate (30P_6M) by tier — probability a lead is delinquent
TIER_BAD_RATE = {"Low": 0.030, "Medium": 0.082, "High": 0.162, "Very High": 0.285}
# Average loan amount by tier (rupees)
TIER_ATS      = {"Low": 55000, "Medium": 75000, "High": 90000, "Very High": 110000}

FEMI_LABELS   = ["Nov '25", "Dec '25", "Jan '26", "Feb '26", "Mar '26", "Apr '26"]


def random_date(start: date, end: date) -> date:
    delta = (end - start).days
    return start + timedelta(days=random.randint(0, delta))


def fmt_date(d: date) -> str:
    return d.strftime("%d-%m-%Y")


# ── Real pincode pool ───────────────────────────────────────────────────────
# Invented pincodes (e.g. random digits after a state prefix) almost never match a
# real allocated pincode, so they'd have no entry in reference/india_pincode_coords.xlsx
# and never render as a dot on the map. Sampling from the real file guarantees every
# synthetic pincode has a valid lat/long.

_COORDS_DF = pd.read_excel("reference/india_pincode_coords.xlsx")


def real_pincodes_for_state(state: str, exclude: set = frozenset()) -> list[str]:
    pins = _COORDS_DF.loc[_COORDS_DF["State"] == state, "Pincode"].astype(str).str.zfill(6).unique().tolist()
    return [p for p in pins if p not in exclude]


# ── Build pincode universe ────────────────────────────────────────────────────

def build_pincode_universe() -> list[dict]:
    pins = []
    for state, info in GEO.items():
        districts = info["districts"]
        n_pins = max(20, int(300 * TIER_WEIGHTS[0]))  # ~30 per state
        real_pins = real_pincodes_for_state(state)
        random.shuffle(real_pins)
        chosen = real_pins[:n_pins]
        for pin in chosen:
            dist = random.choice(districts)
            pins.append({"state": state, "district": dist, "pincode": pin})
    return pins


# ── 1. Portfolio risk data ────────────────────────────────────────────────────

def gen_portfolio(pins: list[dict], n: int = 5000) -> pd.DataFrame:
    rows = []
    states = list(GEO.keys())
    for i in range(n):
        p        = random.choice(pins)
        tier     = random.choices(RISK_TIERS, weights=TIER_WEIGHTS)[0]
        bad      = 1 if random.random() < TIER_BAD_RATE[tier] else 0
        loan_amt = round(TIER_ATS[tier] * random.uniform(0.5, 2.2), -3)
        femi     = random.choice(FEMI_LABELS)

        # ~30% are migrants (perm address differs from current)
        is_migrant = random.random() < 0.30
        if is_migrant:
            perm_state = random.choice(MIGRATION_SOURCES)
            perm_dist  = f"{perm_state} District"
            perm_pin   = f"{random.randint(80, 85)}{random.randint(1000, 9999):04d}"
        else:
            perm_state = p["state"]
            perm_dist  = p["district"]
            perm_pin   = p["pincode"]

        # 90P12M is a stricter/later-maturing default flag than 30P_6M — only ever 1 if 30P_6M is
        # also 1 (a loan that's 90+ dpd at 12mo was necessarily 30+ dpd at 6mo), and only observable
        # once MOB_12_completed. Older FEMI cohorts are more likely to have matured to MOB12.
        mob12_completed = 1 if femi in ("Nov '25", "Dec '25", "Jan '26") else 0
        bad_90p12m = 1 if (bad and mob12_completed and random.random() < 0.6) else 0

        rows.append({
            "Lead_ID":                  f"LD{i+1:06d}",
            "FEMI":                     femi,
            "Current Address State":    p["state"],
            "Current Address Dist":     p["district"],
            "mx_current_address_zip":   p["pincode"],
            "risk_category_final":      tier,
            "30P_6M":                   bad,
            "90P_12M":                  bad_90p12m,
            "MOB_12_completed":         mob12_completed,
            "Disbursed Loan Amt":       loan_amt,
            "Perm. Address State":      perm_state,
            "Perm. Address Dist":       perm_dist,
            "mx_zip_as_per_aadhar":     perm_pin,
        })

    df = pd.DataFrame(rows)
    path = OUT / "portfolio_risk_data.xlsx"
    df.to_excel(path, sheet_name="Base_Data", index=False)
    print(f"  [ok]portfolio_risk_data.xlsx — {len(df):,} rows")
    return df


# ── 2. D1 tracker (disbursements) ────────────────────────────────────────────

def gen_d1_tracker(pins: list[dict], n: int = 3000):
    d_start = date(2026, 1, 1)
    d_end   = date(2026, 4, 30)
    rows = []
    for i in range(n):
        p    = random.choice(pins)
        tier = random.choices(RISK_TIERS, weights=TIER_WEIGHTS)[0]
        loan = round(TIER_ATS[tier] * random.uniform(0.5, 2.2), -3)
        src  = "tp_form" if random.random() < 0.10 else ""
        disb = random_date(d_start, d_end)

        is_migrant = random.random() < 0.30
        perm_state = random.choice(MIGRATION_SOURCES) if is_migrant else p["state"]

        rows.append({
            "prospect_stage":          "Disbursed",
            "source":                  src,
            "mx_lead_substage":        "Disbursed",
            "mx_lender_disbursal_date": fmt_date(disb),
            "mx_risk_category":        tier,
            "mx_final_loan_amount":    loan,
            "mx_current_address_zip":  p["pincode"],
            "Perm Address State":      perm_state,
            "Curr Address State":      p["state"],
        })

    df = pd.DataFrame(rows)
    df.to_csv(OUT / "disbursement_tracker.csv", index=False)
    print(f"  [ok]disbursement_tracker.csv — {len(df):,} rows")


# ── 3. Bureau / market data ───────────────────────────────────────────────────

def gen_market_data(pins: list[dict]):
    quarters   = ["2025-Q2", "2025-Q3", "2025-Q4", "2026-Q1"]
    trade_sizes = ["E", "F", "G", "H", "I", "J", "K"]
    member_groups = ["Banks", "NBFCs", "MFIs", "HFCs"]
    rows = []

    for state, info in GEO.items():
        for dist in info["districts"]:
            for q in quarters:
                for mg in member_groups:
                    loans = random.randint(200, 3000)
                    # Market rates slightly higher than internal to create expansion opportunity
                    rate  = random.uniform(0.04, 0.12)
                    delq  = round(loans * rate)
                    ts    = random.choice(trade_sizes)
                    # 90P12M population is independently computed (MOB12, not MOB6) — ~72-88% of the
                    # MOB6 loan count, per the real denominator-fix rationale in process_data.py
                    loans_90p12m = round(loans * random.uniform(0.72, 0.88))
                    rate_90p12m  = random.uniform(0.03, 0.09)
                    delq_90p12m  = round(loans_90p12m * rate_90p12m)
                    avg_ticket   = random.uniform(45000, 120000)
                    sanctioned_amt = round(loans * avg_ticket)
                    # Pick a pincode for this district
                    dist_pins = [p["pincode"] for p in pins if p["state"] == state and p["district"] == dist]
                    pin = random.choice(dist_pins) if dist_pins else ""
                    rows.append({
                        "STATE":                     state,
                        "DISTRICT":                  dist,
                        "ORG_QRT":                   q,
                        "MEMBER_GROUP":              mg,
                        "TRADE_SIZE":                ts,
                        "NUMBER_OF_LOANS":           loans,
                        "DELINQUENT_30P6M_TRADES":   delq,
                        "NUMBER_OF_LOANS_90P12M":    loans_90p12m,
                        "DELINQUENT_90P12M_TRADES":  delq_90p12m,
                        "TOTAL_SANCTIONED_AMOUNT":   sanctioned_amt,
                        "PINCODE":                   pin,
                    })

    # Add ~30 greenfield pincodes (not in portfolio) so Expansion tab has targets
    portfolio_pins = {p["pincode"] for p in pins}
    for _ in range(30):
        state = random.choice(list(GEO.keys()))
        dist  = random.choice(GEO[state]["districts"])
        candidates = real_pincodes_for_state(state, exclude=portfolio_pins)
        if not candidates:
            continue
        pin = random.choice(candidates)  # real pincode, guaranteed not already in portfolio
        for q in quarters:
            loans = random.randint(100, 800)
            rate  = random.uniform(0.03, 0.08)  # greenfield = lower risk
            delq  = round(loans * rate)
            rows.append({
                "STATE":                   state,
                "DISTRICT":                dist,
                "ORG_QRT":                 q,
                "MEMBER_GROUP":            "NBFCs",
                "TRADE_SIZE":              "G",
                "NUMBER_OF_LOANS":         loans,
                "DELINQUENT_30P6M_TRADES": delq,
                "PINCODE":                 pin,
            })

    df = pd.DataFrame(rows)
    df.to_csv(OUT / "bureau_market_data.csv", index=False)
    print(f"  [ok]bureau_market_data.csv — {len(df):,} rows")


# ── 4. Credit tracker (rejections) ───────────────────────────────────────────

def gen_credit_tracker(pins: list[dict], n: int = 900):
    d_start = date(2026, 3, 1)
    d_end   = date(2026, 4, 30)
    rej_reasons_signal = ["High Risk Pincode", "Negative Location", "Pincode not present"]
    rej_reasons_noise  = ["Income criteria not met", "Age criteria", "Existing obligations", "Bureau score low"]

    rows = []
    for i in range(n):
        p    = random.choice(pins)
        tier = random.choices(["High", "Very High", "Medium"], weights=[0.45, 0.25, 0.30])[0]
        ts   = random_date(d_start, d_end)
        # ~55% pass the STC filter
        stc  = True if random.random() < 0.55 else False
        # 60% have a signal rejection reason (these feed the expansion analysis)
        if random.random() < 0.60:
            reason = random.choice(rej_reasons_signal)
            stage  = "CA - Screening Reject"
            sub    = "Policy norms not met"
        else:
            reason = random.choice(rej_reasons_noise)
            stage  = random.choice(["CA - Screening Reject", "Credit Review", "Ops Check"])
            sub    = random.choice(["Policy norms not met", "Income not verified", "Other"])

        rows.append({
            "prospect_stage":           stage,
            "mx_lead_substage":         sub,
            "primary_rejection_reason": reason,
            "stc_timestamp":            fmt_date(ts),
            "mx_risk_category":         tier,
            "mx_current_address_zip":   p["pincode"],
            "STC":                      stc,
        })

    df = pd.DataFrame(rows)
    df.to_csv(OUT / "application_rejections.csv", index=False)
    print(f"  [ok]application_rejections.csv — {len(df):,} rows")


# ── 5. Pincode mapping ────────────────────────────────────────────────────────

def gen_pincode_mapping(pins: list[dict]):
    rows = []
    pin_list = list({p["pincode"]: p for p in pins}.values())  # dedupe

    for p in pin_list:
        # ~35% red (delinquency), ~60% green (operational), rest unlabelled
        r = random.random()
        red_type   = "Delinquency" if r < 0.35 else ""
        green_type = "Green"       if r >= 0.35 and r < 0.95 else ""
        pin_type   = "Red" if red_type else ("Operational" if green_type else "Non-Operational")

        rows.append({
            "Pincode":                       p["pincode"],
            "City":                          p["district"],
            "District":                      p["district"],
            "Final State":                   p["state"],
            "Type of Pincode":               pin_type,
            "New Pincode Mapping (Cr Add)":  green_type,
            "Type of Red (Finalised)":       red_type,
            "Type of Non-Operational Red":   "",
            "Postal Mapping":                "",
            "Disbursals":                    random.randint(0, 200),
        })

    df = pd.DataFrame(rows)
    df.to_csv(OUT / "pincode_mapping.csv", index=False)
    print(f"  [ok]pincode_mapping.csv — {len(df):,} rows")


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("Generating synthetic sample data...")
    pins = build_pincode_universe()
    print(f"  Built pincode universe: {len(pins)} pincodes across {len(GEO)} states")
    gen_portfolio(pins)
    gen_d1_tracker(pins)
    gen_market_data(pins)
    gen_credit_tracker(pins)
    gen_pincode_mapping(pins)
    print(f"\nDone. Files written to ./{OUT}/")
    print("Next: python process_data.py")
