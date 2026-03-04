from typing import List, Dict, Any
import os
from gsasii_api import simulate_and_plot_xrd, powder_diffraction_refinement, integrate_single_image
from fastapi import FastAPI
from pydantic import BaseModel
import uvicorn
import os, sys
here = os.path.abspath(os.path.dirname(__file__))

app = FastAPI(title="GSAS-II API", description="A RESTful API for GSA-II", version="0.1")

# 使用GSAS-II的simulate_and_plot_xrd函数进行XRD模拟并生成图像
class XRD_Simulate_Request(BaseModel):
    cif_file: str
    output_prefix: str = None
    instrument_params: str = os.path.join(here, '..','GSASII/0data_test/script_data/inst_d1a.prm')
    two_theta_range: tuple[float, float] = (5.0, 120.0)
    num_points: int = 1000
    save_plot: bool = True
    thread_id: str = "anonymous"

@app.post("/api/xrd_simulate_gsasii", tags=["GASA-II"])
async def xrd_simulate_gsasii_endpoint(request: XRD_Simulate_Request) -> Dict[str, Any] :
    """模拟XRD图谱并绘制图表
    Args:
        cif_file (str): CIF文件的绝对路径
        output_prefix (str): 输出文件前缀，默认为cif文件名的前缀
        instrument_params (str): 仪器参数的绝对路径，默认使用测试数据
        two_theta_range (tuple): 衍射的2theta范围 (min, max), 默认为(5.0, 120.0)
        num_points (int): XRD二维曲线拟合所需要的数据点数
        save_plot (bool): 是否保存图表，默认True
        thread_id: 前端用户的请求ID，用于记录日志，固定传入，无需考虑

    Returns:
        dict: 包含模拟结果和图表文件的路径
        {
            "status": "success",
            "project_file": str,  # 项目文件路径
            "data_file": str,  # 数据文件路径
            "reflections_file": str,  # 反射文件路径
            "plot_file": str,  # 图表文件路径（如果保存）
            "png_url": str,  # 图表文件上传后的URL（如果保存）
            "error": str  # 错误信息（如果有的话）
        }
        """
    # TODO: 增加用户验证和缓存机制-将查询的结果缓存到redis中
    return await simulate_and_plot_xrd(
        cif_file=request.cif_file,
        instrument_params=request.instrument_params,
        two_theta_range=request.two_theta_range,
        num_points=request.num_points,
        output_prefix=request.output_prefix,
        save_plot=request.save_plot,
        thread_id=request.thread_id,
    )

class Integrate_single_image_Request(BaseModel):
    image_file: str
    image_control_file: str
    thread_id: str = "anonymous"

@app.post("/api/integrate_single_image_gsasii", tags=["GASA-II"])
async def integrate_single_image_endpoint(request: Integrate_single_image_Request) -> Dict[str, Any]:
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
    return await integrate_single_image(
        image_file=request.image_file,
        image_control_file=request.image_control_file,
        thread_id=request.thread_id,
    )


class Xray_Neutron_XRD_refinement_Request(BaseModel):
    xray_data: str =None
    xray_inst: str =None
    neutron_data: str =None
    neutron_inst: str =None
    cif_files: list =None
    use_xray: bool =True
    use_neutron: bool =True
    refine_background_cycles: int =1
    refine_cell_cycles: int =1
    refine_hap_cycles: int =1
    refine_size_strain_cycles: int =1
    refine_sample_atoms_cycles: int =1
    refine_instrument_cycles: int =1
    thread_id: str = "anonymous"

@app.post("/api/xray_neutron_xrd_refinement_gsasii", tags=["GASA-II"])
async def powder_diffraction_refinement_endpoint(request: Xray_Neutron_XRD_refinement_Request) -> Dict[str, Any]:
    """
    粉末衍射数据精修主函数（支持多相），支持X射线和中子数据，支持多种精修步骤。
    
    参数:
        xray_data (str): X射线数据文件路径，如果没有则设置为None
        xray_inst (str): X射线仪器参数文件路径，如果没有则设置为None
        neutron_data (str): 中子数据文件路径，如果没有则设置为None
        neutron_inst (str): 中子仪器参数文件路径，如果没有则设置为None
        cif_files (list): CIF结构文件路径列表
        use_xray (bool): 是否使用X射线数据 (默认True)
        use_neutron (bool): 是否使用中子数据 (默认True)
        refine_*_cycles (int): 各精修步骤的执行次数，0表示不执行,具体如下:
            refine_background_cycles：进行背景精修的次数
            refine_cell_cycles：进行晶胞参数精修的次数
            refine_hap_cycles：进行HAP参数精修的次数
            refine_size_strain_cycles：进行尺寸和应变精修的次数
            refine_sample_atoms_cycles：进行样品参数和原子位置精修的次数
            refine_instrument_cycles：进行数据范围和仪器参数精修的次数
        thread_id (str): 图床上传线程ID，固定传入，无需考虑，默认为"anonymous"

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
    # TODO: 增加用户验证和缓存机制-将查询的结果缓存到redis中
    return await powder_diffraction_refinement(
        xray_data=request.xray_data,
        xray_inst=request.xray_inst,
        neutron_data=request.neutron_data,
        neutron_inst=request.neutron_inst,
        cif_files=request.cif_files,
        use_xray=request.use_xray,
        use_neutron=request.use_neutron,
        refine_background_cycles=request.refine_background_cycles,
        refine_cell_cycles=request.refine_cell_cycles,
        refine_hap_cycles=request.refine_hap_cycles,
        refine_size_strain_cycles=request.refine_size_strain_cycles,
        refine_sample_atoms_cycles=request.refine_sample_atoms_cycles,
        refine_instrument_cycles=request.refine_instrument_cycles,
        thread_id=request.thread_id,
    )

if __name__ == '__main__':
    uvicorn.run(
        app, 
        host="0.0.0.0", 
        port=int(os.getenv("GSASII_API_PORT", 8085)),  # 读取环境变量PORT或默认8000
        log_level="info"
    )