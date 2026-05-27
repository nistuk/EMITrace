"""Run the analysis notebook once per TEST_MODES entry and refresh reports/.

The notebook source is rewritten in place before each execution to set the
`MODE` configuration knob, then restored to a default at the end.
"""
import json, pathlib, re, subprocess, sys

ROOT = pathlib.Path(__file__).parent
NB = ROOT / 'emi_analysis.ipynb'

src = json.loads(NB.read_text(encoding='utf-8'))
config_src = ''.join(src['cells'][2]['source'])

# Pull TEST_MODES keys straight from the source so this script never goes stale.
modes = re.findall(r"^    '([^']+)':\s*\{\s*$", config_src, flags=re.MULTILINE)
assert modes, 'Could not parse any TEST_MODES keys from cell 2'
print('Modes discovered:', modes)

DEFAULT_MODE = modes[-1]  # what the notebook is left configured for at the end


def set_mode(mode_name: str) -> None:
    lines = src['cells'][2]['source']
    for i, line in enumerate(lines):
        if line.startswith('MODE'):
            lines[i] = (f"MODE           = '{mode_name}'  "
                        f"# any key of TEST_MODES \u2014 drives sections 2\u20138\n")
            break
    NB.write_text(json.dumps(src, indent=1, ensure_ascii=False) + '\n',
                  encoding='utf-8')


def slug(mode_name: str) -> str:
    return (mode_name.lower()
            .replace('+', '_').replace(' ', '_')
            .replace('(', '').replace(')', ''))


for mode in modes:
    set_mode(mode)
    out_name = f'emi_analysis.executed.{slug(mode)}.ipynb'
    print(f'>> Executing notebook with MODE={mode!r} -> {out_name}')
    r = subprocess.run(
        [sys.executable, '-m', 'jupyter', 'nbconvert', '--to', 'notebook',
         '--execute', str(NB), '--output', out_name],
        cwd=ROOT, capture_output=True, text=True,
    )
    print(r.stdout)
    print(r.stderr)
    if r.returncode != 0:
        sys.exit(r.returncode)

set_mode(DEFAULT_MODE)
print(f'Done. Source notebook left with MODE = {DEFAULT_MODE!r}.')
