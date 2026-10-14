
"""
interface.py

BL08A reflection interface
1. Read config.json
2. Load run information
3. Read Rz from metadata
4. Classify direct/reflection runs
5. Build modifications
6. Call CSNS_reflection_script_forinterface.run_reflection()
7. Calculate (m, r)
8. Save plots and summary.csv
"""

import json
import os
import matplotlib.pyplot as plt
import numpy as np
from CSNS_reflection_script_forinterface import run_reflection
from calculatemr import calculate_mr_from_dataset
import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

def load_json(filename):
    """
    通用json读取
    """

    filepath = os.path.join(BASE_DIR, filename)

    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)

    return data



def load_machine_paths():
    """
    读取管理员配置
    """

    return load_json(
        "machine_paths.json"
    )



def load_interface_config():
    """
    读取用户输入
    """

    return load_json(
        "interface_config.json"
    )



def read_rz(data_root, run):
    """
    根据run读取Rz

    文件结构：

    data_root/
        RUNxxxx/
            RUNxxxx
    """

    metadata_file = os.path.join(
        data_root,
        run,
        run
    )

    with open(metadata_file, "r", encoding="utf-8") as f:
        data = json.load(f)


    rz = data["summary"]["json"]["devices"]["Rz"]

    return rz



def get_run_info():

    # 读取两个配置

    paths = load_machine_paths()

    user_config = load_interface_config()


    data_root = paths["data_root"]

    runs = user_config["runs"]


    result = []


    for run in runs:

        rz = read_rz(
            data_root,
            run
        )

        result.append(
            {
                "run": run,
                "Rz": rz
            }
        )


    return result

RZ_THRESHOLD = 0.0001


def classify_runs(run_info):
    """
    根据Rz判断测试类型

    Rz接近0:
        direct

    Rz明显非零:
        reflection
    """

    RZ_THRESHOLD = 0.0001
    classified = []


    for item in run_info:

        rz = item["Rz"]

        if abs(rz) < RZ_THRESHOLD:
            run_type = "direct"

        else:
            run_type = "reflection"


        classified.append(
            {
                "run": item["run"],
                "Rz": rz,
                "type": run_type
            }
        )


    return classified

def build_modifications(classified_runs):
    """
    根据run分类结果生成rongzai需要的modifications
    """

    direct_run = []
    reflection_runs = []


    for item in classified_runs:

        if item["type"] == "direct":

            direct_run.append(
                item["run"]
            )


        elif item["type"] == "reflection":

            reflection_runs.append(
                {
                    "run": item["run"],
                    "theta": item["Rz"]
                }
            )


    # 检查直通run数量
    if len(direct_run) != 1:
        raise ValueError(
            f"需要且只能有一个direct run，目前有{len(direct_run)}个"
        )


    modifications = {
        "direct_run": direct_run,
        "reflection_runs": reflection_runs
    }


    return modifications

def calculate_mr_results(results):

    mr_results = {}

    method = os.environ.get("NEUTRON_REFLECTION_MR_METHOD", "phits")
    if method not in ("phits", "local_edge"):
        raise ValueError("NEUTRON_REFLECTION_MR_METHOD必须为phits或local_edge")
    for run, dataset in results.items():
        if method == "phits":
            from supermirror_fit import fit_supermirror_from_dataset
            mr_results[run] = fit_supermirror_from_dataset(dataset)
        else:
            mr_results[run] = calculate_mr_from_dataset(dataset)

    return mr_results

def plot_results(
        results,
        mr_results,
        save_root
):

    for run, dataset in results.items():

        if mr_results[run].get("method") == "phits_3_37_tanh_fit_v1":
            from supermirror_fit import plot_model_result
            plot_model_result(dataset, mr_results[run], run,
                              os.path.join(save_root, "plot", run))
            continue

        run_dir = os.path.join(
            save_root,
            "plot",
            run
        )

        os.makedirs(
            run_dir,
            exist_ok=True
        )

        x = np.squeeze(
            dataset["xvalue"].values
        )

        y = np.squeeze(
            dataset["histogram"].values
        )

        error = np.squeeze(
            dataset["error"].values
        )


        fig, ax = plt.subplots(figsize=(8, 5))


        # 原始数据 + errorbar
        ax.errorbar(
            x,
            y,
            yerr=error,
            fmt="o",
            markersize=4,
            capsize=2,
            label=run
        )


        # ======================
        # 标记 Qm / m / r
        # ======================

        mr = mr_results[run]

        Qm = mr["Qm"]
        m = mr["m"]
        r = mr["r"]


        # 标出最终(m,r)对应的Q-R点
        ax.scatter(
            Qm,
            r,
            marker="*",
            s=180,
            zorder=5,
            label=f"m={m:.3f}@r={r*100:.1f}%",
            c = "red"
        )


        # Segment1拟合线
        x1_fit = np.linspace(
            mr["x1"].min(),
            Qm,
            100
        )

        y1_fit = (
            mr["slope1"] * x1_fit
            + mr["intercept1"]
        )

        ax.plot(
            x1_fit,
            y1_fit,
            "--",
            label="Segment1 fit",
            c = "green"
        )


        #segment1
        x1 = mr["x1"]
        y1 = mr["y1"]

        ax.scatter(
            x1,
            y1,
            s=70,
            facecolors="None",
            edgecolors="green",
            linewidths=1.5,
            label="Segment1 data"

        )

        #segment2
        x2 = mr["x2"]
        y2 = mr["y2"]

        ax.scatter(
            x2,
            y2,
            s=70,
            facecolors="none",
            edgecolors="orange",
            linewidths=1.5,
            label="Segment2 data"
        )

        # Qm竖线
        ax.axvline(
            Qm,
            linestyle=":",
            label=f"Drop region midpoint Qm={Qm:.5f} Å⁻¹"
        )


        ax.set_yscale("log")

        ax.set_xlabel("Q (Å⁻¹)")
        ax.set_ylabel("Reflectivity")

        ax.set_title(
            f"R(Q) - {run}"
        )

        ax.legend()

        fig.tight_layout()


        filename = os.path.join(
            run_dir,
            "reflectivity.png"
        )

        fig.savefig(
            filename,
            dpi=300
        )

        plt.close(fig)


        print(
            "plot saved:",
            filename
        )

        # -------确认Qm位置-------------
        x_remain = mr["x_remain"]
        d1 = mr["d1"]
        d2 = mr["d2"]


        # 归一化导数，只改变Y轴尺度
        # 不改变峰/谷所在的Q位置
        # -----------------------------
        d1_scale = np.max(np.abs(d1))
        d2_scale = np.max(np.abs(d2))

        d1_norm = d1 / d1_scale if d1_scale > 0 else d1
        d2_norm = d2 / d2_scale if d2_scale > 0 else d2

        # -----------------------------
        # 建图
        # -----------------------------
        fig, ax_r = plt.subplots(figsize=(10, 6))

        # 左轴：R(Q)
        ax_r.errorbar(
            x,
            y,
            yerr=error,
            fmt="o",
            markersize=4,
            capsize=2,
            label=run,
            c = "green"
        )

        ax_r.set_yscale("log")
        ax_r.set_xlabel("Q (Å⁻¹)")
        ax_r.set_ylabel("Reflectivity")
        ax_r.set_title(f"R(Q) and Derivatives - {run}")

        # -----------------------------
        # 右轴：归一化的一阶/二阶导
        # -----------------------------
        ax_d = ax_r.twinx()

        ax_d.plot(
            x_remain,
            d1_norm,
            linewidth=2,
            label="Normalized dR/dQ"
        )

        ax_d.plot(
            x_remain,
            d2_norm,
            linewidth=2,
            label="Normalized d²R/dQ²"
        )

        ax_d.axhline(
            0,
            linewidth=1,
            linestyle="--"
        )

        ax_d.set_ylabel("Normalized derivative")
        ax_d.set_ylim(-1.1, 1.1)

        if "analysis_q_end" in mr:
            ax_r.axvline(mr["analysis_q_end"], color="gray", linestyle="-.",
                         label="End of local derivative analysis")

        # -----------------------------
        # 标Segment2和Qm
        # -----------------------------
        x2 = mr["x2"]
        Qm = mr["Qm"]

        ax_r.axvline(
            x2[0],
            linestyle="--",
            label="Segment2 start"
        )

        ax_r.axvline(
            x2[-1],
            linestyle="--",
            label="Segment2 end"
        )

        ax_r.axvline(
            Qm,
            linestyle=":",
            label=f"Qm={Qm:.5f} Å⁻¹"
        )

        # -----------------------------
        # 合并两个Y轴的legend
        # -----------------------------
        lines1, labels1 = ax_r.get_legend_handles_labels()
        lines2, labels2 = ax_d.get_legend_handles_labels()

        ax_r.legend(
            lines1 + lines2,
            labels1 + labels2,
            loc="best"
        )

        fig.tight_layout()

        filename2 = os.path.join(
            run_dir,
            "reflectivity&derivative.png"
        )

        fig.savefig(
            filename2,
            dpi=300
        )

        print(
            "plot saved:",
            filename2
        )

        plt.close(fig)






def build_summary(
        classified,
        mr_results
):
    summary = []

    for item in classified:

        run = item["run"]

        row = {
        "Run": run,
        "Type": item["type"],
        "Rz": item["Rz"],
        "Qm": None,
        "m": None,
        "r(%)": None
        }

        if run in mr_results:

            row["Qm"] = mr_results[run]["Qm"]
            row["m"] = mr_results[run]["m"]
            row["r(%)"] = mr_results[run]["r"]*100

        for key in ("method", "status", "Q_start", "Q_end", "platform_q_start",
                    "platform_q_end", "analysis_q_end", "smoothing_window"):
            row["mr_" + key] = mr_results.get(run, {}).get(key)

        estimate = mr_results.get(run, {})
        for column, key, scale in (("r_envelope(%)", "r_envelope", 100), ("R_center(%)", "r_center", 100),
                                    ("R0(%)", "R0", 100), ("alpha_A", "alpha", 1),
                                    ("W_A^-1", "W", 1), ("width_90_10_A^-1", "width_90_10", 1),
                                    ("width_99_01_A^-1", "width_99_01", 1), ("Qc_Ni_A^-1", "Qc_Ni", 1)):
            row[column] = estimate[key] * scale if key in estimate else None
        for key in ("r_definition", "fit_q_min", "fit_q_max", "fit_points", "error_floor_fraction",
                    "weighted_rms_with_error_floor", "edge_weighted_rms_with_error_floor", "reference", "fit_window_policy"):
            row[key] = estimate.get(key)

        summary.append(row)

    return summary







if __name__ == "__main__":

    paths = load_machine_paths()
    info = get_run_info()

    classified = classify_runs(info)

    modifications = build_modifications(
        classified
    )

    results = run_reflection(modifications)
    print(results.keys())

    mr_results = calculate_mr_results(results)

    print("\n(m, r) results:")

    for run, value in mr_results.items():

        print(
            run,
            "Qm =",
            value["Qm"],
            "m =",
            value["m"],
            "r =",
            value["r"]
        )



    plot_results(
    results,
    mr_results,
    save_root=paths["save_root"]
    )

    summary = build_summary(
    classified,
    mr_results
    )

    for row in summary:
        print(row)

    from summary_export import write_public_summary
    write_public_summary(os.path.join(paths["save_root"], "summary.csv"), summary)
