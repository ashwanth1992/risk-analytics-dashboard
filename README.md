# Geospatial Credit Risk Intelligence Dashboard

> A self-contained interactive risk analytics product built for a lending company's strategy and risk team — no server, no BI tool license, just a single HTML file any stakeholder can open.

---

## The Problem

The risk team was making geographic credit policy decisions — where to tighten lending, where to expand, which borrower migration corridors carried excess delinquency — using a patchwork of Excel files and manual lookups. Every analysis took days. When assumptions changed (a new date window, an updated threshold), the whole thing had to be rebuilt from scratch. And the output couldn't be shared interactively: you sent a static slide deck and hoped the audience asked the right questions.

The specific gaps:
- No way to *simulate* a policy decision (tighten in X pincodes → what's the monthly loss impact?) before taking it
- Bureau market data and internal portfolio data lived in separate files, never combined
- Geographic patterns — particularly inter-state migration corridors — were invisible in tabular data
- Risk team insights couldn't reach business stakeholders without a manual translation layer

---

## Who This Was Built For

**Primary users:** Risk analysts and the strategy manager running monthly portfolio reviews. They needed to arrive at credit policy recommendations (which pincodes/districts/corridors to flag) with confidence and with numbers they could defend.

**Secondary users:** Business and operations leadership who received the output — they needed an executive summary they could read in 5 minutes, not a 40-tab Excel workbook.

The constraint that shaped every decision: **neither group would install software or run a script to view the output.** The product had to be something you could email.

---

## What It Does

Six capability areas, each a navigation tab in the dashboard:

### 1. Regions
State → district → pincode drilldown on the interactive map. Each pincode is colour-coded by internal delinquency rate (30-day default over 6 months), with risk tier breakdown (Low / Medium / High / Very High). Analysts can drill from national view to a specific pincode in three clicks.

### 2. Tightening Simulation
Select any combination of states, districts, pincodes, or migration corridors and run a simulation. The dashboard calculates:
- Estimated monthly loss (₹) if the portfolio continues in the selection
- Volume of leads affected per risk tier
- Loss as a percentage of the company's monthly disbursal run-rate

This lets the risk team quantify the cost of *not* acting — and size the impact of different tightening options — before writing a policy recommendation.

### 3. Corridors
Borrowers' permanent address (home state) often differs from their current address (where they live and work). Delinquency in this migrant segment follows inter-state movement patterns that are invisible in a pincode-only view. This tab maps those migration corridors and overlays default rates, so risk isn't attributed to just the current location but to the origin-destination flow.

### 4. Expansion
Pincodes where bureau market delinquency data exists but the company has no active portfolio — potential new markets. Filtered by configurable thresholds (internal rate ≤ X%, market rate ≤ Y%, minimum rejected applications as a demand signal). Greenfield districts (zero existing presence) are separated from growth-in-existing districts.

### 5. Market View
A dedicated bureau-vs-internal delinquency comparison, filterable by peer group, loan-range, and quarter, at state/district/pincode grain — including geographies (Northeast states, J&K, Ladakh) where the company has no portfolio at all but bureau data still exists. Adds a quarter-over-quarter trend view and a "Growth Signal" export that flags whether exposure is growing into worsening or improving risk. This is the newest and now-largest capability area in the product; it required extending the bureau data pipeline to carry a full peer-group × loan-range × quarter breakdown at every geographic grain, and fixing an independently-discovered denominator bug that had understated the 90-day/12-month delinquency rate by roughly 35-40%.

### 6. Results (Executive Summary)
A single-screen summary of the selected policy scenario: total leads affected, estimated monthly loss, loss as % of disbursals, and a breakdown by region vs. corridor contribution. Designed for leadership review — no map interaction required. Includes one-click Excel export of all tightening and expansion recommendations.

---

## Key Product Decisions

These are the choices that shaped what got built and why.

**1. Self-contained HTML, no server.**
The obvious infrastructure choices (a hosted dashboard, a BI tool) would have created an access and adoption problem. The risk team needed to share outputs with operations leadership, external stakeholders, and occasionally board-level audiences — none of whom would log into an internal tool. A single HTML file, emailed or dropped in a shared drive, removes all friction. The tradeoff: the file is large (~450 MB at full bureau-data granularity) and data refresh requires re-running the pipeline. That was an acceptable tradeoff given how infrequently the underlying portfolio data changed (monthly refresh cycle) — file size has been tested empirically at every growth step to confirm browser load time stays roughly flat.

**2. Separating data processing from UI iteration.**
Early versions rebuilt everything from scratch on every run. Processing 500+ MB of raw files took 1–5 minutes, which made UI tweaks slow and frustrating. Splitting the pipeline into two stages — `process_data.py` outputs a ~236 MB JSON payload once; `build_dashboard.py` injects it into the template in 15–20 seconds at current scale — meant that CSS changes, label tweaks, or layout adjustments didn't require re-reading 500+ MB of source data. This changed the development loop from minutes to seconds for UI work.

**3. The simulation engine is the product's core value.**
A map that shows delinquency rates is useful. A map where you can select a set of pincodes, simulate stopping lending there, and see the quantified monthly loss impact *before you act* is a decision tool. The distinction matters because it changes what the risk team brings to a policy meeting — instead of "here are the bad pincodes," it's "here's the cost of each option." Building the simulation required agreeing on loss methodology upfront (D1 volume × average ticket size × risk months), which forced alignment between risk, finance, and strategy on how to measure impact — arguably more valuable than the tool itself.

**4. Bureau data as the expansion layer, not a replacement.**
Internal portfolio data tells you where defaults are happening. It tells you nothing about where the company *isn't* present. Bureau market data fills that gap: pincodes where the broader market is healthy but the company hasn't lent. Rather than building two separate analyses, combining both datasets in the same tool meant an analyst could move from "tighten here" to "grow here instead" in the same session — a workflow that previously required two separate Excel files and a manual comparison.

**5. Corridor analysis as a first-class view.**
This was the least obvious inclusion and the one that generated the most insight. The company's borrower base skews toward migrant workers — people living in one state whose financial identity is tied to another. Standard pincode-level analysis misattributes risk: a borrower from a high-risk home state living in a low-risk city looks fine by current-address-only analysis. Building the corridor view (permanent state → current state, overlaid with delinquency) surfaced migration patterns that were driving delinquency invisibly. It also changed how the risk team thought about the portfolio — geographic risk became a two-dimensional problem.

**6. Configuration over code changes.**
All tunable parameters — date windows, risk thresholds, column mappings, filter chains — live in a single config dataclass. When the source file changes columns, you update one place. When the ATS date window shifts monthly, you update two fields. This was a deliberate choice to make the system maintainable by someone who didn't write it.

---

## Architecture

```
[Raw Data Sources]                         [~583 MB total]
  Portfolio risk file (Excel, 17 MB)       — delinquency rates per lead
  D1 disbursement tracker (CSV, 29 MB)     — volume and ATS calculation
  Bureau market data (CSV, ~530 MB)        — external delinquency benchmarks (30P6M + 90P12M)
  Credit rejection log (CSV, 0.4 MB)       — demand signal for expansion
  Pincode classification (CSV, 1.7 MB)     — operational vs. non-operational flags
  India pincode coordinates (Excel, 1.1 MB)— lat/long for map rendering
  India state boundaries (GeoJSON, 3.8 MB) — polygon rendering
         │
         ▼
  process_data.py          (~1–5 min)
  → pipeline_output.json  (~236 MB, 17 serialized data structures)
         │
         ▼
  build_dashboard.py + dashboard_template.html   (~15–20 sec at this scale)
  → demo/dashboard.html   (~450 MB, fully standalone)
```

**Frontend stack:** Leaflet.js (interactive map), vanilla JS simulation engine, XLSX.js (Excel export). No framework, no build step, no dependencies beyond what's bundled in the HTML.

---

## Running It

```bash
# Install dependencies (one-time)
pip install pandas openpyxl

# Step 1: Process raw data → JSON (run when source files change)
python process_data.py

# Step 2: Inject JSON into HTML template → final dashboard
python build_dashboard.py

# Open demo/dashboard.html in any browser
```

For UI-only changes (CSS, labels, layout), skip Step 1 — Step 2 alone takes ~5 seconds.

**Verification scripts** (run after Step 1 to cross-check outputs):
```bash
python verify_volume.py   # verify pincode and corridor volumes against raw CSV
python verify_loss.py     # cross-check loss calculations
python verify_dedup.py    # confirm no disbursals are double-counted
python verify_bureau.py   # internal consistency checks for the bureau/Market View data structures
```

---

## What I Would Do Differently

A few things that would make this better as a product rather than an internal tool:

- **User testing with the actual audience.** The Expansion and Corridor tabs were built based on my own read of what the risk team needed — I never ran a structured session to validate that the workflow matched how they actually made decisions. Some filters that seemed intuitive to me required explanation in practice.
- **Incremental data refresh.** The current pipeline re-reads all source files on every run. With a proper data layer, you'd process only new records and append to the JSON. Practically this wasn't a problem given the monthly refresh cycle, but it would matter at higher data volumes.
- **Richer scenario sharing.** The dashboard supports saving and loading simulation state as a JSON file — so a scenario can be shared and reproduced exactly. What's missing is a more seamless workflow around this: version labelling, notes on why a scenario was constructed, or a quick way to compare two saved scenarios side by side.
- **Audit trail on policy decisions.** The dashboard helps you make a decision but doesn't record it. Knowing which simulation parameters led to a policy recommendation, and when, would be valuable for retrospectives.

