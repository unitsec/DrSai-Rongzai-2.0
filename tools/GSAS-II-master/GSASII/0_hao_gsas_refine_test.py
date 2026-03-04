#这个脚本是为了开发新的智能体框架，在李庆梦代码的基础上修改的，仅用于测试和开发，不用于生产环境
import os
import logging
from pathlib import Path
import numpy as np
import matplotlib.pyplot as plt
import GSASIIscriptable as G2sc
import re
from datetime import datetime
import multiprocessing,queue

from typing import List, Tuple, Dict, Union

from mcp.server.fastmcp import FastMCP
mcp=FastMCP(
    name="GSASII_Refinement_Tool",
    instructions="A tool for refining neutron diffraction data and generating refined structure files.",
    port = 42501, # for transport = "sse" to start a server-sent events (SSE) server
    )

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

def read_gsa_limits(gsa_file):
    """读取GSA文件的时间范围"""
    try:
        with open(gsa_file, 'r') as f:
            lines = f.readlines()
        
        time_values = []
        for line in lines[2:]:  # 跳过头部
            parts = line.strip().split()
            if len(parts) >= 3:
                try:
                    time_us = float(parts[0])
                    time_values.append(time_us)
                except ValueError:
                    continue
        
        if time_values:
            min_time = min(time_values)
            max_time = max(time_values)
            return min_time, max_time
        else:
            return None, None
    except Exception as e:
        print(f"读取GSA文件时间范围失败: {str(e)}")
        return None, None

def initialize_project(neutron_data, neutron_inst, cif_files, output_dir):
    """初始化GSAS-II项目并添加中子数据"""
    logger = logging.getLogger(__name__)

    logger.info("步骤 1/7: 创建新的 GSAS-II 项目")
    gpx = G2sc.G2Project(newgpx=Path(output_dir) / 'neutron_refinement.gpx')
    logger.info("✓ 项目创建成功")

    # 添加中子数据
    if not (neutron_data and neutron_inst):
        raise ValueError("必须提供中子数据和仪器参数文件")

    logger.info("步骤 2/7: 清理数据文件中的NaN值")
    # 清理数据文件中的NaN值和文件头 - 失败则终止程序
    try:
        if isinstance(neutron_data, (str, Path)):
            # 读取原始文件
            logger.info(f"  读取原始数据文件: {neutron_data}")
            with open(neutron_data, 'r') as f:
                lines = f.readlines()

            logger.info(f"  文件总行数: {len(lines)}")

            # 寻找有效数据开始位置并过滤NaN值
            valid_data_lines = []
            data_started = False

            for line in lines:
                line = line.strip()
                if not line:
                    continue

                # 检查是否为数据行（包含数字）
                parts = line.split()
                if len(parts) >= 3:  # 至少需要3列数据
                    try:
                        # 尝试解析所有列为浮点数
                        values = [float(part) for part in parts[:3]]  # 只取前3列
                        # 检查是否包含NaN
                        if not any(np.isnan(values)):
                            valid_data_lines.append(line)
                            data_started = True
                    except ValueError:
                        # 如果转换失败，可能是文件头，继续寻找数据
                        if data_started:
                            # 如果已经开始数据部分但遇到非数值行，可能是数据结束
                            break
                        continue

            if not valid_data_lines:
                raise ValueError("GSA文件中未找到任何有效数据行")

            logger.info(f"  从原始文件中提取了 {len(valid_data_lines)} 行有效数据")

            # 创建临时文件保存有效数据
            temp_dir = Path(output_dir) / 'temp'
            temp_dir.mkdir(exist_ok=True)
            clean_data_file = temp_dir / 'cleaned_neutron_data.dat'

            with open(clean_data_file, 'w') as f:
                for line in valid_data_lines:
                    f.write(line + '\n')

            neutron_data = str(clean_data_file)
            logger.info(f"  已创建清理后的数据文件: {clean_data_file}")

    except Exception as e:
        logger.error(f"GSA文件数据处理失败: {str(e)}")
        raise ValueError(f"无法处理GSA文件中的NaN值: {str(e)}")

    logger.info("✓ 数据清理完成")

    logger.info("步骤 3/7: 添加中子粉末衍射数据")
    hist = gpx.add_powder_histogram(neutron_data, neutron_inst)
    logger.info(f"  添加中子数据: {neutron_data}")
    logger.info("✓ 数据添加成功")

    # #读取GSA文件的时间范围并设置histogram limits
    # logger.info("步骤 4/7: 设置时间范围限制")
    # min_time, max_time = read_gsa_limits(neutron_data)
    # print("默认时间范围:", hist.data['Limits'])
    # if min_time is not None and max_time is not None:
    #     logger.info(f"  GSA文件时间范围: {min_time:.2f} - {max_time:.2f} μs")
    #     # 设置histogram的限制范围
    #     try:
    #         hist.set_limits([min_time, max_time])
    #         logger.info(f"  已设置histogram限制范围: {min_time:.2f} - {max_time:.2f} μs")
    #     except Exception as e:
    #         logger.warning(f"  设置histogram限制失败: {str(e)}")
    #         # 尝试使用不同的方法设置限制
    #         try:
    #             hist.data['Limits'][1] = [min_time, max_time]
    #             logger.info(f"  使用备用方法设置限制范围: {min_time:.2f} - {max_time:.2f} μs")
    #         except Exception as e2:
    #             logger.warning(f"  备用方法也失败: {str(e2)}")
    # else:
    #     logger.warning("  未能读取GSA文件的时间范围，使用默认设置")

    # logger.info("✓ 时间范围设置完成")

    # 添加晶体结构
    logger.info("步骤 5/7: 添加晶体结构")
    phases = []
    if cif_files:
        for i, cif_file in enumerate(cif_files, start=1):
            logger.info(f"  添加第 {i}/{len(cif_files)} 个结构文件: {cif_file}")
            phase_name = f"Phase_{i}"
            phase = gpx.add_phase(cif_file, phasename=phase_name, histograms=[hist])
            phases.append(phase)
            logger.info(f"  ✓ 晶体结构 {i} 添加成功 (名称: {phase_name})")

    logger.info("✓ 所有晶体结构添加完成")

    # 设置最大精修循环次数
    logger.info("步骤 6/7: 设置精修控制参数")
    print("检查controls里面都有什么", gpx.data['Controls'])
    gpx.data['Controls']['data']['max cyc'] = 8
    logger.info("  设置最大精修循环次数: 8")
    logger.info("✓ 控制参数设置完成")

    # 重要：强制进行一次计算以确保数据正确初始化
    logger.info("步骤 7/7: 执行初始化计算")
    try:
        gpx.do_refinements([{}])  # 空精修触发计算
        initial_rwp = hist.get_wR()
        if initial_rwp is not None:
            logger.info(f"  初始化成功，初始Rwp: {initial_rwp:.2f}%")
        else:
            logger.warning("  初始化后Rwp仍为None，但继续进行")
    except Exception as e:
        logger.warning(f"  初始化计算遇到问题: {str(e)}")

    logger.info("✓ 初始化计算完成")
    logger.info("\n" + "="*50)
    logger.info("项目初始化全部完成!")
    logger.info("="*50 + "\n")

    return gpx, [hist], phases

def safe_float(value, default=0.0):
    """安全地将值转换为浮点数"""
    try:
        if isinstance(value, (int, float)):
            return float(value)
        elif isinstance(value, str):
            # 移除可能的非数字字符，只保留数字和小数点
            import re
            clean_value = re.sub(r'[^\d.-]', '', value)
            if clean_value:
                return float(clean_value)
            else:
                return default
        else:
            return default
    except (ValueError, TypeError):
        return default

def create_refined_cif(original_cif_path, phase, rwp, output_dir):
    """
    创建精修后的CIF文件，确保为标准CIF格式
    修复原子坐标行的格式问题
    """
    logger = logging.getLogger(__name__)
    
    # 读取原始CIF文件内容
    try:
        with open(original_cif_path, 'r', encoding='utf-8') as f:
            original_content = f.readlines()
    except Exception as e:
        logger.error(f"无法读取原始CIF文件 {original_cif_path}: {str(e)}")
        return None
    
    # 获取精修后的晶胞参数
    cell = phase.get_cell()
    
    # 获取精修后的原子参数
    atoms = phase.data['Atoms']
    
    # 创建新的CIF内容
    new_content = []
    in_atom_site_block = False
    atom_site_started = False
    atom_headers = []
    atom_data_lines = []
    
    # 首先处理原始CIF文件的头部信息
    for line in original_content:
        stripped_line = line.strip()
        
        # 跳过空行和注释
        if not stripped_line or stripped_line.startswith('#'):
            new_content.append(line)
            continue
            
        # 处理晶胞参数
        if stripped_line.startswith('_cell_length_a'):
            new_content.append(f"_cell_length_a                   {cell['length_a']:.6f}\n")
        elif stripped_line.startswith('_cell_length_b'):
            new_content.append(f"_cell_length_b                   {cell['length_b']:.6f}\n")
        elif stripped_line.startswith('_cell_length_c'):
            new_content.append(f"_cell_length_c                   {cell['length_c']:.6f}\n")
        elif stripped_line.startswith('_cell_angle_alpha'):
            new_content.append(f"_cell_angle_alpha                {cell['angle_alpha']:.4f}\n")
        elif stripped_line.startswith('_cell_angle_beta'):
            new_content.append(f"_cell_angle_beta                 {cell['angle_beta']:.4f}\n")
        elif stripped_line.startswith('_cell_angle_gamma'):
            new_content.append(f"_cell_angle_gamma                {cell['angle_gamma']:.4f}\n")
        elif stripped_line.startswith('_cell_volume'):
            new_content.append(f"_cell_volume                     {cell['volume']:.4f}\n")
            
        # 检测原子位点区块开始
        elif 'loop_' in stripped_line and not atom_site_started:
            # 检查下一个行是否是原子位点相关的表头
            next_line_idx = original_content.index(line) + 1
            if next_line_idx < len(original_content):
                next_line = original_content[next_line_idx].strip()
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
                atom_label = parts[0]
                
                # 查找对应的精修后原子参数
                refined_atom = None
                for atom_data in atoms:
                    if isinstance(atom_data, list) and len(atom_data) >= 8:
                        if atom_data[0] == atom_label or atom_data[1] == atom_label:
                            refined_atom = atom_data
                            break
                
                if refined_atom:
                    # 使用精修后的原子参数，确保格式标准
                    atom_name = refined_atom[0]
                    site_label = refined_atom[1] if len(refined_atom) > 1 else f"{atom_name}{idx+1}"
                    x, y, z = safe_float(refined_atom[3]), safe_float(refined_atom[4]), safe_float(refined_atom[5])
                    occ = safe_float(refined_atom[6], 1.0)
                    uiso = safe_float(refined_atom[7])
                    
                    # 构建标准CIF格式的原子行
                    new_line = f"{atom_name:<6} {site_label:<8} "
                    new_line += f"{x:.5f} {y:.5f} {z:.5f} "
                    new_line += f"{occ:.5f} {uiso:.5f} Uiso\n"
                    
                    new_atom_content.append(new_line)
                else:
                    # 没有找到精修后的参数，尝试解析原始行
                    if len(parts) >= 7:
                        # 假设原始格式：符号 标签 x y z 占位度 Uiso
                        new_line = f"{parts[0]:<6} {parts[1]:<8} "
                        new_line += f"{safe_float(parts[2]):.5f} {safe_float(parts[3]):.5f} {safe_float(parts[4]):.5f} "
                        new_line += f"{safe_float(parts[5], 1.0):.5f} {safe_float(parts[6]):.5f} Uiso\n"
                        new_atom_content.append(new_line)
        
        # 替换原子部分
        final_content = new_content[:first_atom_line_idx] + new_atom_content
        
    else:
        final_content = new_content
    
    # 添加精修信息到文件开头
    refined_content = []
    refined_content.append(f"# CIF文件精修版本 - 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
    refined_content.append(f"# 精修程序: GSAS-II中子粉末衍射精修\n")
    refined_content.append(f"# 精修Rwp: {rwp:.2f}%\n")
    refined_content.append(f"# 原始文件: {os.path.basename(original_cif_path)}\n")
    refined_content.append("#\n")
    
    # 添加精修后的内容
    refined_content.extend(final_content)
    
    # 保存精修后的CIF文件
    original_name = os.path.splitext(os.path.basename(original_cif_path))[0]
    refined_cif_path = os.path.join(output_dir, f"{original_name}_gsas_refined.cif")
    
    try:
        with open(refined_cif_path, 'w', encoding='utf-8') as f:
            f.writelines(refined_content)
        
        # 验证生成的CIF文件格式
        try:
            from ase.io import read
            test_atoms = read(refined_cif_path)
            logger.info(f"精修后的CIF文件格式验证成功: {refined_cif_path}")
            
            # 额外验证：检查文件是否包含重复的loop_
            with open(refined_cif_path, 'r') as f:
                content = f.read()
                loop_count = content.count('loop_')
                if loop_count > 1:
                    logger.warning(f"警告：CIF文件中检测到 {loop_count} 个loop_语句，可能存在格式问题")
                else:
                    logger.info("CIF文件格式检查通过，无重复loop_语句")
                    
        except Exception as verify_error:
            logger.warning(f"精修后的CIF文件格式验证失败: {str(verify_error)}")
            logger.warning("文件已保存但可能需要手动调整格式")
        
        return refined_cif_path
        
    except Exception as e:
        logger.error(f"保存精修后的CIF文件失败: {str(e)}")
        return None

def save_structure_parameters(gpx, phases, output_dir):
    """保存结构参数到文件"""
    logger = logging.getLogger(__name__)
    
    for i, phase in enumerate(phases, 1):
        phase_name = f"Phase_{i}"
        
        # 获取晶胞参数
        cell = phase.get_cell()
        logger.info(f"获取到晶胞参数: {cell}")
        
        # 尝试获取ESD，如果失败就跳过
        has_esd = False
        cell_esd = None
        try:
            cell_esd_data = phase.get_cell_and_esd()
            logger.info(f"ESD数据结构: {type(cell_esd_data)}, 长度: {len(cell_esd_data) if hasattr(cell_esd_data, '__len__') else 'N/A'}")
            if len(cell_esd_data) > 1:
                cell_esd = cell_esd_data[1]
                if hasattr(cell_esd, '__getitem__') and len(cell_esd) >= 7:
                    has_esd = True
                    logger.info(f"成功获取ESD: {cell_esd}")
        except Exception as esd_error:
            logger.warning(f"无法获取{phase_name}的ESD值: {str(esd_error)}")
        
        # 获取原子参数
        atoms = phase.data['Atoms']
        logger.info(f"获取到{len(atoms)}个原子")
        
        # 创建结构参数文件
        struct_file = Path(output_dir) / f'{phase_name}_structure_params.txt'
        
        try:
            with open(struct_file, 'w', encoding='utf-8') as f:
                f.write(f"中子精修结构参数 - {phase_name}\n")
                f.write("=" * 50 + "\n\n")
                
                # 晶胞参数
                f.write("晶胞参数:\n")
                if has_esd:
                    f.write(f"a = {cell['length_a']:.6f}({cell_esd['length_a']:.6f}) Å\n")
                    f.write(f"b = {cell['length_b']:.6f}({cell_esd['length_b']:.6f}) Å\n")
                    f.write(f"c = {cell['length_c']:.6f}({cell_esd['length_c']:.6f}) Å\n")
                    f.write(f"α = {cell['angle_alpha']:.4f}({cell_esd['angle_alpha']:.6f})°\n")
                    f.write(f"β = {cell['angle_beta']:.4f}({cell_esd['angle_beta']:.6f})°\n")
                    f.write(f"γ = {cell['angle_gamma']:.4f}({cell_esd['angle_gamma']:.6f})°\n")
                    f.write(f"体积 = {cell['volume']:.4f}({cell_esd['volume']:.6f}) Å³\n\n")
                else:
                    f.write(f"a = {cell['length_a']:.6f} Å\n")
                    f.write(f"b = {cell['length_b']:.6f} Å\n")
                    f.write(f"c = {cell['length_c']:.6f} Å\n")
                    f.write(f"α = {cell['angle_alpha']:.4f}°\n")
                    f.write(f"β = {cell['angle_beta']:.4f}°\n")
                    f.write(f"γ = {cell['angle_gamma']:.4f}°\n")
                    f.write(f"体积 = {cell['volume']:.4f} Å³\n\n")
                
                # 原子参数
                f.write("原子参数:\n")
                f.write("原子名称\t位点\tx\t\ty\t\tz\t\t占有率\t\tUiso/Å²\n")
                f.write("-" * 80 + "\n")
                
                if isinstance(atoms, dict):
                    # 字典格式的原子数据
                    for atom_label, atom_data in atoms.items():
                        if isinstance(atom_data, list) and len(atom_data) >= 8:
                            atom_name = atom_data[0]
                            site = atom_data[1]
                            x, y, z = safe_float(atom_data[3]), safe_float(atom_data[4]), safe_float(atom_data[5])
                            occ = safe_float(atom_data[6], 1.0)
                            uiso = safe_float(atom_data[7])
                            f.write(f"{atom_name}\t{site}\t{x:.5f}\t{y:.5f}\t{z:.5f}\t{occ:.3f}\t\t{uiso:.5f}\n")
                elif isinstance(atoms, list):
                    # 列表格式的原子数据
                    for atom_data in atoms:
                        if isinstance(atom_data, list) and len(atom_data) >= 8:
                            atom_name = atom_data[0]
                            site = atom_data[1]
                            x, y, z = safe_float(atom_data[3]), safe_float(atom_data[4]), safe_float(atom_data[5])
                            occ = safe_float(atom_data[6], 1.0)
                            uiso = safe_float(atom_data[7])
                            f.write(f"{atom_name}\t{site}\t{x:.5f}\t{y:.5f}\t{z:.5f}\t{occ:.3f}\t\t{uiso:.5f}\n")
                
                f.write(f"\n生成时间: {__import__('datetime').datetime.now()}\n")
                
            logger.info(f"结构参数已成功保存到: {struct_file}")
            
        except Exception as save_error:
            logger.error(f"保存结构参数文件时出错: {str(save_error)}")

def print_structure_parameters(phases):
    """打印结构参数到控制台"""
    print("\n" + "=" * 60)
    print("中子精修结构参数总结")
    print("=" * 60)
    
    for i, phase in enumerate(phases, 1):
        print(f"\nPhase {i}:")
        print("-" * 40)
        
        # 晶胞参数
        cell = phase.get_cell()
        try:
            cell_esd_data = phase.get_cell_and_esd()
            # 检查ESD数据结构
            if len(cell_esd_data) > 1 and hasattr(cell_esd_data[1], '__getitem__'):
                cell_esd = cell_esd_data[1]
                has_esd = True
            else:
                has_esd = False
        except:
            has_esd = False
        
        print("晶胞参数:")
        if has_esd:
            try:
                print(f"  a = {cell['length_a']:.6f}({cell_esd['length_a']:.6f}) Å")
                print(f"  b = {cell['length_b']:.6f}({cell_esd['length_b']:.6f}) Å")
                print(f"  c = {cell['length_c']:.6f}({cell_esd['length_c']:.6f}) Å")
                print(f"  α = {cell['angle_alpha']:.4f}({cell_esd['angle_alpha']:.4f})°")
                print(f"  β = {cell['angle_beta']:.4f}({cell_esd['angle_beta']:.4f})°")
                print(f"  γ = {cell['angle_gamma']:.4f}({cell_esd['angle_gamma']:.4f})°")
                print(f"  体积 = {cell['volume']:.4f}({cell_esd['volume']:.4f}) Å³")
            except (KeyError, TypeError):
                has_esd = False
                
        if not has_esd:
            print(f"  a = {cell['length_a']:.6f} Å")
            print(f"  b = {cell['length_b']:.6f} Å")
            print(f"  c = {cell['length_c']:.6f} Å")
            print(f"  α = {cell['angle_alpha']:.4f}°")
            print(f"  β = {cell['angle_beta']:.4f}°")
            print(f"  γ = {cell['angle_gamma']:.4f}°")
            print(f"  体积 = {cell['volume']:.4f} Å³")
        
        # 原子数量统计
        atoms = phase.data['Atoms']  # 直接访问原子数据字典
        print(f"\n原子位点数量: {len(atoms)}")
        
        # 显示前几个原子的信息
        if atoms:
            print("\n主要原子位点:")
            print("  原子\t位点\tx\t\ty\t\tz\t占有率\tUiso")
            count = 0
            
            if isinstance(atoms, dict):
                # 字典格式的原子数据
                for atom_label, atom_data in atoms.items():
                    if isinstance(atom_data, list) and len(atom_data) >= 8 and count < 5:
                        atom_name = atom_data[0]
                        site = atom_data[1]
                        x, y, z = safe_float(atom_data[3]), safe_float(atom_data[4]), safe_float(atom_data[5])
                        occ = safe_float(atom_data[6], 1.0)
                        uiso = safe_float(atom_data[7])
                        print(f"  {atom_name}\t{site}\t{x:.3f}\t{y:.3f}\t{z:.3f}\t{occ:.3f}\t{uiso:.4f}")
                        count += 1
            elif isinstance(atoms, list):
                # 列表格式的原子数据
                for atom_data in atoms:
                    if isinstance(atom_data, list) and len(atom_data) >= 8 and count < 5:
                        atom_name = atom_data[0]
                        site = atom_data[1]
                        x, y, z = safe_float(atom_data[3]), safe_float(atom_data[4]), safe_float(atom_data[5])
                        occ = safe_float(atom_data[6], 1.0)
                        uiso = safe_float(atom_data[7])
                        print(f"  {atom_name}\t{site}\t{x:.3f}\t{y:.3f}\t{z:.3f}\t{occ:.3f}\t{uiso:.4f}")
                        count += 1
            
            if len(atoms) > 5:
                print(f"  ... 还有 {len(atoms) - 5} 个原子位点")

def export_neutron_results(gpx, hist, phases, output_dir, original_cif_files, refine_idx):
    """导出中子精修结果"""
    logger = logging.getLogger(__name__)
    
    # 首先验证数据一致性
    print("\n🔍 验证数据一致性...")
    x_data = hist.getdata('x')
    y_obs = hist.getdata('yobs')
    y_calc = hist.getdata('ycalc')
    y_bkg = hist.getdata('background')
    
    # 数据统计
    zero_calc = np.sum(y_calc == 0) if hasattr(y_calc, '__len__') else 0
    print(f"  数据点数: {len(x_data)}")
    print(f"  观测强度范围: {y_obs.min():.6f} - {y_obs.max():.6f}")
    print(f"  计算强度范围: {y_calc.min():.6f} - {y_calc.max():.6f}")
    print(f"  背景强度范围: {y_bkg.min():.6f} - {y_bkg.max():.6f}")
    print(f"  计算强度为0的点数: {zero_calc}/{len(y_calc)}")
    
    # 检查数据质量
    if zero_calc > len(y_calc) * 0.5:
        print("  ⚠️  警告：超过50%的计算强度为0，数据可能有问题")
    elif zero_calc > 0:
        print(f"  ℹ️  信息：{zero_calc}个点的计算强度为0，这在某些角度范围是正常的")
    else:
        print("  ✅ 所有数据点都有计算强度")
    
    # 保存详细的数据文件，包含验证信息
    data_file = Path(output_dir) / 'neutron_refinement_data_verified.csv'
    try:
        # 创建包含验证信息的CSV
        with open(data_file, 'w', encoding='utf-8') as f:
            # 写入头部信息
            f.write("# 中子精修数据验证报告\n")
            f.write(f"# 生成时间: {__import__('datetime').datetime.now()}\n")
            f.write(f"# 数据点数: {len(x_data)}\n")
            f.write(f"# 观测强度范围: {y_obs.min():.6f} - {y_obs.max():.6f}\n")
            f.write(f"# 计算强度范围: {y_calc.min():.6f} - {y_calc.max():.6f}\n")
            f.write(f"# 计算强度为0的点数: {zero_calc}/{len(y_calc)}\n")
            current_rwp = hist.get_wR()
            if current_rwp is not None:
                f.write(f"# 当前Rwp: {current_rwp:.2f}%\n")
            f.write("#\n")
            
            # 写入数据表头
            f.write("x,y_obs,y_calc,y_bkg,residual,weight,d_spacing\n")
            
            # 写入数据
            weights = hist.getdata('yweight')
            d_spacing = hist.getdata('d')
            
            for i in range(len(x_data)):
                residual = y_obs[i] - y_calc[i] if y_calc[i] != 0 else 0
                f.write(f"{x_data[i]:.3f},{y_obs[i]:.6f},{y_calc[i]:.6f},{y_bkg[i]:.6f},"
                       f"{residual:.6f},{weights[i]:.2e},{d_spacing[i]:.6f}\n")
        
        logger.info(f"验证数据已保存到: {data_file}")
        
    except Exception as e:
        logger.error(f"保存验证数据时出错: {str(e)}")
    
    # 保存原始格式的数据文件（兼容性）
    try:
        original_data_file = Path(output_dir) / 'neutron_refinement_data.csv'
        hist.Export(str(original_data_file), '.csv', 'hist')
        logger.info(f"原始格式数据已保存到: {original_data_file}")
    except Exception as e:
        logger.warning(f"保存原始格式数据时出错: {str(e)}")
    
    # 生成图表
    try:    
        plt.figure(figsize=(14, 10))
        
        # 创建子图
        # plt.subplot(2, 1, 1)
        plt.plot(x_data, y_obs, 'b.', label='Observed', markersize=2, alpha=0.7)
        plt.plot(x_data, y_calc, 'r-', label='Calculated', linewidth=1.5)
        plt.plot(x_data, y_bkg, 'g-', label='Background', linewidth=1)
        diff = y_obs - y_calc
        plt.plot(x_data, diff, 'k-', label='Difference (Obs-Calc)', linewidth=1)
        # plt.axhline(y=0, color='gray', linestyle='--', alpha=0.5)
        rwp_val = hist.get_wR()
        rwp_display = f"{rwp_val:.2f}%" if rwp_val is not None else "N/A"
        plt.title(f'Neutron Refinement - Rwp: {rwp_display}')
        plt.xlabel('Time (μs)')
        plt.ylabel('Intensity')
        plt.legend()
        plt.grid(True, alpha=0.3)
        
        # # 差值图
        # plt.subplot(2, 1, 2)
        # diff = y_obs - y_calc
        # plt.plot(x_data, diff, 'k-', label='Difference (Obs-Calc)', linewidth=1)
        # plt.axhline(y=0, color='gray', linestyle='--', alpha=0.5)
        # plt.xlabel('Time (μs)')
        # plt.ylabel('Difference')
        # plt.legend()
        # plt.grid(True, alpha=0.3)
        
        plt.tight_layout()
        plot_path = Path(output_dir) / 'neutron_refinement_plot_verified.png'
        plt.savefig(str(plot_path), dpi=300, bbox_inches='tight')
        plt.close()
        
        logger.info(f"验证图表已保存到: {plot_path}")
        
    except ImportError:
        logger.warning("matplotlib不可用，跳过图表生成")
    except Exception as e:
        logger.error(f"生成图表时出错: {str(e)}")
    
    # 保存详细结构参数
    save_structure_parameters(gpx, phases, output_dir)
    
    # 创建精修后的CIF文件
    refined_cif_files = []
    for i, phase in enumerate(phases):
        if i < len(original_cif_files):
            original_cif = original_cif_files[i]
            rwp_val = hist.get_wR()
            refined_cif = create_refined_cif(original_cif, phase, rwp_val if rwp_val is not None else 0.0, output_dir)
            if refined_cif:
                refined_cif_files.append(refined_cif)
    
    # 收集所有生成的文件
    output_files = []
    if data_file.exists():
        output_files.append(str(data_file))
    
    # 添加其他可能的输出文件
    for i in range(len(phases)):
        phase_name = f"Phase_{i+1}"
        struct_file = Path(output_dir) / f'{phase_name}_structure_params.txt'
        if struct_file.exists():
            output_files.append(str(struct_file))
    
    # 添加精修后的CIF文件
    output_files.extend(refined_cif_files)
    
    # 收集结果
    current_rwp = hist.get_wR()
    results = {
        'refine_idx': refine_idx + 1,
        'rwp': current_rwp if current_rwp is not None else 0.0,
        'cell_params': [phase.get_cell() for phase in phases],
        'output_files': output_files,
        'refined_cif_files': refined_cif_files,
        'plot_path': str(plot_path),
        'gpx_path_before': str(Path(output_dir) / f'neutron_refinement.bak{refine_idx}.gpx'),
        'gpx_path_current': str(Path(output_dir) / f'neutron_refinement.gpx'),
        'lst_path': str(Path(output_dir) / f'neutron_refinement.lst'),
        'data_quality': {
            'total_points': len(x_data),
            'zero_calc_points': int(zero_calc),
            'calc_range': [float(y_calc.min()), float(y_calc.max())],
            'obs_range': [float(y_obs.min()), float(y_obs.max())]
        }
    }
    
    return results

def flexible_neutron_refinement(gpx, hist, phases, output_dir,refine_idx, refine_config=None, use_constraints=True):
    """
    灵活的中子精修函数 - 一次性精修所有启用的参数

    参数:
    gpx: GSAS-II项目对象
    hist: 历史数据对象
    phases: 相列表
    output_dir: 输出目录
    refine_config: 精修配置字典，支持以下参数:
        {
            # 基础参数
            'refine_background': True,           # 背景精修
            'background_coeffs': 3,              # 背景系数数量
            'refine_cell': True,                 # 晶胞参数精修
            'refine_scale': True,                # 标度因子精修
            'refine_absorption': False,          # 吸收参数精修

            # 仪器参数 (可以整体或单独控制)
            'refine_instrument': True,           # 整体仪器参数精修
            'refine_instrument_difc': False,     # difC
            'refine_instrument_difa': False,     # difA
            'refine_instrument_difb': False,     # difB
            'refine_instrument_zero': False,     # Zero
            'refine_instrument_alpha': False,    # alpha
            'refine_instrument_beta': False,     # beta-0, beta-1, beta-q
            'refine_instrument_sig': False,      # sig-0, sig-1, sig-2, sig-q
            'refine_instrument_x': False,        # X
            'refine_instrument_y': False,        # Y
            'refine_instrument_z': False,        # Z

            # 原子参数 (可以灵活控制)
            'refine_atoms': True,                # 整体原子参数精修
            'refine_atoms_pos': False,           # 原子位置 (X)
            'refine_atoms_occ': False,           # 原子占位 (F)
            'refine_atoms_uiso': False,          # 各向同性温度因子 (U)

            # 高级参数
            'refine_limits': False,              # Limits精修
            'refine_sample_displacement': False, # 样品位移
            'refine_size': False,                # 晶粒尺寸
            'refine_mustrain': False,            # 微应变
            'refine_phase_fraction': False,      # 相分数
        }
    use_constraints: 是否使用约束
    """
    logger = logging.getLogger(__name__)
    logger.info("开始一次性精修流程...")

    # 默认精修配置（全部禁用）
    default_config = {
        # 基础参数
        'refine_background': False,
        'background_coeffs': 3,
        'refine_cell': False,
        'refine_scale': False,
        'refine_absorption': False,

        # 仪器参数
        'refine_instrument': False,
        'refine_instrument_difc': False,
        'refine_instrument_difa': False,
        'refine_instrument_difb': False,
        'refine_instrument_zero': False,
        'refine_instrument_alpha': False,
        'refine_instrument_beta': False,
        'refine_instrument_sig': False,
        'refine_instrument_x': False,
        'refine_instrument_y': False,
        'refine_instrument_z': False,

        # 原子参数
        'refine_atoms': False,
        'refine_atoms_pos': False,
        'refine_atoms_occ': False,
        'refine_atoms_uiso': False,

        # 高级参数
        'refine_limits': False,
        'refine_sample_displacement': False,
        'refine_size': False,
        'refine_mustrain': False,
        'refine_phase_fraction': False,
    }

    # 使用用户定义的配置或默认配置
    if refine_config is None:
        refine_config = default_config
        logger.info("使用默认精修配置（全部禁用）")
    else:
        # 合并配置，确保所有键都存在
        for key in default_config:
            if key not in refine_config:
                refine_config[key] = default_config[key]
        logger.info("使用自定义精修配置")

    # 记录启用的精修参数
    enabled_refinements = [key for key, value in refine_config.items() if value and isinstance(value, bool)]
    logger.info(f"启用的精修参数: {enabled_refinements}")

    # 构建一次性精修字典
    ref_dict = {"set": {}}

    # 1. 背景精修
    if refine_config.get('refine_background', False):
        logger.info("添加背景参数到精修字典")
        num_coeffs = refine_config.get('background_coeffs', 3)
        ref_dict["set"]["Background"] = {"no. coeffs": num_coeffs, "refine": True}
        logger.info(f"  背景系数数量: {num_coeffs}")

    # 2. 晶胞参数精修
    if refine_config.get('refine_cell', False):
        logger.info("添加晶胞参数到精修字典")
        # 设置收敛标准
        if use_constraints:
            gpx.data['Controls']['data']['min dM/M'] = 1e-4
        else:
            gpx.data['Controls']['data']['min dM/M'] = 1e-3
        ref_dict["set"]["Cell"] = True

    # 3. 标度因子精修
    if refine_config.get('refine_scale', False):
        logger.info("添加标度因子到精修字典")
        if "Sample Parameters" not in ref_dict["set"]:
            ref_dict["set"]["Sample Parameters"] = []
        ref_dict["set"]["Sample Parameters"].append("Scale")

    # 4. 仪器参数精修 (支持整体或单独控制)
    instrument_params = []

    # 如果启用整体仪器参数精修
    if refine_config.get('refine_instrument', False):
        logger.info("添加整体仪器参数到精修字典")
        instrument_params.extend([
            "X", "Y",  # 仪器几何参数
            "alpha",   # 角度相关峰宽参数
            "beta-0", "beta-1", "beta-q",  # 各向异性峰形参数
            "sig-0", "sig-1", "sig-2", "sig-q"  # 峰宽分布参数
        ])
    else:
        # 单独控制各个仪器参数
        if refine_config.get('refine_instrument_difc', False):
            instrument_params.append("difC")
            logger.info("  添加 difC")
        if refine_config.get('refine_instrument_difa', False):
            instrument_params.append("difA")
            logger.info("  添加 difA")
        if refine_config.get('refine_instrument_difb', False):
            instrument_params.append("difB")
            logger.info("  添加 difB")
        if refine_config.get('refine_instrument_zero', False):
            instrument_params.append("Zero")
            logger.info("  添加 Zero")
        if refine_config.get('refine_instrument_alpha', False):
            instrument_params.append("alpha")
            logger.info("  添加 alpha")
        if refine_config.get('refine_instrument_beta', False):
            instrument_params.extend(["beta-0", "beta-1", "beta-q"])
            logger.info("  添加 beta参数")
        if refine_config.get('refine_instrument_sig', False):
            instrument_params.extend(["sig-0", "sig-1", "sig-2", "sig-q"])
            logger.info("  添加 sig参数")
        if refine_config.get('refine_instrument_x', False):
            instrument_params.append("X")
            logger.info("  添加 X")
        if refine_config.get('refine_instrument_y', False):
            instrument_params.append("Y")
            logger.info("  添加 Y")
        if refine_config.get('refine_instrument_z', False):
            instrument_params.append("Z")
            logger.info("  添加 Z")

    if instrument_params:
        ref_dict["set"]["Instrument Parameters"] = instrument_params

    # 5. 样品位移参数
    if refine_config.get('refine_sample_displacement', False):
        logger.info("添加样品位移参数到精修字典")
        if "Sample Parameters" not in ref_dict["set"]:
            ref_dict["set"]["Sample Parameters"] = []
        ref_dict["set"]["Sample Parameters"].extend(["DisplaceX", "DisplaceY"])

    # 6. 吸收参数精修
    if refine_config.get('refine_absorption', False):
        logger.info("添加吸收参数到精修字典")
        if "Sample Parameters" not in ref_dict["set"]:
            ref_dict["set"]["Sample Parameters"] = []
        ref_dict["set"]["Sample Parameters"].append("Absorption")

    # 7. 原子参数精修 (支持灵活控制)
    atom_refine_flags = ""

    # 如果启用整体原子参数精修
    if refine_config.get('refine_atoms', False):
        logger.info("添加整体原子参数到精修字典")
        atom_refine_flags = "XUF"  # 位置(X) + 温度因子(U) + 占位(F)
    else:
        # 单独控制各个原子参数
        if refine_config.get('refine_atoms_pos', False):
            atom_refine_flags += "X"
            logger.info("  添加原子位置精修")
        if refine_config.get('refine_atoms_uiso', False):
            atom_refine_flags += "U"
            logger.info("  添加温度因子精修")
        if refine_config.get('refine_atoms_occ', False):
            atom_refine_flags += "F"
            logger.info("  添加占位精修")

    if atom_refine_flags:
        for phase in phases:
            phase.set_refinements({
                "Atoms": {
                    "all": atom_refine_flags
                }
            })

    # 8. 晶粒尺寸精修
    if refine_config.get('refine_size', False):
        logger.info("添加晶粒尺寸参数到精修字典")
        for phase in phases:
            try:
                # 设置尺寸精修
                phase.set_HAP_refinements({hist.name: {"Size": ["isotropic", True]}})
            except Exception as e:
                logger.warning(f"  设置尺寸精修失败: {str(e)}")

    # 9. 微应变精修
    if refine_config.get('refine_mustrain', False):
        logger.info("添加微应变参数到精修字典")
        for phase in phases:
            try:
                # 设置微应变精修
                phase.set_HAP_refinements({hist.name: {"Mustrain": ["isotropic", True]}})
            except Exception as e:
                logger.warning(f"  设置微应变精修失败: {str(e)}")

    # 10. 相分数精修
    if refine_config.get('refine_phase_fraction', False):
        logger.info("添加相分数参数到精修字典")
        for phase in phases:
            try:
                phase.set_HAP_refinements({hist.name: {"Scale": True}})
            except Exception as e:
                logger.warning(f"  设置相分数精修失败: {str(e)}")

    # 检查是否有需要精修的参数
    # 只把显式的布尔型 refine_ 开头的键视为“启用”标志，避免像 background_coeffs(3) 这类数值被误判为启用
    ref_flag_keys = [k for k in refine_config.keys() if k.startswith('refine_') and isinstance(refine_config[k], bool)]
    any_ref_enabled = any(refine_config.get(k) for k in ref_flag_keys)
    # 检查 ref_dict['set'] 是否有实际要精修的条目
    has_ref_entries = bool(ref_dict.get('set'))
    if not any_ref_enabled or not has_ref_entries:
        logger.warning("没有启用任何精修参数，跳过精修")
        gpx.save(str(Path(output_dir) / f'neutron_refinement_{refine_idx+1}.gpx'))
        return hist.get_wR()

    # 显示最终的ref_dict
    logger.info(f"精修字典: {ref_dict}")

    # 执行一次性精修
    try:
        logger.info("开始执行一次性精修...")
        gpx.do_refinements([ref_dict])
        final_rwp = hist.get_wR()
        if final_rwp is not None:
            logger.info(f"精修完成，最终Rwp: {final_rwp:.2f}%")

        # 保存结果
        gpx.save(str(Path(output_dir) / f'neutron_refinement_{refine_idx+1}.gpx'))

    except Exception as e:
        logger.error(f"精修执行失败: {str(e)}")
        raise

    logger.info("一次性精修流程结束")
    return hist.get_wR()



def neutron_refinement_backup(
    neutron_data,
    neutron_inst,
    cif_files,
    output_dir,
    refine_params,
    refine_idx,
    gpx_path,
    use_constraints,
    que=multiprocessing.Queue()
):
    
    # 创建输出目录
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    
    # 设置日志
    logger = setup_logging(output_dir)
    logger.info("="*50)
    logger.info("开始中子粉末衍射精修")
    logger.info(f"中子数据: {neutron_data}")
    logger.info(f"结构文件: {len(cif_files) if cif_files else 0}个")
    logger.info("="*50)
    logger.info(f"精修配置: {refine_params}")
    
    try:
        # 1. 初始化项目
        gpx, hists, phases = initialize_project(
            neutron_data, neutron_inst, cif_files, gpx_path, output_dir
        )
        hist = hists[0]  # 只有中子数据
        gpx_path_current = str(Path(output_dir) / f'comprehensive_refinement_{refine_idx+1}.gpx')
        gpx.save(gpx_path_current)

         # 2. 执行灵活精修流程
        if refine_params:
            logger.info("开始自定义精修流程...")
            flexible_neutron_refinement(
                gpx, hist, phases, output_dir, refine_idx,
                refine_params=refine_params,
                use_constraints=use_constraints
            )

            results = export_neutron_results(gpx, hist, phases, output_dir, cif_files, refine_idx)

            logger.info("="*50)
            rwp_val = results.get('rwp')
            rwp_val = rwp_val if rwp_val is not None else 0.0
            logger.info(f"精修完成! 最终Rwp: {rwp_val:.2f}%")
            if results.get('refined_cif_files'):
                logger.info(f"生成的精修CIF文件: {', '.join(results['refined_cif_files'])}")
            logger.info("="*50)
        else:
            results = {
                'rwp': (hist.get_wR() if (hist and hist.get_wR() is not None) else 0.0),
                'gpx_path_current': gpx_path_current,
                'gpx_path_before':'',
                'cell_params': [phase.get_cell() for phase in phases],
                'output_files': [],
                'refined_cif_files': []
            }
        
        # 4. 打印结构参数到控制台
        print_structure_parameters(phases)
        
        
        que.put(results)
        # return results
        
    except Exception as e:
        logger.error("!"*50)
        logger.error(f"精修失败: {str(e)}")
        logger.error("!"*50)
        que.put({})
        raise

def neutron_refinement(
    neutron_data=None,
    neutron_inst=None,
    cif_files=None,
    output_dir='neutron_output',
    # 基础参数 (简单模式,向后兼容)
    refine_background=False,       # True/False
    refine_cell=False,             # True/False
    refine_scale=False,            # True/False
    refine_instrument=False,       # True/False
    refine_absorption=False,       # True/False
    refine_atoms=False,            # True/False
    use_constraints=True,          # 是否使用约束
    gpx_file=None,                 # 可选的已有gpx项目文件路径
    refine_idx=0,                  # 精修轮次索引
    # 高级参数 (可选,用于精细控制)
    advanced_config=None,          # 高级配置字典,支持所有GSAS-II精修参数
    que=multiprocessing.Queue()
):
    """
    中子粉末衍射数据精修主函数 - 支持所有GSAS-II精修参数

    参数:
    neutron_data: 中子数据文件路径 (当gpx_file为None时必需)
    neutron_inst: 仪器参数文件路径 (当gpx_file为None时必需)
    cif_files: CIF结构文件列表 (当gpx_file为None时必需)
    output_dir: 输出目录

    基础参数 (简单模式):
    refine_background: True/False - 是否精修背景
    refine_cell: True/False - 是否精修晶胞参数
    refine_scale: True/False - 是否精修标度因子
    refine_instrument: True/False - 是否精修仪器参数(整体)
    refine_absorption: True/False - 是否精修吸收参数
    refine_atoms: True/False - 是否精修原子参数和温度因子(整体)
    use_constraints: 是否使用约束
    gpx_file: 可选,已有的GSAS-II项目文件(.gpx)路径,如果提供则加载现有项目继续精修

    高级参数 (高级模式,提供更精细的控制):
    advanced_config: 字典,包含所有可用的精修参数,例如:
        {
            # 基础参数
            'background_coeffs': 3,              # 背景系数数量

            # 仪器参数 (单独控制)
            'refine_instrument_difc': True,      # difC
            'refine_instrument_difa': True,      # difA
            'refine_instrument_difb': True,      # difB
            'refine_instrument_zero': True,      # Zero
            'refine_instrument_alpha': True,     # alpha
            'refine_instrument_beta': True,      # beta-0, beta-1, beta-q
            'refine_instrument_sig': True,       # sig-0, sig-1, sig-2, sig-q
            'refine_instrument_x': True,         # X
            'refine_instrument_y': True,         # Y
            'refine_instrument_z': True,         # Z

            # 原子参数 (单独控制)
            'refine_atoms_pos': True,            # 原子位置 (X)
            'refine_atoms_occ': True,            # 原子占位 (F)
            'refine_atoms_uiso': True,           # 各向同性温度因子 (U)

            # 高级参数
            'refine_limits': True,               # Limits精修
            'refine_sample_displacement': True,  # 样品位移
            'refine_size': True,                 # 晶粒尺寸
            'refine_mustrain': True,             # 微应变
            'refine_phase_fraction': True,       # 相分数
        }

    que: 多进程队列

    注意:
    - 如果提供了 advanced_config,它会覆盖基础参数
    - 基础参数适合快速使用,高级参数适合精细控制
    """
    # 创建输出目录
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    # 设置日志
    logger = setup_logging(output_dir)
    logger.info("="*50)
    logger.info("开始中子粉末衍射精修")
    logger.info("="*50)

    # 构建精修配置字典
    if advanced_config:
        # 使用高级配置
        logger.info("使用高级配置模式")
        refine_config = advanced_config.copy()
        # 确保基础参数也在配置中
        if 'refine_background' not in refine_config:
            refine_config['refine_background'] = refine_background
        if 'refine_cell' not in refine_config:
            refine_config['refine_cell'] = refine_cell
        if 'refine_scale' not in refine_config:
            refine_config['refine_scale'] = refine_scale
        if 'refine_instrument' not in refine_config:
            refine_config['refine_instrument'] = refine_instrument
        if 'refine_absorption' not in refine_config:
            refine_config['refine_absorption'] = refine_absorption
        if 'refine_atoms' not in refine_config:
            refine_config['refine_atoms'] = refine_atoms
    else:
        # 使用基础配置(向后兼容)
        logger.info("使用基础配置模式")
        refine_config = {
            'refine_background': refine_background,
            'refine_cell': refine_cell,
            'refine_scale': refine_scale,
            'refine_instrument': refine_instrument,
            'refine_absorption': refine_absorption,
            'refine_atoms': refine_atoms,
        }

    logger.info(f"精修配置: {refine_config}")

    try:
        # 1. 加载或初始化项目
        if gpx_file and os.path.exists(gpx_file):
            # 使用用户提供的gpx文件
            logger.info(f"📂 加载现有GSAS-II项目: {gpx_file}")
            gpx = G2sc.G2Project(gpxfile=gpx_file)

            # 获取项目中的histogram和phases
            hists = gpx.histograms()
            phases = gpx.phases()

            if not hists:
                raise ValueError("GPX项目中未找到任何histogram数据")
            if not phases:
                raise ValueError("GPX项目中未找到任何晶体结构")

            hist = hists[0]  # 使用第一个histogram
            logger.info(f"  ✓ 成功加载项目")
            logger.info(f"  - Histogram数量: {len(hists)}")
            logger.info(f"  - Phase数量: {len(phases)}")
            logger.info(f"  - 使用Histogram: {hist.name}")

            # 保存原始cif文件路径(用于后续导出)
            original_cif_files = cif_files if cif_files else []

        else:
            # 初始化新项目
            if gpx_file:
                logger.warning(f"⚠️  指定的gpx文件不存在: {gpx_file}")
                logger.info("  将创建新项目")
            else:
                logger.info("🆕 创建新的GSAS-II项目")

            # 验证必需参数
            if not (neutron_data and neutron_inst and cif_files):
                raise ValueError("创建新项目时必须提供 neutron_data, neutron_inst 和 cif_files 参数")

            logger.info(f"  中子数据: {neutron_data}")
            logger.info(f"  仪器参数: {neutron_inst}")
            logger.info(f"  结构文件: {len(cif_files)}个")

            gpx, hists, phases = initialize_project(
                neutron_data, neutron_inst, cif_files, output_dir
            )
            hist = hists[0]  # 只有中子数据
            original_cif_files = cif_files

         # 2. 执行一次性精修流程
        logger.info("开始一次性精修流程...")
        flexible_neutron_refinement(
            gpx, hist, phases, output_dir,refine_idx,
            refine_config=refine_config,
            use_constraints=use_constraints
        )
        
        # 3. 导出结果
        try:
            results = export_neutron_results(gpx, hist, phases, output_dir, original_cif_files,refine_idx=refine_idx)
        except Exception as e:
            logger.warning(f"导出结果时遇到问题: {str(e)}")
            # 创建基本结果，确保能显示精修参数
            results = {
                'rwp': (hist.get_wR() if (hist and hist.get_wR() is not None) else 0.0),
                'cell_params': [phase.get_cell() for phase in phases],
                'output_files': [],
                'refined_cif_files': []
            }
        
        # 4. 打印结构参数到控制台
        print_structure_parameters(phases)
        
        logger.info("="*50)
        rwp_final = results.get('rwp')
        rwp_final = rwp_final if rwp_final is not None else 0.0
        logger.info(f"精修完成! 最终Rwp: {rwp_final:.2f}%")
        if results['refined_cif_files']:
            logger.info(f"生成的精修CIF文件: {', '.join(results['refined_cif_files'])}")
        logger.info("="*50)
        que.put(results)
        return results

    except Exception as e:
        logger.error("!"*50)
        logger.error(f"精修失败: {str(e)}")
        logger.error("!"*50)
        que.put({})
        raise


id_to_task={}
id_to_result={}

@mcp.tool()
def write_info(
    gsa_path: str,
    instprm_path: str,
    cif_paths: List[str],
    refine_params: Dict = {},
    refine_idx: int = 0,
    gpx_path: str|None = None,
    task_id: str|None = None,
    thread_id: str|None = None,
    use_constraints: bool = True
    ) -> Dict:
    
    id_=task_id
    result_json={"id":id_,'state':None,'result':None}
    if not gpx_path:
        output_dir = os.path.join(os.path.dirname(gsa_path), f"output/{thread_id}/gsas_output_{task_id}")
    else:
        output_dir = os.path.dirname(gpx_path)

    Path(output_dir).mkdir(parents=True, exist_ok=True)

    logger = setup_logging(output_dir)
    
    if id_ not in id_to_task or id_to_task[id_]==None:
        # 任务创建
        id_to_result[id_]=multiprocessing.Queue()
        id_to_task[id_]=multiprocessing.Process(target=neutron_refinement,kwargs={
                    'neutron_data': gsa_path,
                    'neutron_inst': instprm_path,
                    'cif_files': cif_paths,
                    'output_dir': output_dir,
                    'advanced_config': refine_params,
                    'refine_idx': refine_idx,
                    'gpx_file': gpx_path,
                    'use_constraints': use_constraints,        # 使用约束
                    'que': id_to_result[id_]
                    }
        )

        result_json['state']='created'
        id_to_task[id_].start()
        # id_to_task[id_].kill()
        # id_to_task.pop(id_)

    else:
        if not id_to_task[id_].is_alive():
            info=id_to_result[id_].get()
            id_to_task[id_].join()
            if not info:
                # 报错
                result_json['state'] = 'completed'
                result_json['status'] = 'error'
                result_json['result'] = {
                    "status": "error",
                    "error": "中子精修处理失败",
                    "suggestion": "请检查输入文件格式是否正确",
                    'gpx_path_before': str(Path(output_dir) / f'neutron_refinement.bak{refine_idx}.gpx'),
                    'gpx_path_current': str(Path(output_dir) / f'neutron_refinement.gpx'),
                    'lst_path': str(Path(output_dir) / f'neutron_refinement.lst'),
                    'plot_path':str(Path(output_dir) / 'neutron_refinement_plot_verified.png'),
                    "refine_idx": refine_idx + 1
                }
                result_json["summary"] = "GSAS-II中子粉末衍射精修失败"
                # pass
            else:
                # 处理结果
                logger.info(f"精修结果: {info}")
                id_to_task[id_]=None
                result_json['state']='completed'
                result_json['status']='success'
                result_json["name"] = f"{task_id}_neutron_refinement"
                
                # 计算输出文件大小并构建结果信息
                output_files = []
                total_size_bytes = 0
                total_size_mb = 0
                
                # 检查输出目录中的所有文件
                if os.path.exists(output_dir):
                    for filename in os.listdir(output_dir):
                        file_path = os.path.join(output_dir, filename)
                        if os.path.isfile(file_path):
                            file_size = os.path.getsize(file_path)
                            file_size_mb = file_size / (1024 * 1024)
                            
                            output_files.append({
                                "filename": filename,
                                "path": file_path,
                                "size_bytes": file_size,
                                "size_mb": round(file_size_mb, 3)
                            })
                            
                            total_size_bytes += file_size
                            total_size_mb += file_size_mb
                
                # 构建摘要信息
                rwp = info.get('rwp', 0.0)
                rwp_value = rwp if rwp is not None else 0.0
                result_json["summary"] = f"GSAS-II中子粉末衍射精修完成，最终Rwp: {rwp_value:.2f}%\n"
                result_json["summary"] += f"总输出文件大小: {round(total_size_mb, 3)} MB\n"
                result_json["summary"] += f"生成文件数量: {len(output_files)}\n"
                result_json["summary"] += f"精修轮次: {info.get('refine_idx', 0)}\n"
                # 添加主要文件信息  
                if info.get('refined_cif_files'):
                    result_json["summary"] += f"精修CIF文件: {os.path.basename(info['refined_cif_files'][0])}\n"
                
                # 构建详细结果
                next_idx = (refine_idx + 1) if refine_idx is not None else 1
                result_json['result'] = {
                    "status": "success",
                    "gpx_path_current": info.get('gpx_path_current', ''),
                    "gpx_path_before": info.get('gpx_path_before', ''),
                    "plot_path": info.get('plot_path', ''),
                    "lst_path": info.get('lst_path', ''),
                    "rwp": rwp_value,
                    "refine_idx": next_idx,
                }

        else:
            result_json['state']='running'
    return result_json

if __name__ == '__main__':
    # mcp.run(transport="stdio")
    mcp.run(transport="sse")

# # 使用示例
# if __name__ == "__main__":
#     import sys
#     import traceback

#     print("="*60)
#     print("开始执行 GSAS-II 中子精修脚本")
#     print("="*60)

#     # 输入文件路径
#     input_dir = "/home/dur/rozai_agent_test/datasets/BL09"
#     output_dir = "/home/dur/rozai_agent_test/datasets/BL09/gsas_output20251222/output_gpx2"

#     # 方式1: 从头开始创建新项目
#     print("\n=== 方式1: 创建新项目 ===")
#     gsa_file = os.path.join(input_dir, "RUNdataFiles/RUN0000719/RUN0000719_groupHA.gsa")
#     inst_file = os.path.join(input_dir, "instrumentFiles/groupHA_instr.instprm")
#     cif_file = os.path.join(input_dir, "RUNdataFiles/RUN0000719/cifFiles/zeolite.cif")
#     # gsa_file = os.path.join(input_dir, "RUNdataFiles/RUN0023568/RUN0023568_group5.gsa")
#     # inst_file = os.path.join(input_dir, "instrumentFiles/RT_group5.instprm")
#     # cif_file = os.path.join(input_dir, "RUNdataFiles/RUN0023568/cifFiles/Si.cif")

#     # 方式2: 使用已有的gpx文件继续精修
#     # 取消下面的注释来使用已有gpx文件
#     # existing_gpx = "/home/dur/rozai_agent_test/datasets/BL09/gsas_output20251222/neutron_refinement.gpx"
#     existing_gpx = None

#     print("\n检查输入文件:")
#     if existing_gpx:
#         print(f"模式: 加载现有GPX项目")
#         if os.path.exists(existing_gpx):
#             print(f"✓ GPX文件: {existing_gpx}")
#         else:
#             print(f"✗ GPX文件不存在: {existing_gpx}")
#             sys.exit(1)
#     else:
#         print(f"模式: 创建新项目")
#         for fpath, fname in [(gsa_file, "GSA文件"), (inst_file, "仪器参数文件"), (cif_file, "CIF文件")]:
#             if os.path.exists(fpath):
#                 size_mb = os.path.getsize(fpath) / (1024 * 1024)
#                 print(f"✓ {fname}: {fpath} ({size_mb:.2f} MB)")
#             else:
#                 print(f"✗ {fname} 不存在: {fpath}")
#                 sys.exit(1)

#     print("\n开始精修流程...")

#     try:
#         # 执行中子精修（使用True/False控制精修参数）
#         que = multiprocessing.Queue()

#         if existing_gpx:
#             # 使用已有gpx文件
#             results = neutron_refinement(
#                 output_dir=output_dir,
#                 gpx_file=existing_gpx,          # 指定已有的gpx文件
#                 refine_background=True,        # 精修背景
#                 refine_cell=False,              # 精修晶胞
#                 refine_scale=True,             # 精修标度因子
#                 refine_instrument=False,        # 精修仪器参数
#                 refine_absorption=False,       # 不精修吸收参数
#                 refine_atoms=False,             # 精修原子参数和温度因子
#                 use_constraints=False,          # 使用约束
#                 que=que
#             )
#         else:
#             # 创建新项目
#             results = neutron_refinement(
#                 neutron_data=gsa_file,
#                 neutron_inst=inst_file,
#                 cif_files=[cif_file],
#                 output_dir=output_dir,
#                 refine_background=True,        # 精修背景
#                 refine_cell=False,              # 精修晶胞
#                 refine_scale=True,             # 精修标度因子
#                 refine_instrument=False,        # 精修仪器参数
#                 refine_absorption=False,       # 不精修吸收参数
#                 refine_atoms=False,             # 精修原子参数和温度因子
#                 use_constraints=False,          # 使用约束
#                 que=que
#             )

#         print("\n" + "="*60)
#         print("精修完成!")
#         print("="*60)
#         print(f"最终Rwp: {results['rwp']:.2f}%")
#         if results.get('refined_cif_files'):
#             print(f"\n生成的精修CIF文件:")
#             for cif_file_out in results['refined_cif_files']:
#                 print(f"  - {cif_file_out}")
#         print("="*60)

#     except KeyboardInterrupt:
#         print("\n\n用户中断执行")
#         sys.exit(1)
#     except Exception as e:
#         print("\n" + "!"*60)
#         print("精修过程中发生错误:")
#         print("!"*60)
#         print(f"错误类型: {type(e).__name__}")
#         print(f"错误信息: {str(e)}")
#         print("\n完整错误堆栈:")
#         traceback.print_exc()
#         print("!"*60)
#         sys.exit(1)


# # import time,uuid
# # id_=str(uuid.uuid4())
# # for _ in range(1800):
# #     t=write_info("/aifs/user/home/liqingmeng/rozai_agent/tools/GSAS-II-master/GSASII/RUN0021905"
# #     ,"RUN0021905_group5.gsa"
# #     ,"RT_GROUP5.instprm"
# #     ,"mp-149.cif"
# #     ,"/aifs/user/home/liqingmeng/rozai_agent/tools/GSAS-II-master/GSASII/RUN0021905/gsas_output_20250908"
# #     ,id_)
# #     if t['state']=="completed":
# #         print(t)
# #         break
# #     else:
# #         print(t)
# #     time.sleep(100)