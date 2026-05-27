# AGENTS.md — Cardiac Biplane EMI analysis

Single-notebook EMI pre-compliance analysis. See [README.md](README.md) for
science background, CISPR 11 caveats, and the report-file catalogue.

## Environment

- Windows / PowerShell. **Always use `py -3`** (not `python`) — the
  `python.exe` on `PATH` is a Microsoft Store shim that prints an install
  prompt and exits.
- Dependencies: [requirements.txt](requirements.txt). Install with
  `py -3 -m pip install -r requirements.txt`.
- **Do not prefix commands with `& py -3 …`** — PowerShell rejects the
  bare `&` as a parser error. Just write `py -3 …`.
- Some PowerShell sessions are missing common cmdlets (`Get-Content`,
  `Test-Path`, `Remove-Item` are unreliable). When that happens, use
  `type`, `dir`, `del`, or run a tiny Python one-liner via `py -3`.
- `run_in_terminal` sometimes returns *before* a long `jupyter nbconvert`
  finishes and **swallows stdout** silently. Don't trust "no output" as
  success — always verify by listing the expected `reports/*` or
  `emi_analysis.executed.<slug>.ipynb` files afterwards. If a mode looks
  missing, re-run just that one mode (see snippet below) rather than the
  whole `run_all_modes.py`.

## Stable cell-index map (do not grep for these)

Indices are 0-based and stable as long as nobody inserts cells above
section 9. Use these directly in patch helpers.

| Idx | Type | Purpose                                              |
|----:|------|------------------------------------------------------|
|   2 | code | Config: `TEST_MODES`, `MODE`, CISPR knobs            |
|   4 | code | Loader (`POSITIONS`, `traces`)                       |
|   6 | code | CISPR 11 limit line                                  |
|   8 | code | Peak detector                                        |
|  10 | code | Overlay plot + `COLORS` palette                      |
|  12 | code | Worst-case envelope                                  |
|  14 | code | Peak-table styling                                   |
|  18 | code | Export — defines `out_dir`, `_slug`                  |
|  20 | code | Section 9: `MODE_COLORS`, cross-mode overlay         |
|  23 | code | Section 10: generic Δ-vs-`BASELINE` (`'Only cabinets'`) |

If a section 11+ is added in the future, append to this table.

## Architecture in one screen

- [emi_analysis.ipynb](emi_analysis.ipynb) — single source of truth. All
  data wrangling, peak detection, plots and exports live here. Sections:
  1. Config (`TEST_MODES`, `MODE`, CISPR class, distance correction)
  2. Loader for R&S `% [Header]` `.txt` traces
  3. CISPR 11 Group 1 limit line builder
  4. Peak detector (`scipy.signal.find_peaks`)
  5. Overlay plot of all positions in the active mode
  6. Worst-case envelope vs. limit
  7. Peak tables (per position + envelope)
  8. Export — **mode-tagged** filenames in `reports/`
  9. Cross-mode comparison — overlays every `TEST_MODES` envelope and
     writes the `envelope_delta_movementxray_minus_standby` artefacts
  10. Δ vs. *Only cabinets* baseline — runs for any mode whose `positions`
      includes `'Only cabinets'`. Writes `baseline_delta_<slug>.{html,csv}`.
      Used by both the 20 May Configurations and CLEA SAMD sweeps.
- [run_all_modes.py](run_all_modes.py) — discovers every `TEST_MODES` key
  via regex on cell 2, rewrites the `MODE` line, and executes the notebook
  once per mode with `jupyter nbconvert`. Adding a new mode requires
  **no** changes here.
- [reports/](reports) — regenerated on every run; safe to delete. Files
  are slugged from the mode name (`overlay_positions_<slug>.html`, etc.).
- `_compare.py` — legacy matplotlib repro of R&S screenshots; **not** part
  of the main pipeline. Ignore unless asked.

## Conventions that matter

- **Never edit `.ipynb` JSON cells with `replace_string_in_file` on
  multi-line code.** The notebook XML facade merges old + new line-by-line
  and corrupts cells (lines from both versions interleave). For
  non-trivial cell rewrites, write a small `_patch_*.py` helper that
  loads JSON, replaces `cell['source']` with `splitlines(keepends=True)`,
  resets `outputs=[]` and `execution_count=None`, and writes back with
  `json.dumps(..., indent=1, ensure_ascii=False)`. Delete the helper
  afterwards. Single-line edits inside one cell (e.g. dict-literal
  entries) are fine through normal edit tools.
- Distance correction is `+20·log10(MEAS_DISTANCE / LIMIT_DISTANCE)` and
  is applied **once** in the loader (`field_corr_dbuvm`). Do not
  re-apply downstream.
- The R&S `Magnitude with Transducer` column is what we report; antenna
  factor and `_Att.txt` files are informational only.
- Mode slug for filenames: `lower()` then replace `+`→`_`, space→`_`,
  drop `()` (see `slug()` in `run_all_modes.py`).

## Workflow — adding a new measurement set

**TL;DR — fast path** (most new data fits this):

```powershell
# 1. See what's actually on disk (don't assume a new top-level date folder).
Get-ChildItem -Recurse -Directory data | Select-Object FullName
Get-ChildItem -Recurse -Filter *.txt data\<new folder> | Select-Object FullName
```

Then in [emi_analysis.ipynb](emi_analysis.ipynb) cell 2, add **one** new
`TEST_MODES` entry. If the baseline trace is named `'Only cabinets'`,
section 10 fires automatically — **no new section required**. If you
want a different baseline, change `BASELINE` in cell 23.

Run a single mode (avoids the `run_all_modes.py` truncation issue):

```powershell
py -3 -c "import json,pathlib; p=pathlib.Path('emi_analysis.ipynb'); nb=json.loads(p.read_text(encoding='utf-8')); s=nb['cells'][2]['source']; [s.__setitem__(i, \"MODE           = '<new mode>'  # any key of TEST_MODES\n\") for i,l in enumerate(s) if l.startswith('MODE')]; p.write_text(json.dumps(nb,indent=1,ensure_ascii=False)+'\n',encoding='utf-8')"
py -3 -m jupyter nbconvert --to notebook --execute emi_analysis.ipynb --output emi_analysis.executed.<slug>.ipynb
Get-ChildItem reports -Filter *<slug>* | Select-Object Name,Length
```

### Detailed steps

When a new `data/<date>/...` folder appears, treat it as a new entry in
`TEST_MODES` and let sections 9 and 10 pick it up automatically.

1. **Inventory** the new folder with `Get-ChildItem -Recurse data` —
   new captures often appear as **sibling subfolders inside an existing
   date folder**, not a new date folder. Don't ask the user before
   looking. Confirm each `.txt` starts with `% [Description]` /
   `% [Header]` (same R&S format — loader needs no changes).
2. **Extend `TEST_MODES`** in cell 2 of the notebook:
   ```python
   '<Mode name>': {
       'data_root': Path('data/<folder>'),
       'positions': {
           '<human-readable label>': Path('<subdir>') / '<file>.txt',
           ...
       },
   },
   ```
   Use human-readable labels — they appear in legends, plot titles and
   CSV columns. Rename source files only as a last resort; prefer to
   re-label here.
3. **Optional — give the new mode a colour** for the cross-mode overlay
   by adding an entry to `MODE_COLORS` in cell 20.
   Falls back to grey if omitted.
4. **Set `MODE = '<new mode>'`** in cell 2 so opening the notebook
   immediately shows the new dataset.
5. **Re-execute**: prefer `py -3 run_all_modes.py` for a full refresh,
   but if it returns silently with missing outputs, fall back to the
   single-mode snippet above.
6. **Verify** the new files in [reports/](reports):
   - `overlay_positions_<slug>.html`
   - `envelope_vs_cispr11_<slug>.html`
   - `peaks_per_position_<slug>.csv`, `peaks_envelope_<slug>.csv`
   - `envelope_<slug>.csv`
   - `envelope_by_mode.html` (now includes the new envelope)
   - `baseline_delta_<slug>.{html,csv}` *(only if the mode has an
     `'Only cabinets'` position)*
7. **Update the layout section** of [README.md](README.md) with the new
   folder and a one-line description of the mode.

If the new dataset is an **ablation sweep** (variants of the same setup,
like the 20 May Configurations or CLEA SAMD modes), just make sure the
baseline position is named `'Only cabinets'` — section 10 picks it up
automatically and writes `baseline_delta_<slug>.{html,csv}`. For a
different baseline name, either rename in `TEST_MODES` or extend the
`BASELINE` constant in section 10's code cell.

## Verifying changes

- `py -3 run_all_modes.py` — full refresh, prints top-Δ tables to stdout.
  Long output is captured to `reports/*.csv`; if you need only a sanity
  check, look at byte-size of the regenerated `.html` files.
- Notebook should run top-to-bottom without manual intervention for
  every mode. If a mode's section 10 is mode-specific, guard it with
  `if MODE == '<mode>':` (see existing pattern).
- Avoid committing `emi_analysis.executed.*.ipynb` — those are
  per-mode execution artefacts. Keep the source notebook as the only
  versioned `.ipynb`.

## Things to leave alone

- The R&S source `.txt` / `.fig` / `.mat` / `.jpg` files in `data/` are
  the raw measurement record. **Never modify, rename, or delete them.**
- `_compare.py`, `compare.log`, `q.log` — ad-hoc scratch from earlier
  sessions; do not regenerate or rely on them.

## Anti-patterns (cost time in past sessions)

- **Asking the user where new data lives before recursing `data/`.**
  Captures usually appear as sibling subfolders under the most recent
  date folder — `Get-ChildItem -Recurse -Directory data` finds them in
  one shot.
- **Re-running `run_all_modes.py` when one mode's output is missing.**
  The script can finish silently with truncated stdout. Diagnose with
  `Get-ChildItem emi_analysis.executed.*.ipynb` and re-execute only the
  missing mode via the single-mode snippet above.
- **Writing a fresh `_outline.py` / `_dump.py` every session** to map
  cell indices. Use the table at the top of this file; only inspect
  cells directly if that table looks stale.
- **Gating new analysis sections on `MODE == '<specific mode>'`.**
  Prefer a data-driven trigger (e.g. `if BASELINE in traces:`) so the
  same section serves future ablation sweeps without code changes.
- **Hard-coding mode names in output filenames.** Always slug from
  `MODE` (`out_dir / f'<purpose>_{_slug}.html'`) so multiple modes
  coexist in `reports/`.
- **Editing `.ipynb` cells with `replace_string_in_file` on multi-line
  source.** Covered above, but worth repeating — it silently interleaves
  old and new lines.
