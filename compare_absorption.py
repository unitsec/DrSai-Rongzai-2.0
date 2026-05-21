import sys
import os
import json
import importlib.util

sys.path.append(os.path.join(os.getcwd(), "tools", "pyZRietveld"))

def load_zrt_manual_refine():
    module_path = "tools/pyZRietveld/0zrietveld_refine.py"
    spec = importlib.util.spec_from_file_location("zrietveld_refine_module", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.zrt_manual_refine

zrt_manual_refine = load_zrt_manual_refine()

zrietveldnPath = "/home/wangh/rongzai-agent/userRecord/wanghao96/LNO_RUN0001094_BM/6862ac44-3adb-4427-a8af-730310406243.zrietveldn"
user_id = "test_user"

base_cfg = {
    "refine_scale": True,
    "refine_background": True,
    "background_coeffs": 6,
    "refine_cell": True,
    "refine_cycle": 10
}

cases = [
    ("no_absorption", {"refine_absorption": False}),
    ("absorption_A_refine_true_lobanov1", {"refine_absorption": True, "absorption_type": "lobanov1"}),
    ("absorption_A_refine_false_lobanov1", {"refine_absorption": False, "absorption_type": "lobanov1"}),
    ("absorption_A_refine_true_cylindrical", {"refine_absorption": True, "absorption_type": "cylindrical"}),
    ("absorption_default_refine_true_lobanov1", {"refine_absorption": True, "absorption_type": "lobanov1"})
]

for name, specific_cfg in cases:
    cfg = base_cfg.copy()
    cfg.update(specific_cfg)
    mission_name = f"test_{name}"
    
    print(f"\n--- Running case: {name} ---")
    try:
        # Correct order: zrtn_path, cfg, user_id, mission_name
        ok, msg, report_path = zrt_manual_refine(zrietveldnPath, cfg, user_id, mission_name)
        
        if ok:
            rwp = "N/A"
            if os.path.exists(report_path):
                try:
                    with open(report_path, 'r') as f:
                        data = json.load(f)
                        rwp_val = data.get('Rwp', data.get('rwp', 'N/A'))
                        if isinstance(rwp_val, (int, float)):
                            rwp = f"{rwp_val * 100:.4f}%"
                        else:
                            rwp = str(rwp_val)
                except Exception as e:
                    rwp = f"Parse Error: {e}"
            print(f"Result: {name}, ok: {ok}, report_path: {report_path}, Rwp: {rwp}")
        else:
            print(f"Result: {name}, ok: {ok}, error: {msg}")
    except Exception as e:
        print(f"Result: {name}, error: {str(e)}")
