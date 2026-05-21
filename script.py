import importlib.util, re, sys
from pathlib import Path
try:
    spec=importlib.util.spec_from_file_location('z','tools/pyZRietveld/0zrietveld_refine.py')
    m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    z='/home/wangh/rongzai-agent/userRecord/wanghao96/LNO_RUN0001094_BM/6862ac44-3adb-4427-a8af-730310406243.zrietveldn'
    cfg={
    'refine_scale':'true','refine_background':'true','background_coeffs':'6','refine_cell':'true',
    'absorption':'{"A":{"refine":"true","function":"Lobanov1"}}','refine_cycle':'10'
    }
    res=m.zrt_manual_refine(z,cfg,user_id='test_user',mission_name='test_mission_iso')
    print('ok',res.get('ok'))
    print('report',res.get('report_path'))
    report_path = res.get('report_path')
    if report_path:
        rp=Path(report_path)
        if rp.exists():
            t=rp.read_text(encoding='utf-8',errors='ignore')
            for pat in [r"\*\s*Rwp\s*,\s*Re\s*\n\s*([+-]?[0-9]*\.?[0-9]+(?:[Ee][+-]?\d+)?)", r"\*\s*Rwp\s*,\s*Rp\s*,\s*Re\s*,[^\n]*\n\s*([+-]?[0-9]*\.?[0-9]+(?:[Ee][+-]?\d+)?)"]:
                m2=re.search(pat,t)
                print('match',pat[:20], m2.group(1) if m2 else None)
            print('contains999', '9.999900E-01' in t)
        else:
            print('Report file does not exist')
    else:
        print('No report path returned')
except Exception as e:
    print(f'Error: {e}')
