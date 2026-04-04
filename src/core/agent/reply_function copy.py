# -*- coding: utf-8 -*-
import glob
import os, asyncio, json

# Model client
from drsai import HepAIChatCompletionClient, CancellationToken
from drsai import AssistantAgent

# messages
from autogen_core.models import (
    ChatCompletionClient,
    CreateResult,
    FunctionExecutionResultMessage,
    FunctionExecutionResult,
    LLMMessage,
    UserMessage,
    AssistantMessage,
    SystemMessage,
    RequestUsage,
)
from autogen_agentchat.base import Handoff
from autogen_agentchat.messages import (
    AgentEvent,
    ChatMessage,
    HandoffMessage,
    MemoryQueryEvent,
    ModelClientStreamingChunkEvent,
    TextMessage,
    ToolCallExecutionEvent,
    ToolCallRequestEvent,
    ToolCallSummaryMessage,
    UserInputRequestedEvent,
    ThoughtEvent
)

# tools
from autogen_core import FunctionCall
from drsai import (
    BaseTool,
    FunctionTool,
    StaticWorkbench, 
    Workbench, 
    ToolResult,
    ToolSchema
)

# threading
# from drsai import Thread, ThreadsManager

# others
from typing import List, Dict, Any, Union, AsyncGenerator, Optional
import uuid
import traceback
from datetime import datetime
import re

# 文件夹路径
from src.core.sensors.read_data import get_run_data

# 结构API
from tools.material_project.structure_api import mp_search_cif_api
# 获取GSASII工具
# from pathlib import Path
# import sys
# here = Path(__file__).parent.resolve()
# tool_path = here.parent.parent.parent.joinpath("tools/GSAS-II-master/GSASII/0neutron_gsas_refine.py")
# sys.path.insert(0, str(tool_path.parent))
# 当前新架构通过 MCP 工具 write_info 调用 GSAS-II。

# ASE
from ase.io import read
from ase.visualize.plot import plot_atoms
import matplotlib.pyplot as plt
import warnings

def get_data_handle_info(
        reply_type: str|None = None,
        rongzai_status: str|None = None,
        gsas_ii_status: str|None = None,
        beamline: str|None = None,
        run_id: str|None = None,
        detector: str|None = None,
        cifs: List[str]|None=None,
        gpx_path: str|None = None,
        refine_params: Dict|None = None,
        refine_idx: int|None = None,
        info: str|None = None,
) -> Dict[str, str]:
    '''
    
    有3种类型的用户输入和1种类型的GSASII工具输入会让你用到下面的这些提示：
        用户输入类型1: 用户第一次要求你精修衍射数据，你需要收集完整的信息来进行精修。
        用户输入类型2: 你已经完成精修并且返回给用户，用户再次要求你精修。
        用户输入类型3: 你已经完成精修并且返回给用户，用户认为不需要继续精修。
        GSASII工具输入: 它返回给你一个结构化的字典，包含了它刚刚完成的精修的结果。
    
    不同的输入类型需要你收集的信息不同，GSASII工具的输入甚至需要你根据知识和经验来决定下一步的精修参数。
    
    Args:
        reply_type: 请求的类型, 如"input_from_user_type1", "input_from_user_type2", "input_from_user_type3", "input_from_gsasii"
        rongzai_status: 你的状态需要改变为哪个, 有complete、running、awaiting_input三种情况
        gsas_ii_status: GSASII工具状态需要改变为哪个, 有running、awaiting_input两种情况
        beamline: 谱仪名称, 如BL16、BL18、BL09、BL15，BL08,BL13等
        run_id: 需要处理的实验RUN号, 如RUN0021905; 如果用户写成run21905等格式,或者写错(如ru21905等), 需要转换成RUN0021905格式
        detector: 用户需要处理的探测器编号,如"group5",“bank14”,"module11203"等
        cifs: 用户衍射精修需要处理的CIF文件列表，如["a.cif", "b.cif"]; 注意这里用户给的cif文件名可能不规范，比如不包含后缀或后缀不为cif，需要转换成规范的格式
        gpx_path: 用户输入类型2以及GSASII工具输入的时候才需要, 准备返回给用户或者GSASII工具的gpx文件路径,一般在上下文信息中能找到
        refine_params: 用户输入类型2以及GSASII工具输入的时候才需要, 用户要求你继续精修的参数的字典或者你跟为需要继续精修的参数的字典
        refine_idx: 用户输入类型2以及GSASII工具输入的时候才需要, 你最后一次精修的refine_idx值,一般在上下文信息中能找到
        info: 你对你利用这个get_data_handle_info函数做出的输出的理由说明

        这里详细说明一下refine_params的格式:
        它是一个字典，键是你需要精修的参数名称，键值的格式如下
        ## 支持的所有精修参数
        ### 1. 背景参数 (Background)
        | 参数 | 类型 | 默认值 | 说明 |
        |------|------|--------|------|
        | `refine_background` | bool | False | 是否精修背景 |
        | `background_coeffs` | int | 3 | 背景函数系数数量 |

        **GSAS-II 对应**:
        - Background function
        - Number of coeff

        ### 2. Limits 参数
        | 参数 | 类型 | 默认值 | 说明 |
        |------|------|--------|------|
        | `refine_limits` | bool | False | 是否精修时间范围限制 |

        **GSAS-II 对应**:
        - Original Tmin
        - Original Tmax

        ### 3. 仪器参数 (Instrument Parameters)
        
        | 参数 | 类型 | 默认值 | GSAS-II 对应 | 说明 |
        |------|------|--------|--------------|------|
        | `refine_instrument_difc` | bool | False | difC | 衍射常数 C |
        | `refine_instrument_difa` | bool | False | difA | 衍射常数 A |
        | `refine_instrument_difb` | bool | False | difB | 衍射常数 B |
        | `refine_instrument_zero` | bool | False | Zero | 零点偏移 |
        | `refine_instrument_alpha` | bool | False | alpha | 角度相关峰宽参数 |
        | `refine_instrument_beta` | bool | False | beta-0, beta-1, beta-q | 各向异性峰形参数 |
        | `refine_instrument_sig` | bool | False | sig-0, sig-1, sig-2, sig-q | 峰宽分布参数 |
        | `refine_instrument_x` | bool | False | X | 仪器几何参数 X |
        | `refine_instrument_y` | bool | False | Y | 仪器几何参数 Y |
        | `refine_instrument_z` | bool | False | Z | 仪器几何参数 Z |

        ### 4. 样品参数 (Sample Parameters)
        | 参数 | 类型 | 默认值 | GSAS-II 对应 | 说明 |
        |------|------|--------|--------------|------|
        | `refine_scale` | bool | False | scale | 标度因子 |
        | `refine_absorption` | bool | False | absorption | 吸收参数 |
        | `refine_sample_displacement` | bool | False | DisplaceX, DisplaceY | 样品位移 |

        ### 5. 晶胞参数 (Unit Cell)
        | 参数 | 类型 | 默认值 | GSAS-II 对应 | 说明 |
        |------|------|--------|--------------|------|
        | `refine_cell` | bool | False | Unit cell | 晶胞参数 (a, b, c, α, β, γ) |

        ### 6. 原子参数 (Atoms)

        #### 6.1 整体控制
        | 参数 | 类型 | 默认值 | 说明 |
        |------|------|--------|------|
        | `refine_atoms` | bool | False | 整体精修原子参数(位置+温度因子+占位) |

        #### 6.2 单独控制
        | 参数 | 类型 | 默认值 | GSAS-II 标签 | 说明 |
        |------|------|--------|-------------|------|
        | `refine_atoms_pos` | bool | False | X | 原子位置 (fractional coordinates) |
        | `refine_atoms_uiso` | bool | False | U | 各向同性温度因子 |
        | `refine_atoms_occ` | bool | False | F | 原子占位度 |

        ### 7. 高级参数 (Advanced)
        | 参数 | 类型 | 默认值 | GSAS-II 对应 | 说明 |
        |------|------|--------|--------------|------|
        | `refine_size` | bool | False | Domain size model, size | 晶粒尺寸模型和尺寸 |
        | `refine_mustrain` | bool | False | Mustrain model, microstrain | 微应变模型和微应变 |
        | `refine_phase_fraction` | bool | False | Phase fraction | 多相精修中的相分数 |

         
    遇到用户输入类型1的请求：
        从聊天记录中获取用户需要处理的谱仪类型(beamline), run号(run_id), 探测器编号(detector)、cif文件列表(cifs)。

        案例说明:
        案例1: 用户需要对BL16的RUN0021905的group4的衍射数据进行精修处理，精修的CIF的文件都为Si.cif，
        分析: 此时用户给出了明确谱仪：BL16、run_id：RUN0021905，group:"group4"，并要求精修衍射，但没有指定精修参数和轮次, 则相应的参数为：
        {
            "reply_type":"input_from_user_type1",
            "rongzai_status": "running",
            "gsas_ii_status": "running",
            "beamline": "BL16",
            "run_id": "RUN0021905",
            "detector": "group4",
            "cifs":["Si.cif"],
            "refine_params": {},
            "refine_idx": -1,
            "gpx_path": None,
            "info": 你自己决定
        }
    
    遇到用户输入类型2的请求：
        从聊天记录中获取用户需要处理的精修参数。
        默认从上下文中拿到最后一次精修的refine_idx以及对应的gpx文件(gsasii的工程文件)的路径gpx_path，如果用户要求用修之前的结果，则相应调整refine_idx和gpx_path。
        案例1:用户需要继续精修，精修的参数是开放晶胞和仪器参数的精修
        分析: 此时用户给出了明确的精修参数:晶胞和仪器参数，相应参数为：
        {
            "reply_type":"input_from_user_type2",
            "rongzai_status": "awaiting_input",
            "gsas_ii_status": "running",
            "beamline": None,
            "run_id": None,
            "detector": None,
            "cifs": None,
            "gpx_path": 根据用户需求给出gpx文件路径,
            "refine_params": {"refine_cell": True, "refine_instrument": True, "cycle": 3},
            "refine_idx": 你最后一次精修的refine_idx值,
            "info": 你自己决定
        }
        注意cycle是必须有的，用户不给的话，默认就是3轮精修。
    
    遇到用户输入类型3的请求：
        用户认为不需要继续精修，或者用户认为可以结束精修
        分析: 此时用户明确表示不需要继续精修，相应参数为：
        {
            "reply_type":"input_from_user_type3",
            "rongzai_status": "complete",
            "gsas_ii_status": "awaiting_input",
            "beamline": None,
            "run_id": None,
            "detector": None,
            "cifs": None,
            "gpx_path": None,
            "refine_params": None,
            "refine_idx": None
            "info": 你自己决定
        }

    遇到GSASII工具输入的请求：
        这时你是精修专家状态，专注于根据GSASII工具返回的精修结果(GSASII工具输入)和你的知识来决定下一步的精修参数
        GSASII工具的输入包含以下字段:
        {refine_idx: int, gpx_path_current: str, gpx_path_before: str, refine_result: dict}

        案例1: 还没有精修过,工具只是初始化产生了工程文件gpx，这表现为refine_idx=0
        {"reply_type":"input_from_gsasii",
        "rongzai_status": "running",
        "gsas_ii_status": "running",
        "beamline": None,
        "run_id": None,
        "detector": None,
        "cifs": None,
        "gpx_path": 你接收到的gpx_path_current字符串,
        "refine_params": 你自己决定要修哪些参数，然后根据我而和你说的格式组织成字典,
        "refine_idx": 输入的refine_idx值,
        "info": 这里是一个字符串，说明你这样精修的理由
        }

        案例2: 你已经精修过两次了，当前refine_idx=2，精修结果还是不够好
        分析: 第三次精修，可以在上一次基础上开放仪器参数的精修，相应参数为：
        {"reply_type":"input_from_gsasii",
        "rongzai_status": "running",
        "gsas_ii_status": "running",
        "beamline": None,
        "run_id": None,
        "detector": None,
        "cifs": None,
        "gpx_path": 你接收到的gpx_path_current字符串,
        "refine_params": {"refine_background": True, "refine_scale": True,"refine_cell": True,"refine_instrument": True, "cycle": 3},
        "refine_idx": 2
        "info": "这是第三次精修，我决定开放仪器参数的精修，因为上次的精修结果还是不够好"}

        
        案例3: 你已经精修过一次了，当前refine_idx=1，精修结果还不够好
        分析: 第二次精修，可以在上一次基础上开放晶胞参数的精修，相应参数为：
        {"reply_type":"input_from_gsasii",
        "rongzai_status": "running",
        "gsas_ii_status": "running",
        "beamline": None,
        "run_id": None,
        "detector": None,
        "cifs": None,
        "gpx_path": 你接收到的gpx_path_current字符串,
        "refine_params": {"refine_background": True, "refine_scale": True,"refine_cell": True, "cycle": 3},
        "refine_idx": 1
        "info": "这是第二次精修，我决定开放晶胞参数的精修，因为上次的精修结果不够好"}

        
        案例4: 你已经精修过三次了，当前refine_idx=3，精修结果还是不够好
        分析: 第四次精修，可以在上一次基础上开放吸收参数和原子参数的精修，相应参数为：
        {"reply_type":"input_from_gsasii",
        "rongzai_status": "running",
        "gsas_ii_status": "running",
        "beamline": None,
        "run_id": None,
        "detector": None,
        "cifs": None,
        "gpx_path": 你接收到的gpx_path_current字符串,
        "refine_params": {"refine_background": True, "refine_scale": True,"refine_cell": True,"refine_instrument": True,"refine_absorption": True,"refine_atoms": True, "cycle": 3},
        "refine_idx": 3
        "info": "这是第四次精修，我决定开放吸收参数和原子参数的精修，因为上次的精修结果还是不够好"}

        案例6: 当你接收到新一轮的输入后，发现上次的精修结果和上上次比差了很多，
        分析: 说明上次精修结果无法接受，你需要重新从上上次的结果开始精修，相应参数为：
        {"reply_type":"input_from_gsasii",
        "rongzai_status": "running",
        "gsas_ii_status": "running",
        "beamline": None,
        "run_id": None,
        "detector": None,
        "cifs": None,
        "gpx_path": 你接收到的gpx_path_before字符串,
        "refine_params": 在上上次结构的基础上，调整你认为需要调整的精修参数,
        "refine_idx": 你接收到的refine_idx值减1,
        "info": "我认为上次的精修结果不够好，需要重新从上上次的结果开始精修"}

        案例7: 你已经精修n次了，当前refine_idx=n，精修结果已经足够好
        分析: 你可以选择结束精修，相应参数为：
        {"reply_type":"input_from_gsasii",
        "rongzai_status": "awaiting_input",
        "gsas_ii_status": "awaiting_input",
        "beamline": None,
        "run_id": None,
        "detector": None,
        "cifs": None,
        "gpx_path": "",
        "refine_params": {},
        "refine_idx": n
        "info": "我认为当前的精修结果已经足够好，不需要继续精修了"}

        补充注意事项：如果用户精修的是标准样品（如Si），需要避免去修原子位置。


    '''
    return {
        "reply_type": reply_type,
        "rongzai_status": rongzai_status,
        "gsas_ii_status": gsas_ii_status,
        "beamline":beamline,
        "run_id": run_id,
        "detector":detector,
        "cifs":cifs,
        "refine_params": refine_params,
        "refine_idx": refine_idx,
        "gpx_path": gpx_path,
        "info": info
    }

def validate_gsas_refine_params(refine_params: Dict) -> Dict:
        """验证GSAS精修参数格式，确保order值不重复"""
        validated_params = refine_params.copy()
        
        # 定义参数的有效范围
        min_order = 1
        max_order = 6
        min_cycle = 0
        max_cycle = 5
        
        valid_ranges = {
            "refine_background": {"order": (min_order, max_order), "cycles": (min_cycle, max_cycle)},
            "refine_cell": {"order": (min_order, max_order), "cycles": (min_cycle, max_cycle)},
            "refine_scale": {"order": (min_order, max_order), "cycles": (min_cycle, max_cycle)},
            "refine_instrument": {"order": (min_order, max_order), "cycles": (min_cycle, max_cycle)},
            "refine_absorption": {"order": (min_order, max_order), "cycles": (min_cycle, max_cycle)},
            "refine_atoms": {"order": (min_order, max_order), "cycles": (min_cycle, max_cycle)},
        }
        
        # 收集所有order值用于重复检查
        order_values = []
        
        for param_name, param_value in refine_params.items():
            if param_name in valid_ranges:
                # 验证参数格式 [顺序, 循环次数]
                if not isinstance(param_value, list) or len(param_value) != 2:
                    raise ValueError(f"参数 {param_name} 格式错误，应为 [顺序, 循环次数]")
                
                order, cycles = param_value
                if not isinstance(order, int) or not isinstance(cycles, int):
                    raise ValueError(f"参数 {param_name} 的顺序和循环次数必须为整数")
                
                # 验证范围
                if order < min_order or order > max_order:
                    raise ValueError(f"参数 {param_name} 的顺序应在 {min_order}-{max_order} 范围内")
                
                if cycles < min_cycle or cycles > max_cycle:
                    raise ValueError(f"参数 {param_name} 的循环次数应在 {min_cycle}-{max_cycle} 范围内")
                
                # 检查order值是否重复
                if order in order_values:
                    raise ValueError(f"精修顺序值 {order} 重复，所有精修模块的顺序必须唯一")
                order_values.append(order)
        
        return validated_params

def verify_data_handle_info(
        data_handle_info: Dict[str, str],
        data_dir: str,
) -> Dict[str, Any]:
    """
    验证数据处理信息是否合法
    Args:
        data_handle_info: 数据处理信息字典
        data_dir: 用户数据所在的目录
    Returns:
        data: 验证后的处理信息字典，包括各个需要处理数据的文件绝对路径
    """

    # print(f"DEBUG: 接收到的数据信息: {json.dumps(data_handle_info, indent=2, ensure_ascii=False)}")

    status = "success"
    message = ""
    data = {}
    # 直接使用传入的data_handle_info，在其基础上补充信息
    verified_info = data_handle_info.copy()

    ## 检查run_id, beamline, group名称是否存在
    beamline = data_handle_info.get("beamline")
    run_id = data_handle_info.get("run_id")

    if not beamline:
        status = "error"
        message = "我没有获取到谱仪名称，请指定，如BL16、BL18、BL09、BL15等"
        return {"status": status, "message": message, "data": data}

    if not run_id:
        status = "error"
        message = "我没有获取到RUN号，请指定"
        return {"status": status, "message": message, "data": data}

    #根据谱仪与run_id构建路径,查询是否能找到这个RUN
    run_dir = os.path.join(data_dir, f"{beamline}/RUNdataFiles/{run_id}")
    if not os.path.exists(run_dir):
        status = "error"
        message = f"我关于谱仪{beamline}的数据库中没有{run_id},请确认你提供的谱仪名称和RUN号是否正确，或将数据上传"
        return {"status": status, "message": message, "data": data}
    else:
        pass
        # data["run_dir"] = run_dir

    ## 检查experiment.json是否存在
    if not os.path.exists(os.path.join(run_dir, "experiment.json")):
        status = "error"
        message = f"在{run_id}里面没有找到实验信息文件"
        return {"status": status, "message": message, "data": data}

    experiment_info = get_experiment_info(run_dir)
    verified_info["instrument_user_info"] = experiment_info
    
    run_file_list = get_run_data(run_dir)


    # print(f"DEBUG: 接收到的数据信息: {json.dumps(verified_info, indent=2, ensure_ascii=False)}")
    # print(data_handle_info.get("groups",None))
    ## 检查cif_files是否存在 TODO:从material_project_id获取cif文件
    cif_file_dir = os.path.join(run_dir,"cifFiles")
    if not os.path.exists(cif_file_dir):
        status = "error"
        message = f"在{run_id}下没有找到cif文件目录, 请上传"
        return {"status": status, "message": message, "data": data}
    
    # 检查detector是否存在
    detector = data_handle_info.get("detector", None)
    if detector is None:
        status = "error"
        message = "我没有获取到探测器编号，请告诉我"
        return {"status": status, "message": message, "data": data}
    else:
        #根据detector和beamline和runno获取对应的gsa文件
        gsa_file = os.path.join(run_dir, f"{run_id.upper()}_{detector}.gsa")
        if not os.path.exists(gsa_file):
            status = "error"
            message = f"我没有在{run_id}下找到{run_id.upper()}_{detector}.gsa文件，请检查探测器编号是否正确，或数据是否上传完整"
            return {"status": status, "message": message, "data": data}
        else:
            verified_info["gsa_path"] = gsa_file
        
        # 根据detector和beamline获取对应的instrument文件
        pattern = os.path.join(os.path.join(data_dir, beamline, "instrumentFiles"), f"*{detector}*.instprm")
        matches = glob.glob(pattern)
        if matches:
            instprm_file = matches[0]
            verified_info["instprm_path"] = instprm_file
    
    cifs = data_handle_info.get("cifs", None)
    if cifs is None or len(cifs) == 0:
        status = "error"
        message = "我没有获取到cif文件列表, 请告诉我是哪些cif文件"
        return {"status": status, "message": message, "data": data}
    else:
        cif_files = []
        for cif_file in cifs:
            cif_file_path = os.path.join(cif_file_dir, cif_file)
            if os.path.exists(cif_file_path):
                cif_files.append(cif_file_path)
            else:
                status = "error"
                message = f"cif_file {cif_file}不存在或路径不正确"
                return {"status": status, "message": message, "data": data}
        verified_info["cif_paths"] = cif_files


    ### 检查handle_pdf,是否做PDF，接下来判断：
    if data_handle_info.get("handle_pdf", False):
        pdf_cifs = data_handle_info.get("pdf_cifs", None)
        if pdf_cifs is None:
            status = "error"
            message = "处理PDF数据时，必须明确指定需要处理的PDF数据的CIF文件列表和精修参数设置"
            return {"status": status, "message": message, "data": data}
        else:
            pdf_files = []
            data_handle_info["pdf_files"] = {}
            for cif_file in pdf_cifs:
                cif_file_path = os.path.join(cif_file_dir, cif_file)
                if os.path.exists(cif_file_path):
                    pdf_files.append(cif_file_path)
                else:
                    status = "error"
                    message = f"cif_file {cif_file}不存在或路径不正确"
                    return {"status": status, "message": message, "data": data}
            data_handle_info["pdf_files"]["cif_files"] = pdf_files
            ### 检查是否有G_r_*.txt命名规则的文件
            g_r_files = [os.path.join(run_dir, file) for file in run_file_list["files"] if re.match(r"G_r_.*\.txt", file)]
            if not g_r_files:
                status = "error"
                message = f"没有找到G_r_{run_id}.txt文件，请检查是否存在PDF精修文件"
                return {"status": status, "message": message, "data": data}
            else:
                data_handle_info["pdf_files"]["g_r_files"] = g_r_files
            ### 检查是pdf_params是否存在
            pdf_params = data_handle_info.get("pdf_params", None)
            print(f"DEBUG: pdf_params: {pdf_params}")
            if pdf_params is None:
                status = "error"
                message = "处理PDF数据时，必须明确指定需要处理的PDF数据的精修参数设置"
                return {"status": status, "message": message, "data": data}
            else:
                if not all(key in pdf_params for key in ["qmax", "qdamp", "rmax", "rmin"]):
                    status = "error"
                    message = "处理PDF数据时，精修参数设置必须包括qmax、qdamp、rmax、rmin四个参数"
                    return {"status": status, "message": message, "data": data}
                else:
                    data_handle_info["pdf_files"]["pdf_params"] = pdf_params

            verified_info["pdf_files"] = data_handle_info["pdf_files"]

    ### 检查handle_AIndex,是否需要用AI预测空间群，接下来判断：
    if data_handle_info.get("handle_AIndex", False):
        AIndex_files = {}
        ## 首先判断是否有group
        groups = data_handle_info.get("groups",None)
        # print(f"DEBUG: 接收到的数据信息3: {groups}")
        if not groups or len(groups) == 0:
            status = "error"
            message = "通过AI预测中子衍射数据时，必须明确指定需要处理的探测器组"
            return {"status": status, "message": message, "data": data}

        ## 构建txt文件
        txt_files = []
        for group in groups:
            # group_files = {}
            pattern = os.path.join(run_dir,f"*{group}*.txt")
            matching_files = glob.glob(pattern)
            # print("5:",matching_files)
            if not matching_files:
                status = "error"
                message = f"没有找到{group}对应的txt文件"
                return {"status": status, "message": message, "data": data}
            else:
                txt_files.append(matching_files[0])

        AIndex_files["txt_files"] = txt_files
        AIndex_files["groups"] = groups
        ### 重新整理衍射数据数据
        verified_info["AIndex_files"] = AIndex_files

    ### 检查handle_NPD_Simulator,是否需要模拟中子衍射谱，接下来判断：
    if data_handle_info.get("handle_NPD_Simulator", False):
        NPD_Simulator_files = {}
        ## 首先判断是否有group
        groups = data_handle_info.get("groups",None)
        # print(f"DEBUG: 接收到的数据信息3: {groups}")
        if not groups or len(groups) == 0:
            status = "error"
            message = "模拟晶体结构的中子衍射谱时，必须明确指定需要处理的探测器组"
            return {"status": status, "message": message, "data": data}
        NPD_Simulator_files["groups"] = groups
        
        # 根据group获取对应的instrument文件
        instrument_files = []
        for group in groups:
            instprm_file = os.path.join(data_dir, f"{beamline}/instrumentFiles/RT_{group}.instprm")
            if not os.path.exists(instprm_file):
                status = "error"
                message = f"没有找到{group}对应的instrument文件"
                return {"status": status, "message": message, "data": data}
            else:
                instrument_files.append(instprm_file)
        NPD_Simulator_files["instprm_files"] = instrument_files

        # 判断是否有cif文件
        cif_files = data_handle_info.get("cifs",None)
        if not cif_files or len(cif_files) == 0:
            status = "error"
            message = "模拟晶体结构的中子衍射谱时，必须明确指定需要处理的CIF文件"
            return {"status": status, "message": message, "data": data}
        cif_files_abs = []
        for cif_file in cif_files:
            cif_file_path = os.path.join(cif_file_dir, cif_file)
            if not os.path.exists(cif_file_path):
                status = "error"
                message = f"cif_file {cif_file}不存在或路径不正确"
                return {"status": status, "message": message, "data": data}
            else:
                cif_files_abs.append(cif_file_path)
        NPD_Simulator_files["cif_files"] = cif_files_abs

        ### 重新整理
        verified_info["NPD_Simulator_files"] = NPD_Simulator_files
    
    return {"status": status, "message": message, "data": verified_info}


def get_experiment_info(run_dir: str) -> str:
        # experiment.json信息
        with open(os.path.join(run_dir, "experiment.json"), "r") as f:
            exp_dict = json.load(f)
        sample_symbol = exp_dict["sample_correction"]["sample_name"]
        user = exp_dict["user_name"]
        institute = exp_dict["institute"]
        sample_run_id = exp_dict["sample_run"][0]
        beamline = exp_dict["beamline"]
        start_time = exp_dict["start_time"]
        end_time = exp_dict["end_time"]
        v_run = exp_dict["v_run"][0]
        sampleBG_run = exp_dict["sampleBG_run"][0]
        wave_max = exp_dict["wave_max"]
        wave_min = exp_dict["wave_min"]
        norm_name = "monitor"
        if exp_dict["norm_by_pc"]:
            norm_name = "proton_charge"
        title_prompt = f"""
# 实验报告

**用户：** {user}
**单位：** {institute}
**谱仪：** {beamline}

## 中子衍射数据分析报告

### 实验信息

**样品化学式：** {sample_symbol}

**实验RUN号：** {sample_run_id}

**实验时间：** {start_time} --- {end_time}

**所选择的刻度数据分别是：**

- 钒: {v_run}
- 空盒： {sampleBG_run}

**波长范围：** {wave_min}到{wave_max}埃

**归一条件：** {norm_name}
"""
        return title_prompt

def cif_3D_visualization(
    cif_files: str | List[str], 
    ) -> List[str]:
    """
    cif_files: Path to a single CIF file, directory containing CIF files, or list of CIF file paths
    """
    # 抑制ASE的CIF解析警告
    warnings.filterwarnings("ignore", module="ase.io.cif")
    
    # 处理输入参数，统一转换为文件列表
    if isinstance(cif_files, str):
        if os.path.isdir(cif_files):
            # 如果是目录，获取目录下所有CIF文件
            file_list = [os.path.join(cif_files, f) for f in os.listdir(cif_files) if f.endswith(".cif")]
        elif os.path.isfile(cif_files) and cif_files.endswith(".cif"):
            # 如果是单个CIF文件
            file_list = [cif_files]
        else:
            raise ValueError("Input must be a directory or CIF file")
    elif isinstance(cif_files, (list, tuple)):
        # 如果是文件列表
        file_list = [f for f in cif_files if f.endswith(".cif")]
    else:
        raise TypeError("cif_files must be a string (file/directory path) or list of file paths")
    
    # 遍历所有CIF文件
    png_outputs = []
    for cif_path in file_list:
        filename = os.path.basename(cif_path)
        output = os.path.dirname(cif_path)
        
        try:
            # 读取CIF文件
            atoms = read(cif_path)
            
            # 创建图像
            fig = plt.figure(figsize=(10, 8))
            ax = fig.add_subplot(111)
            plot_atoms(atoms, ax=ax, rotation="10x,20y,30z")
            
            # 生成输出文件名（保留原文件名，仅替换扩展名）
            output_name = os.path.splitext(filename)[0] + ".png"
            output_path = os.path.join(output, output_name)
            
            # 保存图像
            fig.savefig(output_path, dpi=300, bbox_inches='tight')
            plt.close(fig)
            
            print(f"成功处理: {filename} -> {output_name}")
            png_outputs.append(output_path)
            
        except Exception as e:
            print(f"处理失败: {filename} | 错误: {str(e)}")
            
    
    print("所有CIF文件处理完成！")
    return png_outputs

async def generate_dataset(
        result: Dict[str, Any],
        user_task: str,
        arguments: Dict[str, Any],
        model_client: HepAIChatCompletionClient,
        agent_name: str,
        run_id: str,
        cancellation_token: CancellationToken,
) -> AsyncGenerator[Union[CreateResult, ModelClientStreamingChunkEvent, Dict], None]:
    '''
    将执行的结果转化为数据集，上传到对应的数据空间中.
    处理后的数据包括：
    {
        "dataset_name": "****",
        "data_type": 2,
        "summary": "****",
        "researchField": "****",
        "size": 16858561952,
        "files": [
            {
                "name": "****",
                "url": "****",
                "summary": "****",
                "size": "****",
                "filePath": "****"
            },
            
            ]
    }
    需要补充datasetId、dataVerseCode、summary、card、researchField等信息
    '''
    result["datasetId"] = uuid.uuid4().hex
    result["dataVerseCode"] = run_id
    get_info_prompt = f'''我之前的任务是{user_task}。处理的数据集参数为：{json.dumps(arguments, indent=4)}\n。然后处理得到的数据为：\n{json.dumps(result, indent=4)}
现在我需要将处理的数据进行详细的总结，总结的要求如下：
1.summary字段：根据处理需求、处理方法、处理结果进行总结。
2.card字段：对数据集的描述，包括数据来源（特别需要说明处理的数据集ID，所属的数据空间ID，处理的数据集名称）、数据类型和格式、数据大小、数据处理过程、数据使用范围等。
3.researchField字段：使用数据集的研究领域，2-3个关键词即可，必须同时包括中文和英文。

请注意,你输出的格式必须为：

**summary**: 
对数据的总结
**card**: 
数据集的描述
**researchField**: 
数据集的研究领域中英文关键词

除了以上的格式外不要输出其他的内容
''' 
    yield ModelClientStreamingChunkEvent(content="正在总结处理后的数据集信息，请稍后...\n\n", source=agent_name)
    async for chunk in model_client.create_stream(
            messages=[UserMessage(source="user", content=get_info_prompt)],
            cancellation_token=cancellation_token
        ):
        if isinstance(chunk, CreateResult):
            model_result = chunk
        else:
            yield chunk
    
    # 对输出内容进行正则表达式匹配，提取出summary、card、researchField字段
    result["card"] = ""
    pattern = r"\*\*summary\*\*:\s*(.*?)\s*\*\*card\*\*:\s*(.*?)\s*\*\*researchField\*\*:\s*(.*)"
    match = re.search(pattern, model_result.content, re.DOTALL)
    if match:
        summary = match.group(1).strip()
        card = match.group(2).strip()
        research_field = match.group(3).strip()
        # print("summary:", summary)
        # print("card:", card)
        # print("researchField:", research_field)
        result["summary"] = summary
        result["card"] = card
        result["researchField"] = research_field
    yield result

async def generate_report(
        group_id: str,
        result: Dict[str, Any],
        result_log: str,
        backgroud_info: str,
        user_task: str,
        arguments: Dict[str, Any],
        model_client: HepAIChatCompletionClient,
        agent_name: str,
        cancellation_token: CancellationToken,
) -> AsyncGenerator[Union[CreateResult, ModelClientStreamingChunkEvent], None]:
    '''
    将执行的结果转化为报告
    '''
    report_prompt = f'''你是一个将GSAS-II中子衍射精修结果转换为报告的助手，你必须按照以下的格式对得到的信息进行整理。

### 第{group_id}组GSAS-II中子衍射精修结果

#### 精修要求

基于{user_task}总结的精修要求，清晰说明精修的要求

#### 精修后结果

基于精修结果{result}展示以下内容：

- **Rw：** 精修结果中的rwp_values

- **空间群：** 精修后的空间群，如果精修结果中没有则直接为None

- **晶胞参数：** 条理清晰，且完整地展示精修结果中的每个相地晶胞参数，如果精修结果中没有则直接为None

- **精修后的图像：**

![image.png](refinement_plot.png的url, 格式如：https://aiapi.ihep.ac.cn/apiv2/files/file-e6f65091e7/preview)

#### 精修后产生的文件列表

以以下格式展示：
| 文件名 | 文件url | 文件路径 | 文件说明 |
| ---- | ---- | ---- | ---- |


#### 精修log分析

基于以下信息总结第{group_id}组GSAS-II中子衍射精修过程中可能存在的一些问题，并给出相应的建议：
背景任务：{backgroud_info}；
精修中的参数：{arguments}；
精修中的log：{result_log}

注意：但是最后一定要说明所以评论来自于AI，请谨慎参考。
    '''
    yield ModelClientStreamingChunkEvent(content="正在生成报告，请稍后...\n\n", source=agent_name)
    async for chunk in model_client.create_stream(
            messages=[UserMessage(source="user", content=report_prompt)],
            cancellation_token=cancellation_token
        ):
        yield chunk

def get_groups_dict(run_dir:str, run_id:str) -> Dict[str, str]:
    groups_dict = {}
    for file in os.listdir(run_dir):
        if file.startswith(run_id.upper()+"_group") and file.endswith(".gsa"):
            group_id = file.split("_")[1].split(".")[0].upper()
            gsa_file = os.path.join(run_dir, file)
            if group_id in groups_dict:
                groups_dict[group_id]["gsa_file"] = gsa_file
            else:
                groups_dict[group_id] = {"gsa_file": gsa_file}
    return groups_dict
