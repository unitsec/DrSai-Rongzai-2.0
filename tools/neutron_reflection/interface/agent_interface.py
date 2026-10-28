"""
Agent interface for CSNS neutron reflectometry analysis

Input:
    - runs: list of run numbers


Output:
    - one-dimensional data
    - plots
    - summary.csv
"""


import os
from summary_export import write_public_summary

from interface import (
    load_machine_paths,
    read_rz,
    classify_runs,
    build_modifications,
    calculate_mr_results,
    plot_results,
    build_summary
)

from CSNS_reflection_script_forinterface import run_reflection



def process_reflection(runs, output_dir=None):

    # 管理员路径配置，从machine_path.json读取
    paths = load_machine_paths()

    data_root = paths["data_root"]
    save_root = os.path.abspath(output_dir or paths["save_root"])
    os.makedirs(save_root, exist_ok=True)


    run_info = []

    for run in runs:

        rz = read_rz(
            data_root,
            run
        )

        run_info.append(
            {
                "run": run,
                "Rz": rz
            }
        )


    classified = classify_runs(run_info)

    modifications = build_modifications(classified)

    results = run_reflection(modifications, output_dir=save_root)

    mr_results = calculate_mr_results(results)

    plot_results(
        results,
        mr_results,
        save_root
    )

    summary = build_summary(
        classified,
        mr_results
    )

    summary_file = os.path.join(
        save_root,
        "summary.csv"
    )

    write_public_summary(summary_file, summary)


    return {
        "status": "success",
        "summary_file": summary_file,
        "output_path": save_root
    }
