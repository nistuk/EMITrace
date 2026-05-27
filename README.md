# EMI Pre-Compliance Analysis — Cardiac Biplane

Notebook-based analysis and presentation aid for radiated-emissions
pre-compliance measurements taken on a Philips cardiac biplane system,
30 MHz – 1 GHz, with the broadband antenna placed at multiple positions
in the lab. Four operating modes are covered:

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

emi_analysis.ipynb                    main Jupyter notebook
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

# 2. Open and run the notebook in VS Code
code emi_analysis.ipynb
#    → Run All Cells
```

To re-execute the notebook headlessly and refresh everything in
`reports/` for **both** modes:

```powershell
py -3 run_all_modes.py
```

This runs the notebook twice (once with `MODE = 'Standby'`, once with
`MODE = 'Movement+Xray'`) and writes mode-tagged outputs into `reports/`.
To refresh a single mode only, edit `MODE` in section 1 of the notebook
and run it normally, or call `jupyter nbconvert --execute` directly.

---

## What the notebook produces

1. **Configuration cell** with one-stop knobs:
   - `MODE` — `'Standby'` or `'Movement+Xray'`; selects which dataset
     drives sections 2–8. The `TEST_MODES` dict above it maps each mode
     to its data root and antenna positions.
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
