# EMI Pre-Compliance Analysis — Cardiac Biplane

Radiated-emissions pre-compliance analysis for a Philips cardiac biplane
system, 30 MHz – 1 GHz, with a broadband antenna placed at multiple
positions in the lab. The **primary interface is the Streamlit dashboard**
([dashboard.py](dashboard.py)) — an interactive tool for comparing traces
and generating full CISPR 11 compliance reports. A Jupyter notebook
([emi_analysis.ipynb](emi_analysis.ipynb)) is retained for batch,
mode-by-mode report generation.

The following operating modes / sweeps are covered:

* **Standby** (18 May 2026) — three antenna positions.
* **Movement + X-ray** (19 May 2026) — two antenna positions (X-ray exposure
  during table / C-arm movement).
* **Configurations (20 May 2026)** — Standby with cabinets only, both stands,
  CLEA only, LARC only (stand-contribution ablation sweep).
* **CLEA SAMD sweep (20 May 2026)** — cabinets-only baseline vs. 10SPC CLEA
  SAMD on, CLEA SAMD unpowered, and CLEA SAMD logic only (24 V SMPS on).
* **Ambient vs Only cabinets** — site ambient (system off / disconnected,
  13 May 2026) compared against the cabinets-only baseline from 20 May, to
  separate environmental noise from cabinet emissions.
* **Full system (22 May 2026)** — ambient, 4SLF OFF Standby, and full-system
  ON Standby; baseline is the same-day ambient.
* **Afternoon standby vs ambient (22 May 2026)** — site ambient vs Standby in
  the afternoon repeat session; baseline is `Ambient`.
* **Afternoon movements vs standby (22 May 2026)** — Standby compared against
  Angulate, FD-shift, Long movement and Rotate; baseline is `Standby`.
* **Standby vs ambient (26 May 2026)** — site ambient vs Standby on the new
  stand; baseline is `Ambient`.
* **CLEA movements vs standby (26 May 2026)** — Standby compared against
  CLEA FD-rotate, FD-shift, Prop, Roll and Pivot movements on the new stand;
  baseline is `Standby`.

The raw scans were captured with a Rohde & Schwarz EMI receiver
(QP detector, 120 kHz IF) using a Schwarzbeck VULB 9162 TRILOG antenna,
at a working distance of roughly 1–3 m. The notebook overlays the traces
per mode, flags peak emitters, compares them against the **CISPR 11
Group 1** radiated-emissions limits (Class A or Class B, normalised to a
10 m reference distance), and finally overlays the two modes' worst-case
envelopes against each other.

---

## Repository layout

```
data/18 May/                          Standby — raw R&S EMI-Scan .txt + screenshots
  Antenna on center/
  Antenna on Cabinets side/
  Antenna away from cabinets corner/
data/19 May/                          Movement + X-ray — raw R&S EMI-Scan .txt + screenshots
  Movement+Xray_antenna near in middle.txt
  Movement+Xray_antenna near to cabinets.txt
data/20 May/                          Standby ablation sweeps (stands + CLEA SAMD)
  Only cabinets/                      cabinets-only baseline
  with both stands/                   with both LARC and CLEA stands
  without CLEA/                       LARC only
  without LARC/                       CLEA only
  With only 10SPC CLEA SAMD ON/       CLEA SAMD running 10SPC firmware
  without powering UP CLEA SAMD/      CLEA SAMD board unpowered
  without powering UP CLEA SAMD_24VSMPS ON/   CLEA SAMD logic only (24 V SMPS on)
data/22May/                           Full system (22 May) + afternoon repeat session
  ambient.txt, 4SLF-OFF_Standby.txt, Full system powered ON_Standby.txt
  afternoon/                          22 May afternoon: ambient, Standby, Angulate,
                                        FD-shift, Long movement, Rotate
data/26May/                           New stand (26 May): Standby2 + Ambient2 +
                                        CLEA FD-rotate / FD-shift / Prop / Roll / Pivot
data/ambient/                         Site ambient (system off) — 13 May 2026
  ambient.txt                         used as the 'Ambient (no system)' trace

dashboard.py                          Streamlit dashboard — the main interface
emc_analysis.py                       EMC compliance analysis engine (validation,
                                        stats, worst-case, margins, HTML report)
emi_analysis.ipynb                    Jupyter notebook — batch per-mode reports
run_all_modes.py                      executes the notebook once per mode and refreshes reports/
reports/                              generated on each notebook run (mode-tagged)
  overlay_positions_<mode>.html       interactive Plotly: all positions for that mode
  envelope_vs_cispr11_<mode>.html     interactive Plotly: worst-case envelope vs limit
  peaks_per_position_<mode>.csv       top-N peaks per position with margins
  peaks_envelope_<mode>.csv           top-N envelope peaks with margins
  envelope_<mode>.csv                 full envelope trace (used by section 9)
  envelope_by_mode.html               cross-mode envelope overlay (all modes)
  envelope_delta_movementxray_minus_standby.csv   per-frequency Δ between modes
  baseline_delta_<mode>.{html,csv}    Δ vs. the *Only cabinets* baseline (sections 10),
                                       written for every mode that includes that position
```

> **Note:** `data/` and `reports/` are git-ignored — the raw measurement
> record and generated artefacts are kept out of version control. They
> still live on disk locally; only the code is tracked.

The `_Att.txt` files alongside each scan record the RF attenuator setting
the receiver chose for auto-ranging. They are **informational only** — the
levels in the `.txt` data files are already referenced to the antenna
port, so no further attenuator correction is applied.

---

## Quick start

Requires Python 3.10+.

```powershell
# 1. Install dependencies
py -3 -m pip install -r requirements.txt

# 2. Launch the dashboard (the main interface)
py -3 -m streamlit run dashboard.py
```

The dashboard opens in your browser at `http://localhost:8501`.

---

## The dashboard (main interface)

`dashboard.py` is a Streamlit app with two tools, selectable from the
sidebar:

### 1. Trace comparison

Pick one **reference** trace and any number of **comparison** traces (from
the workspace `data/` folder or your own uploads). The app renders:

* an **overlay** of every trace against the CISPR 11 limit line, and
* **Δ-vs-reference** subplots highlighting where each comparison rises
  above the reference, with the top-N Δ peaks tabulated.

A standalone HTML report and the Δ data (CSV) are downloadable.

### 2. EMC compliance analysis

A full IEC 60601-1-2 / CISPR 11 style workflow over two or more selected
configurations:

* **Data validation** — empty/NaN checks, frequency-grid integrity, and
  receiver saturation. The R&S **`OverRange` flag is read straight from
  each scan header**: an over-ranged scan is automatically **excluded**
  (its levels may under-report the true emission), and a suspected
  flat-top clipping heuristic flags borderline cases.
* **Per-configuration statistics** — peak level & frequency, mean, median,
  95th percentile, peak count, peaks over limit, worst margin, and a
  broadband-span indicator.
* **Worst-case envelope** — point-by-point maximum across all valid
  configurations, with per-point contributor attribution.
* **Margin analysis** vs the CISPR 11 Group 1 (Class A/B, QP) limit —
  worst/best margin, closest approach, and aggregate bandwidth over limit.
* **Worst-case determination** — keyed off the smallest compliance margin
  with a quantitative, regulatory-style justification.
* **Spectral interpretation** — harmonic-family detection and
  broadband-vs-narrowband reasoning.
* **Plots** — overlay, worst-case envelope, margin plot, auto-zoom on the
  critical band, and summary bar charts.

The downloadable **HTML report** is fully structured (title page, revision
history, executive summary, methodology, configurations, data validation,
results, plots, margin analysis, peak tables, technical discussion,
worst-case determination, risks & uncertainties, conclusion). The
**Methodology** section and a **measurement-conditions** table are
auto-populated from the R&S scan headers (detector, IF bandwidth, dwell,
attenuation, antenna model, start/stop time, OverRange). Optional
*Prepared / Reviewed / Approved by* fields populate a reviewer block.

---

## Notebook (batch report generation)

For headless, mode-by-mode regeneration of everything in `reports/`:

```powershell
py -3 run_all_modes.py
```

This rewrites the notebook's `MODE` knob and executes
[emi_analysis.ipynb](emi_analysis.ipynb) once per `TEST_MODES` entry,
writing mode-tagged HTML + CSV outputs into `reports/`. To refresh a
single mode only, edit `MODE` in the notebook's config cell and run it
normally, or call `jupyter nbconvert --execute` directly.

The notebook itself contains:

1. **Configuration cell** with one-stop knobs:
   - `MODE` — selects which `TEST_MODES` dataset drives sections 2–8.
   - `CISPR_CLASS` — `'A'` (industrial / professional healthcare) or `'B'`
     (residential).
   - `MEAS_DISTANCE` / `LIMIT_DISTANCE` — distance correction in dB,
     applied as `+20·log10(d_meas / d_limit)`.
   - `PEAK_PROMINENCE`, `MIN_PEAK_SEP_MHZ`, `TOP_N_PEAKS` — peak-detector
     tuning.
2. **Loader** for the R&S `% [Header]` / `% [Data]` text format.
3. **Limit-line builder** for CISPR 11 Group 1 radiated QP limits at 10 m
   (Class A: 40 / 47 dBµV/m; Class B: 30 / 37 dBµV/m, step at 230 MHz).
4. **Peak detector** using `scipy.signal.find_peaks` with a minimum
   prominence and a minimum frequency separation so densely sampled
   humps collapse to a single entry per emitter.
5. **Overlay plot** of all positions for the active mode with peak
   markers (interactive Plotly, log-frequency axis).
6. **Worst-case envelope** (`max` across the active mode's positions)
   versus the limit line.
7. **Peak tables** styled with a colour-graded margin column and PASS/FAIL
   flags, per position and for the envelope.
8. **Export** to mode-tagged standalone HTML + CSV in `reports/`.
9. **Cross-mode comparison** — reloads every entry in `TEST_MODES`,
   overlays each mode's worst-case envelope against the CISPR 11 limit,
   and writes a per-frequency Δ table (Movement+Xray − Standby) so you
   can see exactly where the X-ray + table-movement activity lifts the
   emission floor.

---

## Caveats

* Distance correction assumes far-field 1/r propagation. Below ~230 MHz
  at 3 m this is an approximation; treat the resulting margins as
  indicative, not as a formal compliance result.
* Antenna polarisation, height scan and turntable rotation are **not**
  part of this pre-compliance sweep — expect a few dB of additional
  uncertainty when comparing against a 10 m chamber measurement.
* The transducer correction baked into the R&S `Magnitude with Transducer`
  column is what is reported here; no further antenna-factor maths is
  applied.
