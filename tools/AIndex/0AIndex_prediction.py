import logging
from pathlib import Path
import time
import random
from typing import Dict
from mcp.server.fastmcp import FastMCP
import os,sys
import multiprocessing
from scipy.interpolate import interp1d
from iTrain import Conv1DModel_4096, predict
from spacegroup_list import hm_to_space_group_number
import numpy as np

# 创建 FastMCP 实例，端口 50052
mcp = FastMCP(
    name="AIndex_Tool",
    instructions="A tool for predicting the crystal system, space group and lattice parameter from the powder neutron diffraction pattern by using Neural Network.",
    port=50053,
)

def setup_logging(output_dir):
    """配置日志系统"""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler(Path(output_dir) / 'AIndex.log'),
            logging.StreamHandler()
        ]
    )
    return logging.getLogger(__name__)

def load_and_preprocess_data(pattern_file: str):
    """
    加载并预处理衍射谱数据。

    Args:
        pattern_file: 衍射谱文件的路径

    Returns:
        预处理后的数据
    """
    # 读取文件，提取前两列
    data = np.loadtxt(pattern_file, usecols=(0, 1))
    x, y = data[:, 0], data[:, 1]
    # 创建插值函数
    interp_func = interp1d(x, y, kind='linear', bounds_error=False, fill_value=0)
    # 生成新的 x 值
    new_x = np.linspace(0.5, 10, 4096)
    # 对 y 进行插值
    new_y = interp_func(new_x)
    # 对 new_y 进行归一化
    max_y = np.max(new_y)
    if max_y > 0:  # 避免除以 0
        new_y = new_y / max_y
    return new_x, new_y

def predict_crystal_system(y):
    csNet = Conv1DModel_4096(input_channels=1, output_num=3)
    base_path = os.path.dirname(__file__)
    result, _, _= predict(y, csNet, os.path.join(base_path, 'model_weights/csNet.pth'),task_type='classification')
    csmap = ["tetragonal","hexa_trigonal","cubic"]
    cs = csmap[result]
    return cs

def predict_space_group(y, cs):
    symmetrymap = {"tetragonal":31, "hexa_trigonal":9, "cubic":17}
    cs_sg_map={"tetragonal":[75,143],"hexa_trigonal":[143,195],"cubic":[195,231]}
    symmetryNet = Conv1DModel_4096(input_channels=1, output_num=symmetrymap[cs])
    base_path = os.path.dirname(__file__)
    result, _, sg_distribution = predict(y, symmetryNet, os.path.join(base_path, f'model_weights/symmetryNet_{cs}.pth'),task_type='classification')
    space_groups = []
    for index in range(cs_sg_map[cs][0],cs_sg_map[cs][1]):
        if hm_to_space_group_number[str(index)][2] == result:
            space_groups.append([index,hm_to_space_group_number[str(index)][0]])
    space_groups_str = ', '.join([f"{sg[0]} ({sg[1]})" for sg in space_groups])
    return space_groups_str

def predict_lattice_parameters(y, cs):
    latticeNet = Conv1DModel_4096(input_channels=1, output_num=6)
    base_path = os.path.dirname(__file__)
    lattice = predict(y, latticeNet, os.path.join(base_path, f'model_weights/latticeNet_{cs}.pth'), task_type='regression')
    lattice_params = [
        round(lattice[0][0] * 15, 4), 
        round(lattice[0][1] * 15, 4),
        round(lattice[0][2] * 15, 4),
        round(lattice[0][3] * 180, 2),
        round(lattice[0][4] * 180, 2),
        round(lattice[0][5] * 180, 2)
    ] 
    return lattice_params


def run_AIndex_predict(pattern_file: str, output_dir: str, result_queue=multiprocessing.Queue()):
    """
    调用AIndex神经网络来预测空间群。

    Args:
        pattern_file: 衍射谱文件的绝对路径
        output_dir: 输出目录
        result_queue: 用于返回结果的队列
    """
    # 创建输出目录
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    # 设置日志
    logger = setup_logging(output_dir)
    logger.info("="*50)
    logger.info("开始AI结构预测")
    logger.info(f"中子数据: {os.path.basename(pattern_file)}")
    logger.info("="*50)

    try:
        x,y = load_and_preprocess_data(pattern_file)
        cs = predict_crystal_system(y)
        space_groups = predict_space_group(y, cs)
        lattice_params = predict_lattice_parameters(y, cs)
        result = {
            "crystal_system": cs,
            "space_group": space_groups,
            "lattice_parameters": {
                "a": lattice_params[0],
                "b": lattice_params[1],
                "c": lattice_params[2],
                "alpha": lattice_params[3],
                "beta": lattice_params[4],
                "gamma": lattice_params[5]
            }
        }
        
        logger.info("="*50)
        logger.info(f"AI结构预测完成！晶系: {result['crystal_system']}, 空间群: {result['space_group']}, 晶格参数: {result['lattice_parameters']}")
        logger.info("="*50)
        result_queue.put(result)
        return result

    except Exception as e:
        logger.error("!"*50)
        logger.error(f"预测失败: {str(e)}")
        logger.error("!"*50)
        result_queue.put({})
        raise



id_to_task={}
id_to_result={}

@mcp.tool()
def AIndex_Prediction(pattern_file: str,
                      task_id: str | None = None, 
                      thread_id: str | None = None) -> Dict:
    """
    调用AIndex神经网络来预测空间群。

    Args:
        pattern_file: 衍射谱文件的绝对路径
        task_id: 任务 id，默认自动生成
        message: 线程 id,默认自动生成

    Returns:
        一个包含 task_id, state, result 的字典, result本身是一个包含结构化输出的字典
    """
    id_=task_id
    result_json={"id":id_,'state':None,'result':None}
    output_dir = os.path.join(os.path.dirname(pattern_file), f"output/{thread_id}/AIndex_output_{task_id}")

    if id_ not in id_to_task or id_to_task[id_]==None:
        # 任务创建
        id_to_result[id_]=multiprocessing.Queue()
        id_to_task[id_]=multiprocessing.Process(
            target=run_AIndex_predict, 
            args=(pattern_file, output_dir, id_to_result[id_]))
        
        result_json['state']='created'
        id_to_task[id_].start()

    else:
        if not id_to_task[id_].is_alive():
            info=id_to_result[id_].get()
            id_to_task[id_].join()
            if not info:
                # 任务失败
                result_json['state']='completed'
                result_json['status']='error'
                result_json['result']={
                    "status":"error",
                    'error':'结构预测失败',
                    'suggestion':'请检查输入的衍射谱文件是否正确'}
                result_json['summary']="AIndex结构预测失败"
            else:
                # 处理结果
                print(info)
                id_to_task[id_]=None
                result_json['state']='completed'
                result_json['status']='success'
                result_json["name"] = f"{task_id}_AIndex_Prediction"
                
                # 构建摘要信息
                crystal_system = info.get('crystal_system', "Unknown")
                space_group = info.get('space_group', "Unknown")
                lattice_parameters = info.get('lattice_parameters', {})
                result_json["summary"] = f"晶体结构预测完成，晶系: {crystal_system}, 空间群: {space_group}, 晶格参数: {lattice_parameters}"

                # 构建详细结果
                result_json['result'] = {
                    "status":"success",
                    "input_pattern_file": pattern_file,
                    "output_dir": output_dir,
                    "prediction_results": {
                        "crystal_system": crystal_system,
                        "space_group": space_group,
                        "lattice_parameters": lattice_parameters
                    },
                "processing_info": "使用AIndex神经网络进行预测完成，包含晶系、空间群和晶格参数。"
                }
        else:
            result_json['state']='running'

    return result_json


if __name__ == '__main__':
    # 以 SSE 方式启动服务，监听端口 50052
    mcp.run(transport='sse')

# if __name__ == "__main__":
#     pattern_file = "/home/wangh/rz_agent_test/datasets/BL16/RUNdataFiles/RUN0023568/sam_RUN0023568_crop_carpenterCorrection_offset_time(2025-06-24-220934)_group2_group2.txt"
#     output_dir = "/home/wangh/rz_agent_test/datasets/BL16/RUNdataFiles/RUN0023568/AIndex_output_test"
#     result = run_AIndex_predict(pattern_file, output_dir)
#     print("预测结果:", result)
