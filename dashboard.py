"""Interactive Streamlit dashboard for ad-hoc EMI trace comparisons.

Pick one trace as the *reference* and one-or-more comparison traces; the
dashboard renders an overlay + Δ-vs-reference plot and lets you download
a standalone HTML report bundling both.

Run with:

    py -3 -m streamlit run dashboard.py

The trace format is the same Rohde & Schwarz `% [Header]` / 3-column text
file used by the main notebook, so any `.txt` under ``data/`` works.
"""

from __future__ import annotations

import io
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import plotly.io as pio
import streamlit as st
from plotly.subplots import make_subplots

import emc_analysis as emc

DATA_ROOT = Path('data')

# CISPR 11 Group 1 radiated QP limits referenced to a 10 m measurement distance.
CISPR11_LIMITS = {
    'A': [(30.0, 230.0, 40.0), (230.0, 1000.0, 47.0)],
    'B': [(30.0, 230.0, 30.0), (230.0, 1000.0, 37.0)],
}

PALETTE = ['#d62728', '#2ca02c', '#9467bd', '#ff7f0e',
           '#17becf', '#e377c2', '#8c564b', '#bcbd22']


# --------------------------------------------------------------------------- #
# Data loading
# --------------------------------------------------------------------------- #

def load_trace(source, dist_corr_db: float) -> pd.DataFrame:
    """Read a Rohde & Schwarz EMI scan (% header, 3 columns) into a DataFrame.

    ``source`` may be a filesystem path or a file-like object (Streamlit
    upload). Distance correction is applied once at load time.
    """
    df = pd.read_csv(
        source, sep=r'\s+', comment='%',
        names=['freq_hz', 'mag_raw_dbuv', 'mag_field_dbuvm'],
        engine='python', skip_blank_lines=True,
    ).dropna().reset_index(drop=True)
    df['freq_mhz'] = df['freq_hz'] / 1e6
    df['field_corr_dbuvm'] = df['mag_field_dbuvm'] + dist_corr_db
    return df


def discover_trace_files(root: Path) -> list[Path]:
    """All R&S scan .txt files under root, excluding `_Att.txt` auxiliaries."""
    return sorted(p for p in root.rglob('*.txt')
                  if not p.name.endswith('_Att.txt'))


def limit_line(cls: str) -> tuple[np.ndarray, np.ndarray]:
    f = np.array([30, 230, 230, 1000], dtype=float)
    v = np.array([s[2] for s in CISPR11_LIMITS[cls] for _ in (0, 1)],
                 dtype=float)
    return f, v


# --------------------------------------------------------------------------- #
# Plot builders
# --------------------------------------------------------------------------- #

def build_overlay(ref_name: str, ref_df: pd.DataFrame,
                  comparisons: dict[str, pd.DataFrame],
                  cispr_class: str, limit_distance: float) -> go.Figure:
    fig = go.Figure()

    lim_f, lim_v = limit_line(cispr_class)
    fig.add_trace(go.Scatter(
        x=lim_f, y=lim_v, mode='lines',
        name=f'CISPR 11 Class {cispr_class} @ {limit_distance:.0f} m (QP)',
        line=dict(color='black', dash='dash', width=2),
        hovertemplate='Limit: %{y:.0f} dBµV/m<extra></extra>',
    ))

    fig.add_trace(go.Scatter(
        x=ref_df['freq_mhz'], y=ref_df['field_corr_dbuvm'],
        mode='lines', name=f'{ref_name} (reference)',
        line=dict(color='#1f77b4', width=1.6),
        hovertemplate='%{x:.2f} MHz<br>%{y:.2f} dBµV/m<extra>'
                      + ref_name + '</extra>',
    ))

    for i, (name, df) in enumerate(comparisons.items()):
        color = PALETTE[i % len(PALETTE)]
        fig.add_trace(go.Scatter(
            x=df['freq_mhz'], y=df['field_corr_dbuvm'],
            mode='lines', name=name,
            line=dict(color=color, width=1.2),
            hovertemplate='%{x:.2f} MHz<br>%{y:.2f} dBµV/m<extra>'
                          + name + '</extra>',
        ))

    fig.update_xaxes(
        title='Frequency (MHz)', type='log',
        tickvals=[30, 50, 100, 200, 300, 500, 700, 1000],
        range=[np.log10(30), np.log10(1000)],
        showgrid=True, gridcolor='lightgrey',
    )
    fig.update_yaxes(title='Field strength (dBµV/m, QP)',
                     showgrid=True, gridcolor='lightgrey')
    fig.update_layout(
        title=f'Overlay — reference: {ref_name}',
        template='plotly_white', height=560,
        legend=dict(orientation='h', y=-0.18, x=0.0),
        margin=dict(l=70, r=30, t=70, b=80),
    )
    return fig


def build_delta(ref_name: str, ref_df: pd.DataFrame,
                comparisons: dict[str, pd.DataFrame],
                top_n: int) -> tuple[go.Figure, pd.DataFrame]:
    f_grid = ref_df['freq_mhz'].to_numpy()
    base_y = ref_df['field_corr_dbuvm'].to_numpy()

    items = list(comparisons.items())

    # Shared y-range across subplots so subplots are visually comparable.
    all_deltas = np.concatenate([
        np.interp(f_grid, df['freq_mhz'], df['field_corr_dbuvm']) - base_y
        for _, df in items
    ])
    ymin = float(np.min(all_deltas))
    ymax = float(np.max(all_deltas))
    pad = max(2.0, 0.08 * (ymax - ymin))
    yrange = [ymin - pad, ymax + pad]

    fig = make_subplots(
        rows=len(items), cols=1, shared_xaxes=True, shared_yaxes=True,
        vertical_spacing=0.06,
        subplot_titles=[f'Δ = {n} − {ref_name}' for n, _ in items],
    )

    rows = []
    for i, (name, df) in enumerate(items, start=1):
        color = PALETTE[(i - 1) % len(PALETTE)]
        y = np.interp(f_grid, df['freq_mhz'], df['field_corr_dbuvm'])
        delta = y - base_y

        pos = np.clip(delta, 0, None)
        rgb = (int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16))
        fig.add_trace(go.Scatter(
            x=f_grid, y=pos, mode='lines',
            line=dict(color=color, width=0),
            fill='tozeroy',
            fillcolor=f'rgba({rgb[0]},{rgb[1]},{rgb[2]},0.22)',
            hoverinfo='skip', showlegend=False,
        ), row=i, col=1)
        fig.add_trace(go.Scatter(
            x=f_grid, y=delta, mode='lines',
            line=dict(color=color, width=1.3), name=name,
            hovertemplate='%{x:.2f} MHz<br>Δ = %{y:+.2f} dB<extra>'
                          + name + '</extra>',
            showlegend=False,
        ), row=i, col=1)
        fig.add_hline(y=0, line=dict(color='black', width=1),
                      row=i, col=1)

        idx_top = np.argsort(delta)[-top_n:][::-1]
        fig.add_trace(go.Scatter(
            x=f_grid[idx_top], y=delta[idx_top],
            mode='markers+text',
            marker=dict(color=color, size=8, symbol='triangle-up',
                        line=dict(color='black', width=0.6)),
            text=[f'{f:.0f}' for f in f_grid[idx_top]],
            textposition='top center', textfont=dict(size=9),
            showlegend=False, hoverinfo='skip',
        ), row=i, col=1)

        rows.append(pd.DataFrame({
            'freq_mhz': f_grid,
            f'{ref_name}_dbuvm': base_y,
            f'{name}_dbuvm': y,
            'delta_db': delta,
            'comparison': name,
        }))

    fig.update_xaxes(
        type='log',
        tickvals=[30, 50, 100, 200, 300, 500, 700, 1000],
        range=[np.log10(30), np.log10(1000)],
        showgrid=True, gridcolor='lightgrey',
    )
    fig.update_xaxes(title='Frequency (MHz)', row=len(items), col=1)
    fig.update_yaxes(title='Δ (dB)', showgrid=True, gridcolor='lightgrey',
                     zeroline=True, zerolinecolor='black', range=yrange)
    fig.update_layout(
        title=f'Contribution above reference “{ref_name}”',
        template='plotly_white', height=260 * len(items) + 80,
        margin=dict(l=70, r=30, t=80, b=60), showlegend=False,
    )
    return fig, pd.concat(rows, ignore_index=True)


# --------------------------------------------------------------------------- #
# Report builder
# --------------------------------------------------------------------------- #

def build_html_report(overlay_fig: go.Figure, delta_fig: go.Figure,
                      ref_name: str, comparison_names: list[str],
                      cispr_class: str, meas_distance: float,
                      limit_distance: float) -> str:
    overlay_html = pio.to_html(overlay_fig, include_plotlyjs='cdn',
                               full_html=False, div_id='overlay')
    delta_html = pio.to_html(delta_fig, include_plotlyjs=False,
                             full_html=False, div_id='delta')
    stamp = datetime.now().strftime('%Y-%m-%d %H:%M')
    comparisons_li = '\n'.join(f'<li>{n}</li>' for n in comparison_names)

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>EMI custom report — reference: {ref_name}</title>
<style>
 body {{ font-family: -apple-system, Segoe UI, Roboto, sans-serif;
        max-width: 1200px; margin: 1.5em auto; padding: 0 1em;
        color: #222; }}
 h1 {{ margin-bottom: 0; }}
 .meta {{ color: #666; font-size: 0.9em; margin-bottom: 1.2em; }}
 ul {{ margin-top: 0.2em; }}
 hr {{ border: 0; border-top: 1px solid #ddd; margin: 2em 0; }}
</style>
</head>
<body>
<h1>EMI custom comparison report</h1>
<p class="meta">Generated {stamp} · CISPR 11 Class {cispr_class} ·
   measurement distance {meas_distance:.1f} m,
   normalised to {limit_distance:.0f} m.</p>
<p><strong>Reference:</strong> {ref_name}</p>
<p><strong>Comparisons:</strong></p>
<ul>
{comparisons_li}
</ul>
<hr>
<h2>Overlay vs. CISPR 11 limit</h2>
{overlay_html}
<hr>
<h2>Δ vs. reference (shared y-axis)</h2>
{delta_html}
</body>
</html>
"""


# --------------------------------------------------------------------------- #
# Streamlit UI
# --------------------------------------------------------------------------- #

def page_comparison() -> None:
    st.title('EMI trace comparison dashboard')
    st.caption('Pick one reference trace and any number of comparisons; '
               'the dashboard renders an overlay, Δ-vs-reference subplots, '
               'and offers the bundle as a downloadable HTML report.')

    # ---- Sidebar: knobs --------------------------------------------------
    with st.sidebar:
        st.header('Settings')
        cispr_class = st.radio('CISPR 11 class', ['A', 'B'], horizontal=True)
        meas_distance = st.number_input(
            'Measurement distance (m)', min_value=0.5, max_value=20.0,
            value=3.0, step=0.5)
        limit_distance = st.number_input(
            'Limit reference distance (m)', min_value=1.0, max_value=30.0,
            value=10.0, step=1.0)
        top_n = st.slider('Top-N Δ peaks per comparison', 3, 30, 12)
        source_mode = st.radio(
            'Trace source', ['Workspace `data/`', 'Upload'],
            help='Workspace mode globs every `.txt` under data/. '
                 'Upload mode lets you bring your own R&S scans.')

    dist_corr_db = 20.0 * np.log10(meas_distance / limit_distance)
    st.sidebar.caption(f'Distance correction applied: '
                       f'{dist_corr_db:+.2f} dB')

    # ---- Trace selection -------------------------------------------------
    ref_source = None
    ref_label = None
    comparison_sources: dict[str, object] = {}

    if source_mode == 'Workspace `data/`':
        files = discover_trace_files(DATA_ROOT)
        if not files:
            st.error(f'No `.txt` trace files found under {DATA_ROOT}/.')
            return
        labels = [str(p.relative_to(DATA_ROOT)) for p in files]
        path_for_label = dict(zip(labels, files))

        col1, col2 = st.columns([1, 2])
        with col1:
            ref_label = st.selectbox('Reference trace', labels,
                                     index=0, key='ref_workspace')
            ref_source = path_for_label[ref_label]
        with col2:
            comp_labels = st.multiselect(
                'Comparison traces',
                [lab for lab in labels if lab != ref_label],
                key='comps_workspace')
            comparison_sources = {lab: path_for_label[lab]
                                  for lab in comp_labels}
    else:
        col1, col2 = st.columns(2)
        with col1:
            ref_upload = st.file_uploader(
                'Reference trace (.txt)', type=['txt'],
                accept_multiple_files=False, key='ref_upload')
            if ref_upload is not None:
                ref_label = ref_upload.name
                ref_source = ref_upload
        with col2:
            comp_uploads = st.file_uploader(
                'Comparison traces (.txt, multiple)', type=['txt'],
                accept_multiple_files=True, key='comps_upload')
            comparison_sources = {f.name: f for f in (comp_uploads or [])}

    if ref_source is None:
        st.info('Pick a reference trace to begin.')
        return
    if not comparison_sources:
        st.info('Pick one or more comparison traces.')
        return

    # ---- Load + plot -----------------------------------------------------
    try:
        ref_df = load_trace(ref_source, dist_corr_db)
        comparisons = {name: load_trace(src, dist_corr_db)
                       for name, src in comparison_sources.items()}
    except Exception as exc:  # noqa: BLE001
        st.error(f'Failed to read a trace: {exc}')
        return

    overlay_fig = build_overlay(ref_label, ref_df, comparisons,
                                cispr_class, limit_distance)
    delta_fig, delta_df = build_delta(ref_label, ref_df, comparisons, top_n)

    tab_overlay, tab_delta, tab_table = st.tabs(
        ['Overlay', 'Δ vs. reference', 'Top-Δ peaks'])
    with tab_overlay:
        st.plotly_chart(overlay_fig, use_container_width=True)
    with tab_delta:
        st.plotly_chart(delta_fig, use_container_width=True)
    with tab_table:
        for name in comparisons:
            sub = (delta_df[delta_df['comparison'] == name]
                   .nlargest(top_n, 'delta_db')
                   [['freq_mhz', f'{ref_label}_dbuvm',
                     f'{name}_dbuvm', 'delta_db']]
                   .reset_index(drop=True))
            st.markdown(f'**{name}** — top {top_n} Δ peaks')
            st.dataframe(sub.style.format({
                'freq_mhz': '{:.2f}',
                f'{ref_label}_dbuvm': '{:.2f}',
                f'{name}_dbuvm': '{:.2f}',
                'delta_db': '{:+.2f}',
            }), use_container_width=True)

    # ---- Download report -------------------------------------------------
    html = build_html_report(overlay_fig, delta_fig, ref_label,
                             list(comparisons), cispr_class,
                             meas_distance, limit_distance)
    csv_bytes = delta_df.to_csv(index=False).encode('utf-8')
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')

    c1, c2 = st.columns(2)
    with c1:
        st.download_button(
            'Download HTML report', data=html,
            file_name=f'emi_custom_report_{stamp}.html',
            mime='text/html', use_container_width=True)
    with c2:
        st.download_button(
            'Download Δ data (CSV)', data=csv_bytes,
            file_name=f'emi_custom_delta_{stamp}.csv',
            mime='text/csv', use_container_width=True)


# --------------------------------------------------------------------------- #
# EMC compliance analysis page
# --------------------------------------------------------------------------- #

def _select_configs() -> tuple[dict[str, object], dict]:
    """Sidebar/source controls returning {label: source} + load knobs."""
    with st.sidebar:
        st.header('Settings')
        cispr_class = st.radio('CISPR 11 class', ['A', 'B'], horizontal=True,
                               key='emc_class')
        meas_distance = st.number_input(
            'Measurement distance (m)', min_value=0.5, max_value=20.0,
            value=3.0, step=0.5, key='emc_meas')
        limit_distance = st.number_input(
            'Limit reference distance (m)', min_value=1.0, max_value=30.0,
            value=10.0, step=1.0, key='emc_limit')
        prominence = st.slider('Peak prominence (dB)', 3.0, 20.0, 6.0, 0.5,
                               key='emc_prom')
        min_sep = st.slider('Min peak separation (MHz)', 0.5, 10.0, 2.0, 0.5,
                            key='emc_sep')
        st.markdown('**Risk model**')
        sigma_db = st.number_input(
            'Measurement uncertainty σ (dB)', min_value=0.0, max_value=10.0,
            value=float(emc.DEFAULT_SIGMA_DB), step=0.1, key='emc_sigma',
            help='1σ standard uncertainty. Default 3.0 dB assumes a typical '
                 'U≈6 dB (k=2) accredited radiated-emission budget — override '
                 'with your lab\'s value.')
        guard_db = st.number_input(
            'Guard band (dB)', min_value=0.0, max_value=20.0,
            value=float(emc.DEFAULT_GUARD_DB), step=0.5, key='emc_guard',
            help='Margin below the limit counted as "near-limit" for the '
                 'peak-in-guard and near-limit-bandwidth metrics.')
        source_mode = st.radio('Trace source',
                               ['Workspace `data/`', 'Upload'], key='emc_src')

    dist_corr_db = 20.0 * np.log10(meas_distance / limit_distance)
    st.sidebar.caption(f'Distance correction applied: {dist_corr_db:+.2f} dB')

    sources: dict[str, object] = {}
    if source_mode == 'Workspace `data/`':
        files = discover_trace_files(DATA_ROOT)
        if not files:
            st.error(f'No `.txt` trace files found under {DATA_ROOT}/.')
            return {}, {}
        labels = [str(p.relative_to(DATA_ROOT)) for p in files]
        path_for_label = dict(zip(labels, files))
        picked = st.multiselect(
            'Configurations to analyse (pick 2 or more)', labels,
            key='emc_pick')
        sources = {lab: path_for_label[lab] for lab in picked}
    else:
        uploads = st.file_uploader(
            'Configuration traces (.txt, multiple)', type=['txt'],
            accept_multiple_files=True, key='emc_upload')
        sources = {f.name: f for f in (uploads or [])}

    return sources, dict(cispr_class=cispr_class, meas_distance=meas_distance,
                         limit_distance=limit_distance, dist_corr_db=dist_corr_db,
                         prominence=prominence, min_sep=min_sep,
                         sigma_db=sigma_db, guard_db=guard_db)


def page_emc_analysis() -> None:
    st.title('EMC compliance analysis')
    st.caption('Full IEC 60601-1-2 / CISPR 11 style workflow: validation, '
               'per-configuration statistics, worst-case envelope, margin '
               'analysis, spectral interpretation and a structured, '
               'downloadable HTML report.')

    sources, knobs = _select_configs()
    if len(sources) < 2:
        st.info('Pick at least two configurations to compare.')
        return

    with st.expander('Report metadata (optional)'):
        m1, m2 = st.columns(2)
        with m1:
            title = st.text_input('Report title',
                                   value='EMC radiated-emission analysis')
            revision = st.text_input('Revision', value='A')
            prepared_by = st.text_input('Prepared by')
        with m2:
            reviewed_by = st.text_input('Reviewed by')
            approved_by = st.text_input('Approved by')

    try:
        traces = {name: load_trace(src, knobs['dist_corr_db'])
                  for name, src in sources.items()}
        headers = {name: emc.parse_rs_header(src)
                   for name, src in sources.items()}
    except Exception as exc:  # noqa: BLE001
        st.error(f'Failed to read a trace: {exc}')
        return

    inp = emc.ReportInputs(
        title=title, traces=traces, cls=knobs['cispr_class'],
        meas_distance=knobs['meas_distance'],
        limit_distance=knobs['limit_distance'],
        prominence_db=knobs['prominence'], min_sep_mhz=knobs['min_sep'],
        sigma_db=knobs['sigma_db'], guard_db=knobs['guard_db'],
        prepared_by=prepared_by, reviewed_by=reviewed_by,
        approved_by=approved_by, revision=revision, headers=headers)

    try:
        bundle = emc.run_analysis(inp)
    except Exception as exc:  # noqa: BLE001
        st.error(f'Analysis failed: {exc}')
        return

    m = bundle.margin
    if m.worst_margin_db < 0:
        st.error(f'Worst-case envelope EXCEEDS the limit by '
                 f'{-m.worst_margin_db:.1f} dB at '
                 f'{m.worst_margin_freq_mhz:.1f} MHz '
                 f'(from “{m.worst_contributor}”).')
    else:
        st.success(f'Worst-case envelope within limit — minimum margin '
                   f'{m.worst_margin_db:+.1f} dB at '
                   f'{m.worst_margin_freq_mhz:.1f} MHz.')

    tabs = st.tabs(['Summary', 'Validation', 'Plots', 'Peaks',
                    'Worst case'])
    with tabs[0]:
        st.dataframe(bundle.summary_table, use_container_width=True)
    with tabs[1]:
        vrows = [{
            'Configuration': v.name, 'Points': v.n_points,
            'Range (MHz)': f'{v.f_min:.0f}–{v.f_max:.0f}',
            'Step (MHz)': round(v.median_step_mhz, 3),
            'OverRange': ('YES' if v.header['overrange'] else 'No')
                         if 'overrange' in v.header else '—',
            'Status': 'OK' if v.usable and not v.flags else
                      ('FLAGGED' if v.usable else 'EXCLUDED'),
            'Notes': '; '.join(v.issues + v.flags) or '—',
        } for v in bundle.validations.values()]
        st.dataframe(pd.DataFrame(vrows), use_container_width=True)
    with tabs[2]:
        st.plotly_chart(bundle.figures['overlay'], use_container_width=True)
        st.plotly_chart(bundle.figures['envelope'], use_container_width=True)
        st.plotly_chart(bundle.figures['margin'], use_container_width=True)
        st.plotly_chart(bundle.figures['zoom'], use_container_width=True)
        st.plotly_chart(bundle.figures['bars'], use_container_width=True)
    with tabs[3]:
        for name, s in bundle.stats.items():
            st.markdown(f'**{name}** — {len(s.peaks)} peaks')
            if len(s.peaks):
                st.dataframe(s.peaks.head(12), use_container_width=True)
    with tabs[4]:
        w = bundle.worst
        st.markdown(f'### {w.name}')
        c1, c2, c3, c4 = st.columns(4)
        c1.metric('Highest risk', w.by_risk)
        c2.metric('Smallest margin', w.by_margin)
        c3.metric('Highest peak', w.by_peak)
        c4.metric('Highest mean', w.by_mean)
        if w.by_risk != w.by_margin:
            st.info(f'Risk ranking selects **{w.by_risk}** (most near-limit '
                    f'peaks), while the single closest-to-limit point is in '
                    f'**{w.by_margin}** — cite the latter as the formal '
                    f'minimum-margin compliance figure.')
        st.write(w.rationale)
        st.write(emc.spectral_narrative(bundle.stats, w))

    report_html = emc.build_report_html(inp, bundle)
    env_csv = bundle.envelope.to_csv(index=False).encode('utf-8')
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    d1, d2 = st.columns(2)
    with d1:
        st.download_button(
            'Download full HTML report', data=report_html,
            file_name=f'emc_report_{stamp}.html', mime='text/html',
            use_container_width=True)
    with d2:
        st.download_button(
            'Download worst-case envelope (CSV)', data=env_csv,
            file_name=f'emc_envelope_{stamp}.csv', mime='text/csv',
            use_container_width=True)


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #

def main() -> None:
    st.set_page_config(page_title='EMI / EMC Dashboard', layout='wide')
    page = st.sidebar.radio(
        'Tool', ['Trace comparison', 'EMC compliance analysis'])
    st.sidebar.markdown('---')
    if page == 'Trace comparison':
        page_comparison()
    else:
        page_emc_analysis()


if __name__ == '__main__':
    main()
