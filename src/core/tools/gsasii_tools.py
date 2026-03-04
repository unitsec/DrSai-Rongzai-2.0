import subprocess
import json, os

# 获取GSASII工具
from pathlib import Path
import sys
here = Path(__file__).parent.resolve()
tool_path = here.parent.parent.parent.joinpath("tools/GSAS-II-master/MCPS/gsasii_api.py")
# sys.path.insert(0, str(tool_path.parent))
# from src.core.tools.gsasii_tools import powder_diffraction_refinement

def powder_diffraction_refinement(
    xray_data: str =None, 
    xray_inst: str =None, 
    neutron_data: str =None, 
    neutron_inst: str =None, 
    cif_files: list =None, 
    use_xray: bool =False,
    use_neutron: bool =False,
    refine_background_cycles: int =1,
    refine_cell_cycles: int =1,
    refine_hap_cycles: int =1,
    refine_size_strain_cycles: int =1,
    refine_sample_atoms_cycles: int =1,
    refine_instrument_cycles: int =1,
    thread_id: str = "anonymous"
    
) -> dict:
    """
    粉末衍射数据精修主函数（支持多相），支持X射线和中子数据，支持多种精修步骤。
    
    参数:
        xray_data (str): X射线数据文件路径
        xray_inst (str): X射线仪器参数文件路径
        neutron_data (str): 中子数据文件路径
        neutron_inst (str): 中子仪器参数文件路径
        cif_files (list): CIF结构文件路径列表
        use_xray (bool): 是否使用X射线数据 (默认False)
        use_neutron (bool): 是否使用中子数据 (默认False)
        refine_*_cycles (int): 各精修步骤的执行次数，0表示不执行
        refine_background_cycles：进行背景精修的次数
        refine_cell_cycles：进行晶胞参数精修的次数
        refine_hap_cycles：进行HAP参数精修的次数
        refine_size_strain_cycles：进行尺寸和应变精修的次数
        refine_sample_atoms_cycles：进行样品参数和原子位置精修的次数
        refine_instrument_cycles：进行数据范围和仪器参数精修的次数
        thread_id (str): 图床上传线程ID，默认为"anonymous"

    所有的处理文件都将输出到xray_data文件所在的绝对路径的output子目录下。
    返回:
        dict: 包含精修结果的字典，包括:
            - rwp_values: 各数据的Rwp值
            - cell_parameters: 各相的晶胞参数
            - phase_fractions: 各相的相分数
            - output_files: 生成的文件列表
            - refinement_pngs: 生成的图表列表
            - png_urls: 图床上传后的URL列表
            - refinement_logs: 精修日志文本
    """
    # 构建命令行参数
    args = [
    ('--xray_data', xray_data),
    ('--xray_inst', xray_inst),
    ('--neutron_data', neutron_data),
    ('--neutron_inst', neutron_inst),
    ('--cif_files', json.dumps(cif_files)),
    ('--use_xray', str(use_xray).lower()),
    ('--use_neutron', str(use_neutron).lower()),
    ('--refine_background_cycles', str(refine_background_cycles)),
    ('--refine_cell_cycles', str(refine_cell_cycles)),
    ('--refine_hap_cycles', str(refine_hap_cycles)),
    ('--refine_size_strain_cycles', str(refine_size_strain_cycles)),
    ('--refine_sample_atoms_cycles', str(refine_sample_atoms_cycles)),
    ('--refine_instrument_cycles', str(refine_instrument_cycles)),
    ('--thread_id', thread_id)
    ]

    cmd = [
        sys.executable,
        str(tool_path)
    ]
    
    for arg_name, arg_value in args:
        if arg_value is not None:
            cmd += [arg_name, str(arg_value)]

    # 执行子进程
    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    
    # 获取输出和错误日志
    stdout, stderr = process.communicate()
    
    # 检查是否有错误发生
    if process.returncode != 0:
        # print(f"错误: \n {stderr}")
        # return None
        return {
            "status": 1,
            "log": stderr,
            "results": None
            }
    
    # 解析输出结果
    # print(f"输出: \n {stdout}")

    data_file = xray_data if xray_data else neutron_data
    output_dir = os.path.abspath(os.path.dirname(data_file))
    output_path = Path(output_dir) / f"{thread_id}_output"
    output_path.mkdir(parents=True, exist_ok=True)
    output_dir = str(output_path)
    with open(os.path.join(output_dir, "refinement_results.json"), "r") as f:
        results = json.loads(f.read())
    return {
            "status": 0,
            "log": stdout,
            "results": results,
            }


if __name__ == '__main__':
    # 测试代码
    # neutron_inst = "/aifs/user/home/xiongdb/VSproject/rozai_agent/test_datesets/RUN0021905/RT_GROUP5.instprm"
    # neutron_data = "/aifs/user/home/xiongdb/VSproject/rozai_agent/test_datesets/RUN0021905/RUN0021905_group5.gsa"
    # cif_files = ["/aifs/user/home/xiongdb/VSproject/rozai_agent/test_datesets/RUN0021905/test_thread/mp-149.cif"]
    # refine_background_cycles = 3
    # params = {
    #     "neutron_inst": neutron_inst,
    #     "neutron_data": neutron_data,
    #     "cif_files": cif_files,
    #     "use_xray": False,
    #     "use_neutron": True,
    #     "refine_background_cycles": refine_background_cycles,}

    input_dir = "/aifs/user/home/xiongdb/VSproject/mutil_mcp_tools/files/anonymous/XRD_Neutron_power_diffraction/"
    # 定义多个CIF文件（多相）
    cif_files = [
        os.path.join(input_dir, "PbSO4-Wyckoff.cif"),
        #os.path.join(input_dir, "Be.cif")
    ]
    xray_data=os.path.join(input_dir, "PBSO4.XRA")
    xray_inst=os.path.join(input_dir, "INST_XRY.PRM")
    neutron_data=os.path.join(input_dir, "PBSO4.CWN")
    neutron_inst=os.path.join(input_dir, "inst_d1a.prm")
    cif_files=cif_files
    use_xray=True
    use_neutron=True
    refine_cell_cycles=2  # 晶胞参数精修执行2次
    refine_size_strain_cycles=0  # 不执行尺寸和应变精修

    params = {
        "xray_data": xray_data,
        "xray_inst": xray_inst,
        "neutron_data": neutron_data,
        "neutron_inst": neutron_inst,
        "cif_files": cif_files,
        "use_xray": use_xray,
        "use_neutron": use_neutron,
        "refine_cell_cycles": refine_cell_cycles,
        "refine_size_strain_cycles": refine_size_strain_cycles,
    }

    results = powder_diffraction_refinement(**params)
    print(results)
        