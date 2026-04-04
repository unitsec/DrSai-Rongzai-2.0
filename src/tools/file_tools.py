import glob
import os
from typing import List


def find_refinement_files(
    data_dir: str = "",
    user_upload_dir: str = "",
    beamline: str | None = None,
    run_id: str | None = None,
    detector: str | None = None,
    cifs_from_database: List[str] | None = None,
    cifs_from_upload: List[str] | None = None,
    gsa_from_upload: str | None = None,
    inst_from_upload: str | None = None,
) -> dict:
    """
    定位 Rietveld 精修所需的衍射数据、仪器参数和 CIF 结构文件绝对路径。

    参数:
        data_dir: CSNS 数据根目录；为空时尝试使用环境变量 WORKER_SPACE
        user_upload_dir: 用户上传文件目录；为空时尝试使用环境变量 USER_UPLOAD_DIR
        beamline: 谱仪名称，如 BL16、BL18、BL09、BL15
        run_id: 实验 RUN 号，如 RUN0021905，也兼容 run21905 等写法
        detector: 探测器组名，如 group5、bank14、module11203
        cifs_from_database: 从数据库查找的 CIF 文件名列表
        cifs_from_upload: 从用户上传目录查找的 CIF 文件名列表
        gsa_from_upload: 用户上传的衍射数据文件名
        inst_from_upload: 用户上传的仪器参数文件名

    返回:
        成功:
            {
                "status": "success",
                "gsa_path": "...",
                "instprm_path": "...",
                "cif_paths": ["..."],
            }
        失败:
            {
                "status": "error",
                "message": "..."
            }
    """

    data_dir = data_dir or os.getenv("WORKER_SPACE", "")
    user_upload_dir = user_upload_dir or os.getenv("USER_UPLOAD_DIR", "")

    if not data_dir:
        return {
            "status": "error",
            "message": "未提供 data_dir，且环境变量 WORKER_SPACE 未设置。",
        }

    def _find_in_upload(name: str) -> str:
        if not name or not user_upload_dir:
            return ""
        target = os.path.basename(name).lower()
        for root, _, files in os.walk(user_upload_dir):
            for filename in files:
                if filename.lower() == target:
                    return os.path.join(root, filename)
        return ""

    if run_id:
        digits = "".join(ch for ch in run_id if ch.isdigit())
        if digits:
            run_id = f"RUN{digits.zfill(7)}"

    cifs_from_database = cifs_from_database or []
    cifs_from_upload = cifs_from_upload or []

    need_run_lookup = not gsa_from_upload or bool(cifs_from_database)
    need_inst_lookup = not inst_from_upload

    run_dir = None
    cif_dir = ""

    if need_run_lookup:
        if not beamline:
            return {"status": "error", "message": "请告诉我谱仪名称，例如 BL16、BL18、BL09、BL15。"}
        if not run_id:
            return {"status": "error", "message": "请告诉我 RUN 号，例如 RUN0021905。"}

        run_data_dir = os.path.join(data_dir, beamline, "RUNdataFiles")
        if os.path.exists(run_data_dir):
            for folder in os.listdir(run_data_dir):
                if run_id.lower() in folder.lower():
                    run_dir = os.path.join(run_data_dir, folder)
                    break

        if not run_dir:
            return {
                "status": "error",
                "message": f"在 {beamline} 数据库中未找到 {run_id}，请确认谱仪名称和 RUN 号是否正确，或直接上传数据文件。",
            }
        cif_dir = os.path.join(run_dir, "cifFiles")

    if need_inst_lookup:
        if not beamline:
            return {"status": "error", "message": "请告诉我谱仪名称，例如 BL16、BL18、BL09、BL15。"}
        if not detector:
            return {"status": "error", "message": "请告诉我探测器组名，例如 group5、bank14。"}

    gsa_path = _find_in_upload(gsa_from_upload) if gsa_from_upload else ""
    if not gsa_path:
        if not run_dir:
            return {"status": "error", "message": "需要提供正确的 RUN 号，才能从数据库定位衍射数据文件。"}
        run_base = os.path.basename(run_dir).upper()
        candidate = os.path.join(run_dir, f"{run_base}_{detector}.gsa")
        if not os.path.exists(candidate):
            return {
                "status": "error",
                "message": f"在 {run_id} 下未找到 {run_base}_{detector}.gsa，请确认探测器编号是否正确。",
            }
        gsa_path = candidate

    instprm_path = _find_in_upload(inst_from_upload) if inst_from_upload else ""
    if not instprm_path:
        pattern = os.path.join(data_dir, beamline, "instrumentFiles", f"*{detector}*.instprm")
        matches = glob.glob(pattern)
        if not matches:
            return {
                "status": "error",
                "message": f"未找到 {detector} 对应的仪器参数文件（.instprm），请上传或确认探测器编号。",
            }
        instprm_path = matches[0]

    cif_paths = []
    for name in cifs_from_upload:
        path = _find_in_upload(name)
        if not path:
            return {"status": "error", "message": f"上传的 CIF 文件 {name} 未找到，请确认是否已上传。"}
        cif_paths.append(path)

    for name in cifs_from_database:
        path = os.path.join(cif_dir, name)
        if not os.path.exists(path):
            return {"status": "error", "message": f"数据库中未找到 CIF 文件 {name}。"}
        cif_paths.append(path)

    if not cif_paths:
        return {"status": "error", "message": "至少需要一个 CIF 结构文件，请告诉我文件名或上传文件。"}

    return {
        "status": "success",
        "gsa_path": gsa_path,
        "instprm_path": instprm_path,
        "cif_paths": cif_paths,
    }