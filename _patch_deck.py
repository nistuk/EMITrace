import json
from pathlib import Path

p = Path('emi_report.ipynb')
nb = json.loads(p.read_text(encoding='utf-8'))

new_setup = '''from pathlib import Path
from html import escape
from IPython.display import HTML, Markdown, display

REPORTS = Path('reports')
IFRAME_H = 620

# Order in which the modes appear in the deck.
# Each entry: (display title, slug used in reports/*.html, baseline label or None)
MODES = [
    ('Standby (18 May)',                'standby',                       None),
    ('Movement + X-ray (19 May)',       'movement_xray',                 None),
    ('Configurations sweep (20 May)',   'configurations_(20_may)',       'Only cabinets'),
    ('CLEA SAMD sweep (20 May)',        'clea_samd_sweep_(20_may)',      'Only cabinets'),
    ('Ambient vs Only cabinets',        'ambient_vs_only_cabinets',      'Ambient (no system)'),
]

def embed(path: Path, height: int = IFRAME_H):
    """Inline the HTML report via <iframe srcdoc> — no file server, works in
    VS Code notebooks, JupyterLab and classic Jupyter regardless of paths."""
    if not path.exists():
        display(Markdown(f'_Missing report: `{path}`_'))
        return
    doc = path.read_text(encoding='utf-8')
    srcdoc = escape(doc, quote=True)
    display(HTML(
        f'<iframe srcdoc="{srcdoc}" '
        f'style="width:100%; height:{height}px; border:1px solid #ddd;" '
        f'sandbox="allow-scripts allow-same-origin"></iframe>'
    ))

def show_mode(title, slug, baseline):
    display(Markdown(f'## {title}'))
    overlay  = REPORTS / f'overlay_positions_{slug}.html'
    envelope = REPORTS / f'envelope_vs_cispr11_{slug}.html'
    delta    = REPORTS / f'baseline_delta_{slug}.html'

    display(Markdown(f'**1. Positions overlay** — `{overlay.name}`'))
    embed(overlay)

    display(Markdown(f'**2. Envelope vs CISPR 11** — `{envelope.name}`'))
    embed(envelope)

    if baseline and delta.exists():
        display(Markdown(f'**3. Δ vs. baseline `{baseline}`** — `{delta.name}`'))
        embed(delta, height=IFRAME_H + 80)
    elif baseline:
        display(Markdown(f'_Δ plot missing: `{delta}` not found._'))
'''

nb['cells'][1]['source'] = new_setup.splitlines(keepends=True)
nb['cells'][-1]['source'] = "embed(REPORTS / 'envelope_by_mode.html', height=680)\n".splitlines(keepends=True)

for c in nb['cells']:
    if c['cell_type'] == 'code':
        c['outputs'] = []
        c['execution_count'] = None

p.write_text(json.dumps(nb, indent=1, ensure_ascii=False) + '\n', encoding='utf-8')
print('patched')
