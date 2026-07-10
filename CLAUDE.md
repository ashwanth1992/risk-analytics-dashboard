# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A 3-layer data pipeline that transforms raw lending portfolio data (Excel/CSV) into a self-contained interactive geospatial risk dashboard (HTML). Built for FINANCEORG's financial risk analytics team.

Current build is ~450 MB (`pipeline_output.json` ~236 MB) — deliberately large because bureau data carries a full member-group × loan-range breakdown at every grain (state/district/pincode), and district- and pincode-level slices/quarterly-arrays additionally carry the quarter dimension, so Market View's peer-group/loan-range/quarter filters and QoQ trend export work correctly at every granularity, not just state-level. Measured local load time (headless Edge, `file://`): ~29s to the browser `load` event, ~31s to fully interactive, ~610MB JS heap (up from ~390MB heap at the ~257MB pincode-mg×ts-only predecessor build — load/interactive time barely moved, but heap grew substantially). Dropping the pincode-level loan-range breakdown (member-group-only at that grain) cuts the smaller predecessor build to ~74 MB / ~37 MB and ~6-7s load — see the comments in `_load_bureau_data()`'s district- and pincode-level slice-building loops in `process_data.py` if this trade-off needs revisiting.

## Pipeline Commands

Only `build_dashboard.py` is needed for UI/template-only changes. Run both only when raw data or processing logic changes.

```powershell
# Step 1: Process raw data → JSON payload (~1–5 min, ~236 MB output — see pincode loan-range note above)
# Only needed when source files (FINANCEORG xlsx, D1 tracker, etc.) or process_data.py logic change
python process_data.py

# Step 2: Inject JSON into HTML template → final dashboard (~15-20 sec, ~450 MB output)
# Needed for any dashboard_template.html change (JS, CSS, HTML)
python build_dashboard.py

# Verification scripts
python verify_loss.py                                        # cross-check loss calculations
python verify_loss.py --sl 7.5 --sm 10.5 --sh 10.0 --svh 10.0
python verify_volume.py                                          # verify D1_PINCODE_VOLUME and CORRIDOR_D1_VOLUME against raw CSV
python verify_dedup.py                                       # verify no D1 disbursal is double-counted across region/corridor

# Optional: Validate JavaScript syntax in the template
python check_syntax.py
```

`build_dashboard.py` accepts explicit overrides:
```powershell
python build_dashboard.py --data pipeline_output.json --template dashboard_template.html --out demo/dashboard.html
```

## Architecture

```
[Raw Data Files]
  sample_data/portfolio_risk_data.xlsx   (17 MB)  — primary risk portfolio
  sample_data/disbursement_tracker.csv     (29 MB)  — disbursement tracking
  sample_data/bureau_market_data.csv         (~530 MB) — bureau/P&L market overlay (30P6M + 90P12M, current file)
  Credit Tracker CSV            (0.4 MB) — rejection analysis
  Pincode Mapping CSV            (1.7 MB) — delinquency classification
  Full_India_pincodes...xlsx     (1.1 MB) — lat/long coordinates
  reference/india_states.geojson           (3.8 MB) — state boundary polygons
         │
         ▼
  process_data.py  → pipeline_output.json (~236 MB)
         │
         ▼
  build_dashboard.py + dashboard_template.html
         │
         ▼
  demo/dashboard.html (~450 MB)
```

### Layer 1 & 2 — `process_data.py`

The `DataEngine` class orchestrates all processing via a `run()` method that calls these stages in order:

| Method | Output attribute | Purpose |
|--------|-----------------|---------|
| `_load_and_clean()` | `self.df` | Load Excel, rename columns, normalize state/district names |
| `_aggregate_data()` | `self.port_stats`, `self.region_data` | Roll up bad rates by risk tier → state → district → month |
| `_load_bureau_data()` | `self.bureau_data`, `self.bureau_pincode_data` | Market delinquency (30P6M/90P12M) rates + `TOTAL_SANCTIONED_AMOUNT` by state/district/pincode, sliced by member-group × quarter × trade-size at both state and district grain (identical shape, kept in sync deliberately), and member-group × trade-size + a separate quarter-only `quarterly` array at pincode grain (no mg×ts×quarter cross there — kept lean). See "Market View — bureau filter system" below. |
| `_load_ats_data()` | `self.ats_data` | Mean loan amount per risk tier from D1 tracker (ATS date window). Filters: `prospect_stage == "Disbursed"` and `source != "tp_form"` applied before date window. |
| `_build_monthly_disbursal_base()` | `self.monthly_disbursal_base` | Computes monthly disbursal run-rate (₹/mo) from D1 tracker using same `prospect_stage`/`source` filters. Window controlled by `disbursal_base_window` config ("ats" or "d1"). Used as denominator for "% of monthly disbursals" in the dashboard HUD. |
| `_build_d1_pincode_volume()` | `self.d1_pincode_volume` | Monthly disbursed count per pincode × tier (D1 date window) |
| `_build_corridor_d1_volume()` | `self.corridor_d1_volume` | Monthly disbursed count per migration corridor × tier |
| `_process_corridors()` | `self.corridor_data` | Inter-state migration flows (permanent state → current state) |
| `_load_rejection_data()` | `self.rejection_data` | Rejected applications by pincode for expansion opportunity analysis |
| `_build_pincode_risk_data()` | `self.pincode_risk_data` | Default rates per pincode |
| `_build_green_pincode_stats()` | `self.green_pincode_stats` | Benchmark bad rates in operationally active pincodes |
| `_save()` | `pipeline_output.json` | Serialize all 17 structures to JSON |

Geographic normalization uses a 140+ entry alias dictionary in `_load_and_clean()`; unresolved districts fall through to `_auto_match_districts()` (fuzzy match: 82% auto-accept, 65% suggest).

### Layer 3 — `build_dashboard.py`

Reads `pipeline_output.json` and replaces 17 placeholder tokens in `dashboard_template.html`:

`__PORTFOLIO_STATS__`, `__REGION_DATA__`, `__GEOJSON_DATA__`, `__BUREAU_DATA_JSON__`, `__ATS_DATA_JSON__`, `__CORRIDOR_DATA__`, `__CORRIDOR_D1_VOLUME__`, `__REJECTION_DATA__`, `__PINCODE_RISK_DATA__`, `__D1_PINCODE_VOLUME__`, `__MONTHLY_DISBURSAL_BASE__`, and 6 more.

### Frontend — `dashboard_template.html`

- Leaflet.js (v1.9.4) interactive map with state polygons + pincode scatter. Leaflet JS/CSS and XLSX are **inlined into the template** (2026-07-10) — do not re-add CDN `<script src>`/`<link>` tags; the only remaining external fetches are Google Fonts and cartocdn map tiles, both of which degrade gracefully offline.
- Five nav-rail pages (`switchPage()`): **State Risk** (Regions), **Migration** (Corridors), **Greenfield** (Growth + Expansion sub-tabs), **Market View** (compares FINANCEORG's internal portfolio delinquency against bureau/market delinquency — see dedicated section below), **Results**.
- Client-side Excel export via XLSX.js (v0.18.5) — **community edition**: confirmed by direct round-trip test that cell background-fill styles are silently stripped on write (`cell.s.fill` survives `XLSX.write`→`XLSX.read` as `{patternType:'none'}`). Any future "highlight this cell" feature needs a text/emoji marker, not a real fill color — see Market View export below for the pattern already in use.
- No server required — the output HTML is fully standalone (verified: loads with zero errors and full map/export functionality with all external network blocked).
- Theme: Fintech/Crypto dark gold — fonts: IBM Plex Sans (UI), IBM Plex Mono (data).

#### Key JS globals in `dashboard_template.html`

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
| Existing Growth | One per district | Bureau 30P6M%, Bureau 90P12M%, +Leads/mo, Upside/mo |
| Greenfield | One per district | Bureau 30P6M%, Bureau 90P12M%, Target Share%, Upside/mo |

Wz Rate% in the Region sheet uses district-level rate when `stopMode='district'` or `'force'`, pincode-level rate when `stopMode='pincode'`.

Bureau 30P6M%/90P12M% in the Growth/Greenfield sheets are always shown side by side regardless of which metric `marketRateMetric` currently drives qualification/coloring with — 90P12M is blank when a district has no cohort mature enough to report it (see cohort-maturity cutoffs below).

#### Bureau cohort-maturity cutoffs (`bureau_30p6m_mature_through` / `bureau_90p12m_mature_through`)

`_load_bureau_data()`'s blended "overall" state/district rate only sums quarters that have actually had enough time on book to observe the metric — a loan needs ~6mo for 30P6M, ~12mo for 90P12M. Quarters after these cutoffs are excluded from the blended `overall` (and from `loans_90p12m`, a separate denominator from `loans`) but still appear, correctly low/zero, in the per-quarter `quarterly` breakdown. Set these two config values to the last calendar quarter (Q1=Jan-Mar; these are plain calendar quarters, not fiscal-year) that had matured by the time the bureau CSV was pulled — update them whenever you pull a fresh extract. The JS-side quarter filter panel (`aggregateSlices()` in `dashboard_template.html`) applies the same cutoffs (via `BUREAU_DATA_JSON._meta.mature_through`) so a user manually selecting quarters can't produce a diluted blend either.

**D1 Vol/mo (Region sheet):** summed only for tiers that contributed `loss > 0` in the simulation — not all four tiers unconditionally. This ensures the implied ATS (Loss / D1 Vol) is consistent with what the dashboard actually stopped.

#### Visual design — 3D effects & ambient lighting

Applied via CSS in `dashboard_template.html` (around line 1100+, just after the inlined Leaflet/XLSX `<script>` blocks — grep for `body::before` to locate reliably, since inlining those libraries shifts this line number on every rebuild). Do not remove these blocks when editing styles.

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

#### Market View (`mapColorMode = 'market-view'`, page `market`)

Compares FINANCEORG's internal portfolio delinquency (30P6M/90P12M) against bureau/market delinquency at state, district, and pincode grain — the only page that surfaces bureau-only geographies (NE states, J&K, Ladakh) where FINANCEORG has no portfolio at all, since that's the whole point of the page.

- **`classifyMarketEntry(int30, int90, r30, r90, intCases, mktCases, granularity)`** (`granularity`: `'state'|'district'|'pincode'`, default `'district'`) is the single source of truth for color + label — used by `style()`'s map coloring, the hover tooltip (`_showMarketTooltip()`/`_marketTooltipHtml()`), `renderMarketList()`, and `exportMarketViewExcel()`, so none of these can ever disagree with each other. Early-gates on `_mvBelowMinCases()` (sample-size floor, see below) before any rate comparison.
- **`_mvFilter`** — Market View's settings state: `basis` (`'internal'|'market'|'both'`), `metric` (`'30p6m'|'90p12m'|'both'`), 4 independent thresholds (`intTh30/90`, `mktTh30/90`), `opScale`, **separate** `minCasesDistrict`/`minCasesPincode` sample-size floors (a shared single value was either too strict for districts or too loose for pincodes), and `vis` (quadrant visibility Set). Rendered by `renderMarketFilterPanel()` into a draggable panel (`#qdmode-ctrl`, drag handled by `_mvDragStart`/document-level `mousemove`/`mouseup`, position stored as inline style on the outer element so `renderMarketFilterPanel()`'s innerHTML-only re-render never resets it).
- **`_mvBelowMinCases(intCases, mktCases, granularity)`** — below-threshold entries get `color:'rgba(255,255,255,0.05)', opacity:0.22` (bumped up from an original `0.025`/`0.12` — that low an opacity made the polygon fill nearly impossible to see or precisely hover over; **hover itself was verified working correctly via real mouse simulation once actually landed on the shape** — the "no hover" symptom was a visibility/targeting problem, not an event-binding bug).

**Bureau filter system** (`globalBureauFilters = {mg, qtr, ts}` — member-group/quarter/loan-range Sets, shared with Growth/Greenfield):
- `aggregateSlices(slices)` — generic filter+aggregate over a slice array carrying `mg`/`q_raw`/`ts`/`l`/`d`/`d90`/`l90`/`amt`; gates 30P6M/90P12M separately to their own cohort-maturity cutoffs (`_bureauMatureKey()`) so a user-selected quarter range can't dilute the blend any more than the unfiltered default can.
- `getBureauStateData()`/`getBureauDistData()` both call `aggregateSlices()` on `BUREAU_DATA[sn].slices` / `BUREAU_DATA[sn].districts[dn].slices` respectively — **this requires district slices to carry the exact same shape as state slices** (`mg`+`q_raw`+`ts`), which was **not always true**: district slices originally had `mg`-only, then gained `ts` (fixed a bug where the trade-size filter silently did nothing for any district — 0/15 districts responded before that fix), and for a while still had no `q_raw` at all, which was **worse than doing nothing**: `aggregateSlices()` checks `qtr.has(s.q_raw)` on every slice, so with no `q_raw` field, selecting *any* quarter zeroed the entire filtered slice set — not just failing to narrow by quarter, but silently discarding a working member-group filter too when both were active simultaneously (verified: mg-only filtering correctly narrowed a sample district's loan count from 388→1, but adding *any* quarter filter on top silently fell back to the full unfiltered 388). Fixed by adding `q_raw`/`q` to district slices, mirroring the state-level shape exactly.
- `getBureauPincodeData(pin)` is deliberately **not** `aggregateSlices()`-based — pincode slices only ever carry `mg`+`ts` (no quarter dimension, to avoid a pincode×mg×ts×quarter cardinality explosion), and the getter explicitly ignores any active quarter filter rather than zeroing everything out the way reusing `aggregateSlices()` would.
- Falls back to the unfiltered value (never a blank) whenever a filter combination matches zero underlying rows — a sparse pincode/district shouldn't render as empty just because the filter happened to exclude its only data.

**`TOTAL_SANCTIONED_AMOUNT`** (bureau disbursed-amount column, added when the source bureau CSV includes it — `has_amt` gate in `_load_bureau_data()`, same optional-column pattern as the 90P12M columns) is summed into an `amt` field everywhere a `loans`/`rate` value already exists (state/district `overall`, per-quarter `quarterly` records, and all slice shapes) — **not** gated to cohort maturity in the blended "overall" (a portfolio-size metric, not a delinquency rate), **but** the same `mob=6` filter that produces `NUMBER_OF_LOANS`/`TOTAL_SANCTIONED_AMOUNT` in the source SQL means an immature quarter is genuinely under-reported for *volume* too, not just rate — confirmed nationally (the newest quarter showed ~2.3M loans vs. ~5.9M in every prior quarter, still filling in). Any QoQ trend computed from `amt` must gate to the same 30P6M maturity cutoff as delinquency, or the most recent 1-2 quarters will always read as "amount collapsed" (this bit the first cut of Growth Signal below — every district in the country came back as "loan amt decreasing" until the same maturity gate was applied to `amt` too).

#### Market View export (`exportMarketViewExcel()`)

4 sheets per active state — `Districts (All/Selected)`, `Pincodes (All/Selected)` (`Selected` = currently flagged by `classifyMarketEntry()`, not a manual cart — Market View stays cart-free by design). Columns, in order: name/grain identifiers → FINANCEORG + Market 30P6M%/90P12M% (blended, filter-aware) → Bureau Loans → Bureau Sanctioned Amt (₹Cr) → Classification/Flagged (from `classifyMarketEntry`) → **Growth Signal** block → QoQ trend block.

- **QoQ trend block** — grouped by metric across `_mvSelectedQuarters()` (whichever quarters are checked in the bureau filter panel, or all if none checked, chronologically sorted via `_qtrKey()`): all quarters' 30P6M% together, then all 90P12M%, then all Amt (₹Cr) — not interleaved per-quarter — so a trend reads left-to-right within one metric. Rates are written as `"4.85%"` strings (not bare numbers) so they're unambiguous when compared against other files.
- **Growth Signal** (`_mvGrowthSignal()`) — derived credit-trend × exposure-trend bucket, one of: 🟩 Bright Green / 🟢 Deep Green (both metrics stable-or-improving; bright=loan amt stable/up, deep=down) / 🟨 Bright Amber / 🟠 Deep Amber (exactly one metric worsening) / 🔴 Deep Red / 🟥 Bright Red (both worsening — **intensity intentionally reversed vs. Green/Amber**: Deep Red = loan amt *growing* into worsening risk, the worst case; Bright Red = amt shrinking, self-correcting) / ⬜ Insufficient data (<2 usable quarters for any of the three inputs). Delinquency deltas are `latest − earliest` mature quarter (pp); loan-amt delta is `%` change, gated to the same maturity cutoff as 30P6M (see `TOTAL_SANCTIONED_AMOUNT` note above). Emoji+label only — **not** a real cell fill color (see SheetJS community-edition note above).

#### Save/Load Scenario (`saveScenario()`/`loadScenario()`)

Captures cart, simulation circuit-breaker %, expansion settings, region/corridor min-lead filters, `stopModes`, `globalBureauFilters` (mg/qtr/ts), **and `_mvFilter`** (Market View's basis/metric/thresholds/opScale/min-cases/quadrant-visibility — added alongside the bureau filters since Market View has its own settings panel independent of the shared bureau filters). Verified via a captured-not-downloaded round trip: set a distinctive non-default state → save → reset to defaults → load → byte-identical restore.

## Verification Scripts

### `verify_volume.py`
Recomputes `D1_PINCODE_VOLUME` and `CORRIDOR_D1_VOLUME` from raw D1 tracker CSV and compares against `pipeline_output.json`. Reports mismatches and key stats.
```powershell
python verify_volume.py --top 20       # show top N mismatches
python verify_volume.py --pin 380001   # drill into a specific pincode
python verify_volume.py --corridor "Maharashtra -> Gujarat"
```

### `verify_dedup.py`
Two-layer check:
1. **Lead-level**: Checks LEAD_ARRAY for duplicate IDs.
2. **Volume-level**: Identifies D1 disbursals that appear in both `D1_PINCODE_VOLUME` and `CORRIDOR_D1_VOLUME` (inter-state disbursals). These only cause financial double-counting if both a region AND overlapping corridor are selected simultaneously — the JS `maxSev===0` guard prevents lead-level double-counting.
```powershell
python verify_dedup.py
python verify_dedup.py --corridor "Uttar Pradesh -> Haryana"
```

### `verify_loss.py`
Classifies districts as red_nonop/red_op/green and cross-checks D1-based monthly loss. Can spot-check against exported Excel rows.

## Configuration (`Phase2Config` in `process_data.py`)

All tunable parameters live in this single dataclass. Update here — not in processing logic — when input files change.

| Parameter | Purpose |
|-----------|---------|
| `ats_start_date` / `ats_end_date` | Date window for ATS (average loan size) calculation, e.g. `"2026-01-01"` to `"2026-04-30"` |
| `ats_window_months` | Number of months in the ATS window |
| `d1_start_date` / `d1_end_date` | Separate (usually narrower) date window for D1 volume counts |
| `ats_prospect_stage_col` / `ats_prospect_stage_value` | Column + value to filter D1 tracker for ATS and disbursal base (default: `prospect_stage == "Disbursed"`) |
| `ats_source_col` / `ats_source_exclude` | Column + value to exclude from ATS and disbursal base (default: `source != "tp_form"`) |
| `disbursal_base_window` | Which date window to use for monthly disbursal base: `"ats"` (default) or `"d1"` |
| `bureau_30p6m_mature_through` / `bureau_90p12m_mature_through` | Last calendar quarter (e.g. `"2025-Q1"`) mature enough to count toward the bureau "overall" blended rate for that metric — update whenever you pull a fresh bureau CSV extract |
| `mob12_completed_col` | Raw FINANCEORG column (`MOB_12_completed`) gating the internal FINANCEORG 90P12M rate to loans that have actually had 12mo on book |
| `auto_match_threshold` (0.82) | Fuzzy match threshold for automatic district name acceptance |
| `suggest_threshold` (0.65) | Lower threshold for generating manual-review suggestions |
| Column name mappings | 78+ field mappings from raw CSV/Excel headers to internal names |
| District aliases | Canonical name overrides (e.g., `"Bangalore"` → `"Bengaluru Urban"`) |
| `rejection_file` / filter fields | 5-step filter chain to isolate policy-rejected applications |
| `pin_map_type_val` / `pin_map_green_val` | Selectors for red vs. green pincodes from the mapping CSV |

## Diagnostics / Outputs

| File | Description |
|------|-------------|
| `demo/auto_matched_districts.csv` | Districts resolved by fuzzy matching |
| `demo/district_suggestions.csv` | Districts requiring manual review |
| `demo/unmapped_districts_*.csv` | Completely unmatched districts |

Review these CSVs after running `process_data.py` whenever input data changes — unmatched districts silently drop from the map.

## Dependencies

```
pip install -r requirements.txt
```
Tested with Python 3.14.6, pandas 3.0.3, openpyxl 3.1.5 on Windows.
