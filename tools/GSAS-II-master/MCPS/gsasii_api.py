
import os, sys
here = os.path.abspath(os.path.dirname(__file__))
import matplotlib.pyplot as plt
import requests
import base64
from pathlib import Path
import logging
import time
from typing import List, Dict, Any
from openai import OpenAI

# 将GSAS-II-master/GSASII插入系统路径
sys.path.insert(0, os.path.join(here, '..', 'GSASII'))
import GSASIIscriptable as G2sc

client = OpenAI(
    api_key=os.environ.get("HEPAI_API_KEY"),
    base_url="https://aiapi.ihep.ac.cn/apiv2"
)

async def upload_file(file_path: str, purpose: str = "user_data") -> str:
    """上传文件到图床接口
    Args:
        file_path (str): 文件路径
        purpose (str): 上传用途，默认为"user_data"

    Returns:
        str: 上传后的URL
    """
    file_obj = client.files.create(
            file=open(file_path, "rb"),
            purpose=purpose
        )
    return f"https://aiapi.ihep.ac.cn/apiv2/files/{file_obj.id}/preview"

def calcu_byte_size(file_path: str) -> int:
    """计算文件字节大小
    Args:
        file_path (str): 文件路径

    Returns:
        int: 文件字节大小
    """
    return os.path.getsize(file_path)

############
# XRD模拟
############
async def simulate_and_plot_xrd(
        cif_file: str, 
        instrument_params: str = os.path.join(here, '..','GSASII/0data_test/script_data/inst_d1a.prm'), 
        two_theta_range: tuple[float, float] = (5.0, 120.0), 
        num_points: int = 1000, 
        output_prefix: str = None, 
        save_plot: bool = True,
        thread_id: str = "anonymous"
        ) -> dict:
    """模拟XRD图谱并绘制图表

    Args:
        cif_file (str): CIF文件的绝对路径
        instrument_params (str): 仪器参数的绝对路径，默认使用测试数据
        two_theta_range (tuple): 衍射的2theta范围 (min, max), 默认为(5.0, 120.0)
        num_points (int): XRD二维曲线拟合所需要的数据点数
        output_prefix (str): 输出文件前缀，默认为cif文件名
        save_plot (bool): 是否保存图表，默认True
        thread_id (str): 图床上传线程ID，固定传入，无需考虑，默认为"anonymous"

    Returns:
        json.dumps(result): 精修结果的JSON字符串，包含精修参数、输出文件信息、日志等信息
    """
    # 获取 cif 文件名（不包含扩展名）
    phasename = os.path.splitext(os.path.basename(cif_file))[0]
    if output_prefix is None:
        output_prefix = phasename
    
    # 获取instrument_params的文件名（不包含扩展名）
    instrument_name = os.path.splitext(os.path.basename(instrument_params))[0]

    # 设置工作路径
    cif_dir = os.path.dirname(cif_file)
    gpx_dir = os.path.join(f"{cif_dir}", f"{thread_id}_{output_prefix}_{instrument_name}")
    os.makedirs(gpx_dir, exist_ok=True)
  
    # 创建项目
    gpx = G2sc.G2Project(newgpx=f'{gpx_dir}/{output_prefix}.gpx')
  
    # 添加相
    phase0 = gpx.add_phase(cif_file, phasename=phasename, fmthint='CIF')
  
    # 添加模拟的图谱并链接到之前添加的相
    hist1 = gpx.add_simulated_powder_histogram(f'{output_prefix} simulation', instrument_params, *two_theta_range, Npoints=num_points, phases=gpx.phases(), scale=500000.)
  
    # 计算图谱
    gpx.do_refinements()
  
    # 保存项目文件
    gpx.save(f'{gpx_dir}/{output_prefix}.gpx')
  
    # 导出结果
    gpx.histogram(0).Export(os.path.join(gpx_dir, f'{output_prefix}_data'), '.csv', 'hist')  # 数据
    gpx.histogram(0).Export(os.path.join(gpx_dir, f'{output_prefix}_refl'), '.csv', 'refl')  # 反射
  
    # 获取结果并绘制图表
    x = gpx.histogram(0).getdata('x')
    y = gpx.histogram(0).getdata('ycalc')

    # 以横坐标为2theta,纵坐标为intensity导出XRD文件为csv文件
    
    plt.plot(x, y)
    if save_plot:
        plot_png = f'{gpx_dir}/{output_prefix}.png'
        plt.savefig(plot_png)  # 保存图像到DATA_DIR路径下
        plt.close()  # 关闭图像窗口（避免在Jupyter中显示）
        
        # # 6. 上传到图床接口，并返回上传后的 URL
        # with open(plot_png, 'rb') as f:
        #     img_base64 = base64.b64encode(f.read()).decode()
        # response = requests.post(
        #     "http://0.0.0.0:8010/picgo",
        #     json={
        #         "pic_bin": img_base64,
        #         "pic_name": os.path.basename(plot_png),
        #         "thread_id": thread_id
        #     }
        # )
        
        # if response.status_code == 200:
        #     png_url = response.json()
        # else:
        #     # raise Exception(f"图床上传失败: {response.text}")
        #     png_url = None

        png_url = await upload_file(plot_png, purpose="user_data")

    # 上传文件系统
    project_file = f'{gpx_dir}/{output_prefix}.gpx'
    project_file_url =  await upload_file(project_file, purpose="user_data")

    reflections_file = f'{gpx_dir}/{output_prefix}_refl.csv'
    reflections_file_url =  await upload_file(reflections_file, purpose="user_data")

    data_file = f'{gpx_dir}/{output_prefix}_data.csv'
    data_file_url =  await upload_file(data_file, purpose="user_data")

    # results = {
    #     "status": "success",
    #     "project_file": project_file,
    #     "project_file_url": project_file_url,
    #     "data_file": data_file,
    #     "data_file_url": data_file_url,
    #     "reflections_file": reflections_file,
    #     "reflections_file_url": reflections_file_url,
    #     "plot_file": plot_png if save_plot else None,
    #     "png_url": png_url if save_plot else None,
    #     "error": None
    # }

    # 整理结果
    result = {}
    result["name"] = f"cif_{phasename}_{instrument_params}_xrd_simulation"
    result["data_type"] = 3
    with open(instrument_params, 'r') as f:
        instrument_params_content = f.read()
    xrd_params = {
        "two_theta_range": two_theta_range,
        "num_points": num_points,
        "instrument_params": instrument_params_content
    }
    refined_params_str = "使用GSASIIscriptable进行CIF文件的XRD模拟，模拟的一些参数如下：\n"
    for key in xrd_params:
            refined_params_str += f"{key}: {xrd_params[key]}\n"
    result["summary"] = refined_params_str
    result["researchField"] = "X-Ray Diffraction"
    ## 整理上传的文件
    total_size = 0
    files =[]
    file_size = calcu_byte_size(project_file)
    files.append({
        "name": os.path.basename(project_file),
        "url": project_file_url,
        "summary": "GSAS-II项目文件.",
        "size": file_size,
        "filePath": project_file
    })
    total_size += file_size

    file_size = calcu_byte_size(reflections_file)
    files.append({
        "name": os.path.basename(reflections_file),
        "url": reflections_file_url,
        "summary": "反射率数据文件.",
        "size": file_size,
        "filePath": reflections_file
    })
    total_size += file_size
    
    file_size = calcu_byte_size(data_file)
    files.append({
        "name": os.path.basename(data_file),
        "url": data_file_url,
        "summary": "衍射数据文件.",
        "size": file_size,
        "filePath": data_file
    })
    total_size += file_size

    if save_plot:
        file_size = calcu_byte_size(plot_png)
        files.append({
            "name": os.path.basename(plot_png),
            "url": png_url,
            "summary": "XRD图谱图像文件.",
            "size": file_size,
            "filePath": plot_png
        })
        total_size += file_size

    file_size = calcu_byte_size(cif_file)
    cif_url = await upload_file(cif_file, purpose="user_data")
    files.append({
        "name": os.path.basename(cif_file),
        "url": cif_url,
        "summary": "输入的CIF文件.",
        "size": file_size,
        "filePath": cif_file
    })
    total_size += file_size

    file_size = calcu_byte_size(instrument_params)
    instrument_params_url = await upload_file(instrument_params, purpose="user_data")
    files.append({
        "name": os.path.basename(instrument_params),
        "url": instrument_params_url,
        "summary": "输入的仪器参数文件.",
        "size": file_size,
        "filePath": instrument_params
    })
    total_size += file_size

    result["files"] = files
    return result


############
# XRD图像文件积分
############
async def integrate_single_image(
    image_file: str,
    image_control_file: str,
    thread_id: str = "anonymous"
) -> dict[str, Any]:
    """
    处理单个XRD图像文件并生成积分数据（输出TXT格式）
    
    Args:
        image_file: 图像的绝对路径，后缀必须为.tif或.tiff
        image_control_file: 图像控制文件的绝对路径
        thread_id: 图床上传线程ID，固定传入，无需考虑，默认为"anonymous"
        
    Returns:
        {
            "status": "success"/"error",
            "input_image": 输入图像路径,
            "output_files": 生成的TXT文件列表,
            "gpx_file": 项目文件路径,
            "processing_time": 处理耗时(秒),
            "output_path": 输出目录绝对路径,
            "error": 错误信息(如有)
        }
    """
    # 使用image_file的目录作为输出目录
    output_path = os.path.dirname(image_file)
    output_path = os.path.join(output_path, f"{thread_id}")
    
    # 创建输出目录
    os.makedirs(output_path, exist_ok=True)

    try:
        start_time = time.time()
        
        # 验证输入文件
        if not os.path.exists(image_file):
            raise FileNotFoundError(f"图像文件不存在: {image_file}")
        if not os.path.exists(image_control_file):
            raise FileNotFoundError(f"控制文件不存在: {image_control_file}")

        # 创建GSAS-II项目
        gpx_path = os.path.join(output_path, 'integration.gpx')
        gpx = G2sc.G2Project(newgpx=gpx_path)
        
        # 加载图像和控制文件
        img_list = gpx.add_image(image_file, fmthint="TIF")
        img = img_list[0]
        img.loadControls(image_control_file)
        
        # 执行积分
        pwdrList = img.Integrate()
        
        # 保存项目文件
        gpx.save()
        
        # 导出积分数据为TXT格式
        output_files = {}
        base_name = os.path.splitext(os.path.basename(image_file))[0]
        
        for i, pwdr in enumerate(pwdrList):
            # 修改1：指定.txt扩展名
            output_file = os.path.join(output_path, f"{base_name}_{i}.txt")
            
            # 修改2：明确使用.txt作为导出格式
            pwdr.Export(output_file.split('.')[0], '.txt', 'hist')  # 'hist'表示导出衍射数据
            
            output_file_url = await upload_file(output_file, purpose="user_data")
            output_files[output_file] = output_file_url
        
        # return {
        #     "status": "success",
        #     "output_path": output_path,
        #     "output_files": output_files,
        #     "gpx_file": gpx_path,
        #     "gpx_path_url": gpx_path_url,
        #     "processing_time": round(time.time() - start_time, 2),
        #     "error": None
        # }

        # 收集精修参数
        result = {}
        result["name"] = os.path.splitext(os.path.basename(image_file))[0]+f"_integrate"+os.path.splitext(os.path.basename(image_control_file))[0]
        result["data_type"] = 2
        with open(image_control_file, 'r') as f:
            image_control_content = f.read()
        result["summary"] = f"使用GSASIIscriptable进行XRD图像的积分，积分控制文件如下：\n{image_control_content}"
        result["researchField"] = "X-Ray Diffraction"
        ## 整理上传的文件
        total_size = 0
        files =[]
        file_url = await upload_file(gpx_path, purpose="user_data")
        file_size = calcu_byte_size(gpx_path)
        files.append({
            "name": os.path.basename(gpx_path),
            "url": file_url,
            "summary": "GSAS-II项目文件.",
            "size": file_size,
            "filePath": gpx_path
        })
        total_size += file_size
        for output_file in output_files:
            file_url = output_files[output_file]
            file_size = calcu_byte_size(output_file)
            files.append({
                "name": os.path.basename(output_file),
                "url": file_url,
                "summary": "衍射数据文件.",
                "size": file_size,
                "filePath": output_file
            })
            total_size += file_size
        result["files"] = files
        result["size"] = total_size
        return result

    except Exception as e:
        return {
            "status": "error",
            "error": f"积分过程中出错: {str(e)}",
            "suggestion": "请检查输入参数或联系管理员"
        }
    
    finally:
        if 'gpx' in locals():
            del gpx

########################
# 粉末衍射
########################
def setup_logging(output_dir):
    """配置日志系统"""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler(Path(output_dir) / 'refinement.log'),
            logging.StreamHandler()
        ]
    )
    return logging.getLogger(__name__)

def initialize_project(xray_data, xray_inst, neutron_data, neutron_inst, cif_files, output_dir, use_xray=True, use_neutron=True):
    """初始化GSAS-II项目并添加数据"""
    logger = logging.getLogger(__name__)
    gpx = G2sc.G2Project(filename=str(Path(output_dir) / 'refinement.gpx'))
    
    hists = []
    phases = []
    
    # 添加X射线数据（如果选择）
    if use_xray and xray_data and xray_inst:
        hist = gpx.add_powder_histogram(xray_data, xray_inst)
        hists.append(hist)
        logger.info(f"添加X射线数据: {xray_data}")
    
    # 添加中子数据（如果选择）
    if use_neutron and neutron_data and neutron_inst:
        hist = gpx.add_powder_histogram(neutron_data, neutron_inst)
        hists.append(hist)
        logger.info(f"添加中子数据: {neutron_data}")
    
    if not hists:
        raise ValueError("至少需要选择一个衍射数据集（X射线或中子）")
    
    # 添加多个相
    if cif_files:
        for i, cif_file in enumerate(cif_files, start=1):
            phase_name = f"Phase_{i}"
            phase = gpx.add_phase(cif_file, phasename=phase_name, histograms=hists)
            phases.append(phase)
            logger.info(f"添加晶体结构 {i}: {cif_file} (名称: {phase_name})")
    
    # 设置最大精修循环次数
    gpx.data['Controls']['data']['max cyc'] = 8
    
    return gpx, hists, phases

def refine_background(gpx, hists, output_dir, cycles=1, step_name='step4'):
    """背景精修"""
    if cycles <= 0:
        return
    
    logger = logging.getLogger(__name__)
    logger.info(f"执行背景精修（{cycles}次）...")
    ref_dict = {"set": {"Background": {"no. coeffs": 3, "refine": True}}}
    for _ in range(cycles):
        gpx.do_refinements([ref_dict])
    gpx.save(str(Path(output_dir) / f'{step_name}.gpx'))

def refine_cell(gpx, phases, output_dir, cycles=1, step_name='step5'):
    """晶胞参数精修"""
    if cycles <= 0:
        return
    
    logger = logging.getLogger(__name__)
    logger.info(f"执行晶胞参数精修（{cycles}次）...")
    ref_dict = {"set": {"Cell": True}}
    for _ in range(cycles):
        gpx.do_refinements([ref_dict])
    gpx.save(str(Path(output_dir) / f'{step_name}.gpx'))

def refine_hap(gpx, phases, hists, output_dir, cycles=1, step_name='step6'):
    """HAP参数精修"""
    if cycles <= 0:
        return
    
    logger = logging.getLogger(__name__)
    for hist in hists:
        for phase in phases:
            logger.info(f"执行HAP参数精修({phase.name}相, {hist.name}数据, {cycles}次)...")
            ref_dict = {"set": {"HStrain": True}}
            for _ in range(cycles):
                gpx.set_refinement(ref_dict, phase=phase, histogram=[hist])
                gpx.do_refinements([{}])
    gpx.save(str(Path(output_dir) / f'{step_name}.gpx'))

def refine_size_strain(gpx, phases, hists, output_dir, cycles=1, step_name='step7'):
    """尺寸和应变精修"""
    if cycles <= 0:
        return
    
    logger = logging.getLogger(__name__)
    for hist in hists:
        for phase in phases:
            logger.info(f"执行尺寸和应变精修({phase.name}相, {hist.name}数据, {cycles}次)...")
            ref_dict = {
                "set": {
                    "Mustrain": {"type": "isotropic", "refine": True},
                    "Size": {"type": "isotropic", "refine": True}
                }
            }
            for _ in range(cycles):
                gpx.set_refinement(ref_dict, phase=phase, histogram=[hist])
                gpx.do_refinements([{}])
    gpx.save(str(Path(output_dir) / f'{step_name}.gpx'))

def refine_sample_atoms(gpx, phases, hists, output_dir, cycles=1, step_name='step8'):
    """样品参数和原子位置精修"""
    if cycles <= 0:
        return
    
    logger = logging.getLogger(__name__)
    logger.info(f"执行样品参数和原子位置精修（{cycles}次）...")
    
    # 对每个hist设置不同的样品参数
    for i, hist in enumerate(hists):
        if i == 0:  # 第一个hist（通常是X射线）
            hist.set_refinements({'Sample Parameters': ['Shift']})
        else:       # 第二个hist（通常是中子）
            hist.set_refinements({'Sample Parameters': ['DisplaceX', 'DisplaceY']})
            hist.data['Sample Parameters']['Gonio. radius'] = 650.0
    
    # 原子位置精修（对所有相）
    for phase in phases:
        phase.set_refinements({"Atoms": {"all": "XU"}})
    
    for _ in range(cycles):
        gpx.do_refinements([{}])
    gpx.save(str(Path(output_dir) / f'{step_name}.gpx'))

def refine_instrument(gpx, hists, output_dir, cycles=1, step_name='step9'):
    """数据范围和仪器参数精修"""
    if cycles <= 0:
        return
    
    logger = logging.getLogger(__name__)
    logger.info(f"执行数据范围和仪器参数精修（{cycles}次）...")
    
    # 设置数据范围（根据hist类型设置不同范围）
    for i, hist in enumerate(hists):
        if i == 0:  # 第一个hist（通常是X射线）
            hist.set_refinements({'Limits': [8.0, 160.0]})
        else:       # 第二个hist（通常是中子）
            hist.set_refinements({'Limits': [8.0, 158.0]})
    
    # 仪器参数精修
    ref_dict = {"set": {'Instrument Parameters': ['U', 'V', 'W']}}
    for _ in range(cycles):
        gpx.do_refinements([ref_dict])
    gpx.save(str(Path(output_dir) / f'{step_name}.gpx'))

async def export_results(
        gpx: G2sc.G2Project, 
        hists: list, 
        phases: list, 
        output_dir: str, 
        save_plot: bool = True,
) -> dict:
    """导出结果和图表"""
    logger = logging.getLogger(__name__)
    results = {
        'rwp_values': {},
        'cell_parameters': {},
        'output_files': [],
        'refinement_pngs': []
    }
    
    # 获取Rwp值
    for hist in hists:
        results['rwp_values'][hist.name] = hist.get_wR()
        logger.info(f"{hist.name} Rwp: {hist.get_wR():.2f}%")
    
    # 获取晶胞参数和相分数
    for phase in phases:
        results['cell_parameters'][phase.name] = phase.get_cell()
    
    # 导出数据
    for i, hist in enumerate(hists, start=1):
        data_file = Path(output_dir) / f'Refinement_data_hist{i}.csv'
        refl_file = Path(output_dir) / f'Refinement_refl_hist{i}.csv'
        
        hist.Export(str(data_file), '.csv', 'hist')
        hist.Export(str(refl_file), '.csv', 'refl')
        
        # results['output_files'].extend([str(data_file), str(refl_file)])
        data_file_url = await upload_file(str(data_file), purpose="user_data")
        refl_file_url = await upload_file(str(refl_file), purpose="user_data")
        results['output_files'].extend([{str(data_file): str(data_file_url)}, {str(refl_file): str(refl_file_url)}])
    
    # 生成并保存图表（每个hist一个子图）
    if hists:
        plt.figure(figsize=(10, 6 * len(hists)))
        
        for i, hist in enumerate(hists, start=1):
            plt.subplot(len(hists), 1, i)
            x = hist.getdata('x')
            y_obs = hist.getdata('yobs')
            y_calc = hist.getdata('ycalc')
            
            plt.plot(x, y_obs, 'b-', label='Observed', linewidth=1)
            plt.plot(x, y_calc, 'r-', label='Calculated', linewidth=1)
            plt.title(f'{hist.name} ({"X-ray" if i == 1 else "Neutron"}) - Rwp: {hist.get_wR():.2f}%')
            plt.legend()
            plt.grid(True, alpha=0.3)
        
        plt.tight_layout()
        plot_path = Path(output_dir) / 'refinement_plot.png'
        plt.savefig(str(plot_path), dpi=300, bbox_inches='tight')
        plt.close()
        refinement_png_url = await upload_file(str(plot_path), purpose="user_data")
        results['refinement_pngs'].append({str(plot_path): refinement_png_url})
    
    return results

async def powder_diffraction_refinement(
    xray_data: str =None, 
    xray_inst: str =None, 
    neutron_data: str =None, 
    neutron_inst: str =None, 
    cif_files: list =None, 
    use_xray: bool =True,
    use_neutron: bool =True,
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
        use_xray (bool): 是否使用X射线数据 (默认True)
        use_neutron (bool): 是否使用中子数据 (默认True)
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
    # 以xray_data的绝对路径作为输出目录确保输出目录存在
    output_dir = os.path.abspath(os.path.dirname(xray_data))
    output_path = Path(output_dir) / f"{thread_id}_output"
    output_path.mkdir(parents=True, exist_ok=True)
    output_dir = str(output_path)
    
    # 设置日志
    logger = setup_logging(output_dir)
    logger.info("="*50)
    logger.info("开始多相结构精修分析")
    logger.info(f"使用X射线数据: {use_xray}")
    logger.info(f"使用中子数据: {use_neutron}")
    logger.info(f"相数量: {len(cif_files) if cif_files else 0}")
    logger.info("="*50)
    
    try:
        # 1. 初始化项目
        gpx, hists, phases = initialize_project(
            xray_data, xray_inst, neutron_data, neutron_inst, cif_files, output_dir,
            use_xray=use_xray, use_neutron=use_neutron
        )
        
        # 2. 执行精修步骤（根据cycles参数决定执行次数）
        refine_background(gpx, hists, output_dir, cycles=refine_background_cycles)
        refine_cell(gpx, phases, output_dir, cycles=refine_cell_cycles)
        refine_hap(gpx, phases, hists, output_dir, cycles=refine_hap_cycles)
        refine_size_strain(gpx, phases, hists, output_dir, cycles=refine_size_strain_cycles)
        refine_sample_atoms(gpx, phases, hists, output_dir, cycles=refine_sample_atoms_cycles)
        refine_instrument(gpx, hists, output_dir, cycles=refine_instrument_cycles)
        
        # 3. 导出结果
        results = await export_results(gpx, hists, phases, output_dir)

        # if results.get('refinement_pngs'):
        #     results['png_urls'] = {}
        #     for plot_png in results['refinement_pngs']:
                # # 4. 上传图床
                # with open(plot_png, 'rb') as f:
                #     img_base64 = base64.b64encode(f.read()).decode()
                # response = requests.post(
                #     "http://0.0.0.0:8010/picgo",
                #     json={
                #         "pic_bin": img_base64,
                #         "pic_name": os.path.basename(plot_png),
                #         "thread_id": thread_id
                #     }
                # )
                
                # if response.status_code == 200:
                #     png_url = response.json()
                # else:
                #     # raise Exception(f"图床上传失败: {response.text}")
                #     png_url = None
                # png_url = await upload_file(plot_png, purpose="user_data")
                # results['png_urls'][os.path.basename(plot_png)] = png_url
        

        
        logger.info("="*50)
        logger.info("精修完成!")
        logger.info("="*50)

        # 记录log
        # with open(Path(output_dir) / 'refinement.log', "r") as f:
        #     logs_text: str = f.read()
        #     results['refinement_logs'] = logs_text
        
        # results['refinement_logs'] = log_url

        # 整理结果
        result = {}
        name =""
        if use_xray:
            xray_data_name = os.path.splitext(os.path.basename(xray_data))[0]
            name += f"{xray_data_name}_"
        if use_neutron:
            neutron_data_name = os.path.splitext(os.path.basename(neutron_data))[0]
            name += f"{neutron_data_name}"
        result["name"] = f"{name}_xrd_refinement"
        result["data_type"] = 3
        refined_params_str = "使用GSASIIscriptable进行粉末衍射精修，精修的一些参数如下：\n"
        refined_params = {
            "refine_background_cycles": refine_background_cycles,
            "refine_cell_cycles": refine_cell_cycles,
            "refine_hap_cycles": refine_hap_cycles,
            "refine_size_strain_cycles": refine_size_strain_cycles,
            "refine_sample_atoms_cycles": refine_sample_atoms_cycles,
            "refine_instrument_cycles": refine_instrument_cycles,
        }
        for key, value in refined_params.items():
            refined_params_str += f"{key}: {value}\n"
        refined_params_str += "精修的结果如下：\n"
        refined_results = {
            "rwp_values": results['rwp_values'],
            "cell_parameters": results['cell_parameters'],
        }
        for key, value in refined_results.items():
            refined_params_str += f"{key}: {value}\n"
        result["summary"] = refined_params_str
        result["researchField"] = "X-ray Diffraction"
        total_size = 0
        files =[]
        for file in results['output_files']:
            if isinstance(file, dict):
                for file, file_url in file.items():
                    file_size = calcu_byte_size(file)
                    files.append({
                        "name": os.path.basename(file),
                        "url": file_url,
                        "summary": "PDF精修结果文件.",
                        "size": file_size,
                        "filePath": file
                    })
                    total_size += file_size
        for file in results['refinement_pngs']:
            for file, file_url in file.items():
                file_size = calcu_byte_size(file)
                files.append({
                    "name": os.path.basename(file),
                    "url": file_url,
                    "summary": "精修结果图表.",
                    "size": file_size,
                    "filePath": file
                })
                total_size += file_size
        refinement_log_path = os.path.join(output_dir, "refinement.log")
        file_url = await upload_file(refinement_log_path, purpose="user_data")
        file_size = calcu_byte_size(refinement_log_path)
        files.append({
            "name": "refinement.log",
            "url": file_url,
            "summary": "精修日志文件.",
            "size": file_size,
            "filePath": refinement_log_path
        })
        total_size += file_size
        result["files"] = files
        result["size"] = total_size
        
        return results
        
    except Exception as e:
        logger.error("!"*50)
        logger.error(f"精修过程中发生错误: {str(e)}")
        logger.error("!"*50)
        raise


if __name__ == '__main__':
    # XRD模拟测试
    import asyncio
    # cif_file = "/aifs/user/home/xiongdb/VSproject/mutil_mcp_tools/files/anonymous/mp-557771.cif"
    # instrument_params = "/aifs/user/home/xiongdb/VSproject/mutil_mcp_tools/GSAS-II-master/GSASII/0data_test/script_data/inst_d1a.prm"
    # two_theta_range = (5.0, 120.0)
    # num_points = 1000
    # output_prefix = None
    # save_plot = True
    # result = asyncio.run(simulate_and_plot_xrd(
    #     cif_file = cif_file,
    #     instrument_params = instrument_params,
    #     two_theta_range = two_theta_range,
    #     num_points = num_points,
    #     output_prefix = output_prefix,
    #     save_plot = save_plot
    # ))
    # print(result)

    # XRD粉末衍射精修测试
     # 输入文件路径
    # input_dir = "/aifs/user/home/xiongdb/VSproject/mutil_mcp_tools/files/anonymous/XRD_Neutron_power_diffraction/"
    # output_dir = "/aifs/user/home/xiongdb/VSproject/mutil_mcp_tools/files/anonymous/XRD_Neutron_power_diffraction/output"
    
    # # 定义多个CIF文件（多相）
    # cif_files = [
    #     os.path.join(input_dir, "PbSO4-Wyckoff.cif"),
    #     #os.path.join(input_dir, "Be.cif")
    # ]
    
    # 执行精修（示例：使用X射线和中子数据，3个相）
    
    # refinement_results = asyncio.run(powder_diffraction_refinement(
    #     xray_data=os.path.join(input_dir, "PBSO4.XRA"),
    #     xray_inst=os.path.join(input_dir, "INST_XRY.PRM"),
    #     neutron_data=os.path.join(input_dir, "PBSO4.CWN"),
    #     neutron_inst=os.path.join(input_dir, "inst_d1a.prm"),
    #     cif_files=cif_files,
    #     use_xray=True,
    #     use_neutron=True,
    #     refine_background_cycles=1,
    #     refine_cell_cycles=2,  # 晶胞参数精修执行2次
    #     refine_hap_cycles=1,
    #     refine_size_strain_cycles=0,  # 不执行尺寸和应变精修
    #     refine_sample_atoms_cycles=1,
    #     refine_instrument_cycles=1
    # ))
    
    # print(refinement_results)
    # # 打印结果
    # print("\n=== 精修结果 ===")
    # print("Rwp值:")
    # for name, rwp in refinement_results['rwp_values'].items():
    #     print(f"{name}: {rwp:.2f}%")
    
    # print("\n晶胞参数:")
    # for phase_name, cell_params in refinement_results['cell_parameters'].items():
    #     print(f"\n{phase_name}:")
    #     print(cell_params)
    
    # print("\n生成的文件:")
    # for file in refinement_results['output_files']:
    #     print(f"- {file}")
