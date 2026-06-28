# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A 3-layer data pipeline that transforms raw lending portfolio data (Excel/CSV) into a 46.8 MB self-contained interactive geospatial risk dashboard (HTML). Built for FinanceOrg's financial risk analytics team.

## Pipeline Commands

Only `engine_inject.py` is needed for UI/template-only changes. Run both only when raw data or processing logic changes.

```powershell
# Step 1: Process raw data → JSON payload (~1–5 min, 23 MB output)
# Only needed when source files (portfolio risk Excel, D1 tracker, etc.) or engine_data.py logic change
python engine_data.py

# Step 2: Inject JSON into HTML template → final dashboard (~5 sec, 46.8 MB output)
# Needed for any template_Finalized.html change (JS, CSS, HTML)
python engine_inject.py

# Verification scripts
python Verify_Loss.py                                        # cross-check loss calculations
python Verify_Loss.py --sl 7.5 --sm 10.5 --sh 10.0 --svh 10.0
python Verify_D1.py                                          # verify D1_PINCODE_VOLUME and CORRIDOR_D1_VOLUME against raw CSV
python Verify_Dedup.py                                       # verify no D1 disbursal is double-counted across region/corridor

# Optional: Validate JavaScript syntax in the template
python _check_syntax.py
```

`engine_inject.py` accepts explicit overrides:
```powershell
python engine_inject.py --data dashboard_data.json --template template_Finalized.html --out Dashboard_Build/Phase3_Dashboard_Finalized.html
```

## Architecture

```
[Raw Data Files]
  portfolio_risk_data.xlsx               — primary risk portfolio
  D1_Tracker - raw_data.csv     (29 MB)  — disbursement tracking
  Market_Data_3.csv            (186 MB)  — bureau overlay
  Market_Data_4_PL.csv         (202 MB)  — P&L market data
  Credit Tracker CSV            (16 MB)  — rejection analysis
  Pincode Mapping CSV            (1.7 MB) — delinquency classification
  Full_India_pincodes...xlsx     (1.1 MB) — lat/long coordinates
  india_states.geojson           (3.8 MB) — state boundary polygons
         │
         ▼
  engine_data.py  → dashboard_data.json (23 MB)
         │
         ▼
  engine_inject.py + template_Finalized.html
         │
         ▼
  Dashboard_Build/Phase3_Dashboard_Finalized.html (46.8 MB)
```

### Layer 1 & 2 — `engine_data.py`

The `DataEngine` class orchestrates all processing via a `run()` method that calls these stages in order:

| Method | Output attribute | Purpose |
|--------|-----------------|---------|
| `_load_and_clean()` | `self.df` | Load Excel, rename columns, normalize state/district names |
| `_aggregate_data()` | `self.port_stats`, `self.region_data` | Roll up bad rates by risk tier → state → district → month |
| `_load_bureau_data()` | `self.bureau_data`, `self.bureau_pincode_data` | Market delinquency rates by state/district/quarter/trade-size slice |
| `_load_ats_data()` | `self.ats_data` | Mean loan amount per risk tier from D1 tracker (ATS date window). Filters: `prospect_stage == "Disbursed"` and `source != "tp_form"` applied before date window. |
| `_build_monthly_disbursal_base()` | `self.monthly_disbursal_base` | Computes monthly disbursal run-rate (₹/mo) from D1 tracker using same `prospect_stage`/`source` filters. Window controlled by `disbursal_base_window` config ("ats" or "d1"). Used as denominator for "% of monthly disbursals" in the dashboard HUD. |
| `_build_d1_pincode_volume()` | `self.d1_pincode_volume` | Monthly disbursed count per pincode × tier (D1 date window) |
| `_build_corridor_d1_volume()` | `self.corridor_d1_volume` | Monthly disbursed count per migration corridor × tier |
| `_process_corridors()` | `self.corridor_data` | Inter-state migration flows (permanent state → current state) |
| `_load_rejection_data()` | `self.rejection_data` | Rejected applications by pincode for expansion opportunity analysis |
| `_build_pincode_risk_data()` | `self.pincode_risk_data` | Default rates per pincode |
| `_build_green_pincode_stats()` | `self.green_pincode_stats` | Benchmark bad rates in operationally active pincodes |
| `_save()` | `dashboard_data.json` | Serialize all 17 structures to JSON |

Geographic normalization uses a 140+ entry alias dictionary in `_load_and_clean()`; unresolved districts fall through to `_auto_match_districts()` (fuzzy match: 82% auto-accept, 65% suggest).

### Layer 3 — `engine_inject.py`

Reads `dashboard_data.json` and replaces 17 placeholder tokens in `template_Finalized.html`:

`__PORTFOLIO_STATS__`, `__REGION_DATA__`, `__GEOJSON_DATA__`, `__BUREAU_DATA_JSON__`, `__ATS_DATA_JSON__`, `__CORRIDOR_DATA__`, `__CORRIDOR_D1_VOLUME__`, `__REJECTION_DATA__`, `__PINCODE_RISK_DATA__`, `__D1_PINCODE_VOLUME__`, `__MONTHLY_DISBURSAL_BASE__`, and 6 more.

### Frontend — `template_Finalized.html`

- Leaflet.js (v1.9.4) interactive map with state polygons + pincode scatter.
- Five navigation tabs: Regions, Corridors, Expansion, Growth, Results.
- Client-side Excel export via XLSX.js (v0.18.5).
- No server required — the output HTML is fully standalone.
- Theme: Fintech/Crypto dark gold — fonts: Space Grotesk (headings), DM Sans (body), JetBrains Mono (data).

#### Key JS globals in `template_Finalized.html`

| Variable | Purpose |
|----------|---------|
| `useD1Volume` | `true` = D1 Tracker volumes for loss; `false` = historical LEAD_ARRAY |
| `corrPriority` | `'region'` or `'corridor'` — which source claims a dual-eligible lead first |
| `showCartHighlights` | `true/false` — toggles polygon glow + corridor flow lines on map |
| `cart` | `Set` of selected keys: `STATE:X`, `DIST:X:Y`, `PIN:Z`, `CORR:A → B` |
| `hiddenTiers` | `Set` of tier keys hidden in the pincode scatter (`safe`, `approaching`, `high`, `nodata`) |
| `lastSimLeadResults` | Array of per-lead simulation results (1 entry per affected lead) |
| `_d1Processed` | `Set` of `pin|tier` dedup keys already counted in current sim run |
| `_d1ZeroPins` | `Set` of `pin|tier` combos with no D1 volume (zero-loss leads) |
| `MONTHLY_DISBURSAL_BASE` | Pre-computed ₹/month disbursal run-rate from D1 tracker (injected from JSON). Denominator for the "% of monthly disbursals" label in the net impact HUD (`_updateNetPct()`). |

#### Simulation flow (`runSimulation()`)

Each lead in `LEAD_ARRAY` is evaluated once:
1. **Priority-aware classification** — controlled by `corrPriority`:
   - `'region'` (default): region → pincode → corridor (corridor only if `maxSev === 0`)
   - `'corridor'`: corridor → region/pincode (region only if `maxSev === 0`)
2. **Loss calculation** — if `useD1Volume`:
   - Region/pincode leads: `D1_PINCODE_VOLUME[pin][tier] × ATS × risk_months`, deduped by `pin|tier`
   - Corridor leads: `CORRIDOR_D1_VOLUME[ck][tier] × ATS × risk_months`, deduped by `ck|tier`
3. Results written to `lastSimLeadResults` (one entry per lead, never duplicated).

#### Excel export (`exportExecExcel()`)

| Sheet | Rows | Key columns |
|-------|------|-------------|
| Tightening - Region | One per **pincode** | Classification, Trigger, Stop Mode, Wz Rate%, Low/Med/High/VH Leads, Total Leads, D1 Vol/mo, Loss/mo |
| Tightening - Corridor | One per **corridor × tier** | Corridor, Classification, D1 Vol/mo (corridor aggregate), Loss/mo |
| Expansion Pincodes | One per qualifying pincode | Wz Rate%, Mkt Rate%, Total Rej, Gain/mo |
| Existing Growth | One per district | Bureau Rate%, +Leads/mo, Upside/mo |
| Greenfield | One per district | Bureau Rate%, Target Share%, Upside/mo |

Wz Rate% in the Region sheet uses district-level rate when `stopMode='district'` or `'force'`, pincode-level rate when `stopMode='pincode'`.

**D1 Vol/mo (Region sheet):** summed only for tiers that contributed `loss > 0` in the simulation — not all four tiers unconditionally. This ensures the implied ATS (Loss / D1 Vol) is consistent with what the dashboard actually stopped.

#### Visual design — 3D effects & ambient lighting

Applied via CSS in `template_Finalized.html` (lines ~332–373). Do not remove these blocks when editing styles.

| Effect | Selector / Rule | Description |
|--------|----------------|-------------|
| Ambient page glow | `body::before` | Two fixed radial gradients (gold top-left, indigo bottom-right) sit behind all content via `z-index:0` |
| Exec summary illumination | `#exec-summary` background | Three layered radial gradients (gold, purple, green) give the Results panel a lit look |
| Bento KPI card tilt | `#es-kpi-strip>div:hover` | `perspective(800px) rotateX(-3deg)` lift on hover; `preserve-3d` on the container |
| Bento shine overlay | `#es-kpi-strip>div::after` | Mouse-tracked radial highlight (`--mx`/`--my` CSS vars set by JS) fades in on hover |
| Bento entrance animation | `@keyframes es-enter` | Cards slide in with a perspective tilt when exec summary opens |
| Exec summary column tilt | JS `mousemove` handler (~line 3415) | `perspective(900px) rotateY/rotateX` parallax on the three summary columns |
| Nav rail depth | `.rail-btn:hover / .active` | `translateZ` push-forward on hover; active state has gold inner glow |
| Run button pulse | `@keyframes goldPulse` + `.btn-run` | Continuous gold box-shadow pulse; stops on hover and switches to static glow |
| Glass panel edge | `.glass` override | Adds `inset 0 1px 0 rgba(255,255,255,0.04)` top-edge highlight to all glass panels |
| Cart glow (map) | `@keyframes cart-glow` / `.dist-cart-glow` | Pulsing blue drop-shadow on district polygons added to the cart |
| Data card lift | `.data-card:hover` | Subtle `translateY(-2px) scale(1.004)` hover lift on region/corridor list cards |

#### Tier legend / pincode scatter

The Risk Tier legend (`#tier-legend`) only appears when a district is drilled into — `drawPincodesForDistrict()` shows it, `clearPincodeView()` and the back-to-country reset hide it. Chips filter pincode dot visibility via `toggleTierFilter()`.

## Verification Scripts

### `Verify_D1.py`
Recomputes `D1_PINCODE_VOLUME` and `CORRIDOR_D1_VOLUME` from raw D1 tracker CSV and compares against `dashboard_data.json`. Reports mismatches and key stats.
```powershell
python Verify_D1.py --top 20       # show top N mismatches
python Verify_D1.py --pin 380001   # drill into a specific pincode
python Verify_D1.py --corridor "Maharashtra -> Gujarat"
```

### `Verify_Dedup.py`
Two-layer check:
1. **Lead-level**: Checks LEAD_ARRAY for duplicate IDs.
2. **Volume-level**: Identifies D1 disbursals that appear in both `D1_PINCODE_VOLUME` and `CORRIDOR_D1_VOLUME` (inter-state disbursals). These only cause financial double-counting if both a region AND overlapping corridor are selected simultaneously — the JS `maxSev===0` guard prevents lead-level double-counting.
```powershell
python Verify_Dedup.py
python Verify_Dedup.py --corridor "Uttar Pradesh -> Haryana"
```

### `Verify_Loss.py`
Classifies districts as red_nonop/red_op/green and cross-checks D1-based monthly loss. Can spot-check against exported Excel rows.

## Configuration (`Phase2Config` in `engine_data.py`)

All tunable parameters live in this single dataclass. Update here — not in processing logic — when input files change.

| Parameter | Purpose |
|-----------|---------|
| `ats_start_date` / `ats_end_date` | Date window for ATS (average loan size) calculation, e.g. `"2026-01-01"` to `"2026-04-30"` |
| `ats_window_months` | Number of months in the ATS window |
| `d1_start_date` / `d1_end_date` | Separate (usually narrower) date window for D1 volume counts |
| `ats_prospect_stage_col` / `ats_prospect_stage_value` | Column + value to filter D1 tracker for ATS and disbursal base (default: `prospect_stage == "Disbursed"`) |
| `ats_source_col` / `ats_source_exclude` | Column + value to exclude from ATS and disbursal base (default: `source != "tp_form"`) |
| `disbursal_base_window` | Which date window to use for monthly disbursal base: `"ats"` (default) or `"d1"` |
| `auto_match_threshold` (0.82) | Fuzzy match threshold for automatic district name acceptance |
| `suggest_threshold` (0.65) | Lower threshold for generating manual-review suggestions |
| Column name mappings | 78+ field mappings from raw CSV/Excel headers to internal names |
| District aliases | Canonical name overrides (e.g., `"Bangalore"` → `"Bengaluru Urban"`) |
| `rejection_file` / filter fields | 5-step filter chain to isolate policy-rejected applications |
| `pin_map_type_val` / `pin_map_green_val` | Selectors for red vs. green pincodes from the mapping CSV |

## Diagnostics / Outputs

| File | Description |
|------|-------------|
| `Dashboard_Build/auto_matched_districts.csv` | Districts resolved by fuzzy matching |
| `Dashboard_Build/district_suggestions.csv` | Districts requiring manual review |
| `Dashboard_Build/unmapped_districts_*.csv` | Completely unmatched districts |

Review these CSVs after running `engine_data.py` whenever input data changes — unmatched districts silently drop from the map.

## Dependencies

No `requirements.txt` present. Required packages:
```
pandas
openpyxl
```
Install via: `pip install pandas openpyxl`
