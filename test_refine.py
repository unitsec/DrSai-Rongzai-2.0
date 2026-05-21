import importlib.util
import re
from pathlib import Path
import sys
import os

# Add tools/pyZRietveld to sys.path
sys.path.append(os.path.join(os.getcwd(), 'tools/pyZRietveld'))

module_path = 'tools/pyZRietveld/0zrietveld_refine.py'
spec = importlib.util.spec_from_file_location('zrt_mod', module_path)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

zpath = '/home/wangh/rongzai-agent/userRecord/wanghao96/LNO_RUN0001094_BM/6862ac44-3adb-4427-a8af-730310406243.zrietveldn'
base = {
    'refine_scale': 'true',
    'refine_background': 'true',
    'background_coeffs': '6',
    'refine_cell': 'true',
    'refine_cycle': '10',
}
cases = {
    'no_absorption': {},
    'abs_A_refine_true_Lobanov1': {'absorption': '{"A":{"refine":"true","function":"Lobanov1"}}'},
    'abs_A_refine_true_lobanov1': {'absorption': '{"A":{"refine":"true","function":"lobanov1"}}'},
    'abs_A_refine_false_lobanov1': {'absorption': '{"A":{"refine":"false","function":"lobanov1"}}'},
    'abs_default_refine_true_lobanov1': {'absorption': '{"_default":{"refine":"true","function":"lobanov1"}}'},
}

def parse_rwp(report_path):
    if not report_path:
        return None
    p = Path(report_path)
    if not p.exists():
        return None
    txt = p.read_text(encoding='utf-8', errors='ignore')
    m = re.search(r'\*\s*Rwp\s*,\s*Re\s*\n\s*([+-]?[0-9]*\.?[0-9]+(?:[Ee][+-]?\d+)?)', txt)
    if not m:
        return None
    return float(m.group(1))*100.0

for name, extra in cases.items():
    cfg = dict(base)
    cfg.update(extra)
    res = mod.zrt_manual_refine(zpath, cfg, user_id='test_user', mission_name='abs_compare')
    rwp = parse_rwp(res.get('report_path'))
    msg = res.get('message', '') or ''
    warn = 'absorption' in msg.lower()
    print(f"{name}: ok={res.get('ok')}, rwp={rwp}, warning={warn}")
    sys.stdout.flush()
