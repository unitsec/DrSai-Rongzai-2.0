#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""PDF refinement tool for structure analysis."""

import os
import re
import matplotlib.pyplot as plt
import numpy as np
from diffpy.pdffit2 import PdfFit
from diffpy.structure import Structure  # 添加CIF文件加载支持
import multiprocessing, queue
from multiprocessing import Process, Queue
import traceback

from typing import Any, Dict, List
import glob
# import httpx
# from mcp.server.fastmcp import FastMCP
# from starlette.applications import Starlette
# from mcp.server.sse import SseServerTransport
# from starlette.requests import Request
# from starlette.routing import Mount, Route
# from mcp.server import Server
# import uvicorn
# import asyncio

# Initialize FastMCP server for Weather tools (SSE)
# mcp = FastMCP("Tester")
from mcp.server.fastmcp import FastMCP
mcp=FastMCP(
    name="PDF_Refinement_Tool",
    instructions="A tool for refining PDF data and generating refined structure files.",
    host="0.0.0.0",
    port = 42502, # for transport = "sse" to start a server-sent events (SSE) server
    )

def create_output_directory(output_dir):
    """创建输出目录"""
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
        print(f"创建输出目录: {output_dir}")
    return output_dir

def calcu_byte_size(file_path: str) -> int:
    """计算文件字节大小
    Args:
        file_path (str): 文件路径

    Returns:
        int: 文件字节大小
    """
    return os.path.getsize(file_path)

def get_refined_cif_filename(original_cif_path):
    """生成精修后的CIF文件名：原始文件名 + pdf_refined.cif"""
    base_name = os.path.splitext(os.path.basename(original_cif_path))[0]
    return f"{base_name}_pdf_refined.cif"

def update_cif_with_refined_params(original_cif_path, refined_params, output_cif_path, phase_index=1):
    """
    使用精修后的参数更新CIF文件，生成标准格式的CIF
    
    Args:
        original_cif_path: 原始CIF文件路径
        refined_params: 精修后的参数字典
        output_cif_path: 输出CIF文件路径
        phase_index：多相索引
    """
    try:
        # 读取原始CIF文件内容
        with open(original_cif_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        
        # 获取精修参数
        lattice_param = refined_params[f'lattice_parameter_{phase_index}']
        temp_factor = refined_params[f'temperature_factor_{phase_index}']
        
        # 创建新的CIF内容
        new_content = []
        in_atom_site_block = False
        atom_headers = []
        atom_data_lines = []
        
        # 处理原始CIF文件的每一行
        for line in lines:
            stripped_line = line.strip()
            
            # 跳过空行和注释
            if not stripped_line or stripped_line.startswith('#'):
                new_content.append(line)
                continue
                
            # 更新晶胞参数
            if stripped_line.startswith('_cell_length_a'):
                new_content.append(f"_cell_length_a                   {lattice_param:.6f}\n")
            elif stripped_line.startswith('_cell_length_b'):
                new_content.append(f"_cell_length_b                   {lattice_param:.6f}\n")
            elif stripped_line.startswith('_cell_length_c'):
                new_content.append(f"_cell_length_c                   {lattice_param:.6f}\n")
            elif stripped_line.startswith('_cell_volume'):
                # 计算新的晶胞体积（假设立方晶系）
                volume = lattice_param ** 3
                new_content.append(f"_cell_volume                     {volume:.4f}\n")
                
            # 检测原子位点区块开始
            elif 'loop_' in stripped_line and not in_atom_site_block:
                # 检查下一行是否是原子位点相关的表头
                line_idx = lines.index(line)
                if line_idx + 1 < len(lines):
                    next_line = lines[line_idx + 1].strip()
                    if next_line.startswith('_atom_site_'):
                        new_content.append(line)
                        in_atom_site_block = True
                    else:
                        new_content.append(line)
                else:
                    new_content.append(line)
                
            # 检测原子位点表头
            elif stripped_line.startswith('_atom_site_') and in_atom_site_block:
                atom_headers.append(stripped_line)
                new_content.append(line)
                
            # 处理原子位点数据行
            elif in_atom_site_block and not stripped_line.startswith('_') and not stripped_line.startswith('loop_'):
                # 收集原子数据行，稍后处理
                atom_data_lines.append((len(new_content), stripped_line))
                new_content.append(line)  # 先保留原始行
                
            else:
                new_content.append(line)
        
        # 如果有原子数据需要处理，重新构建原子部分
        if atom_data_lines and atom_headers:
            # 确定原子数据行的位置
            first_atom_line_idx = atom_data_lines[0][0]
            
            # 重新构建原子部分
            new_atom_content = []
            
            # 添加标准原子表头
            standard_headers = [
                '_atom_site_type_symbol',
                '_atom_site_label', 
                '_atom_site_fract_x',
                '_atom_site_fract_y', 
                '_atom_site_fract_z',
                '_atom_site_occupancy',
                '_atom_site_U_iso_or_equiv',
                '_atom_site_adp_type'
            ]
            
            # 添加loop_和表头（只添加一次）
            new_atom_content.append("loop_\n")
            for header in standard_headers:
                new_atom_content.append(f"{header}\n")
            
            # 添加精修后的原子数据
            for idx, (line_idx, original_line) in enumerate(atom_data_lines):
                parts = original_line.split()
                if len(parts) >= 1:  # 至少有一个字段（原子符号）
                    # 提取原子信息
                    atom_symbol = parts[0]
                    atom_label = parts[1] if len(parts) > 1 else f"{atom_symbol}{idx+1}"
                    
                    # 提取坐标信息（假设坐标在第2-4列或第3-5列）
                    if len(parts) >= 5:
                        # 尝试不同的列位置
                        try:
                            x = float(parts[2]) if len(parts) > 2 else 0.0
                            y = float(parts[3]) if len(parts) > 3 else 0.0
                            z = float(parts[4]) if len(parts) > 4 else 0.0
                            occ = float(parts[5]) if len(parts) > 5 else 1.0
                        except (ValueError, IndexError):
                            # 如果解析失败，使用默认值
                            x, y, z, occ = 0.0, 0.0, 0.0, 1.0
                    else:
                        x, y, z, occ = 0.0, 0.0, 0.0, 1.0
                    
                    # 构建标准CIF格式的原子行
                    new_line = f"{atom_symbol:<6} {atom_label:<8} "
                    new_line += f"{x:.5f} {y:.5f} {z:.5f} "
                    new_line += f"{occ:.5f} {temp_factor:.5f} Uiso\n"
                    
                    new_atom_content.append(new_line)
            
            # 替换原子部分
            final_content = new_content[:first_atom_line_idx] + new_atom_content
            
        else:
            final_content = new_content
        
        # 添加精修信息到文件开头
        from datetime import datetime
        refinement_info = f"""# PDF refinement results for phase {phase_index}
# Refined lattice parameter: {lattice_param:.6f} A
# Refined temperature factor: {temp_factor:.6f} A^2
# Refined scale factor: {refined_params.get(f'scale_{phase_index}', 1.0):.6f}
# Refined Qdamp: {refined_params.get('qdamp', 0.03):.6f}
# Refined Delta2: {refined_params.get('delta2', 0.0003):.6f}
# R-weight: {refined_params.get('rw', 0.0):.6f}
# Refinement date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

"""
        
        # 将精修信息添加到文件开头
        final_content_with_info = [refinement_info] + final_content
        
        # 保存更新后的CIF文件
        with open(output_cif_path, 'w', encoding='utf-8') as f:
            f.writelines(final_content_with_info)
        
        print(f"已保存精修后的CIF文件（相{phase_index}）: {os.path.basename(output_cif_path)}")
        
        # 验证生成的CIF文件格式
        try:
            # 检查文件是否包含重复的loop_
            with open(output_cif_path, 'r') as f:
                content = f.read()
                loop_count = content.count('loop_')
                if loop_count > 1:
                    print(f"警告: CIF文件中检测到 {loop_count} 个loop_语句")
                else:
                    print("CIF文件格式检查通过")
                    
        except Exception as verify_error:
            print(f"CIF文件格式验证警告: {verify_error}")
        
        return True
        
    except Exception as e:
        print(f"更新CIF文件时出错: {e}")
        import traceback
        traceback.print_exc()
        return False
id_to_task={}
id_to_result={}

def perform_pdf_refinement(
        data_file, 
        structure_files,  # 改为结构文件列表
        qmax, 
        qdamp, 
        rmin, 
        rmax, 
        output_dir, 
        output_prefix,
        que
        ):
    """
    执行PDF精修分析
    
    Args:
        data_file: 实验PDF数据文件路径
        structure_files: 结构文件路径
        qmax: Q最大值(1/Å)
        qdamp: Q分辨率因子
        rmin: 精修最小r值(Å)
        rmax: 精修最大r值(Å)
        output_dir: 输出目录
        output_prefix: 输出文件前缀
        
    Returns:
        {
            "status": "success"|"error",
            "output_files": 生成的文件列表,
            "refined_params": 精修参数,
            "error": 错误信息(如有)
        }
    """
    result = {
        "status": "success",
        "output_files": {},
        "refined_params": {}
    }
    
    try:
        # 验证输入文件是否存在
        if not os.path.exists(data_file):
            raise FileNotFoundError(f"数据文件不存在: {data_file}")
        for i, struct_file in enumerate(structure_files):
            if not os.path.exists(struct_file):
                raise FileNotFoundError(f"结构文件 {i+1} 不存在: {struct_file}")

        # 创建输出目录
        output_dir = create_output_directory(output_dir)
        
        # 创建PDF计算器对象
        pf = PdfFit()

        # 加载数据
        print("正在加载实验数据...")
        pf.read_data(data_file, "X", qmax, qdamp)
        
        # 加载多相结构
        num_phases = len(structure_files)
        print(f"正在加载 {num_phases} 个相的结构文件...")
        
        for i, structure_file in enumerate(structure_files):
            phase_index = i + 1
            
            if structure_file.lower().endswith('.cif'):
                structure = Structure()
                structure.read(structure_file)
                pf.add_structure(structure)
                print(f"成功加载相 {phase_index} CIF文件: {os.path.basename(structure_file)}")
            else:
                pf.read_struct(structure_file)
                print(f"成功加载相 {phase_index} 结构文件: {os.path.basename(structure_file)}")

            # 显示结构信息
            print(f"\n相 {phase_index} 结构信息:")
            pf.show_struct(phase_index)
            num_atoms = len(pf.get_structure(phase_index))
            print(f"相 {phase_index} 中的原子数量: {num_atoms}")

        # 配置精修参数
        print("\n配置多相精修参数...")
        
        # 全局参数
        pf.constrain("pscale", "@20 * 2")
        pf.setpar(20, 0.5)
        pf.constrain(pf.qdamp, "@21")
        pf.setpar(21, 0.03)
        pf.constrain(pf.delta2, 22)
        pf.setpar(22, 0.0003)
        
        # 每个相的精修参数
        param_index = 100
        
        for phase_idx in range(1, num_phases + 1):
            print(f"配置相 {phase_idx} 的参数...")
            
            # 切换到当前相
            pf.setphase(phase_idx)
            
            # 晶格参数约束
            lat_a = pf.lat(1)  # a
            lat_b = pf.lat(2)  # b
            lat_c = pf.lat(3)  # c
            
            pf.constrain(lat_a, f"@{param_index}")
            pf.constrain(lat_b, f"@{param_index}")
            pf.constrain(lat_c, f"@{param_index}")
            pf.setpar(param_index, pf.getvar(lat_a))
            param_index += 1
            
            # 温度因子约束
            num_atoms = len(pf.get_structure(phase_idx))
            for atom_idx in range(1, num_atoms + 1):
                u11 = pf.u11(atom_idx)
                u22 = pf.u22(atom_idx)
                u33 = pf.u33(atom_idx)
                
                pf.constrain(u11, f"@{param_index}")
                pf.constrain(u22, f"@{param_index}")
                pf.constrain(u33, f"@{param_index}")
            
            pf.setpar(param_index, 0.005)
            param_index += 1

             # 添加每个相的比例因子约束
            scale_param = pf.pscale
            pf.constrain(scale_param, f"@{param_index}")
            pf.setpar(param_index, 1.0 / num_phases)  # 平均分配初始比例
            param_index += 1

        # 设置精修范围
        pf.pdfrange(1, rmin, rmax)
        
        print("\n开始多相精修...")
        
        # 逐步精修策略
        print("第一步: 精修晶格参数和比例因子")
        pf.fixpar(21)  # Qdamp
        pf.fixpar(22)  # delta2
        
        # 固定温度因子参数
        for temp_idx in range(101, 101 + num_phases):
            pf.fixpar(temp_idx)
        
        pf.refine()
        
        print("第二步: 释放delta2参数")
        pf.freepar(22)
        pf.refine()
        
        print("第三步: 精修温度因子")
        for temp_idx in range(101, 101 + num_phases):
            pf.freepar(temp_idx)
        pf.refine()
        
        print("第四步: 释放所有参数")
        pf.freepar(21)
        pf.refine()

        print("多相精修完成!")

        # 收集精修参数
        refined_params = {
            "pscale": pf.getpar(20) * 2,
            "qdamp": pf.getpar(21),
            "delta2": pf.getpar(22),
            "rw": pf.getrw()
        }

        # 收集每个相的精修参数
        param_index = 100
        for phase_idx in range(1, num_phases + 1):
            refined_params[f"lattice_parameter_{phase_idx}"] = pf.getpar(param_index)
            param_index += 1
            refined_params[f"temperature_factor_{phase_idx}"] = pf.getpar(param_index)
            param_index += 1
            # 添加比例因子
            refined_params[f"scale_{phase_idx}"] = pf.getpar(param_index)
            param_index += 1

        # 生成输出文件
        output_files = {
            "pdf": os.path.join(output_dir, f"{output_prefix}.fgr"),
            "results": os.path.join(output_dir, f"{output_prefix}.res"),
            "plot": os.path.join(output_dir, f"{output_prefix}.png"),
            "log": os.path.join(output_dir, f"{output_prefix}_log.txt"),
            "refined_structures": [],
            "refined_cif_files": []
        }

        # 保存每个相的结构文件
        for phase_idx in range(1, num_phases + 1):
            struct_file = os.path.join(output_dir, f"{output_prefix}_phase{phase_idx}.rstr")
            pf.save_struct(phase_idx, struct_file)
            output_files["refined_structures"].append(struct_file)

        # 保存结果文件
        pf.save_pdf(1, output_files["pdf"])
        pf.save_res(output_files["results"])

        # 更新CIF文件并保存
        for i, structure_file in enumerate(structure_files):
            phase_idx = i + 1
            if structure_file.lower().endswith('.cif'):
                refined_cif_filename = get_refined_cif_filename(structure_file)
                refined_cif_path = os.path.join(output_dir, refined_cif_filename)
                
                print(f"更新相 {phase_idx} 的CIF文件: {refined_cif_filename}")
                update_success = update_cif_with_refined_params(
                    structure_file, refined_params, refined_cif_path, phase_idx
                )
                output_files[f"refined_cif_phase{phase_idx}"] = refined_cif_path
                output_files["refined_cif_files"].append(refined_cif_path)

        # 保存精修日志
        with open(output_files["log"], 'w') as f:
            f.write("多相PDF精修结果日志\n")
            f.write("=" * 50 + "\n")
            f.write(f"数据文件: {data_file}\n")
            f.write(f"相数量: {num_phases}\n")
            for i, struct_file in enumerate(structure_files):
                f.write(f"相 {i+1} 结构文件: {struct_file}\n")
            f.write(f"精修范围: rmin={rmin}, rmax={rmax}\n")
            f.write(f"精修权重因子Rw: {refined_params['rw']:.6f}\n\n")
            
            f.write("精修参数:\n")
            f.write(f"全局参数:\n")
            f.write(f"  总比例因子: {refined_params['pscale']:.6f}\n")
            f.write(f"  Q阻尼: {refined_params['qdamp']:.6f}\n")
            f.write(f"  Delta2: {refined_params['delta2']:.6f}\n\n")
            
            for phase_idx in range(1, num_phases + 1):
                f.write(f"相 {phase_idx}:\n")
                f.write(f"  晶格参数: {refined_params[f'lattice_parameter_{phase_idx}']:.6f}\n")
                f.write(f"  温度因子: {refined_params[f'temperature_factor_{phase_idx}']:.6f}\n")
                f.write(f"  比例因子: {refined_params[f'scale_{phase_idx}']:.6f}\n\n")

        # 保存结果图表
        print("生成结果图表...")
        r = pf.getR()
        Gobs = pf.getpdf_obs()
        Gfit = pf.getpdf_fit()
        Gdiff = np.array(Gobs) - np.array(Gfit)
        Gdiff_baseline = -max(np.abs(Gobs)) * 0.2

        plt.figure(figsize=(12, 8))
        plt.plot(r, Gobs, "ko", markersize=2, label="Experimental", alpha=0.7)
        plt.plot(r, Gfit, "r-", linewidth=1.5, label="Total Fit")
        plt.plot(r, Gdiff + Gdiff_baseline, "b-", linewidth=1, label="Difference")
        
        plt.xlabel("r (Å)", fontsize=14)
        plt.ylabel("G (Å$^{-2}$)", fontsize=14)
        plt.title(f"Multi-phase PDF Refinement - Rw = {refined_params['rw']:.4f}", fontsize=16)
        plt.legend(fontsize=12)
        plt.grid(True, alpha=0.3)
        plt.tight_layout()
        plt.savefig(output_files["plot"], dpi=300, bbox_inches='tight')
        plt.close()

        result["output_files"] = output_files
        result["refined_params"] = refined_params

    except FileNotFoundError as e:
        result.update({
            "status": "error",
            "error": str(e),
            "suggestion": "请检查文件路径是否正确"
        })
        if que:
            que.put(result)
        raise e
    except Exception as e:
        error_trace = traceback.format_exc()
        result.update({
            "status": "error",
            "error": f"精修过程中出错: {str(error_trace)}",
            "suggestion": "请检查输入参数或联系管理员"
        })
        if que:
            que.put(result)
        raise e
    finally:
        if que:
            try:
                que.put(result)
            except Exception:
                pass
    
    # 如果不是通过队列调用，直接返回结果
    if que is None:
        return result


@mcp.tool()
def write_info(
    data_file:str,
    structure_files: List[str],  # 改为结构文件列表
    qmax:float,
    qdamp:float,
    rmin:float,
    rmax:float,
    task_id:str|None =None, 
    thread_id: str|None = None) -> Dict:
    """
    执行多相PDF精修任务并返回结果信息
    Args:
        data_file: 实验PDF数据文件的绝对路径
        structure_files: 结构文件绝对路径，可以为cif或者.stru等格式
        qmax: Q最大值(1/Å)
        qdamp: Q分辨率因子
        rmin: 精修最小r值(Å)
        rmax: 精修最大r值(Å)
        task_id: 任务ID，可选，默认自动生成
        thread_id: 线程ID，可选，默认自动生成
    Returns:
        返回一个字典，包含任务状态、结果信息等
    """
    id_=task_id
    output_prefix = os.path.splitext(os.path.basename(data_file))[0]
    output_dir = os.path.join(os.path.dirname(data_file), f"output/{thread_id}/pdf_output_{task_id}")
    
    result_json={"id":id_,'state':None,'result':None}
    
    if id_ not in id_to_task or id_to_task[id_]==None:
        id_to_result[id_]=multiprocessing.Queue()
        id_to_task[id_]=multiprocessing.Process(
            target=perform_pdf_refinement,
            args=(
                data_file,
                structure_files,
                qmax,
                qdamp,
                rmin,
                rmax,
                output_dir,
                output_prefix,
                id_to_result[id_]
            )
        )
        result_json['state']='created'
        id_to_task[id_].start()
        
    else:
        if not id_to_task[id_].is_alive():
            info=id_to_result[id_].get()
            id_to_task[id_].join()
            print(info)
            id_to_task[id_] = None
            result_json['state'] = 'completed'
            result_json["name"] = f"{task_id}_pdf_refinement"
            result_json["summary"] = "使用PDFFit进行多相PDF精修，精修所需的文件如下：\n"
            result_json['status'] = 'success'

            # 计算输出文件大小并构建结果信息
            if info["status"] == "success":
                output_files = info["output_files"]
                refined_params = info["refined_params"]
                
                # 构建文件列表和大小信息
                file_info_list = []
                for file_type, file_path in output_files.items():
                    if isinstance(file_path, list):
                        for fp in file_path:
                            if os.path.exists(fp):
                                file_size = calcu_byte_size(fp)
                                file_size_mb = file_size / (1024 * 1024)
                                file_info_list.append({
                                    "type": file_type,
                                    "path": fp,
                                    "size_bytes": file_size,
                                    "size_mb": round(file_size_mb, 3)
                                })
                                result_json["summary"] += f"- {file_type}: {os.path.basename(fp)} ({file_size_mb:.3f} MB)\n"
                    elif os.path.exists(file_path):
                        file_size = calcu_byte_size(file_path)
                        file_size_mb = file_size / (1024 * 1024)
                        file_info_list.append({
                            "type": file_type,
                            "path": file_path,
                            "size_bytes": file_size,
                            "size_mb": round(file_size_mb, 3)
                        })
                        result_json["summary"] += f"- {file_type}: {os.path.basename(file_path)} ({file_size_mb:.3f} MB)\n"
                    
                 # 添加精修参数信息
                result_json["summary"] += f"\n精修结果参数:\n"
                result_json["summary"] += f"- 比例因子: {refined_params['pscale']:.6f}\n"
                result_json["summary"] += f"- Q阻尼: {refined_params['qdamp']:.6f}\n"
                result_json["summary"] += f"- Delta2: {refined_params['delta2']:.6f}\n"
                result_json["summary"] += f"- 精修权重因子Rw: {refined_params['rw']:.6f}\n"
                
                # 添加每个相的参数信息
                num_phases = len(structure_files)
                for phase_idx in range(1, num_phases + 1):
                    result_json["summary"] += f"\n相 {phase_idx} 参数:\n"
                    result_json["summary"] += f"- 晶格参数: {refined_params[f'lattice_parameter_{phase_idx}']:.6f} Å\n"
                    result_json["summary"] += f"- 温度因子: {refined_params[f'temperature_factor_{phase_idx}']:.6f}\n"
                    result_json["summary"] += f"- 比例因子: {refined_params[f'scale_{phase_idx}']:.6f}\n"
                    
                # 构建详细的result字段
                result_json['result'] = {
                    "status": "success",
                    "output_files": output_files,
                    "file_info": file_info_list,
                    "refined_params": refined_params,
                    "total_size_bytes": sum(file_info["size_bytes"] for file_info in file_info_list),
                    "total_size_mb": round(sum(file_info["size_mb"] for file_info in file_info_list), 3)
                }
            else:
                # 处理错误情况
                result_json['status'] = 'error'
                result_json['result'] = {
                    "status": "error",
                    "error": info.get("error", "未知错误"),
                    "suggestion": info.get("suggestion", "请检查输入参数")
                }
                result_json["summary"] = f"精修失败: {info.get('error', '未知错误')}"
        else:
            result_json['state']='running'
    return result_json

# """
# {'status': 'success', 
# 'output_files': {'pdf': 'output/Si_PDF.fgr', 
#                 'structure': 'output/Si_PDF.rstr', 
#                 'results': 'output/Si_PDF.res', 
#                 'plot': 'output/Si_PDF.png', 
#                 'log': 'output/Si_PDF_log.txt', 
#                 'refined_cif': 'output/Si_PDF_refined.cif'}, 
# 'refined_params': {'lattice_parameter': 3.84335418795978, 
#                 'pscale': 1.0186797465811945, 
#                 'qdamp': 0.025481510829003295, 
#                 'delta2': 3.117150085487948, 
#                 'temperature_factor': 0.00591612958643285, 
#                 'rw': 0.07645343380185077}}
# """


if __name__ == '__main__':
    # mcp.run(transport="stdio")
    mcp.run(transport="sse")


# if __name__ == '__main__':
#     # 当前目录下的文件 - 多相示例
#     data_file = "/aifs/user/home/liqingmeng/rozai_agent/tools/GSAS-II-master/GSASII/RUN0021905/Si_PDF.txt"
#     structure_files = ["/aifs/user/home/liqingmeng/rozai_agent/tools/GSAS-II-master/GSASII/RUN0021905/gsas_output/mp-149_gsas_refined.cif",
#                        "/aifs/user/home/liqingmeng/rozai_agent/tools/GSAS-II-master/GSASII/RUN0021905/Si.cif"]
    
#     # 精修参数
#     qmax = 30.0
#     qdamp = 0.01
#     rmin = 1.5
#     rmax = 20.0
#     output_dir = "output_20251024"
#     output_prefix = os.path.splitext(os.path.basename(data_file))[0]
    
#     print("开始多相PDF精修分析...")
#     print(f"数据文件: {data_file}")
#     print(f"相数量: {len(structure_files)}")
#     for i, struct_file in enumerate(structure_files):
#         print(f"相 {i+1} 结构文件: {struct_file}")
#     print(f"输出目录: {output_dir}")
    
#     # 执行精修 - 直接调用函数，不通过队列
#     refinement_result = perform_pdf_refinement(
#         data_file=data_file,
#         structure_files=structure_files,
#         qmax=qmax,
#         qdamp=qdamp,
#         rmin=rmin,
#         rmax=rmax,
#         output_dir=output_dir,
#         output_prefix=output_prefix,
#         que=None  # 不通过队列
#     )
    
#     # 打印结果
#     print("\n" + "="*50)
#     print("精修状态:", refinement_result["status"])
    
#     if refinement_result["status"] == "success":
#         print("\n生成的文件:")
#         for file_type, file_path in refinement_result["output_files"].items():
#             if isinstance(file_path, list):
#                 print(f"  {file_type}:")
#                 for f in file_path:
#                     print(f"    {os.path.basename(f)}")
#             else:
#                 print(f"  {file_type}: {os.path.basename(file_path)}")
        
#         print("\n精修参数:")
#         params = refinement_result["refined_params"]
#         print(f"  比例因子: {params['pscale']:.6f}")
#         print(f"  Q阻尼: {params['qdamp']:.6f}")
#         print(f"  Delta2: {params['delta2']:.6f}")
#         print(f"  精修权重因子Rw: {params['rw']:.6f}")
        
#         # 打印每个相的精修参数
#         for i in range(1, len(structure_files) + 1):
#             print(f"\n  相 {i} 参数:")
#             print(f"    晶格参数: {params[f'lattice_parameter_{i}']:.6f} Å")
#             print(f"    温度因子: {params[f'temperature_factor_{i}']:.6f}")
        
#         print(f"\n所有结果已保存到目录: {output_dir}")
        
#     else:
#         print("\n错误:", refinement_result.get("error", ""))
#         print("建议:", refinement_result.get("suggestion", ""))
    
#     print("="*50)



# def create_starlette_app(mcp_server: Server, *, debug: bool = False) -> Starlette:
#     """Create a Starlette application that can server the provied mcp server with SSE."""
#     sse = SseServerTransport("/messages/")

#     async def handle_sse(request: Request) -> None:
#         async with sse.connect_sse(
#                 request.scope,
#                 request.receive,
#                 request._send,  # noqa: SLF001
#         ) as (read_stream, write_stream):
#             await mcp_server.run(
#                 read_stream,
#                 write_stream,
#                 mcp_server.create_initialization_options(),
#             )

#     return Starlette(
#         debug=debug,
#         routes=[
#             Route("/sse", endpoint=handle_sse),
#             Mount("/messages/", app=sse.handle_post_message),
#         ],
#     )


# if __name__ == "__main__":
#     mcp_server = mcp._mcp_server  # noqa: WPS437

#     # Bind SSE request handling to MCP server
#     starlette_app = create_starlette_app(mcp_server, debug=True)

#     uvicorn.run(starlette_app, host='0.0.0.0', port=42998)










# import uuid



# import time
# id_=str(uuid.uuid4())
# for _ in range(20):
#     t=write_info(data_file = "/aifs/user/home/liqingmeng/rozai_agent/tools/GSAS-II-master/GSASII/RUN0021905/Si_PDF.txt"
#     ,structure_file = "/aifs/user/home/liqingmeng/rozai_agent/tools/GSAS-II-master/GSASII/RUN0021905/gsas_output/mp-149_gsas_refined.cif"
#     ,qmax = 30.0
#     ,qdamp = 0.01
#     ,rmin = 1.5
#     ,rmax = 20.0
#     ,output_dir = "output",
#     task_id=id_,
#     RUN_id="")
#     if t['state']=="completed":
#         print(t)
#         break
#     else:
#         print(t)
#     time.sleep(1)


# # 使用示例
# if __name__ == '__main__':
#     # 当前目录下的文件
#     data_file = "/aifs/user/home/liqingmeng/rozai_agent/tools/GSAS-II-master/GSASII/RUN0021905/Si_PDF.txt"
#     structure_file = "/aifs/user/home/liqingmeng/rozai_agent/tools/GSAS-II-master/GSASII/RUN0021905/gsas_output/mp-149_gsas_refined.cif"
    
#     # 精修参数
#     qmax = 30.0
#     qdamp = 0.01
#     rmin = 1.5
#     rmax = 20.0
#     output_dir = "output"  # 输出目录
#     # output_prefix = "pdf_"
#     output_prefix = os.path.splitext(os.path.basename(data_file))[0]
    
#     print("开始PDF精修分析...")
#     print(f"数据文件: {data_file}")
#     print(f"结构文件: {structure_file}")
#     print(f"输出目录: {output_dir}")
    
#     # 执行精修
#     refinement_result = perform_pdf_refinement(
#         data_file=data_file,
#         structure_file=structure_file,
#         qmax=qmax,
#         qdamp=qdamp,
#         rmin=rmin,
#         rmax=rmax,
#         output_dir=output_dir,
#         output_prefix=output_prefix
#     )
    
#     # 打印结果
#     print("\n" + "="*50)
#     print("精修状态:", refinement_result["status"])
    
#     if refinement_result["status"] == "success":
#         print("\n生成的文件:")
#         for file_type, file_path in refinement_result["output_files"].items():
#             print(f"  {file_type}: {os.path.basename(file_path)}")
        
#         print("\n精修参数:")
#         params = refinement_result["refined_params"]
#         print(f"  晶格参数: {params['lattice_parameter']:.6f} Å")
#         print(f"  比例因子: {params['pscale']:.6f}")
#         print(f"  Q阻尼: {params['qdamp']:.6f}")
#         print(f"  Delta2: {params['delta2']:.6f}")
#         print(f"  温度因子: {params['temperature_factor']:.6f}")
#         print(f"  精修权重因子Rw: {params['rw']:.6f}")
        
#         print(f"\n所有结果已保存到目录: {output_dir}")
        
#     else:
#         print("\n错误:", refinement_result.get("error", ""))
#         print("建议:", refinement_result.get("suggestion", ""))
    
#     print("="*50)