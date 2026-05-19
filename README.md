# EMI Pre-Compliance Analysis — Cardiac Biplane

Notebook-based analysis and presentation aid for radiated-emissions
pre-compliance measurements taken on a Philips cardiac biplane system in
Standby, 30 MHz – 1 GHz, with the broadband antenna placed at three
different positions in the lab.

The raw scans were captured with a Rohde & Schwarz EMI receiver
(QP detector, 120 kHz IF) using a Schwarzbeck VULB 9162 TRILOG antenna,
at a working distance of roughly 1–3 m. The notebook overlays the three
traces, flags peak emitters, and compares them against the **CISPR 11
Group 1** radiated-emissions limits (Class A or Class B, normalised to a
10 m reference distance).

---

## Repository layout

```
data/18 May/                          raw R&S EMI-Scan .txt + screenshots
  Antenna on center/
  Antenna on Cabinets side/
  Antenna away from cabinets corner/

emi_analysis.ipynb                    main Jupyter notebook
reports/                              generated on each notebook run
  overlay_three_positions.html        interactive Plotly: all 3 positions
  envelope_vs_cispr11.html            interactive Plotly: worst-case envelope
  peaks_per_position.csv              top-N peaks per position with margins
  peaks_envelope.csv                  top-N envelope peaks with margins
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
`reports/`:

```powershell
py -3 -m jupyter nbconvert --to notebook --execute emi_analysis.ipynb --output emi_analysis.ipynb
```

---

## What the notebook produces

1. **Configuration cell** with one-stop knobs:
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
5. **Overlay plot** of all three positions with peak markers
   (interactive Plotly, log-frequency axis).
6. **Worst-case envelope** (`max` across the three positions) versus
   the limit line.
7. **Peak tables** styled with a colour-graded margin column and PASS/FAIL
   flags, per position and for the envelope.
8. **Export** to standalone HTML + CSV in `reports/`.

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
