"""
process_data.py — Layer 1+2: All data processing → pipeline_output.json
Run this when source data changes. build_dashboard.py reads the JSON for fast UI rebuilds.
"""

import pandas as pd
import json
import logging
from pathlib import Path
from dataclasses import dataclass, field
from datetime import datetime
import calendar

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s', datefmt="%H:%M:%S")
logger = logging.getLogger("DataEngine")

DATA_OUTPUT = Path("pipeline_output.json")


@dataclass
class Phase2Config:
    input_excel: Path = Path("sample_data/portfolio_risk_data.xlsx")
    sheet_name: str = "Base_Data"
    geojson_path: Path = Path("reference/india_states.geojson")
    output_dir: Path = Path("demo")
    month_col: str = "FEMI"
    state_col: str = "Current Address State"
    dist_col: str = "Current Address Dist"
    perm_state_col: str = "Perm. Address State"
    perm_dist_col: str = "Perm. Address Dist"
    perm_pincode_col: str = "mx_zip_as_per_aadhar"
    curr_state_col: str = "Current Address State"
    curr_dist_col: str = "Current Address Dist"
    risk_cat_col: str = "risk_category_final"
    flag_col: str = "30P_6M"
    flag_col_90p12m: str = "90P_12M"   # internal (FINANCEORG) 90+ DPD within 12mo — mirrors bureau's metric split
    mob12_completed_col: str = "MOB_12_completed"  # 1 = loan has had a full 12mo to mature; filters 90P12M denominator
    loan_amt_col: str = "Disbursed Loan Amt"
    lead_id_col: str = "Lead_ID"
    pincode_col: str = "mx_current_address_zip"
    bureau_files: list = field(default_factory=lambda: ["sample_data/bureau_market_data.csv"])
    # Cohort-maturity cutoffs for the bureau "overall" blended rate — calendar quarters (Q1=Jan-Mar),
    # matching the raw ORG_QRT "YYYY-Qn" format. Data pulled Oct 2025; a loan needs 6mo/12mo on book
    # before 30P6M/90P12M are observable, so quarters after these cutoffs are excluded from the
    # blended "overall" rate (they still appear, correctly near-zero, in the per-quarter breakdown).
    bureau_30p6m_mature_through: str = "2025-Q4"   # aligned to this repo's synthetic data quarter range
    bureau_90p12m_mature_through: str = "2025-Q2"  # aligned to this repo's synthetic data quarter range
    ats_file: Path = Path("sample_data/disbursement_tracker.csv")
    ats_substage_col: str = "mx_lead_substage"
    ats_substage_value: str = "Disbursed"
    ats_disbursal_date_col: str = "mx_lender_disbursal_date"
    ats_risk_cat_col: str = "mx_risk_category"
    ats_amount_col: str = "mx_final_loan_amount"
    ats_prospect_stage_col: str = "prospect_stage"    # additional filter column applied to ATS + disbursal base
    ats_prospect_stage_value: str = "Disbursed"       # keep only rows matching this value (case-insensitive)
    ats_source_col: str = "source"                    # source column — used to exclude certain channels
    ats_source_exclude: str = "tp_form"               # rows with this source value are excluded
    ats_start_date: str = "2026-03-01"
    ats_end_date: str = "2026-04-30"
    # D1 volume window — separate from ATS so you can control which months count for business loss
    d1_start_date: str = "2026-03-01"   # ← change these to pick your months
    d1_end_date: str   = "2026-04-30"
    # Monthly disbursal base — denominator for "net loss as % of monthly disbursals" in the dashboard
    # Set to "ats" to use ats_start_date/ats_end_date, or "d1" to use d1_start_date/d1_end_date
    disbursal_base_window: str = "ats"
    d1_perm_state_col: str = "Perm Address State"
    d1_curr_state_col: str = "Curr Address State"
    rejection_file: Path = Path("sample_data/application_rejections.csv")
    rej_stage_col: str    = "prospect_stage"
    rej_stage_val: str    = "CA - Screening Reject"
    rej_substage_col: str = "mx_lead_substage"
    rej_substage_val: str = "Policy norms not met"
    rej_reason_col: str   = "primary_rejection_reason"
    rej_reason_vals: list = field(default_factory=lambda: ["High Risk Pincode","Negative Location","Pincode not present"])
    rej_start_date: str = "2026-03-01"   # leave blank to use full data range; set e.g. "2025-01-01" to filter
    rej_end_date: str   = "2026-04-30"   # leave blank to use full data range; set e.g. "2025-12-31" to filter
    rej_stc_date_col: str = "stc_timestamp"
    rej_consider_col: str = "STC"
    rej_risk_col: str     = "mx_risk_category"
    rej_pincode_col: str  = "mx_current_address_zip"
    pincode_map_file:    Path = Path("sample_data/pincode_mapping.csv")
    pin_map_pincode_col: str  = "Pincode"
    pin_map_type_col:    str  = "Type of Red (Finalised)"
    pin_map_type_val:    str  = "Delinquency"
    pin_map_green_col:   str  = "New Pincode Mapping (Cr Add)"
    pin_map_green_val:   str  = "Green"
    pincode_coord_file: Path = Path("reference/india_pincode_coords.xlsx")
    coord_pin_col: str = "Pincode"
    auto_match_threshold: float = 0.82
    suggest_threshold: float = 0.65


class DataEngine:
    def __init__(self, config: Phase2Config):
        self.cfg = config
        self.df = pd.DataFrame()
        self.port_stats = {}
        self.region_data = {}
        self.bureau_data = {}
        self.ats_data = {}
        self.corridor_data = {}
        self.rejection_data = {}
        self.pincode_risk_data = {}
        self.bureau_pincode_data = {}
        self.pincode_map_data = {}
        self.green_pincode_set = set()
        self.green_pincode_stats = {}
        self.pincode_coords = {}
        self.district_mapping = {}
        self.geojson_text = "{}"
        self.monthly_disbursal_base = 0.0
        self.ats_min_date = None
        self.ats_max_date = None
        self.rej_min_date = None
        self.rej_max_date = None
        self.d1_min_date = None
        self.d1_max_date = None
        self._has_90p12m_internal = False
        self._has_mob12_col = False

    def run(self):
        logger.info("\u2550\u2550\u2550 DATA ENGINE \u2014 starting \u2550\u2550\u2550")
        self._prepare_directory()
        self._load_and_clean()
        self._aggregate_data()
        self._process_corridors()
        self._load_bureau_data()
        self._load_ats_data()
        self._build_monthly_disbursal_base()
        self._build_d1_pincode_volume()   # Part 3 — D1_Tracker monthly volume by pin+tier + corridor
        self._load_rejection_data()
        self._load_pincode_mapping()
        self._build_pincode_risk_data()
        self._build_green_pincode_stats()
        self._load_pincode_coords()
        self._load_geojson()
        self._save()
        logger.info(f"\u2550\u2550\u2550 DATA ENGINE \u2014 done \u2192 {DATA_OUTPUT.resolve()} \u2550\u2550\u2550")

    def _load_geojson(self):
        """Load raw GeoJSON text — stored as string in pipeline_output.json."""
        if not self.cfg.geojson_path.exists():
            logger.error(f"GeoJSON not found: {self.cfg.geojson_path}")
            return
        with open(self.cfg.geojson_path, 'r', encoding='utf-8') as f:
            self.geojson_text = f.read()
        logger.info(f"GeoJSON loaded ({len(self.geojson_text):,} chars)")

    def _build_lead_array(self) -> list:
        """Return lead array as a list of dicts (no JSON serialisation yet)."""
        cfg = self.cfg
        if self.df.empty:
            return []
        cols = [cfg.state_col, cfg.dist_col, cfg.pincode_col,
                cfg.risk_cat_col, cfg.flag_col, cfg.loan_amt_col, cfg.month_col]
        mig_cols = [c for c in [cfg.perm_state_col, cfg.perm_dist_col,
                                 cfg.perm_pincode_col,
                                 cfg.curr_state_col, cfg.curr_dist_col]
                    if c in self.df.columns]
        all_cols = list(dict.fromkeys(cols + mig_cols))
        all_cols = [c for c in all_cols if c in self.df.columns]
        df_leads = self.df[all_cols].copy()
        rename_map = {
            cfg.state_col: "s", cfg.dist_col: "d", cfg.pincode_col: "pin",
            cfg.risk_cat_col: "r", cfg.flag_col: "b", cfg.loan_amt_col: "a", cfg.month_col: "mo"
        }
        if cfg.perm_state_col in df_leads.columns: rename_map[cfg.perm_state_col] = "ps"
        if cfg.perm_dist_col  in df_leads.columns: rename_map[cfg.perm_dist_col]  = "pd"
        if cfg.perm_pincode_col in df_leads.columns: rename_map[cfg.perm_pincode_col] = "pp"
        if cfg.curr_state_col in df_leads.columns: rename_map[cfg.curr_state_col] = "cs"
        if cfg.curr_dist_col  in df_leads.columns: rename_map[cfg.curr_dist_col]  = "cd"
        df_leads = df_leads.rename(columns=rename_map)
        df_leads["a"] = pd.to_numeric(df_leads["a"], errors="coerce").fillna(0).round(0).astype(int)
        df_leads["b"] = df_leads["b"].astype(int)
        logger.info(f"Lead array: {len(df_leads):,} records")
        return df_leads.to_dict(orient="records")

    def _save(self):
        """Serialise all computed data to pipeline_output.json."""
        logger.info("Serialising to pipeline_output.json …")
        payload = {
            "PORTFOLIO_STATS":    self.port_stats,
            "REGION_DATA":        self.region_data,
            "GEOJSON_DATA":       self.geojson_text,
            "BUREAU_DATA_JSON":   self.bureau_data,
            "ATS_DATA_JSON":      self.ats_data,
            "WINDOW_CONFIG":      self._build_window_config(),
            "CORRIDOR_DATA":      self.corridor_data,
            "LEAD_ARRAY":         self._build_lead_array(),
            "REJECTION_DATA":     self.rejection_data,
            "PINCODE_RISK_DATA":  self.pincode_risk_data,
            "BUREAU_PINCODE_DATA": self.bureau_pincode_data,
            "PINCODE_MAP_DATA":   self.pincode_map_data,
            "GREEN_PINCODE_STATS": self.green_pincode_stats,
            "PINCODE_COORDS":     self.pincode_coords,
            "D1_PINCODE_VOLUME":         self.d1_pincode_volume,       # Part 3
            "CORRIDOR_D1_VOLUME":        self.corridor_d1_volume,      # Part 3b
            "MONTHLY_DISBURSAL_BASE":    self.monthly_disbursal_base,  # rupees/month for dashboard % normalisation
        }
        with open(DATA_OUTPUT, 'w', encoding='utf-8') as f:
            json.dump(payload, f, ensure_ascii=False)
        size_mb = DATA_OUTPUT.stat().st_size / 1_048_576
        logger.info(f"pipeline_output.json written ({size_mb:.1f} MB)")

    def _prepare_directory(self):
        if not self.cfg.output_dir.exists():
            self.cfg.output_dir.mkdir(parents=True, exist_ok=True)

    def _load_and_clean(self):
        logger.info("Loading Excel Data...")
        df = pd.read_excel(self.cfg.input_excel, sheet_name=self.cfg.sheet_name)
        df.rename(columns=lambda x: x.strip() if isinstance(x, str) else x, inplace=True)

        if self.cfg.loan_amt_col not in df.columns:
            df[self.cfg.loan_amt_col] = 100000.0

        def to_fy_quarter(dt):
            if pd.isna(dt): return "Unknown"
            m, y = dt.month, dt.year
            yr2 = str(y)[2:]
            mapping = {
                1: f"Jan - Mar '{yr2}", 2: f"Jan - Mar '{yr2}", 3: f"Jan - Mar '{yr2}",
                4: f"Apr - Jun '{yr2}", 5: f"Apr - Jun '{yr2}", 6: f"Apr - Jun '{yr2}",
                7: f"Jul - Sep '{yr2}", 8: f"Jul - Sep '{yr2}", 9: f"Jul - Sep '{yr2}",
                10: f"Oct - Dec '{yr2}", 11: f"Oct - Dec '{yr2}", 12: f"Oct - Dec '{yr2}",
            }
            return mapping.get(m, "Unknown")

        if self.cfg.month_col not in df.columns:
            logger.warning(f"Month column '{self.cfg.month_col}' not found. Using 'Unknown'.")
            df[self.cfg.month_col] = "Unknown"
            df['month_label'] = "Unknown"
        else:
            parsed_dates = pd.to_datetime(df[self.cfg.month_col], errors='coerce')
            df['month_label'] = parsed_dates.dt.strftime("%b '%y").fillna("Unknown")
            df[self.cfg.month_col] = parsed_dates.apply(to_fy_quarter)

        df[self.cfg.flag_col] = (pd.to_numeric(df[self.cfg.flag_col], errors='coerce').fillna(0) > 0).astype(int)
        self._has_90p12m_internal = self.cfg.flag_col_90p12m in df.columns
        if self._has_90p12m_internal:
            df[self.cfg.flag_col_90p12m] = (pd.to_numeric(df[self.cfg.flag_col_90p12m], errors='coerce').fillna(0) > 0).astype(int)
        # 90P12M needs a full 12mo on book to be observable — without this filter, loans still
        # too young to have reached 90+ DPD get counted as "good" in the denominator, silently
        # deflating the rate (verified: 3.71% unfiltered vs 5.50% on MOB12-mature loans only).
        self._has_mob12_col = self.cfg.mob12_completed_col in df.columns
        if self._has_mob12_col:
            df[self.cfg.mob12_completed_col] = (pd.to_numeric(df[self.cfg.mob12_completed_col], errors='coerce').fillna(0) > 0)
        df[self.cfg.loan_amt_col] = pd.to_numeric(df[self.cfg.loan_amt_col], errors='coerce').fillna(0)
        df[self.cfg.risk_cat_col] = df[self.cfg.risk_cat_col].astype(str).str.title().str.strip()
        df[self.cfg.state_col]    = df[self.cfg.state_col].astype(str).str.title().str.strip()
        df[self.cfg.dist_col]     = df[self.cfg.dist_col].astype(str).str.title().str.strip()

        # Clean Phase 2 migration columns if present
        for col in [self.cfg.perm_state_col, self.cfg.perm_dist_col, self.cfg.curr_state_col, self.cfg.curr_dist_col]:
            if col in df.columns:
                df[col] = df[col].astype(str).str.title().str.strip()

        state_mapping = {
            "Nct Of Delhi": "Delhi", "Orissa": "Odisha", "Chattisgarh": "Chhattisgarh",
            "Tamilnadu": "Tamil Nadu", "Jammu & Kashmir": "Jammu and Kashmir",
            "Pondicherry": "Puducherry",
            "Dadra & Nagar Haveli And Daman & Diu": "Dadra and Nagar Haveli and Daman and Diu",
            # Same UT, spelled without ampersands but still title-cased ("And" not "and") — a
            # second, case-only duplicate of the row above that the ampersand-keyed entry can't
            # catch, leaving 33 leads (district "Daman") stranded under a near-duplicate state
            # key that never merges with the canonical-cased one BUREAU_DATA/GeoJSON use.
            "Dadra And Nagar Haveli And Daman And Diu": "Dadra and Nagar Haveli and Daman and Diu",
            "Andaman & Nicobar Islands": "Andaman and Nicobar Islands",
        }

        df[self.cfg.state_col] = df[self.cfg.state_col].replace(state_mapping)
        if self.cfg.perm_state_col in df.columns:
            df[self.cfg.perm_state_col] = df[self.cfg.perm_state_col].replace(state_mapping)
        if self.cfg.curr_state_col in df.columns:
            df[self.cfg.curr_state_col] = df[self.cfg.curr_state_col].replace(state_mapping)

        # ── District alias mapping: data names → GeoJSON census names ──────────
        # VERIFIED against unmapped_districts diagnostic CSV (2026-05-23).
        # Direction: "Name in your data file" → "Exact GeoJSON feature name"
        # ⚠ Never add reversed entries — they break previously-matched districts.
        district_mapping = {

            # ── ANDHRA PRADESH ───────────────────────────────────────────────
            "Sri Potti Sriramulu Nellore": "Nellore",
            "Spsr Nellore":                "Nellore",
            "Vishakhapatnam":              "Visakhapatnam",
            "Vizag":                       "Visakhapatnam",
            "Ananthapur":                  "Anantapur",
            "Cuddapah":                    "Kadapa",            # GeoJSON census name is Kadapa
            "Chittor":                     "Chittoor",

            # ── CHHATTISGARH ────────────────────────────────────────────────
            "Janjgir Champa":              "Janjgir-Champa",
            "Dantewada":                   "South Bastar Dantewada",
            "Kanker":                      "Uttar Bastar Kanker",

            # ── DELHI ───────────────────────────────────────────────────────
            # The GeoJSON has a single "Delhi" polygon (no NCT sub-district boundaries). Before
            # this fix, Delhi's sub-districts (Central/North/North East/North West/South/South
            # West Delhi — 86/97/92/220/277/433 leads respectively) stayed in REGION_DATA under
            # their own separate keys (so they were still selectable in list-based UI and the
            # simulation), but had NO matching polygon to render or color on the map — meaning
            # the Delhi polygon's map color/hover/rate only ever reflected the exact-match
            # "Delhi" bucket, silently excluding ~1,205 leads' worth of these sub-districts from
            # the map's Delhi numbers. Consolidating them into "Delhi" fixes that undercounting
            # and removes the fragmentation in list views.
            "Central Delhi":              "Delhi",
            "North Delhi":                "Delhi",
            "North East Delhi":           "Delhi",
            "North West Delhi":           "Delhi",
            "South Delhi":                "Delhi",
            "South West Delhi":           "Delhi",
            "New Delhi":                  "Delhi",
            "East Delhi":                 "Delhi",
            "West Delhi":                 "Delhi",

            # ── GUJARAT ─────────────────────────────────────────────────────
            "Ahmedabad City":              "Ahmedabad",
            "Vadodara City":               "Vadodara",
            "Surat City":                  "Surat",
            "Kachchh":                     "Kutch",             # GeoJSON uses English spelling
            "Mahesana":                    "Mehsana",
            # GeoJSON's exact district spelling is lowercase "and" ("Dadra and Nagar Haveli") —
            # both the ampersand form and the already-title-cased "And" form need to resolve to
            # it, or the district stays a step short of matching even after the state gets
            # corrected (see the Gujarat/Dadra And Nagar Haveli state-correction rule below).
            "Dadra & Nagar Haveli":        "Dadra and Nagar Haveli",
            "Dadra And Nagar Haveli":      "Dadra and Nagar Haveli",
            "The Dangs":                   "Dang",
            # Bureau CSV spellings — GeoJSON/portfolio use the joined forms; without these the
            # same district exists under two keys and Market View shows "No Market Data"
            "Banas Kantha":                "Banaskantha",
            "Sabar Kantha":                "Sabarkantha",

            # ── HARYANA ─────────────────────────────────────────────────────
            "Manesar":                     "Gurugram",
            "Gurgaon":                     "Gurugram",

            # ── JHARKHAND ───────────────────────────────────────────────────
            "Saraikela Kharsawan":         "Saraikela-Kharsawan",
            "Seraikela-Kharsawan":         "Saraikela-Kharsawan",
            "West Singhbhum":              "Pashchimi Singhbhum",
            "East Singhbhum":              "Purbi Singhbhum",

            # ── KARNATAKA ───────────────────────────────────────────────────
            "Bangalore":                   "Bengaluru Urban",
            "Bangalore Urban":             "Bengaluru Urban",
            "Bangalore Rural":             "Bengaluru Rural",
            "Mysore":                      "Mysuru",
            "Gulbarga":                    "Kalaburagi",
            "Belgaum":                     "Belagavi",
            "Bijapur":                     "Vijayapura",
            "Bijapur(Kar)":                "Vijayapura",
            "Shimoga":                     "Shivamogga",
            "Bellary":                     "Ballari",
            "Tumkur":                      "Tumakuru",
            "Chikmagalur":                 "Chikkamagaluru",
            "Chikkamagalur":               "Chikkamagaluru",
            "Chickmagalur":                "Chikkamagaluru",
            "Davangere":                   "Davanagere",
            "Chamrajnagar":                "Chamarajanagar",
            "Mangalore":                   "Dakshina Kannada",
            "Hubli Dharwad":               "Dharwad",

            # ── KERALA ──────────────────────────────────────────────────────
            "Kasargod":                    "Kasaragod",

            # ── MADHYA PRADESH ──────────────────────────────────────────────
            "Narmadapuram":                "Hoshangabad",
            "East Nimar":                  "Khandwa",
            "West Nimar":                  "Khargone",

            # ── MAHARASHTRA ─────────────────────────────────────────────────
            "Bombay":                      "Mumbai",
            "Mumbai City":                 "Mumbai",
            "Nasik":                       "Nashik",
            "Raigarh(Mh)":                 "Raigad",
            "Raigarh Mh":                  "Raigad",
            "Sholapur":                    "Solapur",

            # ── ODISHA ──────────────────────────────────────────────────────
            "Jagatsinghapur":              "Jagatsinghpur",
            "Sonapur":                     "Subarnapur",
            "Baleswar":                    "Balasore",
            "Baleshwar":                   "Balasore",
            "Khorda":                      "Khordha",
            "Sundergarh":                  "Sundargarh",
            "Jajapur":                     "Jajpur",
            "Debagarh":                    "Deogarh",

            # ── PUNJAB ──────────────────────────────────────────────────────
            "Sahibzada Ajit Singh Nagar":  "S.A.S. Nagar",
            "Mohali":                      "S.A.S. Nagar",
            "Firozpur":                    "Ferozepur",
            "Nawanshahr":                  "Shahid Bhagat Singh Nagar",

            # ── RAJASTHAN ───────────────────────────────────────────────────
            "Jhujhunu":                    "Jhunjhunu",
            "Jaipur Rural":                "Jaipur",
            "Jodhpur Rural":               "Jodhpur",
            "Anupgarh":                    "Sri Ganganagar",

            # ── TAMIL NADU ──────────────────────────────────────────────────
            "Kanchipuram":                 "Kancheepuram",
            "Tiruvallur":                  "Thiruvallur",
            "Villupuram":                  "Viluppuram",
            "Tiruvarur":                   "Thiruvarur",
            "Tuticorin":                   "Thoothukudi",
            "Madras":                      "Chennai",
            "Tiruchirapalli":              "Tiruchirappalli",
            "Trichy":                      "Tiruchirappalli",
            "Tirunelveli Kattabo":         "Tirunelveli",

            # ── PUDUCHERRY ──────────────────────────────────────────────────
            # District name itself (not just the state) needs correcting — GeoJSON's
            # Puducherry-state district list is Karaikal/Mahe/Puducherry/Yanam; "Pondicherry"
            # is the old name for the "Puducherry" district specifically.
            "Pondicherry":                 "Puducherry",

            # ── TELANGANA ───────────────────────────────────────────────────
            "Hanamkonda":                  "Warangal Urban",
            "Ranga Reddy":                 "Rangareddy",
            "Rangareddi":                  "Rangareddy",
            "Hyderabad Rural":             "Rangareddy",
            "Bhadradri (Kothagudem)":      "Bhadradri Kothagudem",  # bureau CSV spelling

            # ── UTTARAKHAND ─────────────────────────────────────────────────
            # Bureau CSV spellings — GeoJSON/portfolio use the joined forms
            "Dehra Dun":                   "Dehradun",
            "Uttar Kashi":                 "Uttarkashi",

            # ── UTTAR PRADESH ───────────────────────────────────────────────
            "Jyotiba Phule Nagar":         "Amroha",
            "Bagpat":                      "Baghpat",
            # GeoJSON's actual district name is just "Bhadohi" — the previous target
            # "Sant Ravidas Nagar (Bhadohi)" never existed in the GeoJSON either, so this
            # alias silently dropped every affected lead despite looking like it was mapped.
            "Sant Ravidas Nagar":              "Bhadohi",
            "Sant Ravidas Nagar (Bhadohi)":    "Bhadohi",
            "Allahabad":                   "Prayagraj",
            "Faizabad":                    "Ayodhya",
            "Muzaffar Nagar":              "Muzaffarnagar",
            "Kushi Nagar":                 "Kushinagar",

            # ── WEST BENGAL ─────────────────────────────────────────────────
            # GeoJSON uses "North 24 Parganas" (numeral) and Bengali names
            "North Twenty Four Parganas":  "North 24 Parganas",
            "South Twenty Four Parganas":  "South 24 Parganas",
            "24 Paraganas North":          "North 24 Parganas",
            "24 Paraganas South":          "South 24 Parganas",
            "N 24 Parganas":               "North 24 Parganas",
            "S 24 Parganas":               "South 24 Parganas",
            "East Midnapore":              "Purba Medinipur",
            "West Midnapore":              "Paschim Medinipur",
            "East Medinipur":              "Purba Medinipur",
            "West Medinipur":              "Paschim Medinipur",
            "Medinipur East":              "Purba Medinipur",
            "Medinipur West":              "Paschim Medinipur",
            "Puruliya":                    "Purulia",
            "Darjiling":                   "Darjeeling",
            "North Dinajpur":              "Uttar Dinajpur",
            "South Dinajpur":              "Dakshin Dinajpur",
            "Calcutta":                    "Kolkata",
            "Bardhaman":                   "Purba Bardhaman",
            "Burdwan":                     "Purba Bardhaman",

            # ── HIMACHAL PRADESH ────────────────────────────────────────────
            "Lahul & Spiti":               "Lahaul And Spiti",
            "Lahul And Spiti":             "Lahaul And Spiti",
        }

        # Apply to all district columns in risk data
        for col in [self.cfg.dist_col, self.cfg.perm_dist_col, self.cfg.curr_dist_col]:
            if col in df.columns:
                df[col] = df[col].replace(district_mapping)
        self.district_mapping = district_mapping   # reused in _load_bureau_data
        logger.info(f"District alias mapping applied ({len(district_mapping)} aliases defined)")

        # ── State/district cross-mismatches — trust the district, fix the state ─────
        # These (state, district) pairs don't correspond to any real geography: the district
        # named genuinely belongs to a DIFFERENT state/UT than the one recorded (confirmed
        # against the GeoJSON — e.g. "Haridwar" only exists under Uttarakhand, never Uttar
        # Pradesh). A flat district_mapping can't express "only when paired with this specific
        # wrong state" — it remaps a district name unconditionally regardless of state, which
        # would wrongly touch a correctly-labeled district of the same name elsewhere. Decision
        # (2026-07-10): trust the district field as correct and override the state field for
        # just these specific rows. Two of these ("Chandigarh"/"Rupnagar" and "Punjab"/
        # "Chandigarh") look like a mirrored state/district swap at the Punjab-Chandigarh
        # border; "Uttar Pradesh"/"Haridwar" and "Uttarakhand"/"Saharanpur" look like a similar
        # mirrored swap at the UP-Uttarakhand border — both resolve correctly by trusting the
        # district value independently in each row.
        # MUST run after district_mapping (above), not before: raw values like "Gurgaon" and
        # "Dadra & Nagar Haveli" are still their pre-alias spelling before district_mapping
        # runs, so matching against the post-alias literal here ("Gurugram", "Dadra And Nagar
        # Haveli") silently matched zero rows the first time this was tried pre-alias.
        STATE_DISTRICT_CORRECTIONS = [
            # (recorded state, district, corrected state)
            ("Chandigarh", "Rupnagar",                  "Punjab"),
            ("Punjab",     "Chandigarh",                 "Chandigarh"),
            ("Delhi",      "Gurugram",                   "Haryana"),
            ("Gujarat",    "Dadra and Nagar Haveli",     "Dadra and Nagar Haveli and Daman and Diu"),  # district_mapping already lowercases "and" before this runs
            ("Uttar Pradesh",  "Haridwar",               "Uttarakhand"),
            ("Uttarakhand",    "Saharanpur",             "Uttar Pradesh"),
            ("Tamil Nadu", "Puducherry",                 "Puducherry"),  # district_mapping already renames "Pondicherry"→"Puducherry" before this runs
        ]
        for wrong_state, district, correct_state in STATE_DISTRICT_CORRECTIONS:
            mask = (df[self.cfg.state_col] == wrong_state) & (df[self.cfg.dist_col] == district)
            n = int(mask.sum())
            if n:
                df.loc[mask, self.cfg.state_col] = correct_state
                logger.info(f"State/district correction: {n} lead(s) '{wrong_state}: {district}' → state '{correct_state}'")

        # ── Auto fuzzy-match any remaining districts against GeoJSON ─────────────
        # Handles new data or GeoJSON updates automatically without manual dict edits.
        auto_map = self._auto_match_districts(df)
        if auto_map:
            for col in [self.cfg.dist_col, self.cfg.perm_dist_col, self.cfg.curr_dist_col]:
                if col in df.columns:
                    df[col] = df[col].replace(auto_map)
            self.district_mapping.update(auto_map)
            logger.info(f"Auto-matched {len(auto_map)} additional district(s) via fuzzy matching")



        self.df = df

        initial_len = len(self.df)
        canonical_risks = ['Low', 'Medium', 'High', 'Very High']
        self.df = self.df[self.df[self.cfg.risk_cat_col].isin(canonical_risks)]
        logger.info(f"Dropped {initial_len - len(self.df)} non-canonical tagged leads.")


    # ── FUZZY DISTRICT-TO-GEOJSON MATCHING ───────────────────────────────────

    @staticmethod
    def _norm_name(s: str) -> str:
        """Normalise a place name for fuzzy comparison — strips noise, maps
        direction synonyms so Purba↔East, Twenty Four↔24, NCT of Delhi↔Delhi."""
        import unicodedata, re
        s = str(s).strip()
        s = unicodedata.normalize('NFKD', s).encode('ascii', 'ignore').decode('ascii')
        s = s.lower()
        # Expand abbreviations
        s = re.sub(r'\bs\.?a\.?s\.?\b', 'sahibzada ajit singh', s)
        # Normalise number words
        s = re.sub(r'\btwenty\s*[-]?\s*four\b', '24', s)
        # Direction synonyms (Bengali/Hindi ↔ English)
        for pat, rep in [(r'\bpurba\b','east'),(r'\bpaschim\b','west'),
                         (r'\buttar\b','north'),(r'\bdakshin\b','south')]:
            s = re.sub(pat, rep, s)
        # Remove state-name prefix noise: "NCT of Delhi" → "delhi"
        s = re.sub(r'\b(national|capital|territory|nct|union|of)\b', '', s)
        # Remove district-label noise
        s = re.sub(r'\b(district|dist|city|urban|rural|municipal|corporation|zila|tahsil)\b', '', s)
        s = re.sub(r'[^a-z0-9\s]', ' ', s)
        s = re.sub(r'\s+', ' ', s).strip()
        return s

    def _auto_match_districts(self, df=None) -> dict:
        """Fuzzy-match remaining unmatched districts against GeoJSON.
        Returns {data_name: geojson_name} for high-confidence matches.
        Writes district_suggestions.csv and unmapped_districts_<date>.csv."""
        import json, difflib, datetime
        from pathlib import Path

        # Read from Phase2Config rather than hardcoding — previously these were re-declared as
        # local constants with the same default values, so editing the config field silently
        # had no effect at the actual call site below.
        AUTO_THRESH    = self.cfg.auto_match_threshold
        SUGGEST_THRESH = self.cfg.suggest_threshold

        gj_path = Path(self.cfg.geojson_path)
        if not gj_path.exists():
            logger.warning(f"GeoJSON not found at {gj_path} — skipping auto district match")
            return {}

        with open(gj_path, 'r', encoding='utf-8') as f:
            gj = json.load(f)

        # Build GeoJSON index: state_norm → {dist_norm: dist_original}
        geo_idx = {}
        for feat in gj.get('features', []):
            p  = feat.get('properties', {})
            sn = (p.get('st_nm') or p.get('STATE') or p.get('ST_NM') or p.get('NAME_1') or '').strip()
            dn = (p.get('district') or p.get('DISTRICT') or p.get('dtname') or
                  p.get('NAME_2') or p.get('name') or p.get('NAME') or '').strip()
            if sn and dn:
                geo_idx.setdefault(self._norm_name(sn), {})[self._norm_name(dn)] = dn

        all_geo_state_norms = list(geo_idx.keys())

        data = df if df is not None else self.df
        if data is None or data.empty:
            logger.warning("_auto_match_districts: no data available")
            return {}
        dist_leads = (
            data.groupby([self.cfg.state_col, self.cfg.dist_col])
                .size().reset_index(name='leads')
        )

        auto_map, applied, suggestions, unmatched = {}, [], [], []

        for _, row in dist_leads.iterrows():
            st, dt, leads = str(row[self.cfg.state_col]), str(row[self.cfg.dist_col]), int(row['leads'])
            st_k, dt_k = self._norm_name(st), self._norm_name(dt)

            # Find GeoJSON state (exact or fuzzy)
            geo_dists = geo_idx.get(st_k)
            if geo_dists is None:
                close_st = difflib.get_close_matches(st_k, all_geo_state_norms, n=1, cutoff=0.75)
                geo_dists = geo_idx.get(close_st[0]) if close_st else None
            if geo_dists is None:
                unmatched.append({'state':st,'district':dt,'leads':leads,'note':'state not in GeoJSON'})
                continue

            # Exact match after normalisation — nothing to do
            if dt_k in geo_dists:
                continue

            # Fuzzy match
            geo_norms = list(geo_dists.keys())
            best = difflib.get_close_matches(dt_k, geo_norms, n=1, cutoff=SUGGEST_THRESH)
            if not best:
                unmatched.append({'state':st,'district':dt,'leads':leads,'note':'no fuzzy match found'})
                continue

            ratio    = difflib.SequenceMatcher(None, dt_k, best[0]).ratio()
            geo_orig = geo_dists[best[0]]

            if ratio >= AUTO_THRESH:
                if dt not in auto_map:
                    auto_map[dt] = geo_orig
                applied.append({'state':st,'data_district':dt,'geo_district':geo_orig,
                                'similarity':round(ratio,3),'leads':leads})
                logger.info(f"  [auto-match] \'{dt}\' ({st}) → \'{geo_orig}\'  sim={ratio:.2f}")
            else:
                suggestions.append({'state':st,'data_district':dt,'suggested_geo_district':geo_orig,
                                    'similarity':round(ratio,3),'leads':leads,
                                    'action':f'Add  \"{dt}\": \"{geo_orig}\"  to district_mapping if correct'})

        # Write CSVs
        out, today = self.cfg.output_dir, datetime.date.today().isoformat()
        def _w(rows, fn):
            if not rows: return
            import csv
            p = out / fn
            with open(p,'w',newline='',encoding='utf-8') as f:
                w = csv.DictWriter(f, fieldnames=rows[0].keys()); w.writeheader(); w.writerows(rows)
            logger.info(f"  → {p}")

        if applied:    _w(applied,     'auto_matched_districts.csv')
        if suggestions:
            logger.warning(f"{len(suggestions)} district(s) need manual review:")
            for s in suggestions:
                logger.warning(f"  ? \'{s['data_district']}\' ({s['state']}) → \'{s['suggested_geo_district']}\' sim={s['similarity']}")
            _w(suggestions,'district_suggestions.csv')
        if unmatched:
            logger.warning(f"{len(unmatched)} district(s) still unmatched after all mapping:")
            for u in unmatched:
                logger.warning(f"  ✗ {u['state']}: {u['district']} ({u['leads']} leads) — {u['note']}")
            _w(unmatched, f'unmapped_districts_{today}.csv')

        logger.info(f"District geo-match: {len(applied)} auto-applied | "
                    f"{len(suggestions)} suggestions | {len(unmatched)} unmatched")
        return auto_map

    def _rate_90p12m(self, df_slice):
        """Bad-rate over the 90P12M flag, restricted to loans that have actually had a full
        12mo on book (MOB_12_completed) — mirrors the bureau side's cohort-maturity handling.
        Returns (bad, rate) or (None, None) if this slice has no mature loans yet (rather
        than silently reporting 0.0%, which would misleadingly read as "no delinquency").
        """
        if not (self._has_90p12m_internal and self._has_mob12_col):
            bad = int(df_slice[self.cfg.flag_col_90p12m].sum()) if self._has_90p12m_internal else None
            rate = float(df_slice[self.cfg.flag_col_90p12m].mean()) if self._has_90p12m_internal and len(df_slice) else None
            return bad, rate
        mature = df_slice[df_slice[self.cfg.mob12_completed_col]]
        if mature.empty:
            return None, None
        bad = int(mature[self.cfg.flag_col_90p12m].sum())
        rate = float(mature[self.cfg.flag_col_90p12m].mean())
        return bad, rate

    def _aggregate_data(self):
        logger.info("Aggregating Portfolio and Regional Data...")
        port_agg = self.df.groupby(self.cfg.risk_cat_col).agg(
            bad=(self.cfg.flag_col, 'sum'), total=(self.cfg.flag_col, 'size'), amt=(self.cfg.loan_amt_col, 'sum')
        ).reset_index()

        for _, r in port_agg.iterrows():
            cat = r[self.cfg.risk_cat_col]
            self.port_stats[cat] = {
                "bad":   int(r['bad']),
                "total": int(r['total']),
                "rate":  float(r['bad'] / r['total']) if r['total'] else 0.0,
                "ats":   float(r['amt'] / r['total']) if r['total'] else 0.0
            }

        self.port_stats['Total'] = {
            "bad":     int(self.df[self.cfg.flag_col].sum()),
            "total":   len(self.df),
            "rate":    float(self.df[self.cfg.flag_col].mean()),
            "ats":     float(self.df[self.cfg.loan_amt_col].mean()),
            "sum_amt": float(self.df[self.cfg.loan_amt_col].sum())
        }

        for state in self.df[self.cfg.state_col].unique():
            if state == 'Nan': continue
            s_df = self.df[self.df[self.cfg.state_col] == state]

            state_cats = {}
            for cat in s_df[self.cfg.risk_cat_col].unique():
                c_df = s_df[s_df[self.cfg.risk_cat_col] == cat]
                state_cats[cat] = {
                    "bad":     int(c_df[self.cfg.flag_col].sum()),
                    "total":   len(c_df),
                    "sum_amt": float(c_df[self.cfg.loan_amt_col].sum())
                }

            districts = {}
            for dist in s_df[self.cfg.dist_col].unique():
                d_df = s_df[s_df[self.cfg.dist_col] == dist]
                cats = {}
                for cat in d_df[self.cfg.risk_cat_col].unique():
                    c_df = d_df[d_df[self.cfg.risk_cat_col] == cat]
                    months = {}
                    for m in c_df['month_label'].unique():
                        m_df = c_df[c_df['month_label'] == m]
                        months[str(m)] = {
                            "total":   len(m_df),
                            "bad":     int(m_df[self.cfg.flag_col].sum()),
                            "sum_amt": float(m_df[self.cfg.loan_amt_col].sum())
                        }
                    cats[cat] = {
                        "bad":     int(c_df[self.cfg.flag_col].sum()),
                        "total":   len(c_df),
                        "sum_amt": float(c_df[self.cfg.loan_amt_col].sum()),
                        "months":  months
                    }
                districts[dist] = {
                    "bad":   int(d_df[self.cfg.flag_col].sum()),
                    "total": len(d_df),
                    "rate":  float(d_df[self.cfg.flag_col].mean()),
                    "cats":  cats
                }
                bad90, rate90 = self._rate_90p12m(d_df)
                if bad90 is not None:
                    districts[dist]["bad_90p12m"]  = bad90
                    districts[dist]["rate_90p12m"] = rate90

            self.region_data[state] = {
                "bad":       int(s_df[self.cfg.flag_col].sum()),
                "total":     len(s_df),
                "rate":      float(s_df[self.cfg.flag_col].mean()),
                "cats":      state_cats,
                "districts": districts
            }
            bad90, rate90 = self._rate_90p12m(s_df)
            if bad90 is not None:
                self.region_data[state]["bad_90p12m"]  = bad90
                self.region_data[state]["rate_90p12m"] = rate90

    def _process_corridors(self):
        """Build migration corridor data for the Corridor Inspector tab.

        Produces self.corridor_data — a flat dict keyed by "Perm State → Curr State"
        with overall stats, risk-category breakdown, and per-category current-district
        sub-counts for deduplication against district cart items.
        """
        cfg = self.cfg
        if not (cfg.perm_state_col in self.df.columns and cfg.curr_state_col in self.df.columns):
            logger.warning("Migration columns missing from data. Corridor data will be empty.")
            return

        logger.info("Processing Migration Corridor Data...")

        df = self.df[
            self.df[cfg.perm_state_col].notna() & ~self.df[cfg.perm_state_col].isin(["", "Nan", "None"]) &
            self.df[cfg.curr_state_col].notna()  & ~self.df[cfg.curr_state_col].isin(["", "Nan", "None"])
        ].copy()

        df = df[df[cfg.perm_state_col] != df[cfg.curr_state_col]]
        if df.empty:
            logger.warning("No inter-state migration leads found.")
            return

        df["_corridor"] = df[cfg.perm_state_col].str.strip() + " → " + df[cfg.curr_state_col].str.strip()
        canonical = ['Low', 'Medium', 'High', 'Very High']

        corridor_data = {}
        for corr_key, c_df in df.groupby("_corridor"):
            perm_state = c_df[cfg.perm_state_col].iloc[0]
            curr_state = c_df[cfg.curr_state_col].iloc[0]
            total = len(c_df)
            bad   = int(c_df[cfg.flag_col].sum())

            cats = {}
            for cat in canonical:
                cat_df = c_df[c_df[cfg.risk_cat_col] == cat]
                if cat_df.empty:
                    continue
                cat_total = len(cat_df)
                cat_bad   = int(cat_df[cfg.flag_col].sum())
                curr_dists = {}
                if cfg.curr_dist_col in cat_df.columns:
                    for dist, d_df in cat_df.groupby(cfg.curr_dist_col):
                        dist = str(dist).strip()
                        if dist in ("", "Nan", "None", "nan"): continue
                        curr_dists[dist] = {"total": int(len(d_df)), "bad": int(d_df[cfg.flag_col].sum())}
                cats[cat] = {"total": cat_total, "bad": cat_bad, "curr_districts": curr_dists}

            corridor_data[corr_key] = {
                "perm_state": str(perm_state),
                "curr_state":  str(curr_state),
                "total":       total,
                "bad":         bad,
                "rate":        float(bad / total) if total else 0.0,
                "cats":        cats
            }

        self.corridor_data = dict(sorted(corridor_data.items(), key=lambda x: x[1]["total"], reverse=True))
        logger.info(f"Corridor data: {len(self.corridor_data)} inter-state corridors across {len(df):,} migrant leads.")

    @staticmethod
    def _fmt_pin(v):
        """Normalise any pincode value to a clean zero-padded string.

        Handles the common Excel-float issue where 380001 is stored as 380001.0
        and astype(str) produces '380001.0' instead of '380001'.
        """
        try:
            return str(int(float(v))).zfill(6)
        except Exception:
            return str(v).strip()

    def _load_bureau_data(self):
        frames = []
        for fp in self.cfg.bureau_files:
            if not fp: continue
            p = Path(fp)
            if not p.exists():
                logger.warning(f"Bureau file not found, skipping: {p}")
                continue
            try:
                df = pd.read_excel(p) if p.suffix.lower() in ('.xlsx', '.xls') else pd.read_csv(p, low_memory=False)
                frames.append(df)
                logger.info(f"Loaded bureau file: {p} ({len(df):,} rows)")
            except Exception as e:
                logger.warning(f"Could not load bureau file {p}: {e}")

        if not frames:
            logger.warning("No bureau files loaded — market overlay will be empty.")
            return

        df = pd.concat(frames, ignore_index=True)
        df.rename(columns=lambda x: x.strip() if isinstance(x, str) else x, inplace=True)

        needed = ['STATE', 'DISTRICT', 'ORG_QRT', 'NUMBER_OF_LOANS', 'DELINQUENT_30P6M_TRADES']
        missing = [c for c in needed if c not in df.columns]
        if missing:
            logger.error(f"Bureau data missing required columns: {missing}. Skipping market overlay.")
            return

        df['STATE']                   = df['STATE'].astype(str).str.title().str.strip()
        df['DISTRICT']                = df['DISTRICT'].astype(str).str.title().str.strip()
        df['ORG_QRT']                 = df['ORG_QRT'].astype(str).str.strip()
        df['NUMBER_OF_LOANS']         = pd.to_numeric(df['NUMBER_OF_LOANS'],         errors='coerce').fillna(0)
        df['DELINQUENT_30P6M_TRADES'] = pd.to_numeric(df['DELINQUENT_30P6M_TRADES'], errors='coerce').fillna(0)

        # 90P12M is only present in newer bureau files (e.g. Market_Data_4_PL.csv) — optional
        has_90p12m = 'DELINQUENT_90P12M_TRADES' in df.columns
        if has_90p12m:
            df['DELINQUENT_90P12M_TRADES'] = pd.to_numeric(df['DELINQUENT_90P12M_TRADES'], errors='coerce').fillna(0)

        # TOTAL_SANCTIONED_AMOUNT is only present in newer bureau files (the bureau data file) — optional
        has_amt = 'TOTAL_SANCTIONED_AMOUNT' in df.columns
        if has_amt:
            df['TOTAL_SANCTIONED_AMOUNT'] = pd.to_numeric(df['TOTAL_SANCTIONED_AMOUNT'], errors='coerce').fillna(0)

        # NUMBER_OF_LOANS is the MOB6 population — NOT the same population that reached MOB12
        # (verified: ~27% of loans drop out of CIBIL reporting between MOB6 and MOB12, understating
        # 90P12M by ~35-40% when divided by the larger MOB6 count). NUMBER_OF_LOANS_90P12M is the
        # correct, independently-computed MOB12 population; fall back to NUMBER_OF_LOANS (old,
        # understated behavior) only if the SQL hasn't been updated to export it yet.
        has_90p12m_loans_col = 'NUMBER_OF_LOANS_90P12M' in df.columns
        if has_90p12m_loans_col:
            df['NUMBER_OF_LOANS_90P12M'] = pd.to_numeric(df['NUMBER_OF_LOANS_90P12M'], errors='coerce').fillna(0)
            loans90_col = 'NUMBER_OF_LOANS_90P12M'
        else:
            loans90_col = 'NUMBER_OF_LOANS'
            if has_90p12m:
                logger.warning("NUMBER_OF_LOANS_90P12M column not found — 90P12M rate is falling back to "
                                "the MOB6 loan count as its denominator, which UNDERSTATES the true rate "
                                "(verified ~35-40% low). Update the bureau SQL to export a real MOB12 "
                                "population count.")

        # Apply same district alias mapping as risk file so bureau district names
        # match the GeoJSON polygon names and align with normalised R_DATA district keys
        if hasattr(self, 'district_mapping') and self.district_mapping:
            before = df['DISTRICT'].nunique()
            df['DISTRICT'] = df['DISTRICT'].replace(self.district_mapping)
            after = df['DISTRICT'].nunique()
            logger.info(f"Bureau district mapping applied: {before} → {after} unique districts")

        # Maturity gate computed on the raw "YYYY-Qn" ORG_QRT (lexicographically sortable in this
        # fixed-width format) BEFORE it's overwritten below with the display-label conversion.
        df['_mature_30p6m']  = df['ORG_QRT'] <= self.cfg.bureau_30p6m_mature_through
        df['_mature_90p12m'] = df['ORG_QRT'] <= self.cfg.bureau_90p12m_mature_through

        def cal_to_fy_quarter(cal_q):
            try:
                year, q = cal_q.split('-Q')
                year, q = int(year), int(q)
                yr2 = str(year)[2:]
                mapping = {1: f"Jan - Mar '{yr2}", 2: f"Apr - Jun '{yr2}", 3: f"Jul - Sep '{yr2}", 4: f"Oct - Dec '{yr2}"}
                return mapping.get(q, cal_q)
            except Exception:
                return cal_q

        df['ORG_QRT'] = df['ORG_QRT'].apply(cal_to_fy_quarter)
        # No quarter filter on the per-quarter breakdown — every quarter still appears there
        # (correctly near-zero for immature ones); only the blended "overall" rate is gated above.

        state_mapping = {
            "Nct Of Delhi": "Delhi", "Orissa": "Odisha", "Chattisgarh": "Chhattisgarh",
            "Tamilnadu": "Tamil Nadu", "Jammu & Kashmir": "Jammu and Kashmir",
            "Pondicherry": "Puducherry",
            "Dadra & Nagar Haveli And Daman & Diu": "Dadra and Nagar Haveli and Daman and Diu",
            "Andaman & Nicobar Islands": "Andaman and Nicobar Islands",
        }
        df['STATE'] = df['STATE'].replace(state_mapping)
        if hasattr(self, 'district_mapping') and self.district_mapping:
            df['DISTRICT'] = df['DISTRICT'].replace(self.district_mapping)

        def _agg_to_records(grouped_df, quarter_col):
            agg_kwargs = {'loans': ('NUMBER_OF_LOANS', 'sum'), 'delinquent': ('DELINQUENT_30P6M_TRADES', 'sum')}
            if has_90p12m:
                agg_kwargs['delinquent_90p12m'] = ('DELINQUENT_90P12M_TRADES', 'sum')
                agg_kwargs['loans_90p12m'] = (loans90_col, 'sum')
            if has_amt:
                agg_kwargs['amt'] = ('TOTAL_SANCTIONED_AMOUNT', 'sum')
            by_qtr = grouped_df.groupby(quarter_col).agg(**agg_kwargs).reset_index().sort_values(quarter_col)
            # Per-quarter rates stay unfiltered — immature quarters correctly show near-zero here,
            # which is informative. Only the blended "overall" below is gated to mature cohorts.
            by_qtr['rate'] = (by_qtr['delinquent'] / by_qtr['loans'].replace(0, float('nan')) * 100).round(2).fillna(0)

            mature30 = grouped_df[grouped_df['_mature_30p6m']]
            total_loans      = int(mature30['NUMBER_OF_LOANS'].sum())
            total_delinquent = int(mature30['DELINQUENT_30P6M_TRADES'].sum())
            overall_rate     = round(total_delinquent / total_loans * 100, 2) if total_loans else 0.0
            overall = {"loans": total_loans, "delinquent": total_delinquent, "rate": overall_rate}

            if has_amt:
                # Sanctioned amount is a portfolio-size metric, not a delinquency rate — summed
                # over all quarters actually selected, not gated to the 30P6M/90P12M maturity cutoffs.
                overall["amt"] = int(grouped_df['TOTAL_SANCTIONED_AMOUNT'].sum())

            if has_90p12m:
                # rate_90p12m — both per-quarter and overall — divides by the MOB12 population
                # (loans90_col), NOT the MOB6 "loans" column used for 30P6M above.
                by_qtr['rate_90p12m'] = (by_qtr['delinquent_90p12m'] / by_qtr['loans_90p12m'].replace(0, float('nan')) * 100).round(2).fillna(0)
                mature90 = grouped_df[grouped_df['_mature_90p12m']]
                total_loans_90p12m     = int(mature90[loans90_col].sum())
                total_delinquent_90p12m = int(mature90['DELINQUENT_90P12M_TRADES'].sum())
                overall['loans_90p12m'] = total_loans_90p12m
                overall['delinquent_90p12m'] = total_delinquent_90p12m
                overall['rate_90p12m'] = round(total_delinquent_90p12m / total_loans_90p12m * 100, 2) if total_loans_90p12m else 0.0

            quarterly = []
            for _, row in by_qtr.iterrows():
                rec = {"q": row[quarter_col], "loans": int(row['loans']), "delinquent": int(row['delinquent']), "rate": float(row['rate'])}
                if has_90p12m:
                    rec['loans_90p12m'] = int(row['loans_90p12m'])
                    rec['delinquent_90p12m'] = int(row['delinquent_90p12m'])
                    rec['rate_90p12m'] = float(row['rate_90p12m'])
                if has_amt:
                    rec['amt'] = int(row['amt'])
                quarterly.append(rec)
            return overall, quarterly

        bureau_data = {}
        for state, s_df in df.groupby('STATE'):
            if state in ('Nan', 'None', '', 'Nat', 'Others'): continue
            s_overall, s_quarterly = _agg_to_records(s_df, 'ORG_QRT')

            districts = {}
            for dist, d_df in s_df.groupby('DISTRICT'):
                if dist in ('Nan', 'None', '', 'Nat'): continue
                d_overall, d_quarterly = _agg_to_records(d_df, 'ORG_QRT')
                districts[dist] = {"overall": d_overall, "quarterly": d_quarterly}

            bureau_data[state] = {"overall": s_overall, "quarterly": s_quarterly, "districts": districts}

        # ── Phase 4: slices for JS multi-select filtering ──────────────────
        # State slices: per (member_group, quarter, trade_size) for live recompute
        # District slices: per member_group (no quarter dim to keep JSON manageable)
        ts_label_map = {
            'E': '₹50K-75K', 'F': '₹75K-1L', 'G': '₹1L-1.5L', 'H': '₹1.5L-2L',
            'I': '₹2L-2.5L', 'J': '₹2.5L-3L', 'K': '₹3L-3.5L', 'L': '₹3.5L-5L'
        }
        def _ts_label(raw):
            prefix = str(raw).split('.')[0] if '.' in str(raw) else str(raw)
            return ts_label_map.get(prefix, str(raw))

        has_mg = 'MEMBER_GROUP' in df.columns or 'member_group' in df.columns
        mg_col_name = 'MEMBER_GROUP' if 'MEMBER_GROUP' in df.columns else 'member_group'
        has_ts = 'TRADE_SIZE' in df.columns or 'trade_size' in df.columns
        ts_col_name = 'TRADE_SIZE' if 'TRADE_SIZE' in df.columns else 'trade_size'

        # Exclude the literal string 'nan' (from astype(str) on a NaN cell) — without this, a
        # spurious 'nan' filter option shows up in the mg/loan-range panel.
        all_mgs  = sorted(v for v in df[mg_col_name].astype(str).unique().tolist() if v != 'nan') if has_mg else []
        all_ts   = sorted(v for v in df[ts_col_name].astype(str).unique().tolist() if v != 'nan') if has_ts else []

        for state, s_df in df.groupby('STATE'):
            if state not in bureau_data: continue
            slices = []
            grp_cols = (['STATE'] +
                       ([mg_col_name] if has_mg else []) +
                       ['ORG_QRT'] +
                       ([ts_col_name] if has_ts else []))
            for keys, g in s_df.groupby([c for c in grp_cols if c != 'STATE']):
                if not isinstance(keys, tuple): keys = (keys,)
                k = list(keys); idx = 0
                mg_val = str(k[idx]) if has_mg else ''; idx += has_mg
                q_raw  = str(k[idx]);                  idx += 1
                ts_val = str(k[idx]) if has_ts else ''; idx += has_ts
                slice_rec = {
                    "mg": mg_val, "q_raw": q_raw,
                    "q":  str(g['ORG_QRT'].iloc[0]),   # already label from cal_to_fy
                    "ts": ts_val, "tsl": _ts_label(ts_val),
                    "l":  int(g['NUMBER_OF_LOANS'].sum()),
                    "d":  int(g['DELINQUENT_30P6M_TRADES'].sum())
                }
                if has_90p12m:
                    slice_rec["d90"] = int(g['DELINQUENT_90P12M_TRADES'].sum())
                    slice_rec["l90"] = int(g[loans90_col].sum())
                if has_amt:
                    slice_rec["amt"] = int(g['TOTAL_SANCTIONED_AMOUNT'].sum())
                slices.append(slice_rec)
            bureau_data[state]["slices"] = slices

            for dist, d_entry in bureau_data[state].get("districts", {}).items():
                d_df = s_df[s_df['DISTRICT'] == dist]
                d_slices = []
                if has_mg:
                    # Include trade_size AND quarter in the grouping too (mirrors the state-level
                    # slices exactly, incl. "q_raw"/"q" field names) — without "ts" the JS-side
                    # trade-size filter silently matched nothing (verified: 0/15 districts responded
                    # before that fix). Without "q_raw", the JS-side quarter filter has the same
                    # failure mode PLUS a worse one: aggregateSlices() checks qtr.has(s.q_raw) on
                    # every slice, so with no q_raw field at all, selecting ANY quarter zeroes the
                    # entire filtered set (not just the quarter dimension) — silently discarding a
                    # working member-group filter too when combined with a quarter filter (verified:
                    # mg-only filtering narrowed a sample district correctly, but adding a quarter
                    # filter on top silently fell back to the fully unfiltered rate).
                    d_grp_cols = [mg_col_name, 'ORG_QRT'] + ([ts_col_name] if has_ts else [])
                    for keys, g in d_df.groupby(d_grp_cols):
                        if not isinstance(keys, tuple): keys = (keys,)
                        d_slice_rec = {"mg": str(keys[0]), "q_raw": str(keys[1]), "q": str(g['ORG_QRT'].iloc[0]),
                            "l": int(g['NUMBER_OF_LOANS'].sum()),
                            "d": int(g['DELINQUENT_30P6M_TRADES'].sum())}
                        if has_ts:
                            d_slice_rec["ts"] = str(keys[2])
                        if has_90p12m:
                            d_slice_rec["d90"] = int(g['DELINQUENT_90P12M_TRADES'].sum())
                            d_slice_rec["l90"] = int(g[loans90_col].sum())
                        if has_amt:
                            d_slice_rec["amt"] = int(g['TOTAL_SANCTIONED_AMOUNT'].sum())
                        d_slices.append(d_slice_rec)
                d_entry["slices"] = d_slices

        # Available filter options for the global filter panel
        def _qrl(raw):
            # ORG_QRT is already converted to FY label (e.g. "Jan - Mar '25") by cal_to_fy_quarter.
            # Return it as-is for the display label.
            return str(raw)

        all_qtrs_raw = sorted(df['ORG_QRT'].unique().tolist())
        bureau_data['_meta'] = {
            "member_groups": all_mgs,
            "quarters":   [{"q_raw": q, "q_label": _qrl(q)} for q in all_qtrs_raw],
            "trade_sizes": [{"ts": t, "label": _ts_label(t)} for t in all_ts],
            # Same cohort-maturity cutoffs as the "overall" gate above, converted to the display-label
            # format the JS-side quarter filter (slices' q_raw) actually uses — so a user manually
            # selecting quarters via the global filter panel gets the same maturity-aware 90P12M/30P6M
            # behavior as the default (no-filter) view, instead of being able to dilute it themselves.
            "mature_through": {
                "30p6m":  cal_to_fy_quarter(self.cfg.bureau_30p6m_mature_through),
                "90p12m": cal_to_fy_quarter(self.cfg.bureau_90p12m_mature_through),
            } if has_90p12m else {"30p6m": cal_to_fy_quarter(self.cfg.bureau_30p6m_mature_through)}
        }

        self.bureau_data = bureau_data

        # Also aggregate at PINCODE level for Phase 3 & Growth calculation
        if 'PINCODE' in df.columns:
            # Vectorized pin normalisation — avoids 2.4M Python calls via .apply()
            pin_num = pd.to_numeric(df['PINCODE'], errors='coerce')
            df['_pin'] = pin_num.dropna().astype(int).astype(str).str.zfill(6)
            df['_pin'] = df['_pin'].where(pin_num.notna(), '')
            valid_pins = df[df['_pin'].str.match(r'^\d{6}$')].copy()

            # Aggregate totals once via groupby (single pass, no nested loops)
            pin_agg_kwargs = {
                'loans':    ('NUMBER_OF_LOANS',          'sum'),
                'delinquent': ('DELINQUENT_30P6M_TRADES', 'sum'),
                'state':    ('STATE',    lambda x: x.mode().iloc[0] if len(x) else ''),
                'district': ('DISTRICT', lambda x: x.mode().iloc[0] if len(x) else ''),
            }
            if has_90p12m:
                pin_agg_kwargs['delinquent_90p12m'] = ('DELINQUENT_90P12M_TRADES', 'sum')
                pin_agg_kwargs['loans_90p12m'] = (loans90_col, 'sum')
            if has_amt:
                pin_agg_kwargs['amt'] = ('TOTAL_SANCTIONED_AMOUNT', 'sum')
            grp = valid_pins.groupby('_pin').agg(**pin_agg_kwargs).reset_index().rename(columns={'_pin': 'pincode'})

            # Pre-build slices in one pass if member-group column exists — includes trade_size
            # too (same as the district slices above), so loan-range filtering works at pincode
            # grain too. Cost: pincode x member_group x trade_size cardinality (6,500+ pincodes)
            # takes pipeline_output.json from ~37MB to ~128MB. Measured load-time impact (headless
            # Edge, local file://): browser 'load' event ~3.7s -> ~9.5s, JS heap ~100MB -> ~310MB,
            # fully interactive well under 10s either way — accepted deliberately for full filter
            # fidelity at every level.
            slices_map = {}  # always initialised before conditional
            if has_mg:
                pin_grp_cols = ['_pin', mg_col_name] + ([ts_col_name] if has_ts else [])
                mg_agg_kwargs = {'l': ('NUMBER_OF_LOANS', 'sum'), 'd': ('DELINQUENT_30P6M_TRADES', 'sum')}
                if has_90p12m:
                    mg_agg_kwargs['d90'] = ('DELINQUENT_90P12M_TRADES', 'sum')
                    mg_agg_kwargs['l90'] = (loans90_col, 'sum')
                if has_amt:
                    mg_agg_kwargs['amt'] = ('TOTAL_SANCTIONED_AMOUNT', 'sum')
                mg_grp = valid_pins.groupby(pin_grp_cols).agg(**mg_agg_kwargs).reset_index().rename(columns={'_pin': 'pincode'})
                for row in mg_grp.itertuples(index=False):
                    slice_rec = {"mg": str(getattr(row, mg_col_name)), "l": int(row.l), "d": int(row.d)}
                    if has_ts:
                        slice_rec["ts"] = str(getattr(row, ts_col_name))
                    if has_90p12m:
                        slice_rec["d90"] = int(row.d90)
                        slice_rec["l90"] = int(row.l90)
                    if has_amt:
                        slice_rec["amt"] = int(row.amt)
                    slices_map.setdefault(row.pincode, []).append(slice_rec)

            # Per-quarter breakdown (no mg/ts cross — pincode x quarter is only ~6,500 x 7 rows,
            # unlike the mg/ts slices above whose cost comes from the mg x ts cross-product) so
            # exports can show a QoQ trend per pincode without repeating the earlier size blowup.
            pin_qtr_agg_kwargs = {'loans': ('NUMBER_OF_LOANS', 'sum'), 'delinquent': ('DELINQUENT_30P6M_TRADES', 'sum')}
            if has_90p12m:
                pin_qtr_agg_kwargs['delinquent_90p12m'] = ('DELINQUENT_90P12M_TRADES', 'sum')
                pin_qtr_agg_kwargs['loans_90p12m'] = (loans90_col, 'sum')
            if has_amt:
                pin_qtr_agg_kwargs['amt'] = ('TOTAL_SANCTIONED_AMOUNT', 'sum')
            pin_qtr_grp = valid_pins.groupby(['_pin', 'ORG_QRT']).agg(**pin_qtr_agg_kwargs).reset_index().rename(columns={'_pin': 'pincode'})
            pin_quarterly_map = {}
            for row in pin_qtr_grp.itertuples(index=False):
                ql, dl = int(row.loans), int(row.delinquent)
                qrec = {"q": row.ORG_QRT, "loans": ql, "delinquent": dl, "rate": round(dl / ql * 100, 2) if ql else 0.0}
                if has_90p12m:
                    ql90 = int(row.loans_90p12m)
                    qrec["loans_90p12m"] = ql90
                    qrec["delinquent_90p12m"] = int(row.delinquent_90p12m)
                    qrec["rate_90p12m"] = round(qrec["delinquent_90p12m"] / ql90 * 100, 2) if ql90 else 0.0
                if has_amt:
                    qrec["amt"] = int(row.amt)
                pin_quarterly_map.setdefault(row.pincode, []).append(qrec)

            bureau_pincode_data = {}
            for row in grp.itertuples(index=False):
                l, d = int(row.loans), int(row.delinquent)
                entry = {
                    "state": row.state, "district": row.district,
                    "loans": l, "delinquent": d,
                    "rate": round(d / l * 100, 2) if l else 0.0,
                    "slices": slices_map.get(row.pincode, []),
                    "quarterly": pin_quarterly_map.get(row.pincode, [])
                }
                if has_90p12m:
                    d90 = int(row.delinquent_90p12m)
                    l90 = int(row.loans_90p12m)
                    entry["loans_90p12m"] = l90
                    entry["delinquent_90p12m"] = d90
                    entry["rate_90p12m"] = round(d90 / l90 * 100, 2) if l90 else 0.0
                if has_amt:
                    entry["amt"] = int(row.amt)
                bureau_pincode_data[row.pincode] = entry
            self.bureau_pincode_data = bureau_pincode_data
            logger.info(f"Bureau pincode data: {len(self.bureau_pincode_data):,} unique pincodes.")

    def _load_ats_data(self):
        """Loads ATS file and dynamically establishes date limits without hardcoding."""
        cfg = self.cfg
        p = Path(cfg.ats_file)
        if not p.exists():
            logger.warning(f"ATS file not found: {p}. Falling back to default baseline.")
            return

        try:
            df = pd.read_excel(p) if p.suffix.lower() in ('.xlsx', '.xls') else pd.read_csv(p, low_memory=False)
        except Exception as e:
            logger.warning(f"Could not load ATS file {p}: {e}.")
            return

        df.rename(columns=lambda x: x.strip() if isinstance(x, str) else x, inplace=True)
        needed = [cfg.ats_substage_col, cfg.ats_disbursal_date_col, cfg.ats_risk_cat_col, cfg.ats_amount_col]
        missing = [c for c in needed if c not in df.columns]
        if missing:
            logger.error(f"ATS file missing required columns: {missing}.")
            return

        # Core filtering matching dynamic pipeline guidelines
        df = df[df[cfg.ats_substage_col].astype(str).str.strip() == cfg.ats_substage_value]

        if cfg.ats_prospect_stage_col in df.columns:
            before = len(df)
            df = df[df[cfg.ats_prospect_stage_col].astype(str).str.strip().str.lower() == cfg.ats_prospect_stage_value.lower()]
            logger.info(f"ATS: prospect_stage='{cfg.ats_prospect_stage_value}' filter: {len(df):,} of {before:,} rows kept")
        else:
            logger.warning(f"ATS: column '{cfg.ats_prospect_stage_col}' not found — skipping prospect_stage filter")

        if cfg.ats_source_col in df.columns:
            before = len(df)
            df = df[df[cfg.ats_source_col].astype(str).str.strip() != cfg.ats_source_exclude]
            logger.info(f"ATS: excluded source='{cfg.ats_source_exclude}': {len(df):,} of {before:,} rows kept")
        else:
            logger.warning(f"ATS: column '{cfg.ats_source_col}' not found — skipping source exclusion filter")

        df[cfg.ats_disbursal_date_col] = pd.to_datetime(df[cfg.ats_disbursal_date_col], dayfirst=True, errors='coerce')
        df = df[df[cfg.ats_disbursal_date_col].notna()]

        # Apply configured date window FIRST, then record bounds from the filtered data
        start = pd.Timestamp(cfg.ats_start_date)
        end   = pd.Timestamp(cfg.ats_end_date) + pd.Timedelta(days=1) - pd.Timedelta(seconds=1)
        before_dt = len(df)
        df = df[(df[cfg.ats_disbursal_date_col] >= start) & (df[cfg.ats_disbursal_date_col] <= end)]
        logger.info(f"ATS: date filter {cfg.ats_start_date} → {cfg.ats_end_date}: {len(df):,} of {before_dt:,} rows kept")

        if df.empty:
            logger.warning("ATS file has no disbursals in the configured window.")
            return

        # Min/max now reflect the filtered window — used for the header strip label
        self.ats_min_date = df[cfg.ats_disbursal_date_col].min()
        self.ats_max_date = df[cfg.ats_disbursal_date_col].max()

        df[cfg.ats_risk_cat_col] = df[cfg.ats_risk_cat_col].astype(str).str.title().str.strip()
        df[cfg.ats_amount_col]   = pd.to_numeric(df[cfg.ats_amount_col], errors='coerce')

        canonical = ['Low', 'Medium', 'High', 'Very High']
        df = df[df[cfg.ats_risk_cat_col].isin(canonical) & df[cfg.ats_amount_col].notna()]

        ats_data = {}
        for cat in canonical:
            c_df = df[df[cfg.ats_risk_cat_col] == cat]
            if len(c_df) > 0:
                ats_data[cat] = {"ats": float(c_df[cfg.ats_amount_col].mean()), "count": int(len(c_df))}
            else:
                ats_data[cat] = {"ats": 0.0, "count": 0}

        if len(df) > 0:
            ats_data["Total"] = {"ats": float(df[cfg.ats_amount_col].mean()), "count": int(len(df))}
        else:
            ats_data["Total"] = {"ats": 0.0, "count": 0}

        self.ats_data = ats_data
        logger.info(f"ATS computed — Dynamic Bounds: {self.ats_min_date.strftime('%Y-%m-%d')} to {self.ats_max_date.strftime('%Y-%m-%d')}")

    def _build_monthly_disbursal_base(self):
        """Compute monthly disbursal run-rate from D1 tracker for dashboard % normalisation.

        Filters: prospect_stage == ats_prospect_stage_value, source != ats_source_exclude.
        Date window is controlled by disbursal_base_window ("ats" or "d1") in Phase2Config.
        Result stored as self.monthly_disbursal_base (rupees per month).
        """
        cfg = self.cfg
        p = Path(cfg.ats_file)
        if not p.exists():
            logger.warning(f"Disbursal base: D1 file not found at {p}.")
            return

        try:
            df = pd.read_csv(p, low_memory=False) if p.suffix.lower() == '.csv' else pd.read_excel(p)
        except Exception as e:
            logger.warning(f"Disbursal base: could not load D1 file: {e}.")
            return

        df.rename(columns=lambda x: x.strip() if isinstance(x, str) else x, inplace=True)

        needed = [cfg.ats_disbursal_date_col, cfg.ats_amount_col]
        missing = [c for c in needed if c not in df.columns]
        if missing:
            logger.error(f"Disbursal base: D1 file missing required columns: {missing}.")
            return

        if cfg.ats_prospect_stage_col in df.columns:
            before = len(df)
            df = df[df[cfg.ats_prospect_stage_col].astype(str).str.strip().str.lower() == cfg.ats_prospect_stage_value.lower()]
            logger.info(f"Disbursal base: prospect_stage='{cfg.ats_prospect_stage_value}': {len(df):,} of {before:,} rows kept")
        else:
            logger.warning(f"Disbursal base: column '{cfg.ats_prospect_stage_col}' not found — skipping filter")

        if cfg.ats_source_col in df.columns:
            before = len(df)
            df = df[df[cfg.ats_source_col].astype(str).str.strip() != cfg.ats_source_exclude]
            logger.info(f"Disbursal base: excluded source='{cfg.ats_source_exclude}': {len(df):,} of {before:,} rows kept")
        else:
            logger.warning(f"Disbursal base: column '{cfg.ats_source_col}' not found — skipping filter")

        df[cfg.ats_disbursal_date_col] = pd.to_datetime(df[cfg.ats_disbursal_date_col], dayfirst=True, errors='coerce')
        df = df[df[cfg.ats_disbursal_date_col].notna()]

        if cfg.disbursal_base_window == "d1":
            start = pd.Timestamp(cfg.d1_start_date)
            end   = pd.Timestamp(cfg.d1_end_date) + pd.Timedelta(days=1) - pd.Timedelta(seconds=1)
            window_label = f"D1 ({cfg.d1_start_date} → {cfg.d1_end_date})"
        else:
            start = pd.Timestamp(cfg.ats_start_date)
            end   = pd.Timestamp(cfg.ats_end_date) + pd.Timedelta(days=1) - pd.Timedelta(seconds=1)
            window_label = f"ATS ({cfg.ats_start_date} → {cfg.ats_end_date})"

        before_dt = len(df)
        df = df[(df[cfg.ats_disbursal_date_col] >= start) & (df[cfg.ats_disbursal_date_col] <= end)]
        logger.info(f"Disbursal base: date window {window_label}: {len(df):,} of {before_dt:,} rows kept")

        if df.empty:
            logger.warning("Disbursal base: no disbursals found in the configured window.")
            return

        df[cfg.ats_amount_col] = pd.to_numeric(df[cfg.ats_amount_col], errors='coerce')
        df = df[df[cfg.ats_amount_col].notna()]

        if df.empty:
            logger.warning("Disbursal base: no rows with valid loan amounts.")
            return

        total_amt = float(df[cfg.ats_amount_col].sum())
        s = df[cfg.ats_disbursal_date_col].min()
        e = df[cfg.ats_disbursal_date_col].max()
        window_months = max(1, (e.year - s.year) * 12 + (e.month - s.month) + 1)

        self.monthly_disbursal_base = total_amt / window_months
        logger.info(
            f"Disbursal base: ₹{self.monthly_disbursal_base / 100_000:.2f} L/mo "
            f"(total ₹{total_amt / 100_000:.2f} L, {window_months} months, {len(df):,} disbursals, window={window_label})"
        )

    def _build_d1_pincode_volume(self):
        """Part 3 — monthly disbursed volume by (pincode, risk_tier) from D1_Tracker."""
        cfg = self.cfg
        self.d1_pincode_volume = {}
        p = Path(cfg.ats_file)
        if not p.exists():
            logger.warning(f"D1_Tracker not found: {p} — skipping pincode volume build")
            return

        try:
            df = pd.read_excel(p) if p.suffix.lower() in ('.xlsx', '.xls') else pd.read_csv(p, low_memory=False)
        except Exception as e:
            logger.warning(f"D1_Tracker load failed: {e}")
            return

        df.rename(columns=lambda x: x.strip() if isinstance(x, str) else x, inplace=True)
        logger.info(f"[D1] File loaded: {len(df):,} rows | columns: {list(df.columns[:10])}")

        # ── Verify required columns exist ─────────────────────────────────
        needed = [cfg.ats_substage_col, cfg.ats_disbursal_date_col,
                  cfg.ats_risk_cat_col, cfg.pincode_col]
        missing = [c for c in needed if c not in df.columns]
        if missing:
            logger.error(f"[D1] Missing columns: {missing}")
            logger.error(f"[D1] Available columns: {list(df.columns)}")
            return

        # ── Step 1: substage filter ────────────────────────────────────────
        before = len(df)
        unique_substages = df[cfg.ats_substage_col].astype(str).str.strip().unique()
        logger.info(f"[D1] Unique substage values: {list(unique_substages[:10])}")
        df = df[df[cfg.ats_substage_col].astype(str).str.strip() == cfg.ats_substage_value].copy()
        logger.info(f"[D1] After substage='{cfg.ats_substage_value}': {len(df):,} of {before:,} rows")

        # ── Step 2: date parse and window filter ───────────────────────────
        df[cfg.ats_disbursal_date_col] = pd.to_datetime(df[cfg.ats_disbursal_date_col], dayfirst=True, errors='coerce')
        df = df[df[cfg.ats_disbursal_date_col].notna()]
        start = pd.Timestamp(cfg.d1_start_date)
        end   = pd.Timestamp(cfg.d1_end_date) + pd.Timedelta(days=1) - pd.Timedelta(seconds=1)
        before2 = len(df)
        df = df[(df[cfg.ats_disbursal_date_col] >= start) & (df[cfg.ats_disbursal_date_col] <= end)]
        logger.info(f"[D1] After date window {cfg.d1_start_date}→{cfg.d1_end_date}: {len(df):,} of {before2:,} rows")
        if df.empty:
            logger.warning("[D1] No disbursals in D1 date window — check d1_start_date / d1_end_date config")
            return

        # ── Step 3: risk category normalisation ───────────────────────────
        df[cfg.ats_risk_cat_col] = df[cfg.ats_risk_cat_col].astype(str).str.title().str.strip()
        unique_tiers = df[cfg.ats_risk_cat_col].unique()
        logger.info(f"[D1] Unique risk categories (after title): {list(unique_tiers)}")
        canonical = {'Low', 'Medium', 'High', 'Very High'}
        before3 = len(df)
        df['_pin'] = df[cfg.pincode_col].apply(type(self)._fmt_pin)
        # _fmt_pin returns the literal string "nan" for unparseable input (not real NaN, and not
        # ''), so notna()/!='' alone don't catch it — it was silently becoming a phantom
        # D1_PINCODE_VOLUME["nan"] key: counted in totals but unreachable by any real pincode.
        df = df[df[cfg.ats_risk_cat_col].isin(canonical) & df['_pin'].str.match(r'^\d{4,6}$')]
        logger.info(f"[D1] After canonical tier filter: {len(df):,} of {before3:,} rows")
        logger.info(f"[D1] Unique pincodes after all filters: {df['_pin'].nunique():,}")

        if df.empty:
            logger.warning("[D1] Zero rows after tier filter — check risk category column values above")
            return

        num_months = max(1, (end.year - start.year) * 12 + (end.month - start.month) + 1)
        self.d1_num_months = num_months
        self.d1_min_date = df[cfg.ats_disbursal_date_col].min()
        self.d1_max_date = df[cfg.ats_disbursal_date_col].max()

        grp = df.groupby(['_pin', cfg.ats_risk_cat_col]).size().reset_index(name='cnt')
        result: dict = {}
        for _, row in grp.iterrows():
            pin  = row['_pin']
            tier = row[cfg.ats_risk_cat_col]
            if pin not in result:
                result[pin] = {}
            result[pin][tier] = round(row['cnt'] / num_months, 4)

        self.d1_pincode_volume = result
        logger.info(
            f"[D1] DONE: {len(result):,} pincodes | "
            f"{num_months} months | {len(df):,} disbursals used"
        )

        # ── Part 3b: Corridor D1 volume ───────────────────────────────────
        self._build_corridor_d1_volume(df, num_months)

    def _build_corridor_d1_volume(self, df: pd.DataFrame, num_months: int):
        """Part 3b — monthly disbursed volume by (perm_state → curr_state, tier).
        df is already filtered to disbursed + date window + canonical tiers.
        """
        cfg = self.cfg
        self.corridor_d1_volume = {}

        needed = [cfg.d1_perm_state_col, cfg.d1_curr_state_col]
        missing = [c for c in needed if c not in df.columns]
        if missing:
            logger.warning(f"[D1 Corridor] Missing columns {missing} — skipping. "
                           f"Available: {list(df.columns)}")
            return

        # Same title-case + alias normalization as the risk file's perm/curr state columns
        # (_load_and_clean) — without it, a raw D1-tracker spelling like "orissa" or
        # "NCT OF DELHI" produces a corridor key that never matches CORRIDOR_DATA (built from
        # the normalized risk file), silently zeroing that corridor's D1 volume.
        state_mapping = {
            "Nct Of Delhi": "Delhi", "Orissa": "Odisha", "Chattisgarh": "Chhattisgarh",
            "Tamilnadu": "Tamil Nadu", "Jammu & Kashmir": "Jammu and Kashmir",
            "Pondicherry": "Puducherry",
            "Dadra & Nagar Haveli And Daman & Diu": "Dadra and Nagar Haveli and Daman and Diu",
            "Andaman & Nicobar Islands": "Andaman and Nicobar Islands",
        }
        d = df.copy()
        d['_ps'] = d[cfg.d1_perm_state_col].astype(str).str.title().str.strip().replace(state_mapping)
        d['_cs'] = d[cfg.d1_curr_state_col].astype(str).str.title().str.strip().replace(state_mapping)

        # Only inter-state migrants with valid state values
        bad = {'', 'nan', 'none', 'na'}
        d = d[
            d['_ps'].str.lower().map(lambda x: x not in bad) &
            d['_cs'].str.lower().map(lambda x: x not in bad) &
            (d['_ps'] != d['_cs'])
        ]

        if d.empty:
            logger.warning("[D1 Corridor] No inter-state records after filtering")
            return

        d['_ck'] = d['_ps'] + ' → ' + d['_cs']

        grp = d.groupby(['_ck', cfg.ats_risk_cat_col]).size().reset_index(name='cnt')
        result: dict = {}
        for _, row in grp.iterrows():
            ck   = row['_ck']
            tier = row[cfg.ats_risk_cat_col]
            if ck not in result:
                result[ck] = {}
            result[ck][tier] = round(row['cnt'] / num_months, 4)

        self.corridor_d1_volume = result
        logger.info(
            f"[D1 Corridor] DONE: {len(result):,} corridors | "
            f"{num_months} months | {len(d):,} inter-state disbursals used"
        )

    def _build_window_config(self) -> dict:
        """Derives time-window lengths and structural tracking text automatically from files."""
        cfg = self.cfg
        MONTH_ABBRS = {m[:3].lower(): i for i, m in enumerate(calendar.month_abbr) if m}

        def parse_quarter(q_str):
            try:
                parts = [p.strip() for p in q_str.split(' - ')]
                if len(parts) != 2: return None
                start_abbr     = parts[0][:3].lower()          
                end_with_year  = parts[1]                       
                end_abbr       = end_with_year.split("'")[0].strip()[:3].lower()  
                year_token     = end_with_year.replace('\u2019', "'").split("'")[-1].strip()  
                year           = 2000 + int(year_token) if len(year_token) <= 2 else int(year_token)
                start_m        = MONTH_ABBRS.get(start_abbr)
                end_m          = MONTH_ABBRS.get(end_abbr)
                if not start_m or not end_m: return None
                return start_m, end_m, year
            except Exception:
                return None

        def is_monthly(val):
            try: datetime.strptime(val, "%Y-%m"); return True
            except: return False

        def fmt_ym(ym):
            try: return datetime.strptime(ym, "%Y-%m").strftime("%b %Y")
            except: return ym

        def fmt_my(month_int, year_int):
            return datetime(year_int, month_int, 1).strftime("%b %Y")

        # --- Dynamic Risk Window Evaluation ---
        # Same chronological-sort requirement as the bureau window below: quarter labels must
        # not be sorted alphabetically — "Oct - Dec '25" sorts after "Jan - Mar '26" as a
        # string, which would freeze risk_months at the stale window length and silently
        # understate every simulated loss once a new-year quarter appears.
        femi_raw = [m for m in self.df[cfg.month_col].unique() if m != 'Unknown']
        def _femi_qtr_sort_key(v):
            parsed = parse_quarter(v)
            return (parsed[2], parsed[0]) if parsed else (0, 0)  # (year, start_month)
        if femi_raw and not is_monthly(femi_raw[0]):
            femi_vals = sorted(femi_raw, key=_femi_qtr_sort_key)
        else:
            femi_vals = sorted(femi_raw)  # "YYYY-MM" monthly labels sort correctly as strings
        if femi_vals:
            if is_monthly(femi_vals[0]):
                risk_months = len(femi_vals)
                risk_label  = f"{fmt_ym(femi_vals[0])} – {fmt_ym(femi_vals[-1])}"
            else:
                first_q = parse_quarter(femi_vals[0])
                last_q  = parse_quarter(femi_vals[-1])
                if first_q and last_q:
                    f_start_m, _, f_year  = first_q
                    l_start_m, _, l_year  = last_q
                    risk_months = (l_year - f_year) * 12 + (l_start_m - f_start_m) + 1
                    risk_label  = f"{fmt_my(f_start_m, f_year)} – {fmt_my(l_start_m, l_year)}"
                else:
                    risk_months = len(femi_vals) * 3
                    risk_label  = f"{femi_vals[0]} – {femi_vals[-1]}"
        else:
            risk_months, risk_label = 0, "Unknown"

        # --- Dynamic ATS Window Evaluation ---
        if self.ats_min_date is not None and self.ats_max_date is not None:
            s = self.ats_min_date
            e = self.ats_max_date
            ats_months = (e.year - s.year) * 12 + (e.month - s.month) + 1
            ats_months = max(1, ats_months)
            ats_label  = f"{s.strftime('%b %Y')} – {e.strftime('%b %Y')}"
        else:
            ats_months, ats_label = 0, "Unknown"

        # --- Dynamic Bureau Window Evaluation ---
        # NOTE: must sort chronologically via parse_quarter(), not alphabetically on the raw
        # "MMM - MMM 'YY" label — plain string sort puts "Apr" before "Jan" before "Jul" before
        # "Oct", silently picking the wrong start/end quarter whenever the window spans more
        # than a handful of same-year quarters (this was already subtly wrong for 3-quarter
        # single-year windows — it dropped Q1 — and becomes badly wrong across multiple years).
        bureau_quarters_raw = {
            q["q"] for state_data in self.bureau_data.values() for q in state_data.get("quarterly", [])
        }
        def _bureau_qtr_sort_key(q_str):
            parsed = parse_quarter(q_str)
            return (parsed[2], parsed[0]) if parsed else (0, 0)  # (year, start_month)
        bureau_quarters = sorted(bureau_quarters_raw, key=_bureau_qtr_sort_key)
        if not bureau_quarters:
            bureau_months, bureau_label = 0, "No data"
        else:
            first_q = parse_quarter(bureau_quarters[0])
            last_q  = parse_quarter(bureau_quarters[-1])
            if first_q and last_q:
                f_start_m, _, f_year = first_q
                _, l_end_m, l_year   = last_q
                bureau_months = (l_year - f_year) * 12 + (l_end_m - f_start_m) + 1
                bureau_label  = f"{fmt_my(f_start_m, f_year)} – {fmt_my(l_end_m, l_year)}"
            else:
                bureau_months = len(bureau_quarters) * 3
                q0, q1 = bureau_quarters[0], bureau_quarters[-1]
                bureau_label = f"{q0} – {q1}" if q0 != q1 else q0

        # --- Rejection window: from actual STC dates in the filtered data ---
        if self.rej_min_date is not None and self.rej_max_date is not None:
            s, e = self.rej_min_date, self.rej_max_date
            rej_months = (e.year - s.year) * 12 + (e.month - s.month) + 1
            rej_label  = f"{s.strftime('%b %Y')} – {e.strftime('%b %Y')}"
        else:
            rej_months, rej_label = 0, "No data"

        # --- D1 Volume window ---
        if self.d1_min_date is not None and self.d1_max_date is not None:
            s, e = self.d1_min_date, self.d1_max_date
            d1_months = max(1, (e.year - s.year) * 12 + (e.month - s.month) + 1)
            d1_label  = f"{s.strftime('%b %Y')} – {e.strftime('%b %Y')}"
        else:
            d1_months, d1_label = 0, "No data"

        return {
            "risk":       {"months": risk_months,   "label": risk_label},
            "ats":        {"months": ats_months,    "label": ats_label},
            "d1_volume":  {"months": d1_months,     "label": d1_label},
            "rejections": {"months": rej_months,    "label": rej_label},
            "bureau":     {"months": bureau_months, "label": bureau_label}
        }

    def _load_rejection_data(self):
        """Load and filter the rejection/STC tracker file.

        Applies 5 filter conditions, then splits results by rejection reason:
          - "Negative Location"  → neg_loc bucket (further filtered by pincode map)
          - "High Risk Pincode"  → high_risk bucket (Gujarat-specific)
          - "Pincode not present" → always excluded from gain calculation

        Produces self.rejection_data keyed by pincode:
          {
            "560001": {
              "neg_loc":   {"High": 3, "Very High": 1},
              "high_risk": {"High": 2, "Very High": 0}
            }, ...
          }
        """
        cfg = self.cfg
        p = Path(cfg.rejection_file)
        if not p.exists():
            logger.warning(f"Rejection file not found: {p}. Expansion analysis will be empty.")
            return

        try:
            df = pd.read_excel(p) if p.suffix.lower() in ('.xlsx', '.xls') else pd.read_csv(p, low_memory=False)
        except Exception as e:
            logger.warning(f"Could not load rejection file {p}: {e}.")
            return

        df.rename(columns=lambda x: x.strip() if isinstance(x, str) else x, inplace=True)
        logger.info(f"Loaded rejection file: {p} ({len(df):,} rows)")

        needed = [cfg.rej_stage_col, cfg.rej_substage_col, cfg.rej_reason_col,
                  cfg.rej_stc_date_col, cfg.rej_risk_col, cfg.rej_pincode_col]
        missing = [c for c in needed if c not in df.columns]
        if missing:
            logger.error(f"Rejection file missing required columns: {missing}. Skipping.")
            return

        before = len(df)
        df = df[df[cfg.rej_stage_col].astype(str).str.strip() == cfg.rej_stage_val]
        logger.info(f"Rejection step 1 ({cfg.rej_stage_col}=='{cfg.rej_stage_val}'): {len(df):,} rows")

        df = df[df[cfg.rej_substage_col].astype(str).str.strip() == cfg.rej_substage_val]
        logger.info(f"Rejection step 2 ({cfg.rej_substage_col}=='{cfg.rej_substage_val}'): {len(df):,} rows")

        df = df[df[cfg.rej_reason_col].astype(str).str.strip().isin(cfg.rej_reason_vals)]
        logger.info(f"Rejection step 3 (reason filter): {len(df):,} rows")

        df[cfg.rej_stc_date_col] = pd.to_datetime(df[cfg.rej_stc_date_col], errors='coerce', dayfirst=True)
        df = df[df[cfg.rej_stc_date_col].notna()]
        logger.info(f"Rejection step 4 (stc_timestamp not null): {len(df):,} rows")

        # Step 4b: optional date window (rej_start_date / rej_end_date)
        if cfg.rej_start_date:
            rej_start_ts = pd.Timestamp(cfg.rej_start_date)
            before_dt = len(df)
            df = df[df[cfg.rej_stc_date_col] >= rej_start_ts]
            logger.info(f"Rejection step 4b (start date {cfg.rej_start_date}): {len(df):,} of {before_dt:,} rows")
        if cfg.rej_end_date:
            rej_end_ts = pd.Timestamp(cfg.rej_end_date) + pd.Timedelta(days=1) - pd.Timedelta(seconds=1)
            before_dt = len(df)
            df = df[df[cfg.rej_stc_date_col] <= rej_end_ts]
            logger.info(f"Rejection step 4b (end date {cfg.rej_end_date}): {len(df):,} of {before_dt:,} rows")

        if cfg.rej_consider_col in df.columns:
            def _truthy(v):
                if pd.isna(v): return False
                return str(v).strip().lower() not in ('', '0', 'no', 'n', 'false', 'nan', 'none')
            df = df[df[cfg.rej_consider_col].apply(_truthy)]
            logger.info(f"Rejection step 5 ({cfg.rej_consider_col} truthy): {len(df):,} rows")
        else:
            logger.warning(f"Column '{cfg.rej_consider_col}' not found — skipping step 5.")

        logger.info(f"Rejection filter: {len(df):,} of {before:,} rows passed all steps")
        if df.empty:
            logger.warning("No rejections matched filter criteria.")
            return

        # STC date range for per-month normalization — use configured bounds if set, else auto-derive
        if cfg.rej_start_date:
            self.rej_min_date = pd.Timestamp(cfg.rej_start_date)
        else:
            self.rej_min_date = df[cfg.rej_stc_date_col].min()
        if cfg.rej_end_date:
            self.rej_max_date = pd.Timestamp(cfg.rej_end_date)
        else:
            self.rej_max_date = df[cfg.rej_stc_date_col].max()
        rej_months = (self.rej_max_date.year - self.rej_min_date.year) * 12 + \
                     (self.rej_max_date.month - self.rej_min_date.month) + 1
        logger.info(f"Rejection window: {self.rej_min_date.strftime('%b %Y')} – "
                    f"{self.rej_max_date.strftime('%b %Y')} ({rej_months} mo)")

        df[cfg.rej_risk_col]    = df[cfg.rej_risk_col].astype(str).str.title().str.strip()
        # Normalise to clean integer string — handles Excel float format (380001.0 → "380001")
        df['_pin_norm'] = df[cfg.rej_pincode_col].apply(self._fmt_pin)
        df = df[df['_pin_norm'].str.match(r'^\d{4,6}$')]
        df['_reason']           = df[cfg.rej_reason_col].astype(str).str.strip()

        canonical_expand = ['High', 'Very High']
        df = df[df[cfg.rej_risk_col].isin(canonical_expand)]

        # Split into the two actionable buckets — "Pincode not present" is never aggregated
        df_neg  = df[df['_reason'] == "Negative Location"]
        df_high = df[df['_reason'] == "High Risk Pincode"]

        def _agg_bucket(bucket_df):
            result = {}
            for pin, g in bucket_df.groupby('_pin_norm'):
                if not pin or pin in ('nan', 'None', ''): continue
                counts = g[cfg.rej_risk_col].value_counts().to_dict()
                result[pin] = {
                    "High":      int(counts.get('High', 0)),
                    "Very High": int(counts.get('Very High', 0)),
                }
            return result

        neg_data  = _agg_bucket(df_neg)
        high_data = _agg_bucket(df_high)

        # Merge into single rejection_data dict keyed by pincode
        all_pins = set(neg_data) | set(high_data)
        rejection_data = {
            pin: {
                "neg_loc":   neg_data.get(pin,  {"High": 0, "Very High": 0}),
                "high_risk": high_data.get(pin, {"High": 0, "Very High": 0}),
            }
            for pin in all_pins
        }

        self.rejection_data = rejection_data
        logger.info(
            f"Rejection data: {len(rejection_data):,} unique pincodes "
            f"(Negative Location: {len(neg_data)}, High Risk: {len(high_data)}). "
            f"'Pincode not present' ({len(df[df['_reason']=='Pincode not present'])} cases) excluded."
        )

    def _load_pincode_mapping(self):
        """Load the Red Operational / Non-Operational pincode mapping file.

        Filters to rows where pin_map_type_col == pin_map_type_val ("Delinquency").
        Pincodes in this set are eligible for the Negative Location expansion analysis.

        Produces self.pincode_map_data: {"560001": 1, "380001": 1, ...}
        (truthy dict — JS checks PINCODE_MAP_DATA[pin] for membership)
        """
        cfg = self.cfg
        p = Path(cfg.pincode_map_file)
        if not p.exists():
            logger.warning(f"Pincode mapping file not found: {p}. Negative Location expansion will use all pincodes.")
            return

        try:
            df = pd.read_excel(p) if p.suffix.lower() in ('.xlsx', '.xls') else pd.read_csv(p, low_memory=False)
        except Exception as e:
            logger.warning(f"Could not load pincode mapping file {p}: {e}.")
            return

        df.rename(columns=lambda x: x.strip() if isinstance(x, str) else x, inplace=True)

        if cfg.pin_map_pincode_col not in df.columns or cfg.pin_map_type_col not in df.columns:
            logger.error(f"Pincode mapping file missing columns. Available: {list(df.columns)}")
            return

        df_del = df[df[cfg.pin_map_type_col].astype(str).str.strip() == cfg.pin_map_type_val]
        # Use _fmt_pin to handle Excel float pincodes (380001.0 → "380001")
        pins = df_del[cfg.pin_map_pincode_col].apply(self._fmt_pin)
        pins = pins[pins.str.match(r'^\d{4,6}$')]

        self.pincode_map_data = {pin: 1 for pin in pins.unique()}
        logger.info(f"Pincode mapping: {len(self.pincode_map_data):,} '{cfg.pin_map_type_val}' pincodes loaded.")

        # Also capture green / operational pincodes from the SEPARATE green column
        if cfg.pin_map_green_col not in df.columns:
            logger.warning(f"Green pincode column '{cfg.pin_map_green_col}' not found in mapping file. Available: {list(df.columns)}")
        else:
            df_green = df[df[cfg.pin_map_green_col].astype(str).str.strip().str.lower() == cfg.pin_map_green_val.lower()]
            if not df_green.empty:
                green_pins = df_green[cfg.pin_map_pincode_col].apply(self._fmt_pin)
                green_pins = green_pins[green_pins.str.match(r'^\d{4,6}$')]
                self.green_pincode_set = set(green_pins.unique())
                logger.info(f"Green/operational pincodes: {len(self.green_pincode_set):,} (col='{cfg.pin_map_green_col}', val='{cfg.pin_map_green_val}').")
            else:
                vals = df[cfg.pin_map_green_col].unique().tolist()
                logger.warning(f"No rows matched '{cfg.pin_map_green_val}' in '{cfg.pin_map_green_col}'. Found values: {vals}")

    def _load_pincode_coords(self):
        """Loads Latitude/Longitude centroids for the UI map."""
        p = self.cfg.pincode_coord_file
        if not p.exists():
            logger.warning(f"Coordinate file not found: {p}. Pincode map dots will not render.")
            return

        try:
            df = pd.read_excel(p) if p.suffix.lower() in ('.xlsx', '.xls') else pd.read_csv(p, low_memory=False)
            df = df.dropna(subset=['Latitude', 'Longitude'])
            
            coords = {}
            for _, row in df.iterrows():
                pin = self._fmt_pin(row[self.cfg.coord_pin_col])
                if pd.notna(pin) and len(pin) >= 4:
                    # Round to 4 decimal places (gives ~11m accuracy, saves huge file space)
                    coords[pin] = [round(float(row['Latitude']), 4), round(float(row['Longitude']), 4)]
                    
            self.pincode_coords = coords
            logger.info(f"Loaded coordinates for {len(self.pincode_coords):,} pincodes.")
        except Exception as e:
            logger.warning(f"Could not load pincode coordinates: {e}")

    def _build_pincode_risk_data(self):
        """Aggregate FINANCEORG portfolio default rate per pincode from the risk file.

        Produces self.pincode_risk_data:
          {"560001": {"state": "Karnataka", "district": "Bengaluru Urban",
                      "total": 150, "bad": 10, "rate": 6.67}, ...}
        """
        cfg = self.cfg
        if cfg.pincode_col not in self.df.columns:
            logger.warning(f"Pincode column '{cfg.pincode_col}' not found in risk file.")
            return

        df = self.df.copy()
        # Use _fmt_pin to handle Excel float pincodes (e.g. 380001.0 → "380001")
        df['_pin'] = df[cfg.pincode_col].apply(self._fmt_pin)
        df = df[df['_pin'].str.match(r'^\d{4,6}$')]   # valid pincode format
        if df.empty:
            logger.warning("No valid pincodes in risk file.")
            return

        pincode_risk_data = {}
        for pin, p_df in df.groupby('_pin'):
            tot = len(p_df)
            bad = int(p_df[cfg.flag_col].sum())
            cats = {}
            for cat in ['Low', 'Medium', 'High', 'Very High']:
                c_df = p_df[p_df[cfg.risk_cat_col] == cat]
                cats[cat] = {
                    "total": len(c_df), "bad": int(c_df[cfg.flag_col].sum()),
                    "rate": float(round(c_df[cfg.flag_col].mean()*100,2)) if len(c_df)>0 else 0.0
                }
            pincode_risk_data[pin] = {
                "state": p_df[cfg.state_col].mode().iloc[0] if tot > 0 else '',
                "district": p_df[cfg.dist_col].mode().iloc[0] if tot > 0 else '',
                "total": tot, "bad": bad, "rate": float(round(bad/tot*100,2)) if tot > 0 else 0.0,
                "cats": cats
            }
            bad90, rate90_frac = self._rate_90p12m(p_df)
            if bad90 is not None:
                pincode_risk_data[pin]["bad_90p12m"]  = bad90
                pincode_risk_data[pin]["rate_90p12m"] = float(round(rate90_frac*100, 2))  # percent, matching this dict's convention
        self.pincode_risk_data = pincode_risk_data

        logger.info(f"Pincode risk data: {len(self.pincode_risk_data):,} unique pincodes from risk file.")

    def _build_green_pincode_stats(self):
        """Calculate Low / Medium / Combined default rates for green (operational) pincodes.

        These serve as a benchmark in the expansion tab: how does our current performance
        in approved pincodes compare to the delinquency-tagged pincodes we might open?
        """
        cfg = self.cfg
        if not self.green_pincode_set:
            logger.info("No green pincodes identified — skipping benchmark stats.")
            return
        if cfg.pincode_col not in self.df.columns:
            logger.warning("Pincode column not in main data — cannot build green stats.")
            return

        df = self.df.copy()
        df['_pin'] = df[cfg.pincode_col].apply(self._fmt_pin)
        df = df[df['_pin'].isin(self.green_pincode_set)]

        if df.empty:
            logger.warning("No portfolio leads found matching green pincode set — benchmark will be empty.")
            return

        result = {}
        for tier in ['Low', 'Medium']:
            df_t = df[df[cfg.risk_cat_col] == tier]
            total = len(df_t)
            bad   = int(df_t[cfg.flag_col].sum())
            result[tier] = {
                "total": total,
                "bad":   bad,
                "rate":  round(bad / total * 100, 2) if total > 0 else 0.0
            }

        df_comb = df[df[cfg.risk_cat_col].isin(['Low', 'Medium'])]
        c_total, c_bad = len(df_comb), int(df_comb[cfg.flag_col].sum())
        result['Combined'] = {
            "total": c_total,
            "bad":   c_bad,
            "rate":  round(c_bad / c_total * 100, 2) if c_total > 0 else 0.0
        }
        result['pincode_count'] = int(df['_pin'].nunique())

        self.green_pincode_stats = result
        logger.info(
            f"Green pincode benchmark: {result['pincode_count']} pincodes | "
            f"Low={result['Low']['rate']}%  Med={result['Medium']['rate']}%  "
            f"Combined={result['Combined']['rate']}%"
        )

if __name__ == "__main__":
    config = Phase2Config()
    engine = DataEngine(config)
    engine.run()