"""
CSNS_reflection_script.py

BL08A 反射离线数据处理入口脚本。
数据处理逻辑已迁移至 rongzai/workflows/reflection/bl08a.py。
"""
import os, sys
import logging
import logging.handlers
import matplotlib.pyplot as plt
import numpy as np

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    datefmt='%H:%M:%S',
    handlers=[
        logging.StreamHandler(),
        logging.handlers.RotatingFileHandler(
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "BL08A.log"),
            maxBytes=1 * 1024 * 1024,  # 1 MB，约 1 万行后覆盖
            backupCount=0,             # 不保留备份，直接覆盖
            encoding="utf-8",
        ),
    ]
)

current_dir = os.path.dirname(os.path.abspath(__file__))
from rongzai.workflows.reflection.bl08a import BL08AReflectionOffline
from rongzai.algSvc.neutron import calculate_neutron_data, convert_neutron_unit_elastic

def run_reflection(modifications, output_dir=None):
    """
    执行BL08A反射规约

    Parameters
    ----------
    modifications:
        interface生成的动态参数

    Returns
    -------
    results:
        {run: xarray.Dataset}
    """

    json1 = os.path.join(current_dir, "BL08A_base.json")
    json2 = os.path.join(current_dir, "BL08A_reflection.json")


    # Load the shared templates without writing modified JSON files.
    from rongzai.utils import load_config
    config = load_config(json1, json2)
    config.update(modifications)
    if output_dir is not None:
        config["save_path"] = os.path.join(os.path.abspath(output_dir), "BL08A")
    task = BL08AReflectionOffline.from_merged_conf(config)

    results = task.run()


    return results

##——————原来的——————

if __name__ == "__main__":
    json1 = "BL08A_base.json"
    json2 = "BL08A_reflection.json"

    modifications = {
        'direct_run':["RUN0000219"],
        "reflection_runs": [
            {"run": "RUN0000216", "theta": 1.22},
            {"run": "RUN0000217", "theta": 1.22},
            {"run": "RUN0000218", "theta": 1.22},
        ],
    }

    task = BL08AReflectionOffline(json1, json2, modifications)
    results = task.run()

    plt.figure(figsize=(8, 5))
    for run, dataset in results.items():
        x = np.squeeze(dataset["xvalue"].values)
        y = np.squeeze(dataset["histogram"].values)
        plt.semilogy(x, y, label=f"rongzai {run}")

    plt.xlabel("Q (Å⁻¹)")
    plt.ylabel("Reflectivity")
    plt.title("R(Q)")
    plt.legend()
    plt.tight_layout()
    plt.show()
