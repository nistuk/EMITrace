"""EMC compliance analysis engine for Rohde & Schwarz radiated-emission scans.

This module implements a senior-EMC-engineer analysis workflow on top of the
same ``% [Header]`` 3-column trace format used by the rest of the project:

  * data validation  (missing data, frequency-grid integrity, suspected
    clipping / over-range),
  * per-configuration statistics  (peak, mean, median, 95th pct, peak count,
    broadband indicators),
  * worst-case envelope determination,
  * CISPR 11 margin analysis,
  * spectral / harmonic interpretation,
  * publication-quality Plotly figures, and
  * a fully structured, self-contained HTML report.

Everything operates on already-loaded ``pandas.DataFrame`` traces with the
columns produced by ``dashboard.load_trace`` (``freq_mhz`` and
``field_corr_dbuvm``), so the engine is independent of how the data was read
and can be driven from the Streamlit dashboard *or* the notebook.

All thresholds are parameters — no dataset-specific behaviour is hard-coded —
so the same analysis runs on any set of configurations.
"""

from __future__ import annotations

import html
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import plotly.io as pio
from plotly.subplots import make_subplots
from scipy.signal import find_peaks

FREQ_COL = 'freq_mhz'
LEVEL_COL = 'field_corr_dbuvm'

# CISPR 11, Group 1, radiated QP limits referenced to a 10 m measurement
# distance (single source of truth for the analysis engine).
CISPR11_LIMITS = {
    'A': [(30.0, 230.0, 40.0), (230.0, 1000.0, 47.0)],
    'B': [(30.0, 230.0, 30.0), (230.0, 1000.0, 37.0)],
}

PALETTE = ['#1f77b4', '#d62728', '#2ca02c', '#9467bd', '#ff7f0e',
           '#17becf', '#e377c2', '#8c564b', '#bcbd22', '#7f7f7f']


# --------------------------------------------------------------------------- #
# Limit helpers
# --------------------------------------------------------------------------- #

def limit_at(freq_mhz: np.ndarray, cls: str) -> np.ndarray:
    """Interpolated (stepped) CISPR 11 limit for each frequency, in dBµV/m.

    Frequencies outside the 30–1000 MHz limit definition return ``nan``.
    """
    freq_mhz = np.asarray(freq_mhz, dtype=float)
    out = np.full_like(freq_mhz, np.nan, dtype=float)
    for f_lo, f_hi, lvl in CISPR11_LIMITS[cls]:
        mask = (freq_mhz >= f_lo) & (freq_mhz <= f_hi)
        out[mask] = lvl
    return out


def limit_line(cls: str) -> tuple[np.ndarray, np.ndarray]:
    """Stepped (freq, level) arrays for plotting the limit, incl. 230 MHz step."""
    f = np.array([30, 230, 230, 1000], dtype=float)
    v = np.array([s[2] for s in CISPR11_LIMITS[cls] for _ in (0, 1)],
                 dtype=float)
    return f, v


# --------------------------------------------------------------------------- #
# 1. Data validation
# --------------------------------------------------------------------------- #

@dataclass
class ValidationResult:
    name: str
    n_points: int
    f_min: float
    f_max: float
    median_step_mhz: float
    grid_regular: bool
    issues: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)

    @property
    def usable(self) -> bool:
        # "issues" are blocking; "flags" are advisory only.
        return not self.issues


def validate_trace(name: str, df: pd.DataFrame,
                   clip_run_len: int = 6) -> ValidationResult:
    """Validate one trace and surface data-quality problems.

    Detects empty/short traces, NaNs, non-monotonic or irregular frequency
    grids, and suspected clipping / over-range (long runs of an identical
    maximum value, which the R&S 3-column export shows when a reading is
    pinned at the receiver ceiling).
    """
    issues: list[str] = []
    flags: list[str] = []

    if df is None or len(df) == 0:
        return ValidationResult(name, 0, float('nan'), float('nan'),
                                float('nan'), False, ['Trace is empty.'])

    f = df[FREQ_COL].to_numpy(dtype=float)
    y = df[LEVEL_COL].to_numpy(dtype=float)
    n = len(df)

    n_nan = int(np.isnan(f).sum() + np.isnan(y).sum())
    if n_nan:
        issues.append(f'{n_nan} missing/NaN sample(s) present.')

    if n < 50:
        flags.append(f'Only {n} points — coarse sweep, peak detection limited.')

    diffs = np.diff(f)
    if np.any(diffs <= 0):
        issues.append('Frequency axis is not strictly increasing '
                      '(duplicate or out-of-order points).')
    median_step = float(np.median(diffs)) if len(diffs) else float('nan')
    # Regular grid: 99% of steps within 5% of the median step.
    if len(diffs) and median_step > 0:
        rel = np.abs(diffs - median_step) / median_step
        grid_regular = bool(np.mean(rel < 0.05) >= 0.99)
    else:
        grid_regular = False
    if not grid_regular and not issues:
        flags.append('Irregular frequency grid — steps vary >5% from median.')

    # Suspected clipping / over-range: a flat top at the global maximum.
    if n:
        ymax = float(np.nanmax(y))
        at_max = np.isclose(y, ymax, atol=1e-6)
        # longest consecutive run at the ceiling
        run = best = 0
        for v in at_max:
            run = run + 1 if v else 0
            best = max(best, run)
        if best >= clip_run_len:
            flags.append(f'Suspected over-range/clipping: {best} consecutive '
                         f'samples pinned at {ymax:.1f} dBµV/m.')

    return ValidationResult(
        name=name, n_points=n,
        f_min=float(np.nanmin(f)), f_max=float(np.nanmax(f)),
        median_step_mhz=median_step, grid_regular=grid_regular,
        issues=issues, flags=flags,
    )


# --------------------------------------------------------------------------- #
# 2. Peak detection + per-configuration statistics
# --------------------------------------------------------------------------- #

def detect_peaks(df: pd.DataFrame, cls: str,
                 prominence_db: float = 6.0,
                 min_sep_mhz: float = 2.0) -> pd.DataFrame:
    """Prominence-based peak picking with CISPR 11 margin per peak."""
    y = df[LEVEL_COL].to_numpy()
    f = df[FREQ_COL].to_numpy()
    if len(f) < 3:
        return pd.DataFrame(columns=['freq_mhz', 'level_dbuvm', 'prominence_db',
                                     'limit_dbuvm', 'margin_db'])
    step = float(np.median(np.diff(f)))
    distance = max(1, int(round(min_sep_mhz / step))) if step > 0 else 1
    idx, props = find_peaks(y, prominence=prominence_db, distance=distance)
    if len(idx) == 0:
        return pd.DataFrame(columns=['freq_mhz', 'level_dbuvm', 'prominence_db',
                                     'limit_dbuvm', 'margin_db'])
    peaks = pd.DataFrame({
        'freq_mhz': f[idx],
        'level_dbuvm': y[idx],
        'prominence_db': props['prominences'],
    })
    peaks['limit_dbuvm'] = limit_at(peaks['freq_mhz'].to_numpy(), cls)
    peaks['margin_db'] = peaks['limit_dbuvm'] - peaks['level_dbuvm']
    return peaks.sort_values('level_dbuvm', ascending=False).reset_index(drop=True)


@dataclass
class TraceStats:
    name: str
    peak_dbuvm: float
    peak_freq_mhz: float
    mean_dbuvm: float
    median_dbuvm: float
    p95_dbuvm: float
    n_peaks: int
    n_exceed: int
    worst_margin_db: float
    worst_margin_freq_mhz: float
    broadband_span_mhz: float
    broadband_center_mhz: float
    peaks: pd.DataFrame


def broadband_indicator(df: pd.DataFrame, elevation_db: float = 10.0
                        ) -> tuple[float, float]:
    """Largest contiguous bandwidth (MHz) elevated >``elevation_db`` above the
    trace median, plus the centre frequency of that span.

    A wide elevated span signals broadband emission (e.g. switching noise,
    motor commutation) as opposed to a narrowband carrier.
    """
    f = df[FREQ_COL].to_numpy(dtype=float)
    y = df[LEVEL_COL].to_numpy(dtype=float)
    if len(f) < 3:
        return 0.0, float('nan')
    thr = float(np.median(y)) + elevation_db
    above = y > thr
    best_span = 0.0
    best_center = float('nan')
    i = 0
    while i < len(above):
        if above[i]:
            j = i
            while j + 1 < len(above) and above[j + 1]:
                j += 1
            span = f[j] - f[i]
            if span > best_span:
                best_span = span
                best_center = 0.5 * (f[i] + f[j])
            i = j + 1
        else:
            i += 1
    return float(best_span), best_center


def trace_statistics(name: str, df: pd.DataFrame, cls: str,
                     prominence_db: float, min_sep_mhz: float) -> TraceStats:
    y = df[LEVEL_COL].to_numpy(dtype=float)
    f = df[FREQ_COL].to_numpy(dtype=float)
    peaks = detect_peaks(df, cls, prominence_db, min_sep_mhz)

    imax = int(np.nanargmax(y))
    lim = limit_at(f, cls)
    margin = lim - y  # positive = pass
    valid = ~np.isnan(margin)
    if valid.any():
        worst_idx = int(np.nanargmin(np.where(valid, margin, np.inf)))
        worst_margin = float(margin[worst_idx])
        worst_margin_f = float(f[worst_idx])
    else:
        worst_margin = float('nan')
        worst_margin_f = float('nan')

    n_exceed = int((peaks['margin_db'] < 0).sum()) if len(peaks) else 0
    span, center = broadband_indicator(df)

    return TraceStats(
        name=name,
        peak_dbuvm=float(y[imax]),
        peak_freq_mhz=float(f[imax]),
        mean_dbuvm=float(np.nanmean(y)),
        median_dbuvm=float(np.nanmedian(y)),
        p95_dbuvm=float(np.nanpercentile(y, 95)),
        n_peaks=len(peaks),
        n_exceed=n_exceed,
        worst_margin_db=worst_margin,
        worst_margin_freq_mhz=worst_margin_f,
        broadband_span_mhz=span,
        broadband_center_mhz=center,
        peaks=peaks,
    )


def stats_table(stats: dict[str, TraceStats]) -> pd.DataFrame:
    rows = []
    for s in stats.values():
        rows.append({
            'Configuration': s.name,
            'Peak (dBµV/m)': s.peak_dbuvm,
            'Freq of peak (MHz)': s.peak_freq_mhz,
            'Mean (dBµV/m)': s.mean_dbuvm,
            'Median (dBµV/m)': s.median_dbuvm,
            '95th pct (dBµV/m)': s.p95_dbuvm,
            'Peaks > thr': s.n_peaks,
            'Peaks > limit': s.n_exceed,
            'Worst margin (dB)': s.worst_margin_db,
            'Broadband span (MHz)': s.broadband_span_mhz,
        })
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------- #
# 3. Worst-case envelope + margin analysis
# --------------------------------------------------------------------------- #

def worst_case_envelope(traces: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Point-by-point maximum across all traces on a shared frequency grid.

    The grid is taken from the trace with the finest median step so no detail
    is lost; every other trace is interpolated onto it.
    """
    # finest grid
    grids = {n: df[FREQ_COL].to_numpy(dtype=float) for n, df in traces.items()}
    fine_name = min(grids, key=lambda n: np.median(np.diff(grids[n]))
                    if len(grids[n]) > 1 else np.inf)
    f_grid = grids[fine_name]

    stack = []
    names = list(traces)
    for n in names:
        df = traces[n]
        stack.append(np.interp(f_grid, df[FREQ_COL], df[LEVEL_COL]))
    arr = np.vstack(stack)
    env = arr.max(axis=0)
    contributor = np.array(names)[arr.argmax(axis=0)]
    return pd.DataFrame({
        'freq_mhz': f_grid,
        'envelope_dbuvm': env,
        'contributor': contributor,
    })


@dataclass
class MarginResult:
    worst_margin_db: float
    worst_margin_freq_mhz: float
    worst_contributor: str
    best_margin_db: float
    best_margin_freq_mhz: float
    n_exceed_points: int
    exceed_bandwidth_mhz: float


def margin_analysis(envelope: pd.DataFrame, cls: str) -> MarginResult:
    f = envelope['freq_mhz'].to_numpy()
    y = envelope['envelope_dbuvm'].to_numpy()
    contr = envelope['contributor'].to_numpy()
    lim = limit_at(f, cls)
    margin = lim - y
    valid = ~np.isnan(margin)
    fv, mv, cv, yv = f[valid], margin[valid], contr[valid], y[valid]

    worst_i = int(np.argmin(mv))
    best_i = int(np.argmax(mv))
    exceed = mv < 0
    # crude bandwidth where the envelope exceeds the limit
    if exceed.any():
        step = float(np.median(np.diff(fv))) if len(fv) > 1 else 0.0
        exceed_bw = float(exceed.sum() * step)
    else:
        exceed_bw = 0.0

    return MarginResult(
        worst_margin_db=float(mv[worst_i]),
        worst_margin_freq_mhz=float(fv[worst_i]),
        worst_contributor=str(cv[worst_i]),
        best_margin_db=float(mv[best_i]),
        best_margin_freq_mhz=float(fv[best_i]),
        n_exceed_points=int(exceed.sum()),
        exceed_bandwidth_mhz=exceed_bw,
    )


# --------------------------------------------------------------------------- #
# 4. Worst-case configuration determination
# --------------------------------------------------------------------------- #

@dataclass
class WorstCase:
    name: str
    by_peak: str
    by_mean: str
    by_margin: str
    peak_dbuvm: float
    runner_up: Optional[str]
    peak_delta_db: float
    rationale: str


def determine_worst_case(stats: dict[str, TraceStats]) -> WorstCase:
    """Pick the worst-case configuration and justify it quantitatively."""
    by_peak = max(stats.values(), key=lambda s: s.peak_dbuvm)
    by_mean = max(stats.values(), key=lambda s: s.mean_dbuvm)
    # smallest (most negative) margin = closest to / over the limit
    by_margin = min(
        (s for s in stats.values() if not np.isnan(s.worst_margin_db)),
        key=lambda s: s.worst_margin_db, default=by_peak)

    # Overall worst-case keys off the smallest compliance margin, which is the
    # quantity that actually governs pass/fail.
    worst = by_margin
    others = sorted((s for s in stats.values() if s.name != worst.name),
                    key=lambda s: s.peak_dbuvm, reverse=True)
    runner_up = others[0] if others else None
    peak_delta = (worst.peak_dbuvm - runner_up.peak_dbuvm) if runner_up else 0.0

    parts = [
        f'Configuration “{worst.name}” represents the worst-case EMC '
        f'radiated-emission condition. Its strongest emission reaches '
        f'{worst.peak_dbuvm:.1f} dBµV/m at {worst.peak_freq_mhz:.1f} MHz, '
        f'leaving a worst-case CISPR 11 margin of '
        f'{worst.worst_margin_db:+.1f} dB at '
        f'{worst.worst_margin_freq_mhz:.1f} MHz.'
    ]
    if runner_up is not None:
        parts.append(
            f'This is {peak_delta:+.1f} dB relative to the next-highest '
            f'configuration “{runner_up.name}” '
            f'({runner_up.peak_dbuvm:.1f} dBµV/m at '
            f'{runner_up.peak_freq_mhz:.1f} MHz).')
    if by_peak.name != worst.name:
        parts.append(
            f'Note the absolute peak occurs in “{by_peak.name}” '
            f'({by_peak.peak_dbuvm:.1f} dBµV/m); however “{worst.name}” '
            f'governs because its emission sits closest to the limit line.')
    if worst.broadband_span_mhz > 20:
        parts.append(
            f'It also shows the broadest elevated band '
            f'(~{worst.broadband_span_mhz:.0f} MHz around '
            f'{worst.broadband_center_mhz:.0f} MHz), indicating a broadband '
            f'rather than purely narrowband mechanism.')

    return WorstCase(
        name=worst.name, by_peak=by_peak.name, by_mean=by_mean.name,
        by_margin=by_margin.name, peak_dbuvm=worst.peak_dbuvm,
        runner_up=runner_up.name if runner_up else None,
        peak_delta_db=peak_delta, rationale=' '.join(parts),
    )


# --------------------------------------------------------------------------- #
# 5. Spectral / harmonic interpretation
# --------------------------------------------------------------------------- #

def harmonic_series(peaks: pd.DataFrame, tol_frac: float = 0.03,
                    min_members: int = 3) -> list[dict]:
    """Find approximate harmonic families among a peak list.

    For each candidate fundamental (the lower-frequency peaks), count how many
    other peaks fall near integer multiples within ``tol_frac`` relative
    tolerance. Returns families with at least ``min_members`` members,
    strongest first.
    """
    if len(peaks) < min_members:
        return []
    freqs = np.sort(peaks['freq_mhz'].to_numpy())
    families = []
    for f0 in freqs[freqs < freqs.max() / 2 + 1e-9]:
        members = [f0]
        for fk in freqs:
            if fk <= f0:
                continue
            ratio = fk / f0
            nearest = round(ratio)
            if nearest >= 2 and abs(ratio - nearest) / nearest <= tol_frac:
                members.append(fk)
        if len(members) >= min_members:
            families.append({'fundamental_mhz': float(f0),
                             'n_members': len(members),
                             'members_mhz': [float(m) for m in members]})
    # de-duplicate: keep families whose fundamental is not itself a harmonic
    families.sort(key=lambda d: (-d['n_members'], d['fundamental_mhz']))
    kept: list[dict] = []
    for fam in families:
        if any(abs((fam['fundamental_mhz'] / k['fundamental_mhz'])
                   - round(fam['fundamental_mhz'] / k['fundamental_mhz']))
               <= tol_frac and fam['fundamental_mhz'] > k['fundamental_mhz']
               for k in kept):
            continue
        kept.append(fam)
    return kept[:5]


def spectral_narrative(stats: dict[str, TraceStats], worst: WorstCase) -> str:
    """Engineering-grade prose describing the dominant spectral behaviour."""
    s = stats[worst.name]
    lines = []
    lines.append(
        f'The dominant emission in the worst-case configuration '
        f'(“{worst.name}”) is centred at {s.peak_freq_mhz:.1f} MHz '
        f'({s.peak_dbuvm:.1f} dBµV/m).')

    fams = harmonic_series(s.peaks)
    if fams:
        fam = fams[0]
        members = ', '.join(f'{m:.0f}' for m in fam['members_mhz'][:6])
        lines.append(
            f'A harmonic family is evident on a ~{fam["fundamental_mhz"]:.1f} '
            f'MHz fundamental ({fam["n_members"]} members at {members} MHz), '
            f'consistent with a periodic switching source (e.g. an SMPS or '
            f'digital clock) coupling onto cabling that acts as the radiator.')
    else:
        lines.append(
            'No clean integer-harmonic ladder dominates, pointing to '
            'broadband or resonance-driven coupling rather than a single '
            'clock line.')

    if s.broadband_span_mhz > 20:
        lines.append(
            f'A broadband elevation spans ~{s.broadband_span_mhz:.0f} MHz '
            f'around {s.broadband_center_mhz:.0f} MHz, typical of motor '
            f'commutation, motion-induced contact noise, or wideband digital '
            f'activity rather than a narrowband carrier.')

    # cross-config motion sensitivity: compare peak spread
    peaks = [st.peak_dbuvm for st in stats.values()]
    spread = max(peaks) - min(peaks)
    if spread > 6:
        lines.append(
            f'Peak levels vary by {spread:.1f} dB across configurations, so '
            f'configuration/motion state materially changes the radiated '
            f'profile — the worst case must be captured under the most active '
            f'state, not standby alone.')
    return ' '.join(lines)


# --------------------------------------------------------------------------- #
# 6. Plots
# --------------------------------------------------------------------------- #

_XAXIS = dict(title='Frequency (MHz)', type='log',
              tickvals=[30, 50, 100, 200, 300, 500, 700, 1000],
              range=[np.log10(30), np.log10(1000)],
              showgrid=True, gridcolor='lightgrey')


def plot_overlay(traces: dict[str, pd.DataFrame], cls: str,
                 limit_distance: float) -> go.Figure:
    fig = go.Figure()
    lf, lv = limit_line(cls)
    fig.add_trace(go.Scatter(
        x=lf, y=lv, mode='lines',
        name=f'CISPR 11 Class {cls} @ {limit_distance:.0f} m (QP)',
        line=dict(color='black', dash='dash', width=2)))
    for i, (name, df) in enumerate(traces.items()):
        fig.add_trace(go.Scatter(
            x=df[FREQ_COL], y=df[LEVEL_COL], mode='lines', name=name,
            line=dict(color=PALETTE[i % len(PALETTE)], width=1.3),
            hovertemplate='%{x:.2f} MHz<br>%{y:.2f} dBµV/m<extra>'
                          + name + '</extra>'))
    fig.update_xaxes(**_XAXIS)
    fig.update_yaxes(title='Field strength (dBµV/m, QP)',
                     showgrid=True, gridcolor='lightgrey')
    fig.update_layout(title='Overlay — all configurations vs CISPR 11',
                      template='plotly_white', height=540,
                      legend=dict(orientation='h', y=-0.2, x=0),
                      margin=dict(l=70, r=30, t=70, b=90))
    return fig


def plot_envelope(envelope: pd.DataFrame, cls: str,
                  limit_distance: float) -> go.Figure:
    fig = go.Figure()
    lf, lv = limit_line(cls)
    fig.add_trace(go.Scatter(
        x=lf, y=lv, mode='lines',
        name=f'CISPR 11 Class {cls} @ {limit_distance:.0f} m (QP)',
        line=dict(color='black', dash='dash', width=2)))
    fig.add_trace(go.Scatter(
        x=envelope['freq_mhz'], y=envelope['envelope_dbuvm'],
        mode='lines', name='Worst-case envelope',
        line=dict(color='#d62728', width=1.6),
        customdata=envelope['contributor'],
        hovertemplate='%{x:.2f} MHz<br>%{y:.2f} dBµV/m<br>'
                      'from %{customdata}<extra></extra>'))
    fig.update_xaxes(**_XAXIS)
    fig.update_yaxes(title='Field strength (dBµV/m, QP)',
                     showgrid=True, gridcolor='lightgrey')
    fig.update_layout(title='Worst-case envelope vs CISPR 11',
                      template='plotly_white', height=520,
                      legend=dict(orientation='h', y=-0.2, x=0),
                      margin=dict(l=70, r=30, t=70, b=90))
    return fig


def plot_margin(envelope: pd.DataFrame, cls: str) -> go.Figure:
    f = envelope['freq_mhz'].to_numpy()
    y = envelope['envelope_dbuvm'].to_numpy()
    lim = limit_at(f, cls)
    margin = lim - y
    color = np.where(margin < 0, '#d62728', '#2ca02c')
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=f, y=margin, mode='lines', name='Margin (limit − envelope)',
        line=dict(color='#1f77b4', width=1.3)))
    fig.add_trace(go.Scatter(
        x=f, y=margin, mode='markers', name='', showlegend=False,
        marker=dict(color=color, size=3),
        hovertemplate='%{x:.2f} MHz<br>margin %{y:+.2f} dB<extra></extra>'))
    fig.add_hline(y=0, line=dict(color='black', width=1.5))
    # annotate the closest approach
    valid = ~np.isnan(margin)
    if valid.any():
        wi = int(np.nanargmin(np.where(valid, margin, np.inf)))
        fig.add_annotation(x=np.log10(f[wi]), y=margin[wi],
                           text=f'min margin {margin[wi]:+.1f} dB<br>'
                                f'@ {f[wi]:.0f} MHz',
                           showarrow=True, arrowhead=2, ax=0, ay=-40)
    fig.update_xaxes(**_XAXIS)
    fig.update_yaxes(title='Compliance margin (dB)',
                     showgrid=True, gridcolor='lightgrey', zeroline=True)
    fig.update_layout(title='Compliance margin — worst-case envelope',
                      template='plotly_white', height=480,
                      margin=dict(l=70, r=30, t=70, b=60),
                      legend=dict(orientation='h', y=-0.2, x=0))
    return fig


def plot_zoom(traces: dict[str, pd.DataFrame], center_mhz: float,
              cls: str, half_decade: float = 0.35) -> go.Figure:
    """Linear-axis zoom centred on the dominant emission band."""
    lo = max(30.0, center_mhz / (10 ** half_decade))
    hi = min(1000.0, center_mhz * (10 ** half_decade))
    fig = go.Figure()
    lf, lv = limit_line(cls)
    fig.add_trace(go.Scatter(x=lf, y=lv, mode='lines',
                             name=f'CISPR 11 Class {cls}',
                             line=dict(color='black', dash='dash', width=2)))
    for i, (name, df) in enumerate(traces.items()):
        m = (df[FREQ_COL] >= lo) & (df[FREQ_COL] <= hi)
        fig.add_trace(go.Scatter(
            x=df[FREQ_COL][m], y=df[LEVEL_COL][m], mode='lines', name=name,
            line=dict(color=PALETTE[i % len(PALETTE)], width=1.5)))
    fig.update_xaxes(title='Frequency (MHz)', range=[lo, hi],
                     showgrid=True, gridcolor='lightgrey')
    fig.update_yaxes(title='Field strength (dBµV/m, QP)',
                     showgrid=True, gridcolor='lightgrey')
    fig.update_layout(
        title=f'Critical region {lo:.0f}–{hi:.0f} MHz '
              f'(centred on {center_mhz:.0f} MHz)',
        template='plotly_white', height=460,
        legend=dict(orientation='h', y=-0.22, x=0),
        margin=dict(l=70, r=30, t=70, b=90))
    return fig


def plot_summary_bars(stats: dict[str, TraceStats]) -> go.Figure:
    names = list(stats)
    peaks = [stats[n].peak_dbuvm for n in names]
    means = [stats[n].mean_dbuvm for n in names]
    margins = [stats[n].worst_margin_db for n in names]
    fig = make_subplots(rows=1, cols=3, subplot_titles=(
        'Peak (dBµV/m)', 'Mean (dBµV/m)', 'Worst margin (dB)'))
    fig.add_trace(go.Bar(x=names, y=peaks, marker_color='#d62728',
                         showlegend=False), row=1, col=1)
    fig.add_trace(go.Bar(x=names, y=means, marker_color='#1f77b4',
                         showlegend=False), row=1, col=2)
    fig.add_trace(go.Bar(x=names, y=margins,
                         marker_color=['#d62728' if m < 0 else '#2ca02c'
                                       for m in margins],
                         showlegend=False), row=1, col=3)
    fig.update_layout(title='Per-configuration summary',
                      template='plotly_white', height=440,
                      margin=dict(l=60, r=20, t=80, b=120))
    fig.update_xaxes(tickangle=-35)
    return fig


# --------------------------------------------------------------------------- #
# 7. Executive summary + full report assembly
# --------------------------------------------------------------------------- #

def executive_summary(stats: dict[str, TraceStats], worst: WorstCase,
                      margin: MarginResult, cls: str) -> str:
    n = len(stats)
    verdict = ('exceeds' if margin.worst_margin_db < 0 else 'remains within')
    p1 = (
        f'This report analyses {n} radiated-emission configuration'
        f'{"s" if n != 1 else ""} against the CISPR 11 Group 1, Class {cls} '
        f'quasi-peak limit. The worst-case envelope across all configurations '
        f'{verdict} the applicable limit, with a minimum compliance margin of '
        f'{margin.worst_margin_db:+.1f} dB at '
        f'{margin.worst_margin_freq_mhz:.1f} MHz '
        f'(driven by “{margin.worst_contributor}”).')
    p2 = (
        f'Configuration “{worst.name}” is the governing worst case. '
        + worst.rationale.split('. ', 1)[-1])
    if margin.n_exceed_points > 0:
        p3 = (
            f'The envelope crosses the limit over approximately '
            f'{margin.exceed_bandwidth_mhz:.1f} MHz of aggregate bandwidth. '
            f'Mitigation should target the dominant emission and its coupling '
            f'path before formal compliance testing.')
    else:
        p3 = (
            f'No configuration exceeds the limit in this pre-compliance scan; '
            f'however the {abs(margin.worst_margin_db):.1f} dB closest '
            f'approach is below a typical 6 dB pre-compliance guard band, so '
            f'design margin remains a watch item ahead of accredited testing.')
    return '\n\n'.join([p1, p2, p3])


def _fig_html(fig: go.Figure, div_id: str, first: bool) -> str:
    return pio.to_html(fig, include_plotlyjs='cdn' if first else False,
                       full_html=False, div_id=div_id)


def _df_to_html(df: pd.DataFrame, floatfmt: str = '{:.2f}') -> str:
    return df.to_html(index=False, border=0,
                      float_format=lambda v: floatfmt.format(v),
                      classes='emc-table', escape=True)


@dataclass
class ReportInputs:
    title: str
    traces: dict[str, pd.DataFrame]
    cls: str
    meas_distance: float
    limit_distance: float
    prominence_db: float = 6.0
    min_sep_mhz: float = 2.0
    prepared_by: str = ''
    reviewed_by: str = ''
    approved_by: str = ''
    revision: str = 'A'


@dataclass
class AnalysisBundle:
    validations: dict[str, ValidationResult]
    stats: dict[str, TraceStats]
    envelope: pd.DataFrame
    margin: MarginResult
    worst: WorstCase
    figures: dict[str, go.Figure]
    summary_table: pd.DataFrame


def run_analysis(inp: ReportInputs) -> AnalysisBundle:
    """Execute the full analysis workflow and return every artefact."""
    validations = {n: validate_trace(n, df) for n, df in inp.traces.items()}
    usable = {n: df for n, df in inp.traces.items() if validations[n].usable}
    if not usable:
        raise ValueError('No usable traces after validation.')

    stats = {n: trace_statistics(n, df, inp.cls, inp.prominence_db,
                                 inp.min_sep_mhz)
             for n, df in usable.items()}
    envelope = worst_case_envelope(usable)
    margin = margin_analysis(envelope, inp.cls)
    worst = determine_worst_case(stats)

    center = stats[worst.name].peak_freq_mhz
    figures = {
        'overlay': plot_overlay(usable, inp.cls, inp.limit_distance),
        'envelope': plot_envelope(envelope, inp.cls, inp.limit_distance),
        'margin': plot_margin(envelope, inp.cls),
        'zoom': plot_zoom(usable, center, inp.cls),
        'bars': plot_summary_bars(stats),
    }
    return AnalysisBundle(
        validations=validations, stats=stats, envelope=envelope,
        margin=margin, worst=worst, figures=figures,
        summary_table=stats_table(stats),
    )


def build_report_html(inp: ReportInputs, bundle: AnalysisBundle) -> str:
    """Assemble the structured, self-contained HTML compliance report."""
    stamp = datetime.now().strftime('%Y-%m-%d %H:%M')
    esc = html.escape

    # --- validation table ---
    vrows = []
    for v in bundle.validations.values():
        status = ('OK' if v.usable and not v.flags else
                  'FLAGGED' if v.usable else 'EXCLUDED')
        notes = '; '.join(v.issues + v.flags) or '—'
        vrows.append(
            f'<tr><td>{esc(v.name)}</td><td>{v.n_points}</td>'
            f'<td>{v.f_min:.0f}–{v.f_max:.0f}</td>'
            f'<td>{v.median_step_mhz:.3f}</td>'
            f'<td>{status}</td><td>{esc(notes)}</td></tr>')
    validation_rows = '\n'.join(vrows)

    summary_html = _df_to_html(bundle.summary_table)

    # --- per-config peak tables ---
    peak_sections = []
    for name, s in bundle.stats.items():
        if len(s.peaks):
            top = s.peaks.head(12)[
                ['freq_mhz', 'level_dbuvm', 'prominence_db',
                 'limit_dbuvm', 'margin_db']].copy()
            peak_sections.append(
                f'<h3>{esc(name)}</h3>' + _df_to_html(top))
        else:
            peak_sections.append(
                f'<h3>{esc(name)}</h3><p>No peaks above the '
                f'{inp.prominence_db:.0f} dB prominence threshold.</p>')
    peaks_html = '\n'.join(peak_sections)

    spectral = spectral_narrative(bundle.stats, bundle.worst)
    exec_sum = executive_summary(bundle.stats, bundle.worst,
                                 bundle.margin, inp.cls)

    # --- risks / uncertainties ---
    risks = []
    for v in bundle.validations.values():
        for issue in v.issues:
            risks.append(f'<li><strong>{esc(v.name)} (excluded):</strong> '
                         f'{esc(issue)}</li>')
        for flag in v.flags:
            risks.append(f'<li><strong>{esc(v.name)}:</strong> '
                         f'{esc(flag)}</li>')
    risks.append(
        '<li><strong>Distance extrapolation:</strong> levels were captured at '
        f'{inp.meas_distance:.1f} m and normalised to {inp.limit_distance:.0f} '
        'm via a 20·log₁₀(d) far-field correction; near-field effects at the '
        'measurement distance add uncertainty.</li>')
    risks.append(
        '<li><strong>Pre-compliance setup:</strong> open-area/lab scans are '
        'not a substitute for accredited OATS/SAC quasi-peak measurement; '
        'apply the lab measurement-uncertainty budget before a pass/fail '
        'declaration.</li>')
    risks_html = '\n'.join(risks)

    # --- reviewer block ---
    reviewer_rows = ''
    if any([inp.prepared_by, inp.reviewed_by, inp.approved_by]):
        reviewer_rows = f"""
<table class="emc-table">
 <tr><th>Prepared by</th><td>{esc(inp.prepared_by) or '&nbsp;'}</td></tr>
 <tr><th>Reviewed by</th><td>{esc(inp.reviewed_by) or '&nbsp;'}</td></tr>
 <tr><th>Approved by</th><td>{esc(inp.approved_by) or '&nbsp;'}</td></tr>
</table>"""

    config_li = '\n'.join(f'<li>{esc(n)}</li>' for n in inp.traces)

    f = bundle.figures
    fig_overlay = _fig_html(f['overlay'], 'overlay', first=True)
    fig_env = _fig_html(f['envelope'], 'envelope', first=False)
    fig_margin = _fig_html(f['margin'], 'margin', first=False)
    fig_zoom = _fig_html(f['zoom'], 'zoom', first=False)
    fig_bars = _fig_html(f['bars'], 'bars', first=False)

    m = bundle.margin
    w = bundle.worst

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{esc(inp.title)}</title>
<style>
 body {{ font-family: 'Segoe UI', Roboto, -apple-system, sans-serif;
         max-width: 1200px; margin: 0 auto; padding: 2em 1.5em; color: #1a1a1a;
         line-height: 1.5; }}
 h1 {{ font-size: 1.9em; margin-bottom: 0.1em; }}
 h2 {{ border-bottom: 2px solid #d62728; padding-bottom: 0.2em;
       margin-top: 2em; }}
 h3 {{ margin-top: 1.4em; color: #333; }}
 .meta {{ color: #555; font-size: 0.92em; }}
 .titlepage {{ border: 1px solid #ddd; border-radius: 8px; padding: 1.5em 2em;
               background: #fafafa; margin-bottom: 1em; }}
 .verdict {{ font-size: 1.05em; padding: 0.8em 1em; border-radius: 6px;
             margin: 1em 0; }}
 .pass {{ background: #e8f5e9; border-left: 5px solid #2ca02c; }}
 .fail {{ background: #fdeaea; border-left: 5px solid #d62728; }}
 table.emc-table {{ border-collapse: collapse; width: 100%; margin: 1em 0;
                    font-size: 0.9em; }}
 table.emc-table th, table.emc-table td {{ border: 1px solid #ddd;
       padding: 6px 10px; text-align: right; }}
 table.emc-table th {{ background: #f0f0f0; text-align: left; }}
 table.emc-table td:first-child, table.emc-table th:first-child
       {{ text-align: left; }}
 ul {{ margin-top: 0.3em; }}
 .toc a {{ text-decoration: none; color: #1f77b4; }}
 footer {{ margin-top: 3em; color: #888; font-size: 0.82em;
           border-top: 1px solid #eee; padding-top: 1em; }}
</style>
</head>
<body>

<div class="titlepage">
<h1>{esc(inp.title)}</h1>
<p class="meta">EMC radiated-emission pre-compliance analysis ·
  CISPR 11 Group 1, Class {inp.cls} (quasi-peak)<br>
  Measurement distance {inp.meas_distance:.1f} m, normalised to
  {inp.limit_distance:.0f} m · Generated {stamp} · Revision {esc(inp.revision)}
</p>
{reviewer_rows}
</div>

<div class="verdict {'fail' if m.worst_margin_db < 0 else 'pass'}">
<strong>Headline:</strong> worst-case envelope minimum margin
<strong>{m.worst_margin_db:+.1f} dB</strong> at
{m.worst_margin_freq_mhz:.1f} MHz
({'EXCEEDS LIMIT' if m.worst_margin_db < 0 else 'within limit'},
driven by “{esc(m.worst_contributor)}”).
</div>

<h2 id="revhist">1. Revision history</h2>
<table class="emc-table">
 <tr><th>Rev</th><th>Date</th><th>Description</th></tr>
 <tr><td>{esc(inp.revision)}</td><td>{stamp}</td>
     <td>Automated EMC analysis of {len(inp.traces)} configuration(s).</td></tr>
</table>

<h2 id="exec">2. Executive summary</h2>
{''.join(f'<p>{esc(p)}</p>' for p in exec_sum.split(chr(10)+chr(10)))}

<h2 id="method">3. Methodology</h2>
<p>Each configuration was captured as a Rohde &amp; Schwarz EMI scan
(quasi-peak, transducer-corrected field strength). Levels were normalised
from the {inp.meas_distance:.1f} m measurement distance to the
{inp.limit_distance:.0f} m CISPR 11 reference distance using an inverse-distance
far-field correction of 20·log₁₀(d<sub>meas</sub>/d<sub>limit</sub>). Peaks
were identified by prominence (&ge; {inp.prominence_db:.0f} dB, &ge;
{inp.min_sep_mhz:.0f} MHz separation). A point-by-point worst-case envelope
was formed across all valid configurations and compared to the Class
{inp.cls} limit.</p>

<h2 id="configs">4. Test configurations</h2>
<ul>{config_li}</ul>

<h2 id="validation">5. Data validation</h2>
<table class="emc-table">
 <tr><th>Configuration</th><th>Points</th><th>Range (MHz)</th>
     <th>Median step (MHz)</th><th>Status</th><th>Notes</th></tr>
 {validation_rows}
</table>

<h2 id="results">6. Results — per-configuration statistics</h2>
{summary_html}

<h2 id="plots">7. Plots</h2>
<h3>7.1 Overlay — all configurations</h3>
{fig_overlay}
<h3>7.2 Worst-case envelope</h3>
{fig_env}
<h3>7.3 Compliance margin</h3>
{fig_margin}
<h3>7.4 Critical region (zoom)</h3>
{fig_zoom}
<h3>7.5 Summary comparison</h3>
{fig_bars}

<h2 id="margin">8. Margin analysis</h2>
<ul>
 <li>Worst-case (smallest) margin: <strong>{m.worst_margin_db:+.1f} dB</strong>
     at {m.worst_margin_freq_mhz:.1f} MHz (from “{esc(m.worst_contributor)}”).</li>
 <li>Best-case margin: {m.best_margin_db:+.1f} dB at
     {m.best_margin_freq_mhz:.1f} MHz.</li>
 <li>Aggregate bandwidth above limit: {m.exceed_bandwidth_mhz:.1f} MHz
     ({m.n_exceed_points} envelope points).</li>
</ul>

<h2 id="peaks">9. Peak tables (top peaks per configuration)</h2>
{peaks_html}

<h2 id="discussion">10. Technical discussion</h2>
<p>{esc(spectral)}</p>

<h2 id="worstcase">11. Worst-case determination</h2>
<p>{esc(w.rationale)}</p>
<p class="meta">Cross-check — highest absolute peak: “{esc(w.by_peak)}”;
highest mean spectrum: “{esc(w.by_mean)}”; smallest compliance margin:
“{esc(w.by_margin)}”.</p>

<h2 id="risks">12. Risks &amp; uncertainties</h2>
<ul>
{risks_html}
</ul>

<h2 id="conclusion">13. Conclusion</h2>
<p>{esc(w.rationale)} {'The worst-case envelope exceeds the Class '
   + inp.cls + ' limit and corrective action is required before formal '
   'testing.' if m.worst_margin_db < 0 else 'The worst-case envelope remains '
   'within the Class ' + inp.cls + ' limit in this pre-compliance scan, but '
   'the closest approach should be re-verified in an accredited facility with '
   'the full measurement-uncertainty budget applied.'}</p>

<footer>
Generated by the project EMC analysis engine on {stamp}. Pre-compliance
indicative analysis only — not a substitute for accredited CISPR 11
measurement. CISPR 11 limits shown are Group 1, Class {inp.cls}, quasi-peak.
</footer>
</body>
</html>
"""
