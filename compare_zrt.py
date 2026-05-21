import sys
import json
import os
from tools.pyZRietveld import refineTool

def run_compare():
    histogram_path = "tools/pyZRietveld/testData/RUN0001094_groupBM.histogramIgor"
    diffractometer_path = "tools/pyZRietveld/testData/BM.zDiffractometer"
    cif_path = "tools/pyZRietveld/testData/LNO.cif"
    
    new_zrt_path = "/tmp/zrt_compare_new.zrietveldn"
    old_zrt_path = "/home/wangh/rongzai-agent/userRecord/wanghao96/LNO_RUN0001094_BM/6862ac44-3adb-4427-a8af-730310406243.zrietveldn"
    
    # 1) Generate new zrietveldn
    print(f"Generating new zrietveldn to {new_zrt_path}...")
    if os.path.exists(new_zrt_path):
        os.remove(new_zrt_path)
    refineTool.genZRTn(histogram_path, diffractometer_path, [cif_path], new_zrt_path)
    
    cfg = {
        "refine_scale": "true",
        "refine_background": "true",
        "background_coeffs": "6",
        "refine_cell": "true",
        "absorption": "{\"A\":{\"refine\":\"true\",\"function\":\"Lobanov1\"}}",
        "refine_cycle": "10"
    }
    
    results = {}
    for label, path in [("OLD", old_zrt_path), ("NEW", new_zrt_path)]:
        print(f"\nRefining {label}: {path}")
        # refineTool.refine expects a dict, not a string
        res = refineTool.refine(path, cfg)
        results[label] = res
        print(f"{label} keys: {list(res.keys())}")
        print(f"{label} history_index: {res.get('history_index')}")
        print(f"{label} report_path: {res.get('report_path')}")
        print(f"{label} ok: {res.get('ok')}")
        print(f"{label} fit_status: {res.get('fit_status')}")

    # 4) Extract info from report files
    for label in ["OLD", "NEW"]:
        report_path = results[label].get('report_path')
        print(f"\n--- Analysis of {label} Report ({report_path}) ---")
        if not report_path or not os.path.exists(report_path):
            print("Report path not found.")
            continue
            
        with open(report_path, 'r') as f:
            lines = f.readlines()
            
        print(f"Rwp lines:")
        for line in lines:
            if "Rwp" in line:
                print(line.strip())
                
        print(f"\nHistogram_A section:")
        in_hist_a = False
        for line in lines:
            if "Histogram_A" in line:
                in_hist_a = True
            if in_hist_a:
                print(line.strip())
                if line.strip() == "" and "Histogram_A" not in line: # Simple section break
                    in_hist_a = False
                    
        print(f"\nAbsorption/Density related lines:")
        keywords = ["absorption", "PowderDensity", "Lobanov", "cross_section"]
        for i, line in enumerate(lines):
            if any(k.lower() in line.lower() for k in keywords):
                print(f"{i+1}: {line.strip()}")

if __name__ == "__main__":
    run_compare()
