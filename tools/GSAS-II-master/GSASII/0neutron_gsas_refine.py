import os
import logging
import sys
import io
import json
import time
import csv
import copy
from pathlib import Path
import numpy as np
import matplotlib
import matplotlib.pyplot as plt
import GSASIIscriptable as G2sc
import GSASIImath as G2mth
import GSASIIElem as G2elem
import shutil
import pprint
from typing import List, Dict, Union, Optional
from mcp.server.fastmcp import FastMCP
from read_lst import prune_lst_keep_main

try:
    from src.core.tools import ProcessPollingMcpToolBase
except ImportError:
    repo_root = Path(__file__).resolve().parents[3]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    from src.core.tools import ProcessPollingMcpToolBase

import os
from dotenv import load_dotenv
here = Path(__file__).parent.parent.parent.parent.absolute()
print(f"Loading environment variables from: {here / '.env'}")
load_dotenv(dotenv_path=os.path.join(here, ".env"), override=True)
TMP_DIR = Path(os.getenv("WORKDIR", ""))
print(f"Using WORKER_SPACE: {TMP_DIR}")

def generate_fit_plot(hist, phases, user_id, session_id, mission_name, task_id):
    
    x_data = hist.getdata('x')
    y_obs = hist.getdata('yobs')
    y_calc = hist.getdata('ycalc')
    y_bkg = hist.getdata('background')
    [tofmin, tofmax] = hist.data['Limits'][1]
    # 保存详细的数据文件，包含验证信息
    save_dir = TMP_DIR / f"{user_id}"/f"{session_id}"/f"{mission_name}"
    save_dir.mkdir(parents=True, exist_ok=True)
    
    
    # 生成图表
    plot_path = None  # 初始化plot_path变量，防止后续使用时未定义  
    # 使用较小画布与较低输出分辨率，避免图片过大占用上下文与带宽。
    plt.figure(figsize=(10, 6))
    
    # 创建子图
    # plt.subplot(2, 1, 1)
    # 更可读的字体与样式设置
    title_fs = 14
    label_fs = 12
    tick_fs = 11
    legend_fs = 11

    # 先计算差值
    diff = y_obs - y_calc

    # 仅绘制 limits 范围内的数据，避免超出范围的数据干扰图像
    try:
        plot_mask = (x_data >= tofmin) & (x_data <= tofmax)
        x_plot = x_data[plot_mask]
        y_obs_plot = y_obs[plot_mask]
        y_calc_plot = y_calc[plot_mask]
        y_bkg_plot = y_bkg[plot_mask]
        diff_plot = diff[plot_mask]
    except Exception:
        x_plot = x_data
        y_obs_plot = y_obs
        y_calc_plot = y_calc
        y_bkg_plot = y_bkg
        diff_plot = diff

    # 将 y_obs/y_calc/y_bkg 整体上移，避免与 diff 曲线重叠。
    try:
        signal_min = float(np.nanmin([np.nanmin(y_obs_plot), np.nanmin(y_calc_plot), np.nanmin(y_bkg_plot)]))
        signal_max = float(np.nanmax([np.nanmax(y_obs_plot), np.nanmax(y_calc_plot), np.nanmax(y_bkg_plot)]))
        diff_min = float(np.nanmin(diff_plot))
        diff_max = float(np.nanmax(diff_plot))
        y_span = max(signal_max, diff_max) - min(signal_min, diff_min)
        margin = 0.08 * y_span if y_span > 0 else 1.0
        target_lower = diff_max + margin
        y_offset = max(0.0, target_lower - signal_min)
    except Exception:
        y_offset = 0.0

    y_obs_shifted = y_obs_plot + y_offset
    y_calc_shifted = y_calc_plot + y_offset
    y_bkg_shifted = y_bkg_plot + y_offset
    
    plt.plot(x_plot, diff_plot, 'k-', label='Difference (Obs-Calc)', linewidth=1)
    # 观测谱使用细阶梯线更接近粉末衍射原始计数形态，细节与峰肩更清晰
    plt.step(x_plot, y_obs_shifted, where='mid', color='#1f77b4', label='Experiment Pattern', linewidth=0.9, alpha=0.95)
    plt.step(x_plot, y_calc_shifted, where='mid', color='r', label='Calculated Pattern', linewidth=1.1, alpha=0.95)
    plt.plot(x_plot, y_bkg_shifted, 'g-', label='Background', linewidth=1.2)
    plt.axvline(x=tofmin, color='m', linestyle='--', linewidth=1, label='TOF Min')
    plt.axvline(x=tofmax, color='c', linestyle='--', linewidth=1, label='TOF Max')
    plt.title(f'Neutron Refinement - Rwp: {hist.get_wR():.2f}%', fontsize=title_fs)
    plt.xlabel('Time (μs)', fontsize=label_fs)
    plt.ylabel('Intensity', fontsize=label_fs)
    # 延后显示 legend，以便包含下面绘制的 Bragg 竖线
    plt.grid(True, alpha=0.3)
    ax = plt.gca()
    ax.tick_params(labelsize=tick_fs)
    # 使用 GSAS-II 生成的反射表在图上标注 Bragg 位置（短且下调的竖线）
    try:
        import GSASIIpwd as G2pwd
        import GSASIIlattice as G2lat
        Inst = hist.data['Instrument Parameters'][0]
        try:
            d_spacing = hist.getdata('d')
            dmin = float(np.nanmin(d_spacing)) if hasattr(d_spacing, '__len__') and len(d_spacing) else 0.0
        except Exception:
            dmin = 0.0
        ax = plt.gca()
        y0, y1 = ax.get_ylim()
        yspan = y1 - y0 if (y1 - y0) != 0 else 1.0
        # 将竖线上移（避免太靠下）并适度缩短长度，避免覆盖谱峰
        start = y0 + 0.05 * yspan
        tick_len = 0.02 * yspan
        cmap = matplotlib.colormaps['tab10']
        # 收集自定义图例句柄（用于竖线样式的 Bragg peaks）
        from matplotlib.lines import Line2D
        bragg_handles = []
        bragg_labels = []
        for ip, phase in enumerate(phases):
            try:
                SGData = phase.data['General']['SGData']
                cell = phase.data['General']['Cell'][1:7]
                A = G2lat.cell2A(cell)
                hkls = G2pwd.getHKLpeak(dmin, SGData, A, Inst)
                if hkls is None or len(hkls) == 0:
                    continue
                col = cmap(ip % 10)
                # 收集该相所有反射位置，批量绘制以减少 Artist 数量
                positions = []
                for hkl in hkls:
                    try:
                        pos = float(hkl[4])
                    except Exception:
                        pos = float(G2lat.Dsp2pos(Inst, hkl[3]))
                    # 仅保留位于当前显示范围内的反射，减少不必要绘制
                    if pos >= tofmin and pos <= tofmax:
                        positions.append(pos)
                if positions:
                    ax.vlines(positions, start, start + tick_len, color=col, linewidth=1.6, alpha=0.9)
                    # 创建图例句柄（每相一次）
                    phase_name = None
                    try:
                        phase_name = phase.data.get('General', {}).get('Name')
                    except Exception:
                        phase_name = None
                    if not phase_name:
                        phase_name = getattr(phase, 'name', None)
                    if not phase_name:
                        phase_name = f'Phase {ip+1}'
                    handle = Line2D([0], [0], color=col, marker='|', linestyle='None', markersize=12, markeredgewidth=4)
                    bragg_handles.append(handle)
                    bragg_labels.append(f'Bragg peaks ({phase_name})')
            except Exception:
                continue
        # 在所有竖线绘制完成后再显示 legend，以包含 Bragg peaks 条目
        try:
            # 获取当前默认 legend handles/labels（前面绘制的 obs/calc/bkg/diff/limits）
            cur_handles, cur_labels = ax.get_legend_handles_labels()
            # 将 bragg 的自定义句柄附加到末尾
            if bragg_handles:
                cur_handles.extend(bragg_handles)
                cur_labels.extend(bragg_labels)
            # 布局：两列以节省空间，字体更大
            # 放置在图内上方（使用轴坐标），防止跑出外框
            # 将 legend 放到左上角内侧并增大字体，减少对数据的遮挡
            ax.legend(cur_handles, cur_labels, fontsize=9, ncol=1, loc='upper left', bbox_to_anchor=(1.01, 1.0), borderaxespad=0.0, frameon=True, framealpha=0.9)
        except Exception:
            pass
    except Exception:
        pass
    
    # # 差值图
    # plt.subplot(2, 1, 2)
    # diff = y_obs - y_calc
    # plt.plot(x_data, diff, 'k-', label='Difference (Obs-Calc)', linewidth=1)
    # plt.axhline(y=0, color='gray', linestyle='--', alpha=0.5)
    # plt.xlabel('Time (μs)')
    # plt.ylabel('Difference')
    # plt.legend()
    # plt.grid(True, alpha=0.3)
    
    plt.tight_layout(pad=2.0)
    plot_path = save_dir / f'{task_id}_plot.png'
    plt.savefig(str(plot_path), dpi=120, bbox_inches='tight')
    plt.close()

    return str(plot_path)

def _is_occupancy_param(param):
    p = str(param).strip().lower()
    return p in {'occupancy', 'occ', 'fraction', 'afrac'}


def _extract_atom_label(item):
    if isinstance(item, dict):
        return item.get('atom', item.get('label', item.get('atom_label', None)))
    if item is None:
        return None
    return str(item)


def _merge_atom_refine_flag(flag):
    s = str(flag) if flag is not None else ''
    if 'F' in s:
        return s
    return s + 'F'


def _normalize_atom_refine_flags(flag):
    s = str(flag) if flag is not None else ''
    # 仅保留 GSAS-II 原子精修支持的标志位，并按固定顺序去重
    return ''.join(ch for ch in 'XUF' if ch in s)


def _remove_atom_refine_flags(current_flags, flags_to_close):
    current = _normalize_atom_refine_flags(current_flags)
    to_close = set(_normalize_atom_refine_flags(flags_to_close))
    return ''.join(ch for ch in current if ch not in to_close)


def _to_gsas_atom_refine_flags(flag):
    """将规范化标志转换为 GSAS 期望格式：无标志时用单空格表示关闭。"""
    norm = _normalize_atom_refine_flags(flag)
    return norm if norm else ' '


def _resolve_initial_atom_value(init_values, phase, phase_idx, atom_idx, atom_label):
    """按统一规则解析按原子配置的初始值。"""
    if isinstance(init_values, dict):
        if atom_label in init_values:
            return init_values[atom_label]
        if f"{phase_idx}:{atom_label}" in init_values:
            return init_values[f"{phase_idx}:{atom_label}"]
        if hasattr(phase, 'name'):
            key = f"{phase.name}:{atom_label}"
            if key in init_values:
                return init_values[key]
        return None

    if isinstance(init_values, (list, tuple)):
        if phase_idx >= len(init_values):
            return None
        phase_values = init_values[phase_idx]
        if isinstance(phase_values, (list, tuple)):
            if atom_idx < len(phase_values):
                return phase_values[atom_idx]
            return None
        if isinstance(phase_values, dict):
            return phase_values.get(atom_label)
        return phase_values

    return init_values


def _resolve_phase_scoped_value(values, phase, phase_idx):
    """按统一规则解析按相配置的值（支持 all/default/phase/index）。"""
    if isinstance(values, dict):
        if hasattr(phase, 'name') and phase.name in values:
            return values[phase.name]
        if phase_idx in values:
            return values[phase_idx]
        if str(phase_idx) in values:
            return values[str(phase_idx)]
        if 'all' in values:
            return values['all']
        if 'default' in values:
            return values['default']
        return None

    if isinstance(values, (list, tuple)):
        if phase_idx < len(values):
            return values[phase_idx]
        return None

    return values


def _resolve_initial_instrument_params(init_values, hist):
    """解析 initial_instrument_params，支持全局与按直方图配置。"""
    if init_values is None:
        return {}
    if not isinstance(init_values, dict):
        return None

    resolved = {}
    reserved_keys = {'all', 'default', 'histograms'}

    # 1) 直接在顶层写参数（视为全局）
    direct_pairs = {
        k: v for k, v in init_values.items()
        if k not in reserved_keys and not isinstance(v, dict)
    }
    resolved.update(direct_pairs)

    # 2) all/default 全局参数
    for global_key in ('all', 'default'):
        global_cfg = init_values.get(global_key)
        if isinstance(global_cfg, dict):
            resolved.update(global_cfg)

    # 3) 按 histogram 名称配置（支持 histograms 子字典或顶层同名键）
    hist_name = getattr(hist, 'name', None)
    if hist_name:
        hist_cfg = None
        histograms_cfg = init_values.get('histograms')
        if isinstance(histograms_cfg, dict):
            hist_cfg = histograms_cfg.get(hist_name)
        if hist_cfg is None:
            top_hist_cfg = init_values.get(hist_name)
            if isinstance(top_hist_cfg, dict):
                hist_cfg = top_hist_cfg
        if isinstance(hist_cfg, dict):
            resolved.update(hist_cfg)

    return resolved


def _resolve_spacegroup_symbol(spacegroup_value):
    """把空间群输入解析为 GSAS-II 可用符号。

    支持：
    - 编号 1-230（int/数字字符串）
    - H-M 符号（允许大小写与多空格）
    - full H-M（会尝试转 short H-M）
    """
    try:
        import GSASIIspc as G2spc
    except Exception as e:
        raise RuntimeError(f"导入 GSASIIspc 失败: {e}")

    if spacegroup_value is None:
        return None

    # 1) 优先支持 1-230 编号
    if isinstance(spacegroup_value, int) and not isinstance(spacegroup_value, bool):
        sg_no = int(spacegroup_value)
        if 1 <= sg_no <= 230:
            return G2spc.spgbyNum[sg_no]
        raise ValueError(f"空间群编号超出范围(1-230): {sg_no}")

    sg_text = str(spacegroup_value).strip()
    if not sg_text:
        return None

    if sg_text.isdigit():
        sg_no = int(sg_text)
        if 1 <= sg_no <= 230:
            return G2spc.spgbyNum[sg_no]
        raise ValueError(f"空间群编号超出范围(1-230): {sg_no}")

    # 2) 对符号做标准化尝试
    cleaned = ' '.join(sg_text.split())
    candidates = [cleaned]

    try:
        short_hm = G2spc.fullHM2shortHM(cleaned)
        if short_hm and short_hm not in candidates:
            candidates.append(short_hm)
    except Exception:
        pass

    try:
        normalized = G2spc.StandardizeSpcName(cleaned)
        if normalized and normalized not in candidates:
            candidates.append(normalized)
    except Exception:
        pass

    for cand in candidates:
        err, _ = G2spc.SpcGroup(cand)
        if err == 0:
            return cand

    raise ValueError(f"空间群无效: {spacegroup_value}")


def _apply_spacegroup_to_phase(phase, phase_idx, spacegroup_value, logger):
    """将单个相切换到指定空间群，并同步各原子的 site sym/mult。"""
    if spacegroup_value is None:
        return False

    try:
        import GSASIIspc as G2spc
    except Exception as e:
        raise RuntimeError(f"导入 GSASIIspc 失败: {e}")

    sg_text = _resolve_spacegroup_symbol(spacegroup_value)
    if not sg_text:
        return False

    err, sg_data = G2spc.SpcGroup(sg_text)
    if err != 0:
        raise ValueError(f"空间群无效: {sg_text}; 详情: {G2spc.SGErrors(err)}")

    general = phase.data.setdefault('General', {})
    old_sg = general.get('SGData', {}).get('SpGrp', 'UNKNOWN')
    general['SGData'] = sg_data

    # 按当前相的 AtomPtrs 重新计算 site sym/mult，避免空间群切换后原子元数据不一致。
    atom_ptrs = general.get('AtomPtrs', None)
    if not (isinstance(atom_ptrs, (list, tuple)) and len(atom_ptrs) >= 3):
        atom_ptrs = [3, 1, 7, 9]

    cx = int(atom_ptrs[0])
    cs = int(atom_ptrs[2])
    updated_atoms = 0

    atoms = phase.data.get('Atoms', [])
    for atom in atoms:
        if not isinstance(atom, list):
            continue
        if len(atom) <= max(cx + 2, cs + 1):
            continue
        try:
            xyz = [float(atom[cx]), float(atom[cx + 1]), float(atom[cx + 2])]
            sytsym, mult = G2spc.SytSym(xyz, sg_data)[:2]
            atom[cs] = sytsym
            atom[cs + 1] = mult
            updated_atoms += 1
        except Exception:
            continue

    logger.info(
        f"相{phase_idx} 空间群已切换: {old_sg} -> {sg_data.get('SpGrp', sg_text)}; 同步原子数: {updated_atoms}"
    )
    return True


def _auto_open_related_constrained_atoms(gpx, phases, refine_config, logger):
    """若某约束连通分量中已有参数被放开，则自动放开同分量的其余占位参数。"""
    info = {
        'enabled': True,
        'trigger_opened_atoms': [],
        'auto_opened_atoms': [],
    }

    if not isinstance(refine_config, dict):
        return info

    constraints = refine_config.get('constraints', [])
    refine_atoms = refine_config.get('refine_atoms', {})
    if not isinstance(constraints, list) or not constraints:
        return info
    if not isinstance(refine_atoms, dict) or not refine_atoms:
        return info

    all_open = ('all' in refine_atoms and 'F' in str(refine_atoms.get('all', '')))

    def is_opened_label(label):
        if all_open:
            return True
        if label not in refine_atoms:
            return False
        return 'F' in str(refine_atoms.get(label, ''))

    # 图节点: (phase_index, atom_label)
    adj = {}

    def add_node(node):
        if node not in adj:
            adj[node] = set()

    def connect_nodes(nodes):
        nodes = [n for n in nodes if n is not None]
        if len(nodes) < 2:
            return
        head = nodes[0]
        add_node(head)
        for n in nodes[1:]:
            add_node(n)
            adj[head].add(n)
            adj[n].add(head)

    # 解析约束，构建连通关系
    for c in constraints:
        if not isinstance(c, dict):
            continue
        ctype = str(c.get('type', '')).strip().lower()

        if ctype in {'occupancy_sum', 'occupancy_equiv'}:
            phase_ref = c.get('phase', c.get('phase_name', c.get('phase_index', None)))
            try:
                _, p_idx = _resolve_phase_obj(phases, phase_ref)
            except Exception:
                continue
            atoms = c.get('atoms', [])
            nodes = []
            for a in atoms if isinstance(atoms, list) else []:
                lbl = _extract_atom_label(a)
                if lbl:
                    nodes.append((p_idx, str(lbl)))
            for n in nodes:
                add_node(n)
            connect_nodes(nodes)

        elif ctype in {'eqn', 'equiv', 'hold', 'newvar'}:
            vars_cfg = c.get('vars', c.get('variables', []))
            nodes = []
            for v in vars_cfg if isinstance(vars_cfg, list) else []:
                if not isinstance(v, dict):
                    continue
                if not _is_occupancy_param(v.get('parameter', v.get('param', ''))):
                    continue
                phase_ref = v.get('phase', v.get('phase_name', v.get('phase_index', None)))
                atom = v.get('atom', v.get('atom_label', None))
                if atom is None:
                    continue
                try:
                    _, p_idx = _resolve_phase_obj(phases, phase_ref)
                except Exception:
                    continue
                nodes.append((p_idx, str(atom)))
            for n in nodes:
                add_node(n)
            connect_nodes(nodes)

    if not adj:
        return info

    # 找连通分量
    visited = set()
    components = []
    for node in adj:
        if node in visited:
            continue
        stack = [node]
        comp = set()
        visited.add(node)
        while stack:
            cur = stack.pop()
            comp.add(cur)
            for nb in adj.get(cur, set()):
                if nb not in visited:
                    visited.add(nb)
                    stack.append(nb)
        if comp:
            components.append(comp)

    opened_now = []
    opened_already = []
    for comp in components:
        has_open = any(is_opened_label(lbl) for _, lbl in comp)
        if not has_open:
            continue
        for p_idx, lbl in sorted(comp):
            if is_opened_label(lbl):
                opened_already.append({'phase_index': p_idx, 'atom': lbl})
                continue
            phase = phases[p_idx]
            try:
                phase.set_refinements({"Atoms": {lbl: 'F'}})
            except Exception:
                continue
            refine_atoms[lbl] = _merge_atom_refine_flag(refine_atoms.get(lbl, ''))
            opened_now.append({
                'phase_index': p_idx,
                'phase_name': getattr(phase, 'name', f'Phase_{p_idx+1}'),
                'atom': lbl,
                'flag': refine_atoms[lbl],
            })

    info['trigger_opened_atoms'] = sorted({x['atom'] for x in opened_already})
    info['auto_opened_atoms'] = opened_now
    if opened_now:
        logger.info(f"根据约束联动自动放开占位参数: {[x['atom'] for x in opened_now]}")
    return info

def flexible_neutron_refinement(gpx, hist, phases, user_id, session_id, mission_name, task_id, refine_config=None, use_constraints=False):
    logger = logging.getLogger(__name__)
    logger.info("开始一次性精修流程...")

    # 默认精修配置（全部禁用）
    default_config = {
        # 基础参数
        'refine_background': False,
        'background_coeffs': 3,
        'remove_fixed_background': False,
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
        'initial_absorption': None,
        'initial_instrument_params': None,

        # 原子参数
        'refine_atoms': {},
        'close_atoms': {},
        'initial_atom_uiso': None,  # 原子热无序因子初始值
        'initial_atom_occupancy': None,  # 原子占有率初始值

        # 高级参数
        'refine_limits': False,
        'refine_sample_displacement': False,
        'refine_size': False,
        'refine_mustrain': False,
        'initial_mustrain': None,  # 微应变初始值：支持 'isotropic'模式
        'refine_preferred_orientation': False,
        'preferred_orientation_model': 'MD',
        'initial_preferred_orientation': None,
        'preferred_orientation_axis': None,
        'refine_phase_fraction': False,
        'initial_limits': None,
        'spacegroup': None,
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

    # 若约束中已有部分参数被放开，自动放开同约束连通分量里的相关占位参数。
    auto_open_info = _auto_open_related_constrained_atoms(gpx, phases, refine_config, logger)
    refine_config['_constraint_auto_open_info'] = auto_open_info

    # 记录启用的精修参数
    enabled_refinements = [key for key, value in refine_config.items() if value and isinstance(value, bool)]
    logger.info(f"启用的精修参数: {enabled_refinements}")

    # 如果用户提供空间群(spacegroup)，在精修前更新相的 SGData，并同步原子位点对称与重数。
    spacegroup_cfg = refine_config.get('spacegroup')
    if spacegroup_cfg is not None:
        logger.info('检测到 spacegroup 配置，尝试切换相空间群')
        applied_count = 0
        for i, phase in enumerate(phases):
            sg_value = _resolve_phase_scoped_value(spacegroup_cfg, phase, i)
            if sg_value is None:
                continue
            try:
                if _apply_spacegroup_to_phase(phase, i, sg_value, logger):
                    applied_count += 1
            except Exception as e:
                logger.warning(f"相{i} 切换空间群失败: {e}")
        if applied_count == 0:
            logger.warning('spacegroup 已提供，但未成功应用到任何相')

    # 如果用户提供初始晶胞参数(initial_cell)，在执行任何晶胞精修前写入 phase
    # 支持格式：列表（按相索引）或字典（按相名或索引键）
    if refine_config.get('initial_cell'):
        logger.info('检测到 initial_cell 配置，尝试写入各相的初始晶胞参数')
        init_cells = refine_config.get('initial_cell')
        import math
        for i, phase in enumerate(phases):
            cell_vals = None
            # 列表或元组形式
            if isinstance(init_cells, (list, tuple)):
                if i < len(init_cells):
                    cell_vals = init_cells[i]
            elif isinstance(init_cells, dict):
                # 支持相名或索引键
                if hasattr(phase, 'name') and phase.name in init_cells:
                    cell_vals = init_cells[phase.name]
                elif i in init_cells:
                    cell_vals = init_cells[i]
                elif str(i) in init_cells:
                    cell_vals = init_cells[str(i)]

            if cell_vals is None:
                continue

            # 允许直接传入列表/元组 [a,b,c,alpha,beta,gamma]
            try:
                if isinstance(cell_vals, (list, tuple)) and len(cell_vals) >= 6:
                    a, b, c, alpha, beta, gamma = [float(x) for x in cell_vals[:6]]
                elif isinstance(cell_vals, dict):
                    a = float(cell_vals.get('length_a', cell_vals.get('a', cell_vals.get('A'))))
                    b = float(cell_vals.get('length_b', cell_vals.get('b', cell_vals.get('B'))))
                    c = float(cell_vals.get('length_c', cell_vals.get('c', cell_vals.get('C'))))
                    alpha = float(cell_vals.get('angle_alpha', cell_vals.get('alpha', 90.0)))
                    beta = float(cell_vals.get('angle_beta', cell_vals.get('beta', 90.0)))
                    gamma = float(cell_vals.get('angle_gamma', cell_vals.get('gamma', 90.0)))
                else:
                    logger.warning(f"initial_cell: 无法解析相{i}的格式: {type(cell_vals)}")
                    continue
            except Exception as e:
                logger.warning(f"解析 initial_cell 第{i}项失败: {e}")
                continue

            # 计算体积（实空间）并写回 phase.data['General']['Cell']，保留原有 refine flag 如果存在
            try:
                ra, rb, rg = math.radians(alpha), math.radians(beta), math.radians(gamma)
                cosA, cosB, cosG = math.cos(ra), math.cos(rb), math.cos(rg)
                vol = a * b * c * math.sqrt(max(0.0, 1.0 - cosA*cosA - cosB*cosB - cosG*cosG + 2*cosA*cosB*cosG))
            except Exception:
                vol = None

            try:
                old_cell = phase.data.get('General', {}).get('Cell')
                refine_flag = False
                if isinstance(old_cell, (list, tuple)) and len(old_cell) > 0:
                    refine_flag = bool(old_cell[0])

                # 按 GSAS-II 内部格式写入: [refine_flag, a,b,c,alpha,beta,gamma, volume]
                new_cell = [refine_flag, a, b, c, alpha, beta, gamma]
                if vol is not None:
                    new_cell.append(vol)
                phase.data.setdefault('General', {})['Cell'] = new_cell
                logger.info(f"为相{i}写入初始晶胞: {new_cell}")

                # 如果配置启用了晶胞精修，确保设置相的精修标志
                if refine_config.get('refine_cell', False):
                    try:
                        phase.set_refinements({"Cell": True})
                        logger.info(f"为相{i}启用晶胞精修标志")
                    except Exception:
                        logger.debug(f"相{i}不支持 set_refinements 或设置失败，已写入数据结构但未设置标志")

            except Exception as e:
                logger.warning(f"写入相{i}初始晶胞失败: {e}")

    # 如果用户提供初始原子热无序因子(initial_atom_uiso)，在执行任何精修前写入各相的原子
    # 支持格式：字典（按原子标签或相-原子标签）、列表（按相索引）或单个数值
    if refine_config.get('initial_atom_uiso') is not None:
        logger.info('检测到 initial_atom_uiso 配置，尝试写入各相的原子热无序因子')
        init_atom_uiso = refine_config.get('initial_atom_uiso')
        
        for phase_idx, phase in enumerate(phases):
            # 获取该相的原子列表
            if 'Atoms' not in phase.data:
                logger.warning(f"  相{phase_idx}中不存在Atoms数据")
                continue
            
            atoms = phase.data['Atoms']
            if not atoms:
                logger.warning(f"  相{phase_idx}中没有原子")
                continue
            
            # 遍历该相中的所有原子
            for atom_idx, atom_data in enumerate(atoms):
                if not isinstance(atom_data, list) or len(atom_data) < 11:
                    logger.warning(f"  相{phase_idx}的原子{atom_idx}数据格式不正确，跳过")
                    continue
                
                # 获取原子标签 (atom_data[1])
                atom_label = atom_data[1]
                uiso_val = _resolve_initial_atom_value(init_atom_uiso, phase, phase_idx, atom_idx, atom_label)
                
                # 设置Uiso值
                if uiso_val is not None:
                    try:
                        uiso_float = float(uiso_val)
                        # Uiso值在atom_data[10]
                        atom_data[10] = uiso_float
                        logger.info(f"为相{phase_idx}的原子{atom_label}(索引{atom_idx})设置初始Uiso: {uiso_float:.6f}")
                    except (ValueError, TypeError) as e:
                        logger.warning(f"为相{phase_idx}的原子{atom_label}设置Uiso失败: 无法将{uiso_val}转换为数值 ({e})")
                    except Exception as e:
                        logger.warning(f"为相{phase_idx}的原子{atom_label}设置Uiso失败: {e}")

    # 如果用户提供初始原子占有率(initial_atom_occupancy)，在执行任何精修前写入各相的原子
    # 支持格式：字典（按原子标签或相-原子标签）、列表（按相索引）或单个数值
    if refine_config.get('initial_atom_occupancy') is not None:
        logger.info('检测到 initial_atom_occupancy 配置，尝试写入各相的原子占有率')
        init_atom_occupancy = refine_config.get('initial_atom_occupancy')

        for phase_idx, phase in enumerate(phases):
            if 'Atoms' not in phase.data:
                logger.warning(f"  相{phase_idx}中不存在Atoms数据")
                continue

            atoms = phase.data['Atoms']
            if not atoms:
                logger.warning(f"  相{phase_idx}中没有原子")
                continue

            for atom_idx, atom_data in enumerate(atoms):
                if not isinstance(atom_data, list) or len(atom_data) < 7:
                    logger.warning(f"  相{phase_idx}的原子{atom_idx}数据格式不正确，跳过")
                    continue

                atom_label = atom_data[1]
                occ_val = _resolve_initial_atom_value(init_atom_occupancy, phase, phase_idx, atom_idx, atom_label)

                if occ_val is not None:
                    try:
                        occ_float = float(occ_val)
                        atom_data[6] = occ_float
                        logger.info(f"为相{phase_idx}的原子{atom_label}(索引{atom_idx})设置初始占有率: {occ_float:.6f}")
                    except (ValueError, TypeError) as e:
                        logger.warning(f"为相{phase_idx}的原子{atom_label}设置占有率失败: 无法将{occ_val}转换为数值 ({e})")
                    except Exception as e:
                        logger.warning(f"为相{phase_idx}的原子{atom_label}设置占有率失败: {e}")

    # 如果用户提供初始微应变参数(initial_mustrain)，在执行任何微应变精修前写入
    # 仅支持isotropic模式：单个数值
    if refine_config.get('initial_mustrain') is not None:
        logger.info('检测到 initial_mustrain 配置，尝试写入各相的初始微应变参数')
        init_mustrain = refine_config.get('initial_mustrain')
        
        for i, phase in enumerate(phases):
            mustrain_val = None
            
            # 支持列表或字典形式的初始值
            if isinstance(init_mustrain, (list, tuple)):
                if i < len(init_mustrain):
                    mustrain_val = init_mustrain[i]
            elif isinstance(init_mustrain, dict):
                # 支持相名或索引键
                if hasattr(phase, 'name') and phase.name in init_mustrain:
                    mustrain_val = init_mustrain[phase.name]
                elif i in init_mustrain:
                    mustrain_val = init_mustrain[i]
                elif str(i) in init_mustrain:
                    mustrain_val = init_mustrain[str(i)]
                else:
                    # 如果没有指定具体相，则使用作为全局配置
                    mustrain_val = init_mustrain
            else:
                mustrain_val = init_mustrain

            if mustrain_val is None:
                continue

            try:
                # 转换为浮点数
                mustrain_value = float(mustrain_val)
                
                # 获取该直方图中该相对应的Mustrain数据
                # Mustrain数据位置：phase.data['Histograms'][hist.name]['Mustrain']
                if hist.name not in phase.data.get('Histograms', {}):
                    logger.warning(f"  直方图 {hist.name} 在相{i}中不存在")
                    continue
                
                if 'Mustrain' not in phase.data['Histograms'][hist.name]:
                    logger.warning(f"  直方图 {hist.name} 中的Mustrain数据不存在，跳过")
                    continue
                
                mustrain = phase.data['Histograms'][hist.name]['Mustrain']
                
                # isotropic模式：写入第一个参数值
                # Mustrain结构: [mode, [param_values], [refine_flags], ...]
                mustrain[1][0] = mustrain_value
                print(f"为相{i}设置isotropic微应变初始值: {mustrain_value}")
                
            except (ValueError, TypeError) as e:
                logger.warning(f"写入相{i}初始微应变失败: 无法将{mustrain_val}转换为数值 ({e})")
            except Exception as e:
                logger.warning(f"写入相{i}初始微应变失败: {e}")

    # 如果用户提供择优取向配置，在精修前写入 HAP 的 Pref.Ori. 数据
    po_value_cfg = refine_config.get('initial_preferred_orientation')
    po_axis_cfg = refine_config.get('preferred_orientation_axis')
    po_model_cfg = refine_config.get('preferred_orientation_model')
    if po_value_cfg is not None or po_axis_cfg is not None or po_model_cfg is not None:
        logger.info('检测到 preferred orientation 配置，尝试写入各相的 Pref.Ori. 参数')
        for i, phase in enumerate(phases):
            try:
                hdata = phase.data.get('Histograms', {}).get(hist.name, None)
                if not isinstance(hdata, dict) or 'Pref.Ori.' not in hdata:
                    logger.warning(f"相{i}在直方图 {hist.name} 下不存在 Pref.Ori.，已跳过")
                    continue

                po_data = hdata['Pref.Ori.']
                if not isinstance(po_data, list) or len(po_data) < 4:
                    logger.warning(f"相{i} 的 Pref.Ori. 数据结构异常，已跳过")
                    continue

                model_val = _resolve_phase_scoped_value(po_model_cfg, phase, i)
                if model_val is not None:
                    model_text = str(model_val).strip().upper()
                    if model_text in {'MD', 'MARCH', 'MARCH-DOLLASE', 'MARCH_DOLLASE'}:
                        po_data[0] = 'MD'
                    elif model_text in {'SH', 'SPHERICAL_HARMONICS', 'SPHERICAL-HARMONICS'}:
                        po_data[0] = 'SH'
                    else:
                        logger.warning(f"相{i} 的 preferred_orientation_model 不支持: {model_val}，保留当前模型 {po_data[0]}")

                po_val = _resolve_phase_scoped_value(po_value_cfg, phase, i)
                if po_val is not None:
                    if str(po_data[0]).upper() == 'MD':
                        po_data[1] = float(po_val)
                    else:
                        logger.warning(f"相{i} 当前模型为 {po_data[0]}，暂不支持 initial_preferred_orientation 直接写入")

                axis_val = _resolve_phase_scoped_value(po_axis_cfg, phase, i)
                if axis_val is not None:
                    if isinstance(axis_val, str):
                        axis_parts = axis_val.replace(',', ' ').split()
                    else:
                        axis_parts = list(axis_val) if isinstance(axis_val, (list, tuple)) else None
                    if axis_parts is None or len(axis_parts) != 3:
                        logger.warning(f"相{i} 的 preferred_orientation_axis 格式错误: {axis_val}")
                    else:
                        axis_hkl = [int(axis_parts[0]), int(axis_parts[1]), int(axis_parts[2])]
                        if any(axis_hkl):
                            po_data[3] = axis_hkl
                        else:
                            logger.warning(f"相{i} 的 preferred_orientation_axis 不能为 [0,0,0]，已忽略")

                logger.info(f"相{i} Pref.Ori. 设置完成: model={po_data[0]}, value={po_data[1]}, axis={po_data[3]}, refine={po_data[2]}")
            except Exception as e:
                logger.warning(f"写入相{i}择优取向参数失败: {e}")

    # 如果用户提供 TOF 范围初值(initial_limits)，在精修前写入当前直方图 limits
    init_limits = refine_config.get('initial_limits')
    if init_limits:
        lim = init_limits.get(hist.name, init_limits.get('all'))
        if lim:
            hist.data['Limits'][1][0], hist.data['Limits'][1][1] = float(lim[0]), float(lim[1])
            logger.info(f"设置初始TOF范围: {hist.data['Limits'][1][0]} - {hist.data['Limits'][1][1]}")

    # 如果用户提供吸收参数初始值(initial_absorption)，在执行精修前写入当前直方图
    # 支持格式：
    # 1) 单值：0.12
    # 2) 字典：{'all': 0.12} / {'default': 0.12} / {'PWDR xxx': 0.12}
    # 3) 带 histograms 子字典：{'histograms': {'PWDR xxx': 0.12}, 'all': 0.1}
    if refine_config.get('initial_absorption') is not None:
        logger.info('检测到 initial_absorption 配置，尝试写入当前直方图的吸收参数')
        init_absorption = refine_config.get('initial_absorption')
        resolved_absorption = init_absorption

        if isinstance(init_absorption, dict):
            resolved_absorption = None
            hist_name = getattr(hist, 'name', None)

            if hist_name:
                histograms_cfg = init_absorption.get('histograms')
                if isinstance(histograms_cfg, dict) and hist_name in histograms_cfg:
                    resolved_absorption = histograms_cfg.get(hist_name)
                elif hist_name in init_absorption and not isinstance(init_absorption.get(hist_name), dict):
                    resolved_absorption = init_absorption.get(hist_name)

            if resolved_absorption is None:
                if 'all' in init_absorption:
                    resolved_absorption = init_absorption.get('all')
                elif 'default' in init_absorption:
                    resolved_absorption = init_absorption.get('default')

        if resolved_absorption is None:
            logger.warning('initial_absorption 未匹配到当前直方图或全局值，已跳过')
        else:
            try:
                abs_value = float(resolved_absorption)
            except (TypeError, ValueError):
                logger.warning(f"initial_absorption 的值无法转为数值，已跳过: {resolved_absorption}")
            else:
                try:
                    sample_params = hist.data.get('Sample Parameters')
                except Exception as e:
                    sample_params = None
                    logger.warning(f"无法获取 Sample Parameters，已跳过初值写入: {e}")

                if isinstance(sample_params, dict):
                    abs_entry = sample_params.get('Absorption', None)
                    if isinstance(abs_entry, list):
                        if len(abs_entry) >= 1:
                            abs_entry[0] = abs_value
                        else:
                            sample_params['Absorption'] = [abs_value, False]
                    elif isinstance(abs_entry, tuple):
                        abs_list = list(abs_entry)
                        if len(abs_list) >= 1:
                            abs_list[0] = abs_value
                        else:
                            abs_list = [abs_value, False]
                        sample_params['Absorption'] = abs_list
                    elif abs_entry is None:
                        sample_params['Absorption'] = [abs_value, False]
                    else:
                        sample_params['Absorption'] = [abs_value, False]
                    logger.info(f"设置吸收参数初值: Absorption = {abs_value}")
                else:
                    logger.warning('Sample Parameters 数据格式异常，已跳过 initial_absorption 写入')

    # 如果用户提供仪器参数初始值(initial_instrument_params)，在执行精修前写入当前直方图
    if refine_config.get('initial_instrument_params') is not None:
        logger.info('检测到 initial_instrument_params 配置，尝试写入当前直方图的仪器参数')
        resolved_inst_params = _resolve_initial_instrument_params(
            refine_config.get('initial_instrument_params'),
            hist,
        )

        if resolved_inst_params is None:
            logger.warning('initial_instrument_params 格式错误：应为字典，已跳过')
        else:
            try:
                inst = hist.data['Instrument Parameters'][0]
            except Exception as e:
                inst = None
                logger.warning(f"无法获取 Instrument Parameters，已跳过初值写入: {e}")

            if isinstance(inst, dict):
                # 仅允许写入当前工具支持精修的仪器参数键，避免误写 Type/Bank 等非数值字段
                allowed_keys = {
                    'difc': 'difC',
                    'difa': 'difA',
                    'difb': 'difB',
                    'zero': 'Zero',
                    'alpha': 'alpha',
                    'beta-0': 'beta-0',
                    'beta-1': 'beta-1',
                    'beta-q': 'beta-q',
                    'sig-0': 'sig-0',
                    'sig-1': 'sig-1',
                    'sig-2': 'sig-2',
                    'sig-q': 'sig-q',
                    'x': 'X',
                    'y': 'Y',
                    'z': 'Z',
                }
                # 兼容当前 histogram 中实际键名（大小写）
                inst_key_lower_map = {str(k).strip().lower(): k for k in inst.keys()}

                for raw_key, raw_value in resolved_inst_params.items():
                    if raw_value is None:
                        continue

                    key_norm = str(raw_key).strip().replace('_', '-').lower()
                    if key_norm not in allowed_keys:
                        logger.warning(f"initial_instrument_params 包含不支持的键，已跳过: {raw_key}")
                        continue

                    preferred_key = allowed_keys[key_norm]
                    target_key = preferred_key if preferred_key in inst else inst_key_lower_map.get(preferred_key.lower())
                    if target_key is None:
                        logger.warning(f"当前直方图不存在仪器参数键 {preferred_key}，已跳过")
                        continue

                    try:
                        value = float(raw_value)
                    except (TypeError, ValueError):
                        logger.warning(f"initial_instrument_params 的值无法转为数值，已跳过: {raw_key}={raw_value}")
                        continue

                    try:
                        entry = inst[target_key]
                        if isinstance(entry, list) and len(entry) >= 2:
                            entry[0] = value
                            entry[1] = value
                        elif isinstance(entry, list) and len(entry) == 1:
                            entry[0] = value
                        else:
                            logger.warning(f"仪器参数 {target_key} 数据格式异常，已跳过写入")
                            continue
                        logger.info(f"设置仪器参数初值: {target_key} = {value}")
                    except Exception as e:
                        logger.warning(f"写入仪器参数 {target_key} 失败: {e}")

    # 增加精修约束
    logger.info("处理精修约束配置...")
    if 'set_constraints' in refine_config:
        logger.info(f"添加约束配置数量: {len(refine_config.get('set_constraints', []))}")
        cdata = gpx.data.get('Constraints', {}).get('data', None)
        if cdata is None:
            gpx.data.setdefault('Constraints', {}).setdefault('data', {})
            cdata = gpx.data['Constraints']['data']
        for scope in ('Hist', 'HAP', 'Phase', 'Global'):
            cdata[scope] = []
        
        for i, c in enumerate(refine_config.get('set_constraints', [])):
            if not isinstance(c, dict):
                logger.warning(f"第 {i} 个约束配置格式错误，应该是字典，已跳过")
                continue
            if 'type' not in c:
                logger.warning(f"第 {i} 个约束配置缺少 type 字段，已跳过")
                continue
            if c['type'] == 'eqn':
                vars_cfg = c.get('vars', [])
                varlist = [_build_var_from_spec(gpx, phases, v) for v in vars_cfg]
                multlist = c.get('mults', [])
                total = float(c.get('total', 0.0))
                gpx.add_EqnConstr(total=total, varlist=varlist, multlist=multlist, reloadIdx=True)
            if c['type'] == 'equiv':
                vars_cfg = c.get('vars', [])
                varlist = [_build_var_from_spec(gpx, phases, v) for v in vars_cfg]
                multlist = c.get('mults', [])
                gpx.add_EquivConstr(varlist=varlist, multlist=multlist, reloadIdx=True)
            if c['type'] == 'hold':
                vars_cfg = c.get('vars', [])
                varlist = [_build_var_from_spec(gpx, phases, v) for v in vars_cfg]
                gpx.add_HoldConstr(varlist=varlist, reloadIdx=True)
            if c['type'] == 'newvar':
                vars_cfg = c.get('vars', [])
                varlist = [_build_var_from_spec(gpx, phases, v) for v in vars_cfg]
                multlist = c.get('mults', [])
                name = c.get('name', None)
                vary = bool(c.get('vary', False))
                gpx.add_NewVarConstr(varlist=varlist, multlist=multlist, name=name, vary=vary, reloadIdx=True)
    
    # 构建一次性精修字典
    ref_dict = {"set": {}}

    # 可选：清除固定背景文件引用并删除其背景直方图
    if refine_config.get('remove_fixed_background', False):
        logger.info("检测到 remove_fixed_background=True，开始清理固定背景")
        clear_fixed_background(
            gpx,
            hist,
            logger=logger,
        )

    # 0. 设置最大精修循环次数
    if refine_config.get('refine_cycle', False):
        gpx.data['Controls']['data']['max cyc'] = refine_config['refine_cycle']
        logger.info(f"设置最大精修循环次数: {gpx.data['Controls']['data']['max cyc']}")
    
    # 1. 标度因子精修
    if refine_config.get('refine_scale', False):
        logger.info("添加标度因子到精修字典")
        if "Sample Parameters" not in ref_dict["set"]:
            ref_dict["set"]["Sample Parameters"] = []
        ref_dict["set"]["Sample Parameters"].append("Scale")
    
    # 2. 背景精修
    if refine_config.get('refine_background', False):
        logger.info("添加背景参数到精修字典")
        num_coeffs = refine_config.get('background_coeffs', 3)
        ref_dict["set"]["Background"] = {"no. coeffs": num_coeffs, "refine": True}
        logger.info(f"  背景系数数量: {num_coeffs}")

    # 3. 晶胞参数精修
    if refine_config.get('refine_cell', False):
        logger.info("添加晶胞参数到精修字典")
        # 设置收敛标准
        if use_constraints:
            gpx.data['Controls']['data']['min dM/M'] = 1e-4
        else:
            gpx.data['Controls']['data']['min dM/M'] = 1e-3
        ref_dict["set"]["Cell"] = True

    

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
    if refine_config.get('refine_atoms', False) and refine_config['refine_atoms']:
        logger.info("添加原子参数到精修字典")
        if 'all' in refine_config['refine_atoms']:
            for phase in phases:
                phase.set_refinements({
                    "Atoms": {
                        "all": refine_config['refine_atoms']['all']  # 例如 "XUF"
                    }
                })
        else:
            for phase in phases:
                phase.set_refinements({
                    "Atoms": refine_config['refine_atoms']  # 例如 {"Fe1": "XU", "O2": "F"}
                })

    # 7.1 关闭原子参数精修 (按指定标志位从当前状态中移除)
    if refine_config.get('close_atoms', False) and refine_config['close_atoms']:
        logger.info("应用 close_atoms，关闭指定原子参数精修")
        close_cfg = refine_config['close_atoms']

        # 支持 all: 对每个 phase 中所有原子移除指定标志
        if 'all' in close_cfg:
            close_all_flags = close_cfg.get('all', '')
            for phase in phases:
                atom_updates = {}
                for atom in phase.atoms():
                    current_flags = atom.refinement_flags
                    current_norm = _normalize_atom_refine_flags(current_flags)
                    new_norm = _remove_atom_refine_flags(current_flags, close_all_flags)
                    if new_norm != current_norm:
                        atom_updates[atom.label] = _to_gsas_atom_refine_flags(new_norm)
                if atom_updates:
                    phase.set_refinements({"Atoms": atom_updates})

        # 支持按原子标签关闭（可与 all 同时使用，后者先执行）
        for atom_label, flags_to_close in close_cfg.items():
            if atom_label == 'all':
                continue
            found = False
            for phase in phases:
                atom = phase.atom(atom_label)
                if atom is None:
                    continue
                found = True
                current_flags = atom.refinement_flags
                current_norm = _normalize_atom_refine_flags(current_flags)
                new_norm = _remove_atom_refine_flags(current_flags, flags_to_close)
                if new_norm != current_norm:
                    phase.set_refinements({"Atoms": {atom_label: _to_gsas_atom_refine_flags(new_norm)}})
            if not found:
                logger.warning(f"close_atoms 指定的原子不存在，已跳过: {atom_label}")

    # 8. 晶粒尺寸精修
    if refine_config.get('refine_size', False):
        logger.info("添加晶粒尺寸参数到精修字典")
        for phase in phases:
            try:
                # 设置尺寸精修
                phase.set_HAP_refinements({"Size": {"type": "isotropic", "refine": True}}, [hist.name])
            except Exception as e:
                logger.warning(f"  设置尺寸精修失败: {str(e)}")

    # 9. 微应变精修
    if refine_config.get('refine_mustrain', False):
        logger.info("添加微应变参数到精修字典")
        for phase in phases:
            try:
                # 设置微应变精修
                phase.set_HAP_refinements({"Mustrain": {"type": "isotropic", "refine": True}}, [hist.name])
            except Exception as e:
                logger.warning(f"  设置微应变精修失败: {str(e)}")

    # 10. 择优取向精修（Preferred Orientation, March-Dollase/SH）
    if refine_config.get('refine_preferred_orientation', False):
        logger.info("添加择优取向参数到精修字典")
        for phase_idx, phase in enumerate(phases):
            try:
                phase.set_HAP_refinements({"Pref.Ori.": True}, [hist.name])
                po_data = phase.data.get('Histograms', {}).get(hist.name, {}).get('Pref.Ori.', None)
                if isinstance(po_data, list) and len(po_data) >= 4:
                    logger.info(f"  相{phase_idx} 择优取向精修已启用: model={po_data[0]}, value={po_data[1]}, axis={po_data[3]}")
            except Exception as e:
                logger.warning(f"  设置择优取向精修失败: {str(e)}")

    # 11. 相分数精修
    if refine_config.get('refine_phase_fraction', False):
        logger.info("添加相分数参数到精修字典")
        for phase in phases:
            try:
                phase.set_HAP_refinements({"Scale": True}, [hist.name])
            except Exception as e:
                logger.warning(f"  设置相分数精修失败: {str(e)}")

    # 检查是否有需要精修的参数
    if not any(refine_config.values()) and not refine_config.get('initial_cell') and not refine_config.get('initial_mustrain') and not refine_config.get('initial_preferred_orientation') and not refine_config.get('preferred_orientation_axis'):
        logger.warning("没有启用任何精修参数，跳过精修")
        save_dir = TMP_DIR / f"{user_id}"/f"{session_id}"/f"{mission_name}"
        save_dir.mkdir(parents=True, exist_ok=True)
        gpx.save(str(save_dir / f'{task_id}.gpx')) #备份这次的结果
        return hist.get_wR(), "没有执行任何精修"

    # 显示最终的ref_dict
    print(f"精修字典: {ref_dict}")
    # 在实际执行精修前，打印/记录所有精修参数及其是否启用，便于对比
    try:
        print("即将执行的精修参数和启用状态:")
        # 列出 refine_config 中的键和值
        for key in sorted(refine_config.keys()):
            val = refine_config.get(key)
            print(f"  {key}: {val}")

        # 也打印即将传递给 GSAS-II 的精修字典，便于对比
        print("最终将用于 GSAS-II 的精修字典:")
        print(str(ref_dict))
    except Exception:
        # 不应该阻塞精修流程
        logger.exception("在打印精修参数时发生错误，但将继续执行精修")

    # 执行一次性精修
    try:
        logger.info("开始执行一次性精修...")
        
        # 截获精修过程的输出
        refinement_output = io.StringIO()
        original_stdout = sys.stdout
        original_stderr = sys.stderr
        
        try:
            sys.stdout = refinement_output
            sys.stderr = refinement_output
            gpx.do_refinements([ref_dict])
        finally:
            sys.stdout = original_stdout
            sys.stderr = original_stderr
        
        # 获取捕获的输出
        captured_output = refinement_output.getvalue()
        if auto_open_info and auto_open_info.get('auto_opened_atoms'):
            captured_output = (
                "\nConstraint auto-open info:\n"
                + json.dumps(auto_open_info, ensure_ascii=False)
                + "\n"
                + (captured_output or '')
            )
        if captured_output:
            logger.info(f"精修过程输出:\n{captured_output}\n输出结束")
        
        final_rwp = hist.get_wR()
        if final_rwp is not None:
            logger.info(f"精修完成，最终Rwp: {final_rwp:.2f}%")
    except Exception as e:
        logger.error(f"精修执行失败: {str(e)}")
        raise

    logger.info("一次性精修流程结束")
    return hist.get_wR(), captured_output

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


def export_reflection_table(gpx, user_id, session_id, mission_name, task_id):
    """导出反射表（hkl 等）到 CSV，返回文件路径与行数。"""
    save_dir = TMP_DIR / f"{user_id}" / f"{session_id}" / f"{mission_name}"
    save_dir.mkdir(parents=True, exist_ok=True)
    csv_path = save_dir / f"{task_id}_reflections.csv"

    def _build_ref_columns(n_cols, is_super=False):
        cols = ['h', 'k', 'l']
        if is_super:
            cols.append('m')
        cols.extend(['multiplicity', 'd_spacing', 'position', 'sig', 'gam', 'f_obs_sq', 'f_calc_sq', 'phase_term', 'i_corr'])
        remain = n_cols - len(cols)
        if remain == 3:
            cols.extend(['prfo', 'abs', 'ext'])
        elif remain == 5:
            cols.extend(['alp', 'bet', 'prfo', 'abs', 'ext'])
        elif remain == 6:
            cols.extend(['alp', 'bet', 'wave', 'prfo', 'abs', 'ext'])
        else:
            cols.extend([f'extra_{i+1}' for i in range(max(0, remain))])
        return cols

    rows = []
    all_ref_cols = set()
    for hist in gpx.histograms():
        hname = getattr(hist, 'name', str(hist))
        hnode = gpx.data.get(hname, {})
        refl_lists = hnode.get('Reflection Lists', {})
        if not isinstance(refl_lists, dict) or not refl_lists:
            continue

        for phase_name, phase_refl in refl_lists.items():
            if not isinstance(phase_refl, dict):
                continue
            ref_arr = phase_refl.get('RefList', None)
            if ref_arr is None or len(ref_arr) == 0:
                continue

            is_super = bool(phase_refl.get('Super', False))
            ref_cols = _build_ref_columns(int(ref_arr.shape[1]), is_super=is_super)
            all_ref_cols.update(ref_cols)
            for row in ref_arr:
                rec = {
                    'histogram': hname,
                    'phase': str(phase_name),
                    'is_super': is_super,
                }
                for idx, col in enumerate(ref_cols):
                    val = row[idx]
                    rec[col] = val.item() if hasattr(val, 'item') else val
                rows.append(rec)

    if not rows:
        return None, 0

    preferred_order = [
        'histogram', 'phase', 'is_super',
        'h', 'k', 'l', 'm', 'multiplicity', 'd_spacing', 'position',
        'sig', 'gam', 'f_obs_sq', 'f_calc_sq', 'phase_term', 'i_corr',
        'alp', 'bet', 'wave', 'prfo', 'abs', 'ext',
    ]
    extra_cols = sorted([c for c in all_ref_cols if c not in preferred_order])
    fieldnames = [c for c in preferred_order if c in {'histogram', 'phase', 'is_super'} or c in all_ref_cols] + extra_cols

    with open(csv_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    return str(csv_path), len(rows)

def ensure_fixed_background(gpx, hist):
    """如果 histogram 的 Background 包含 FixedPoints，则用线性插值写回 data[1][4]，
    并克隆为一个新的 PWDR histogram，设置为 background PWDR。
    此操作直接修改 gpx.data 并保存由调用方控制何时写入磁盘。
    """
    try:
        import numpy as _np
        import numpy.ma as _ma
    except Exception:
        return

    try:
        histname = hist.name if hasattr(hist, 'name') else str(hist)
        bg = gpx.data[histname]['Background']
        fixed = bg[1].get('FixedPoints')
        if not fixed:
            return

        # 插值 FixedPoints 到原始 x 网格
        X = _np.array([pt[0] for pt in fixed])
        Y = _np.array([pt[1] for pt in fixed])
        order = _np.argsort(X)
        X = X[order]; Y = Y[order]
        xgrid = _np.array(gpx.data[histname]['data'][1][0])
        full_bkg = _np.interp(xgrid, X, Y, left=Y[0], right=Y[-1])

        # 写回背景通道（使用MaskedArray以保留mask）
        try:
            mask = _ma.getmaskarray(gpx.data[histname]['data'][1][1]) if hasattr(gpx.data[histname]['data'][1][1], 'mask') else None
            gpx.data[histname]['data'][1][4] = _ma.MaskedArray(full_bkg, mask=mask)
        except Exception:
            try:
                gpx.data[histname]['data'][1][4] = full_bkg.tolist()
            except Exception:
                print('无法将 full_bkg 写回 data channel 4')

        # 克隆新直方图并设置 background PWDR
        try:
            base = histname[5:] if histname.startswith('PWDR ') else histname
            newbase = f"Autobkg for {base}"
            # GSASIIscriptable.clone_powder_histogram 假设 yobs 是 MaskedArray 并访问 .mask
            yobs = gpx.data[histname]['data'][1][1]
            if not hasattr(yobs, 'mask'):
                gpx.data[histname]['data'][1][1] = _ma.MaskedArray(yobs, mask=_ma.getmaskarray(yobs))
            newhist = gpx.clone_powder_histogram(histname, newbase, full_bkg)
            clone_name = newhist.name if hasattr(newhist, 'name') else str(newhist)
            if not clone_name.startswith('PWDR '):
                clone_name = 'PWDR ' + clone_name
            bg[1]['background PWDR'] = [clone_name, 1.0, False]
            # 关闭 computed 标志并清零系数（如有）
            try:
                bg[0][1] = False
                ncoeff = int(bg[0][2]) if len(bg[0]) > 2 else 0
                if ncoeff > 0:
                    while len(bg[0]) < 3 + ncoeff:
                        bg[0].append(0.0)
                    bg[0][3:3 + ncoeff] = [0.0] * ncoeff
            except Exception:
                pass
        except Exception as e:
            print(f'克隆背景直方图失败: {e}')
    except Exception as e:
        print(f'ensure_fixed_background 出错: {e}')
    return


def _remove_histogram_from_project(gpx, hist_name):
    """尽力从项目中移除一个 histogram（仅用于清理自动生成的背景 histogram）。"""
    removed = False
    if not hist_name:
        return removed

    try:
        if hist_name in gpx.data:
            del gpx.data[hist_name]
            removed = True
    except Exception:
        pass

    try:
        gpx.names = [node for node in gpx.names if not (node and node[0] == hist_name)]
    except Exception:
        pass

    try:
        phases_dict = gpx.data.get('Phases', {})
        if isinstance(phases_dict, dict):
            for _, phase_data in phases_dict.items():
                if not isinstance(phase_data, dict):
                    continue
                hdict = phase_data.get('Histograms')
                if isinstance(hdict, dict) and hist_name in hdict:
                    del hdict[hist_name]
    except Exception:
        pass

    try:
        seq_data = gpx.data.get('Controls', {}).get('data', {}).get('Seq Data')
        if isinstance(seq_data, list):
            gpx.data['Controls']['data']['Seq Data'] = [h for h in seq_data if h != hist_name]
    except Exception:
        pass

    return removed


def clear_fixed_background(gpx, hist, logger=None):
    """清除当前 histogram 的固定背景文件绑定，并删除对应背景 histogram。"""
    info = {
        'cleared_reference': False,
        'removed_fixed_points': False,
        'removed_auto_params': False,
        'removed_background_histogram': False,
        'background_histogram_name': '',
    }

    try:
        histname = hist.name if hasattr(hist, 'name') else str(hist)
        hdata = gpx.data.get(histname, {})
        bg = hdata.get('Background')
        if not (isinstance(bg, list) and len(bg) >= 2 and isinstance(bg[1], dict)):
            return info

        bkg_dict = bg[1]
        back_ref = bkg_dict.get('background PWDR', ['', 1.0, False])
        back_name = ''
        if isinstance(back_ref, (list, tuple)) and back_ref:
            back_name = str(back_ref[0]).strip()
        elif isinstance(back_ref, str):
            back_name = back_ref.strip()
        info['background_histogram_name'] = back_name

        bkg_dict['background PWDR'] = ['', 1.0, False]
        info['cleared_reference'] = True

        if bkg_dict.pop('FixedPoints', None) is not None:
            info['removed_fixed_points'] = True
        if bkg_dict.pop('autoPrms', None) is not None:
            info['removed_auto_params'] = True

        if back_name:
            candidate_names = [back_name]
            if back_name.startswith('PWDR '):
                candidate_names.append(back_name[5:])
            else:
                candidate_names.append(f'PWDR {back_name}')

            removed_any = False
            for candidate in candidate_names:
                if candidate == histname:
                    continue
                if candidate in gpx.data:
                    removed_any = _remove_histogram_from_project(gpx, candidate) or removed_any
            info['removed_background_histogram'] = removed_any

        if logger:
            logger.info(f"固定背景清理结果: {info}")
        else:
            print(f"固定背景清理结果: {info}")
    except Exception as e:
        if logger:
            logger.warning(f"清理固定背景失败: {e}")
        else:
            print(f"清理固定背景失败: {e}")

    return info


def _resolve_phase_obj(phases, phase_ref):
    """Resolve phase object by index/name/id/ranId. Defaults to first phase."""
    if not phases:
        raise ValueError("GPX 项目中没有可用的相")

    if phase_ref is None:
        return phases[0], 0

    if isinstance(phase_ref, int):
        if phase_ref < 0 or phase_ref >= len(phases):
            raise ValueError(f"phase 索引越界: {phase_ref}")
        return phases[phase_ref], phase_ref

    # numeric string -> index
    if isinstance(phase_ref, str):
        s = phase_ref.strip()
        if s.isdigit():
            idx = int(s)
            if idx < 0 or idx >= len(phases):
                raise ValueError(f"phase 索引越界: {phase_ref}")
            return phases[idx], idx

        # name / id / ranId matching
        for i, p in enumerate(phases):
            if getattr(p, 'name', None) == s:
                return p, i
            if str(getattr(p, 'id', '')) == s:
                return p, i
            if str(getattr(p, 'ranId', '')) == s:
                return p, i

    raise ValueError(f"无法定位 phase: {phase_ref}")


def _resolve_hist_obj(hists, hist_ref):
    """Resolve histogram object by index/name/id/ranId. Defaults to first histogram."""
    if not hists:
        raise ValueError("GPX 项目中没有可用的 histogram")

    if hist_ref is None:
        return hists[0], 0

    if isinstance(hist_ref, int):
        if hist_ref < 0 or hist_ref >= len(hists):
            raise ValueError(f"histogram 索引越界: {hist_ref}")
        return hists[hist_ref], hist_ref

    if isinstance(hist_ref, str):
        s = hist_ref.strip()
        if s.isdigit():
            idx = int(s)
            if idx < 0 or idx >= len(hists):
                raise ValueError(f"histogram 索引越界: {hist_ref}")
            return hists[idx], idx

        for i, h in enumerate(hists):
            if getattr(h, 'name', None) == s:
                return h, i
            if str(getattr(h, 'id', '')) == s:
                return h, i
            if str(getattr(h, 'ranId', '')) == s:
                return h, i

    raise ValueError(f"无法定位 histogram: {hist_ref}")


def _extract_atom_xyz(atom_cfg):
    if all(k in atom_cfg for k in ('x', 'y', 'z')):
        return atom_cfg['x'], atom_cfg['y'], atom_cfg['z']
    raise ValueError("每个新增原子必须提供 x/y/z")


def _unique_atom_label(phase_obj, requested_label):
    base = str(requested_label)
    if not phase_obj.atom(base):
        return base
    n = 1
    while True:
        candidate = f"{base}_{n}"
        if not phase_obj.atom(candidate):
            return candidate
        n += 1

def _map_param_to_gsas_var(param_name):
    p = str(param_name).strip().lower()
    mapping = {
        'occupancy': 'Afrac',
        'occ': 'Afrac',
        'fraction': 'Afrac',
        'uiso': 'AUiso',
        'u_iso': 'AUiso',
        'u': 'AUiso',
        # 坐标约束通常作用在 dA* 变量
        'x': 'dAx',
        'y': 'dAy',
        'z': 'dAz',
    }
    if p in mapping:
        return mapping[p]
    # 允许直接传 GSAS 变量名
    return str(param_name)


def _make_var_obj_safe(gpx, phase_obj, varname, atom_label=None):
    """稳健创建变量对象，优先用刷新索引后的标签解析，失败时做一次兜底重试。"""
    try:
        return gpx.make_var_obj(
            phase=phase_obj,
            varname=varname,
            atomId=atom_label,
            reloadIdx=True,
        )
    except KeyError as e:
        # 新增原子后偶发 ranId 映射未命中：先显式重建索引，再按标签重试一次。
        if atom_label is None:
            raise
        try:
            gpx.index_ids()
        except Exception:
            pass
        atom_obj = phase_obj.atom(atom_label)
        if atom_obj is None:
            raise ValueError(f"未找到原子标签: {atom_label}") from e
        return gpx.make_var_obj(
            phase=phase_obj,
            varname=varname,
            atomId=atom_label,
            reloadIdx=True,
        )


def _build_var_from_spec(gpx, phases, var_spec):
    """Build a GSAS variable reference accepted by add_*Constr APIs.

    var_spec 仅支持:
    {"phase": "Phase_1", "atom": "O1", "parameter": "occupancy"}
    """
    if not isinstance(var_spec, dict):
        raise ValueError(f"变量定义必须是字典，收到: {type(var_spec)}")

    phase_ref = var_spec.get('phase', var_spec.get('phase_name', var_spec.get('phase_index', None)))
    atom = var_spec.get('atom', var_spec.get('atom_label', None))
    param = var_spec.get('parameter', var_spec.get('param', None))
    if atom is None or param is None:
        raise ValueError("变量字典必须提供 var，或提供 phase + atom + parameter")

    phase_obj, _ = _resolve_phase_obj(phases, phase_ref)
    gsas_var = _map_param_to_gsas_var(param)
    return _make_var_obj_safe(gpx, phase_obj, gsas_var, atom)

def initialize_project(neutron_data, neutron_inst, cif_files, task_id, user_id=None, session_id=None, mission_name=None):
    '''
    Args:
        neutron_data: GSAS的gsa文件绝对路径 (必需)
        neutron_inst: GSAS的instprm文件绝对路径 (必需)
        cif_files: 结构文件列表 (必需)
        user_id: 用户ID，字符串，标识当前用户身份
        session_id: 线程ID，字符串，标识当前对话session
        mission_name: 大任务名称。同一个精修任务下会涉及多次该工具调用，需保持相同 mission_name；
                  工具会使用它和 user_id 共同定位该大任务的文件目录。
        task_id: mission 内的子任务ID，必须指定。继续轮询已有未完成任务时，必须传入之前的 task_id，并保持 mission_name 一致。

    Returns:
        dict: 包含初始化结果的字典，包含以下键:
            "status":字符串，当前精修任务状态，通常为 "completed"；在超时场景下可能为 "running"，
            "task_id":字符串，当前子任务ID，供后续轮询使用，
            'gpx_path': 初始化后拿到的gpx项目文件路径,
            'plot_path': 生成的fit plot图表路径。蓝色线是实验谱线，红色线是计算谱线，绿色线是背景，黑色线是拟合差值谱线，左右两侧竖虚线是Limits范围，图下方的竖线簇是各相计算的每个衍射峰的位置。
            'refinement_log': 精修过程中的日志信息,
            'main_lst_content': 精修生成的lst文件内容文本,
            'lst_path': 精修生成的lst文件路径,
            'reflection_table_path': 导出的反射表CSV文件路径,
            'execution_time_sec': initialize_project 总执行时长（秒）,
            "error":字符串，错误信息（仅当status为'failed'时存在）
    '''

    start_time = time.perf_counter()

    def _finalize_result(payload: Dict) -> Dict:
        payload['execution_time_sec'] = round(time.perf_counter() - start_time, 3)
        return payload

    result = {'status': 'failure', 'task_id': task_id}
    """初始化GSAS-II项目并添加中子数据"""

    print("步骤 1/8: 创建新的 GSAS-II 项目")
    save_dir = TMP_DIR / f"{user_id}"/f"{session_id}"/f"{mission_name}"
    save_dir.mkdir(parents=True, exist_ok=True)
    gpx = G2sc.G2Project(newgpx=save_dir / f'{task_id}.gpx')
    print("✓ 项目创建成功")

    # 添加中子数据
    if not (neutron_data and neutron_inst):
        result['error'] = "必须提供中子数据和仪器参数文件"
        return _finalize_result(result)

    print("步骤 2/8: 清理数据文件中的NaN值")
    # 对 GSAS 原生格式文件（.gsa/.gda/.raw）保持原样，避免丢失 BANK/头信息。
    # 对纯文本三列数据（x y e）执行 NaN 过滤后再导入。
    try:
        if isinstance(neutron_data, (str, Path)):
            neutron_data = str(neutron_data)
            ext = Path(neutron_data).suffix.lower()

            if ext in {'.gsa', '.gda', '.raw'}:
                print(f"  检测到GSAS原生格式({ext})，跳过重写，仅做文件可读性检查")
                if not Path(neutron_data).exists():
                    result['error'] = f"数据文件不存在: {neutron_data}"
                    return _finalize_result(result)
                if Path(neutron_data).stat().st_size <= 0:
                    result['error'] = f"数据文件为空: {neutron_data}"
                    return _finalize_result(result)
            else:
            # 读取原始文件
                print(f"  读取原始数据文件: {neutron_data}")
                with open(neutron_data, 'r') as f:
                    lines = f.readlines()

                print(f"  文件总行数: {len(lines)}")

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
                    raise ValueError("输入文件中未找到任何有效三列数据行")

                print(f"  从原始文件中提取了 {len(valid_data_lines)} 行有效数据")

                # 创建临时文件保存有效数据。使用 .xye 扩展名以匹配文本读取器。
                
                temp_dir = TMP_DIR / f'{user_id}' / f'{session_id}' / f'{mission_name}'
                temp_dir.mkdir(parents=True, exist_ok=True)
                clean_data_file = temp_dir / 'cleaned_neutron_data.xye'

                with open(clean_data_file, 'w') as f:
                    for line in valid_data_lines:
                        f.write(line + '\n')

                neutron_data = str(clean_data_file)
                print(f"  已创建清理后的数据文件: {clean_data_file}")

    except Exception as e:
        print(f"GSA文件数据处理失败: {str(e)}")
        result['error'] = f"无法处理GSA文件中的NaN值: {str(e)}"
        return _finalize_result(result)

    print("✓ 数据清理完成")

    print("步骤 3/8: 添加中子粉末衍射数据")
    hist = gpx.add_powder_histogram(neutron_data, neutron_inst)
    print(f"  添加中子数据: {neutron_data}")
    print("✓ 数据添加成功")


    # 添加晶体结构
    print("步骤 4/8: 添加晶体结构")
    phases = []
    if cif_files:
        for i, cif_file in enumerate(cif_files, start=1):
            print(f"  添加第 {i}/{len(cif_files)} 个结构文件: {cif_file}")
            phase_name = f"Phase_{i}"
            phase = gpx.add_phase(cif_file, phasename=phase_name, histograms=[hist])
            phases.append(phase)
            print(f"  ✓ 晶体结构 {i} 添加成功 (名称: {phase_name})")

    print("✓ 所有晶体结构添加完成")

    # 设置最大精修循环次数
    print("步骤 5/8: 设置精修控制参数")
    gpx.data['Controls']['data']['max cyc'] = 3
    print("  设置最大精修循环次数: 3")
    print("✓ 控制参数设置完成")

    # 步骤7：计算自动背底 -> 设固定点 -> 用Chebyschev拟合固定点并写回背景参数/通道
    print("步骤 6/8: 自动背底计算并用固定点拟合背景参数")
    try:
        import GSASIIpwd as G2pwd
        from scipy import interpolate as si
        import numpy.ma as ma

        # 获取背景数据与XY数据
        background = hist.data['Background']
        bkgDict = background[1]
        xydata = hist.data['data'][1]
        npts = len(xydata[0])

        # 初始化autoPrms
        bkgDict['autoPrms'] = bkgDict.get('autoPrms', {})
        bkgDict['autoPrms']['opt'] = bkgDict['autoPrms'].get('opt', 0)
        logLam = bkgDict['autoPrms'].get('logLam', min(10, float(int(10 * np.log10(max(1, npts)) ** 1.5) - 9.5) / 10.))
        bkgDict['autoPrms']['logLam'] = logLam

        # 步骤1: 计算自动背底
        bkgdata = G2pwd.autoBkgCalc(bkgDict, ma.getdata(xydata[1]))

        # 步骤2: 从自动背底中取固定点（均匀采样，约100点）
        step = max(1, npts // 100)
        try:
            xs = ma.getdata(xydata[0])
            ys = ma.getdata(bkgdata)
        except Exception:
            xs = xydata[0]
            ys = bkgdata
        bkgDict['FixedPoints'] = [tuple(pt) for pt in zip(xs[::step], ys[::step])]
        bkgDict['autoPrms']['Mode'] = 'fit'

        # 步骤3: 用OnBkgFit逻辑拟合固定点 -> 得到背景参数
        if 'FixedPoints' not in bkgDict or not len(bkgDict['FixedPoints']):
            print("  没有固定点，跳过拟合")
        else:
            # 排序并扩展范围
            bkgDict['FixedPoints'] = sorted(bkgDict['FixedPoints'], key=lambda pair: pair[0])
            X = [x for x, y in bkgDict['FixedPoints']]
            Y = [y for x, y in bkgDict['FixedPoints']]

            limits = hist.data['Limits'][1]
            if X[0] > limits[0]:
                X = [limits[0]] + X
                Y = [Y[0]] + Y
            if X[-1] < limits[1]:
                X += [limits[1]]
                Y += [Y[-1]]

            # 内插到数据网格
            xBeg = np.searchsorted(xydata[0], limits[0])
            xFin = np.searchsorted(xydata[0], limits[1])
            xdata = xydata[0][xBeg:xFin]
            ydata = si.interp1d(X, Y, bounds_error=False, fill_value=(Y[0], Y[-1]))(ma.getdata(xdata))

            # 权重和零向量
            W = [1] * len(xdata)
            Z = [0] * len(xdata)

            # 获取仪器参数
            inst = hist.data['Instrument Parameters'][0]
            inst2 = hist.data['Instrument Parameters'][1]

            # 清除仪器参数的精修标志（避免在拟合背景时改变仪器）
            def ClearInstRefineFlags(inst_dict):
                for parm in inst_dict:
                    if parm in ['U','V','W','X','Y','Z','SH/L','I(L2)/I(L1)',
                               'alpha','A','B','C','beta-0','beta-1','beta-q',
                               'sig-0','sig-1','sig-2','sig-q']:
                        if isinstance(inst_dict[parm], (list, tuple)) and len(inst_dict[parm]) > 2:
                            inst_dict[parm][2] = False

            ClearInstRefineFlags(inst)

            # 获取背景参数描述与可变列表
            bakType, bakDict, bakVary = G2pwd.SetBackgroundParms(background)

            # 检查参数点是否足够
            if len(bakVary) * 1.5 > len(X):
                print(f"  警告：要精修{len(bakVary)}个背景参数，但只有{len(X)}个点")

            # 调用 DoPeakFit 执行拟合
            controls = gpx.data['Controls']['data']
            try:
                G2pwd.DoPeakFit('LSQ', [], background, limits, inst, inst2,
                               np.array((xdata, ydata, W, Z, Z, Z)), None,
                               prevVaryList=bakVary, controls=controls)
            except Exception as e:
                print(f"  DoPeakFit 拟合失败: {e}")

            # 拟合后重新计算并写回背景到数据通道
            bakType, bakDict, bakVary = G2pwd.SetBackgroundParms(background)
            # 收集仪器参数值
            insDict = {k: inst[k][1] for k in inst}
            parmDict = {}
            parmDict.update(bakDict)
            parmDict.update(insDict)
            dataType = inst['Type'][0]
            # 计算整个数据范围的背景值并直接写回项目数据结构，确保持久化
            try:
                full_bkg = G2pwd.getBackground('', parmDict, bakType, dataType, gpx.data[hist.name]['data'][1][0], None)[0]
            except Exception:
                full_bkg = bkgdata

            try:
                # 直接写入到项目数据结构（避免局部副本问题）
                mask = ma.getmaskarray(gpx.data[hist.name]['data'][1][1]) if hasattr(gpx.data[hist.name]['data'][1][1], 'mask') else None
                gpx.data[hist.name]['data'][1][4] = ma.MaskedArray(full_bkg, mask=mask)
            except Exception:
                try:
                    gpx.data[hist.name]['data'][1][4] = full_bkg
                except Exception:
                    print('  无法写回背景到数据通道')

            # 按照 UI 的 addAutoBack 行为：克隆一个新的直方图作为背景文件并设置为 background PWDR
            try:
                base = hist.name[5:] if hasattr(hist, 'name') and hist.name.startswith('PWDR ') else (hist.name if hasattr(hist, 'name') else str(hist))
                newbase = f"Autobkg for {base}"
                # GSASIIscriptable.clone_powder_histogram 假设 yobs 是 MaskedArray 并访问 .mask
                yobs = gpx.data[hist.name]['data'][1][1]
                if not hasattr(yobs, 'mask'):
                    gpx.data[hist.name]['data'][1][1] = ma.MaskedArray(yobs, mask=ma.getmaskarray(yobs))
                newhist = gpx.clone_powder_histogram(hist.name if hasattr(hist, 'name') else hist, newbase, full_bkg)

                # clone_powder_histogram 返回的是 histogram 对象，获取其名称
                clone_name = newhist.name if hasattr(newhist, 'name') else str(newhist)

                # 将新直方图设为当前 histogram 的 background PWDR（写入完整名称）
                bg = gpx.data[hist.name]['Background']
                # clone hist 名通常以 'PWDR ' 前缀存在，确保写入该格式
                if not clone_name.startswith('PWDR '):
                    clone_name = 'PWDR ' + clone_name
                bg[1]['background PWDR'] = [clone_name, 1.0, False]

                # 关闭 computed 标志并清零系数
                try:
                    bg[0][1] = False
                    ncoeff = int(bg[0][2]) if len(bg[0]) > 2 else 0
                    if ncoeff > 0:
                        while len(bg[0]) < 3 + ncoeff:
                            bg[0].append(0.0)
                        bg[0][3:3 + ncoeff] = [0.0] * ncoeff
                except Exception:
                    pass

            except Exception as e:
                import traceback
                print(traceback.format_exc())
                print(f"  无法创建背景直方图并设置 background PWDR: {e}")

        # 保存项目并执行一次空精修以提交更改
        try:
            gpx.save()
        except Exception:
            pass

        print("  ✓ 背景拟合并写回完成")
    except Exception as e:
        import traceback
        print(traceback.format_exc())
        print(f"背景拟合流程遇到问题: {e}")

    ensure_fixed_background(gpx, hist)
    
    # 步骤7：执行一次空的精修以计算统计量并持久化
    print("步骤 7/8: 执行一次scale精修以提交背景更改")
    try:
        # 截获精修过程的输出
        refinement_output = io.StringIO()
        original_stdout = sys.stdout
        original_stderr = sys.stderr
        
        try:
            sys.stdout = refinement_output
            sys.stderr = refinement_output
            gpx.do_refinements([{"set":{"Sample Parameters": ["Scale"]}}])
        finally:
            sys.stdout = original_stdout
            sys.stderr = original_stderr
        # 获取捕获的输出
        captured_output = refinement_output.getvalue()
        
        try:
            rwp_now = hist.get_wR()
            if rwp_now is not None:
                print(f"  ✓ 空精修完成，当前Rwp: {rwp_now:.2f}%")
        except Exception:
            print("  ✓ 空精修完成")
    except Exception as e:
        print(f"空精修执行失败: {str(e)}")

    print("✓ 初始化计算完成")
    print("\n" + "="*50)
    print("项目初始化全部完成!")
    print("="*50 + "\n")

    result['status'] = "success"
    result['gpx_path'] = str(save_dir / f'{task_id}.gpx')
    result['plot_path'] = generate_fit_plot(hist,phases, user_id,session_id, mission_name, task_id)
    result['refinement_log'] = captured_output
    result['lst_path'] = str(save_dir / f'{task_id}.lst')
    result['main_lst_content'] = prune_lst_keep_main(save_dir / f'{task_id}.lst') if (save_dir / f'{task_id}.lst').exists() else ''
    refl_csv, refl_rows = export_reflection_table(gpx, user_id, session_id, mission_name, task_id)
    result['reflection_table_path'] = refl_csv
    return _finalize_result(result)



def set_atoms(
    gpx_file: str,
    add_atoms_config: Union[List[Dict], Dict],
    remove_atoms_config: Union[List[Dict], Dict] = None,
    mission_name: str = None,
    user_id: str = None,
    session_id: str = None,
    task_id: Optional[str] = None,
) -> Dict:
    """
    在不修改原始 gpx 文件的前提下，创建一个新 gpx 并执行“新增原子”操作。

    Args:
        gpx_file: 原始 .gpx 文件路径（必填）
        add_atoms_config: 新增原子配置，只支持包含原子字典的列表格式，示例如下：
            [{"phase": "Phase_1", "element": "O", "label": "O_new", "x": 0.1, "y": 0.2, "z": 0.3, "occ": 1.0, "uiso": 0.01}]
        remove_atoms_config: 删除原子配置,示例如下：
            [{"phase": "Phase_1", "label": "O1"},{"phase": "Phase_2", "label": "C1"}]
        mission_name: 任务命名空间，用于轮询隔离
        user_id: 用户 ID
        session_id: 对话session ID
        task_id: 子任务 ID，由框架注入或调用方传入

    Returns:
        {
            "status": "success"|"failed",
            "new_gpx_path": "...",
            "added_count": 2,
            "added_atoms": [...]
          }
        }
    """
    results = {'status': 'failed'}
    if not gpx_file:
        results['error'] = "gpx_file 不能为空"
        return results
    if not task_id:
        results['error'] = "task_id 不能为空，且将用于新 gpx 文件命名"
        return results

    src = Path(gpx_file)
    if not src.exists():
        results['error'] = f"gpx 文件不存在: {gpx_file}"
        return results
    if src.suffix.lower() != '.gpx':
        results['error'] = f"输入文件必须是 .gpx: {gpx_file}"
        return results

    # 生成输出路径并复制，确保原文件不被修改
    # 新文件名严格使用传入 task_id
    dst = src.with_name(f"{task_id}.gpx")
    shutil.copy2(src, dst)

    # 在副本上执行新增原子
    gpx = G2sc.G2Project(gpxfile=str(dst))
    phases = gpx.phases()
    if not phases:
        results['error'] = "GPX 中未找到任何 phase"
        return results
    
    if not add_atoms_config and not remove_atoms_config:
        results['error'] = "add_atoms_config 和 remove_atoms_config 不能同时为空"
        return results

    added_atoms = []
    removed_atoms = []

    # 新增原子
    if add_atoms_config:
        for i, atom_cfg in enumerate(add_atoms_config):
            if not isinstance(atom_cfg, dict):
                results['error'] = f"第 {i} 个新增原子配置不是字典"
                return results

            required = ['phase', 'element', 'label', 'x', 'y', 'z']
            miss = [k for k in required if k not in atom_cfg]
            if miss:
                results['error'] = f"第 {i} 个新增原子缺少必填字段: {miss}"
                return results

            phase_ref = atom_cfg['phase']
            phase_obj, phase_index = _resolve_phase_obj(phases, phase_ref)

            element = atom_cfg['element']

            label = atom_cfg['label']

            x, y, z = _extract_atom_xyz(atom_cfg)
            occ = atom_cfg.get('occ', atom_cfg.get('occupancy', 1.0))
            uiso = atom_cfg.get('uiso', atom_cfg.get('u_iso', 0.01))

            final_label = _unique_atom_label(phase_obj, label)
            phase_obj.add_atom(x=x, y=y, z=z, element=element, lbl=final_label, occ=occ, uiso=uiso)

            added_atoms.append({
                'phase_name': phase_obj.name,
                'phase_index': phase_index,
                'element': str(element),
                'label': final_label,
                'x': float(x),
                'y': float(y),
                'z': float(z),
                'occ': float(occ),
                'uiso': float(uiso),
            })

    # 删除原子
    if remove_atoms_config:
        for i, atom_cfg in enumerate(remove_atoms_config):
            if not isinstance(atom_cfg, dict):
                results['error'] = f"第 {i} 个删除原子配置不是字典"
                return results

            required = ['phase', 'label']
            miss = [k for k in required if k not in atom_cfg]
            if miss:
                results['error'] = f"第 {i} 个删除原子缺少必填字段: {miss}"
                return results

            phase_ref = atom_cfg['phase']
            atom_label = str(atom_cfg['label'])
            phase_obj, phase_index = _resolve_phase_obj(phases, phase_ref)

            atom = phase_obj.atom(atom_label)
            if atom is None:
                results['error'] = f"第 {i} 个删除原子失败: phase={phase_ref} 中不存在原子 {atom_label}"
                return results

            try:
                atom_record = atom.data
                atom_data_list = phase_obj.data.get('Atoms', [])
                removed = False
                # 优先按对象身份删除，避免同名导致误删
                for idx, row in enumerate(atom_data_list):
                    if row is atom_record:
                        del atom_data_list[idx]
                        removed = True
                        break
                # 兜底：按标签匹配删除
                if not removed:
                    for idx, row in enumerate(atom_data_list):
                        if isinstance(row, list) and len(row) > 1 and str(row[1]) == atom_label:
                            del atom_data_list[idx]
                            removed = True
                            break
                if not removed:
                    results['error'] = f"第 {i} 个删除原子失败: 未在 phase={phase_ref} 的 Atoms 表中定位到 {atom_label}"
                    return results
            except Exception as e:
                results['error'] = f"第 {i} 个删除原子失败: {e}"
                return results

            removed_atoms.append({
                'phase_name': phase_obj.name,
                'phase_index': phase_index,
                'label': atom_label,
            })

    gpx.save(str(dst))
    results = {
        'status': 'success',
        'new_gpx_path': str(dst),
        'added_count': len(added_atoms),
        'added_atoms': added_atoms,
        'removed_count': len(removed_atoms),
        'removed_atoms': removed_atoms,
    }
    print(f"新增/删除原子结果: {results}")
    return results

def set_constraints(
    gpx_file: str,
    constraints_config: List[Dict],
    mission_name: str = None,
    user_id: str = None,
    session_id: str = None,
    task_id: Optional[str] = None,
) -> Dict:
    """
    在不修改原始 gpx 文件的前提下，创建一个新 gpx 并执行“替换约束”操作。
    注意！该工具会先把gpx文件中现有的所有constraints全部清空，再去添加指定的约束配置。所以你想在添加新约束情况下保持之前约束的话，就要把之前的约束也包含在 constraints_config 里重新添加一遍。
    约束配置支持多种类型，包括线性方程约束、等值约束、固定约束和新变量定义约束。
    添加的约束涉及的变量会被自动放开以确保约束生效。

    Args:
        gpx_file: 原始 .gpx 文件路径（必填）
        constraints_config: 约束配置，list[dict] 格式，示例如下：
            [
                {"type": "eqn", "vars": [{"phase": "Phase_1", "atom": "Ni_Li", "parameter": "occupancy"},{"phase": "Phase_1", "atom": "Li1", "parameter": "occupancy"}],"mults": [1,1], "total": 1},
                {"type": "equiv", "vars": [{"phase": "Phase_1", "atom": "Ni_Li", "parameter": "occupancy"},{"phase": "Phase_1", "atom": "Li_Ni", "parameter": "occupancy"}],"mults": [1,1]},
                {"type": "hold", "vars": [{"phase": "Phase_1", "atom": "Mn1", "parameter": "x"}]},
                {"type": "newvar", "vars": [{"phase": "Phase_1", "atom": "Mn1", "parameter": "x"},{"phase": "Phase_1", "atom": "Mn2", "parameter": "x"}],"mults": [1,-1], "name": "Mn_x_diff", "vary": True}
            ]
            eqn: 线性方程约束: mult1*var1 + mult2*var2 + ... = total
            equiv: 等值约束: mult1*var1 = mult2*var2 = ...
            hold: 固定约束: 这个约束中的变量在精修过程中保持不变，优先于其他约束的自动调整，也优先于精修的自动调整
            newvar: 新变量约束（定义一个新变量等于现有变量的线性组合，可选是否参与精修）
        mission_name: 任务命名空间，用于轮询隔离
        user_id: 用户 ID
        session_id: 对话session ID
        task_id: 子任务 ID，由框架注入或调用方传入
    
        returns:
        {
            "status": "success"|"failed",
            "new_gpx_path": "...",
            "added_constraints": [...],
            "constraint_warnings": "...",  # ReadCheckConstraints 的警告文本（可能为空），注意，equiv约束可能会因为其他约束而降级为等价的eqn约束，这个会在警告文本中体现，不会影响约束的应用
            "constraint_auto_open_info": [...],  # 为了约束生效，自动放开的变量信息列表，格式示例: [{"phase_name": "Phase_1", "atom_label": "Mn1", "flags": "UX"}]，放开的参数会继承到gpx文件中，如果后续不主动关闭（关闭会影响相关约束的生效），会参与精修
            "error": "错误信息（仅在失败时）"
        }
    """
    results = {'status': 'failed'}
    if not gpx_file:
        results['error'] = "gpx_file 不能为空"
        return results
    if not task_id:
        results['error'] = "task_id 不能为空，且将用于新 gpx 文件命名"
        return results
    src = Path(gpx_file)
    if not src.exists():
        results['error'] = f"gpx 文件不存在: {gpx_file}"
        return results
    if src.suffix.lower() != '.gpx':
        results['error'] = f"输入文件必须是 .gpx: {gpx_file}"
        return results
    dst = src.with_name(f"{task_id}.gpx")
    shutil.copy2(src, dst)
    gpx = G2sc.G2Project(gpxfile=str(dst))
    phases = gpx.phases()
    try:
        # 先清空已有约束（覆盖式更新）
        cdata = gpx.data.get('Constraints', {}).get('data', None)
        if cdata is None:
            gpx.data.setdefault('Constraints', {}).setdefault('data', {})
            cdata = gpx.data['Constraints']['data']
        for scope in ('Hist', 'HAP', 'Phase', 'Global'):
            cdata[scope] = []

        for i, c in enumerate(constraints_config):
            if not isinstance(c, dict):
                results['error'] = f"第 {i} 个约束配置不是字典"
                return results
            if 'type' not in c:
                results['error'] = f"第 {i} 个约束配置缺少 type 字段"
                return results
            if c['type'] == 'eqn':
                vars_cfg = c.get('vars', [])
                varlist = [_build_var_from_spec(gpx, phases, v) for v in vars_cfg]
                multlist = c.get('mults', [])
                total = float(c.get('total', 0.0))
                gpx.add_EqnConstr(total=total, varlist=varlist, multlist=multlist, reloadIdx=True)
            if c['type'] == 'equiv':
                vars_cfg = c.get('vars', [])
                varlist = [_build_var_from_spec(gpx, phases, v) for v in vars_cfg]
                multlist = c.get('mults', [])
                gpx.add_EquivConstr(varlist=varlist, multlist=multlist, reloadIdx=True)
            if c['type'] == 'hold':
                vars_cfg = c.get('vars', [])
                varlist = [_build_var_from_spec(gpx, phases, v) for v in vars_cfg]
                gpx.add_HoldConstr(varlist=varlist, reloadIdx=True)
            if c['type'] == 'newvar':
                vars_cfg = c.get('vars', [])
                varlist = [_build_var_from_spec(gpx, phases, v) for v in vars_cfg]
                multlist = c.get('mults', [])
                name = c.get('name', None)
                vary = bool(c.get('vary', False))
                gpx.add_NewVarConstr(varlist=varlist, multlist=multlist, name=name, vary=vary, reloadIdx=True)

        # 在校验前，把本方法里添加约束所涉及的变量尽量放开，避免被判定为 not varied
        def _constraint_param_to_flag(param_name):
            p = str(param_name).strip().lower()
            if p in {'occupancy', 'occ', 'fraction', 'afrac'}:
                return 'F'
            if p in {'uiso', 'u_iso', 'u'}:
                return 'U'
            if p == 'x':
                return 'X'
            if p == 'y':
                return 'Y'
            if p == 'z':
                return 'Z'
            return None

        open_flags = {}
        for c in constraints_config:
            if not isinstance(c, dict):
                continue
            ctype = str(c.get('type', '')).strip().lower()
            if ctype not in {'eqn', 'equiv', 'hold', 'newvar'}:
                continue
            vars_cfg = c.get('vars', c.get('variables', []))
            if not isinstance(vars_cfg, list):
                continue
            for v in vars_cfg:
                if not isinstance(v, dict):
                    continue
                param_name = v.get('parameter', v.get('param', None))
                flag = _constraint_param_to_flag(param_name)
                if not flag:
                    continue
                phase_ref = v.get('phase', v.get('phase_name', v.get('phase_index', None)))
                atom_label = v.get('atom', v.get('atom_label', None))
                if not atom_label:
                    continue
                try:
                    _, phase_idx = _resolve_phase_obj(phases, phase_ref)
                except Exception:
                    continue
                key = (phase_idx, str(atom_label))
                open_flags.setdefault(key, set()).add(flag)

        auto_open_info = []
        for (phase_idx, atom_label), flags in sorted(open_flags.items()):
            phase = phases[phase_idx]
            flag_str = ''.join(sorted(flags))
            try:
                phase.set_refinements({"Atoms": {atom_label: flag_str}})
                auto_open_info.append({
                    'phase_index': phase_idx,
                    'phase_name': getattr(phase, 'name', f'Phase_{phase_idx + 1}'),
                    'atom': atom_label,
                    'flags': flag_str,
                })
            except Exception:
                continue

        # 校验约束
        import GSASIIstrIO as G2stIO
        gpx.save(str(dst))
        errmsg, warnmsg = G2stIO.ReadCheckConstraints(str(dst))
        if errmsg:
            results['error'] = f"约束检查失败: {errmsg}"
            return results

        results['status'] = 'success'
        results['new_gpx_path'] = str(dst)
        results['added_constraints'] = constraints_config
        results['constraint_warnings'] = warnmsg or ''
        if auto_open_info:
            results['constraint_auto_open_info'] = auto_open_info
    except Exception as e:
        results['error'] = f"替换约束失败: {str(e)}"
    print(f"约束替换结果: {results}")
    return results

def set_restraints(
    gpx_file: str,
    restraints_config: List[Dict],
    mission_name: str = None,
    user_id: str = None,
    session_id: str = None,
    task_id: Optional[str] = None,
) -> Dict:
    """
    在不修改原始 gpx 文件的前提下，创建一个新 gpx 并执行“替换 restraints”操作。
    注意此工具会先把gpx文件中现有的所有restraints全部清空，再去添加指定的约束配置。所以你想在添加新约束情况下保持之前约束的话，就要把之前的约束也包含在 restraints_config 里重新添加一遍。
    当前版本实现键长（Bond）与键角（Angle）约束。

    Args：
        gpx_file: 原始 .gpx 文件路径（必填）
        restraints_config: 约束配置，list[dict] 格式，示例如下
            [{
            "type": "bond",
            "phase": "Phase_1",
            "weight": 1000,
            "rules": [
                {"origin": {"labels": ["Si1"]}, "target": {"element": "O"}, "distance": 1.64},
                {"origin": {"labels": ["Si2"]}, "target": {"labels": ["O1", "O2"]}, "distance": 1.62, "esd": 0.02}
            ]},
            {"type": "bond", "phase": "Phase_2", "rules": {"origin": {"labels": ["C1"]}, "target": {"labels": ["O1"]}, "distance": 1.43}}
            ]
             - type: 约束类型，支持 "bond"（键长）或 "angle"（键角）
             - phase: 约束作用的 phase，可以通过名称或索引指定
             - weight: 约束权重（可选，默认为 1.0），值越大约束在精修中被违反时的惩罚越大
             - rules: 约束规则，包含一个或多个规则的列表。每条规则包含：
                - origin: 原子选择器，指定约束中原子角色的选择条件，支持以下字段（至少一个）：
                    - label: str，选择特定标签的原子
                    - labels: list[str]，选择多个标签的原子
                    - element: str，选择特定元素的原子
                    - elements: list[str]，选择多个元素的原子
                - target: 原子选择器，指定约束中原子角色的选择条件，支持同 origin
                - distance: float，约束的目标距离（bond 必填）
                - angle: float，约束的目标角度（angle 必填，单位度）
                - esd: float，约束的标准偏差（可选）；bond 默认 0.01，angle 默认 1.0
        mission_name: 任务命名空间，用于轮询隔离
        user_id: 用户 ID
        session_id: 对话session ID
        task_id: 子任务 ID，由你传入，每次任务调用都应该是独一无二的task_id，且将用于新 gpx 文件命名
    
        Returns:
        {
            "status": "success"|"failed",
            "new_gpx_path": "...", 新的 gpx 文件路径
            "total_added_bonds": int, 键长约束总数
            "total_added_angles": int, 键角约束总数
            "added_restraint_rules": list, 新增的约束规则详情列表，格式示例: [{"phase_name": "Phase_1", "origin": ["Si1"], "target": ["O"], "distance": 1.64, "esd": 0.0, "weight": 1000}, ...]
            "error": "错误信息（仅在失败时）"
        }
    """
    results = {'status': 'failed'}
    if not gpx_file:
        results['error'] = "gpx_file 不能为空"
        return results
    if not task_id:
        results['error'] = "task_id 不能为空，且将用于新 gpx 文件命名"
        return results
    src = Path(gpx_file)
    if not src.exists():
        results['error'] = f"gpx 文件不存在: {gpx_file}"
        return results
    if src.suffix.lower() != '.gpx':
        results['error'] = f"输入文件必须是 .gpx: {gpx_file}"
        return results

    dst = src.with_name(f"{task_id}.gpx")
    shutil.copy2(src, dst)
    gpx = G2sc.G2Project(gpxfile=str(dst))
    phases = gpx.phases()
    if not phases:
        results['error'] = "GPX 中未找到任何 phase"
        return results

    if not isinstance(restraints_config, list):
        results['error'] = "restraints_config 必须是 list[dict]"
        return results

    def _ensure_phase_bond_bucket(phase_name: str):
        rdata = gpx.data.setdefault('Restraints', {}).setdefault('data', {})
        pdata = rdata.setdefault(phase_name, {})
        if 'Bond' not in pdata:
            pdata['Bond'] = {'wtFactor': 1.0, 'Range': 1.1, 'Bonds': [], 'Use': True}
        return pdata['Bond']

    def _ensure_phase_angle_bucket(phase_name: str):
        rdata = gpx.data.setdefault('Restraints', {}).setdefault('data', {})
        pdata = rdata.setdefault(phase_name, {})
        if 'Angle' not in pdata:
            pdata['Angle'] = {'wtFactor': 1.0, 'Range': 0.85, 'Angles': [], 'Use': True}
        return pdata['Angle']

    def _resolve_atom_selector(phase_obj, selector, role_name='origin'):
        atoms = phase_obj.atoms()

        def _from_dict(d):
            if 'label' in d:
                return [str(d['label'])]
            if 'labels' in d:
                return [str(x) for x in d.get('labels', [])]
            if 'element' in d:
                elem = str(d.get('element')).strip().upper()
                return [a for a in atoms if str(a.element).strip().upper() == elem]
            if 'elements' in d:
                elems = {str(x).strip().upper() for x in d.get('elements', [])}
                return [a for a in atoms if str(a.element).strip().upper() in elems]
            raise ValueError(f"{role_name} 仅支持 label/labels/element/elements: {d}")

        if selector is None:
            raise ValueError(f"{role_name} 不能为空")
        if not isinstance(selector, dict):
            raise ValueError(f"{role_name} 必须是字典，且仅支持 label/labels/element/elements")
        out = _from_dict(selector)

        if not out:
            raise ValueError(f"{role_name} 未选中任何原子")
        return out

    def _selector_to_labels(phase_obj, picked_atoms, role_name='origin'):
        labels = []
        for item in picked_atoms:
            if isinstance(item, str):
                labels.append(item)
                continue
            atom_label = getattr(item, 'label', None)
            if atom_label is None:
                raise ValueError(f"{role_name} 选择结果中包含无法识别的原子类型: {type(item)}")
            labels.append(str(atom_label))

        resolved = []
        for lbl in labels:
            if phase_obj.atom(lbl) is None:
                raise ValueError(f"{role_name} 原子不存在: {lbl}")
            resolved.append(lbl)
        return resolved

    try:
        # 覆盖式更新：先清空所有已有 restraints
        gpx.data.setdefault('Restraints', {})['data'] = {}

        added_rules = []
        total_added_bonds = 0
        total_added_angles = 0

        for i, cfg in enumerate(restraints_config):
            if not isinstance(cfg, dict):
                results['error'] = f"第 {i} 个 restraints 配置不是字典"
                return results

            rtype = str(cfg.get('type', 'bond')).strip().lower()
            if rtype not in {'bond', 'distance', 'dist', 'angle'}:
                results['error'] = f"第 {i} 个 restraints 类型暂不支持: {cfg.get('type')}（当前仅支持 bond/angle）"
                return results

            phase_ref = cfg.get('phase', cfg.get('phase_name', cfg.get('phase_index', None)))
            phase_obj, phase_idx = _resolve_phase_obj(phases, phase_ref)
            bond_bucket = _ensure_phase_bond_bucket(phase_obj.name)
            angle_bucket = _ensure_phase_angle_bucket(phase_obj.name)

            if bool(cfg.get('clear_existing', False)):
                if rtype in {'bond', 'distance', 'dist'}:
                    try:
                        phase_obj.clearDistRestraint()
                    except Exception:
                        bond_bucket['Bonds'] = []
                elif rtype == 'angle':
                    angle_bucket['Angles'] = []

            if cfg.get('weight', cfg.get('wtFactor', None)) is not None:
                wt = float(cfg.get('weight', cfg.get('wtFactor')))
                if rtype in {'bond', 'distance', 'dist'}:
                    try:
                        phase_obj.setDistRestraintWeight(wt)
                    except Exception:
                        bond_bucket['wtFactor'] = wt
                elif rtype == 'angle':
                    angle_bucket['wtFactor'] = wt

            rules = cfg.get('rules', None)
            if not isinstance(rules, list) or not rules:
                results['error'] = f"第 {i} 个 restraints 的 rules 必须是非空 list"
                return results

            for j, rule in enumerate(rules):
                if not isinstance(rule, dict):
                    results['error'] = f"第 {i} 项第 {j} 条规则不是字典"
                    return results

                origin_sel = rule.get('origin', cfg.get('origin', None))
                target_sel = rule.get('target', cfg.get('target', None))
                if rtype in {'bond', 'distance', 'dist'}:
                    distance = rule.get('distance', rule.get('bond', cfg.get('distance', cfg.get('bond', None))))
                    factor = float(rule.get('factor', cfg.get('factor', 1.1)))
                    esd = float(rule.get('esd', cfg.get('esd', 0.01)))

                    if distance is None:
                        results['error'] = f"第 {i} 项第 {j} 条规则缺少 distance/bond"
                        return results

                    origin = _resolve_atom_selector(phase_obj, origin_sel, 'origin')
                    target = _resolve_atom_selector(phase_obj, target_sel, 'target')

                    nadd = phase_obj.addDistRestraint(
                        origin=origin,
                        target=target,
                        bond=float(distance),
                        factor=factor,
                        ESD=esd,
                    )
                    total_added_bonds += int(nadd)
                    added_rules.append({
                        'type': 'bond',
                        'phase_index': phase_idx,
                        'phase_name': getattr(phase_obj, 'name', f'Phase_{phase_idx+1}'),
                        'rule_index': j,
                        'distance': float(distance),
                        'factor': factor,
                        'esd': esd,
                        'added_count': int(nadd),
                    })
                else:
                    # Angle 模式：origin 作为中心原子，target 生成两侧原子对。
                    from itertools import combinations

                    angle_val = rule.get('angle', cfg.get('angle', None))
                    esd = float(rule.get('esd', cfg.get('esd', 1.0)))
                    target2_sel = rule.get('target2', rule.get('other', cfg.get('target2', cfg.get('other', None))))

                    if angle_val is None:
                        results['error'] = f"第 {i} 项第 {j} 条 angle 规则缺少 angle"
                        return results

                    center_raw = _resolve_atom_selector(phase_obj, origin_sel, 'origin')
                    target_raw = _resolve_atom_selector(phase_obj, target_sel, 'target')
                    centers = _selector_to_labels(phase_obj, center_raw, 'origin')
                    targets_a = _selector_to_labels(phase_obj, target_raw, 'target')

                    if target2_sel is not None:
                        target2_raw = _resolve_atom_selector(phase_obj, target2_sel, 'target2')
                        targets_b = _selector_to_labels(phase_obj, target2_raw, 'target2')
                        pair_list = [(a, b) for a in targets_a for b in targets_b if a != b]
                    else:
                        pair_list = list(combinations(targets_a, 2))

                    if not pair_list:
                        results['error'] = f"第 {i} 项第 {j} 条 angle 规则未生成任何 target 原子对"
                        return results

                    nadd = 0
                    for center_label in centers:
                        center_atom = phase_obj.atom(center_label)
                        if center_atom is None:
                            continue
                        for a_label, b_label in pair_list:
                            if center_label in (a_label, b_label):
                                continue
                            a_atom = phase_obj.atom(a_label)
                            b_atom = phase_obj.atom(b_label)
                            if a_atom is None or b_atom is None:
                                continue

                            new_angle = [[a_atom.ranId, center_atom.ranId, b_atom.ranId], ['1', '1', '1'], float(angle_val), esd]
                            if new_angle not in angle_bucket['Angles']:
                                angle_bucket['Angles'].append(new_angle)
                                nadd += 1

                    total_added_angles += nadd
                    added_rules.append({
                        'type': 'angle',
                        'phase_index': phase_idx,
                        'phase_name': getattr(phase_obj, 'name', f'Phase_{phase_idx+1}'),
                        'rule_index': j,
                        'angle': float(angle_val),
                        'esd': esd,
                        'added_count': int(nadd),
                    })

        gpx.save(str(dst))

        results['status'] = 'success'
        results['new_gpx_path'] = str(dst)
        results['total_added_bonds'] = total_added_bonds
        results['total_added_angles'] = total_added_angles
        results['added_restraint_rules'] = added_rules
    except Exception as e:
        results['error'] = f"设置 restraints 失败: {str(e)}"

    print(f"Restraints 设置结果: {results}")
    return results

def export_cifs(    
    gpx_file: str,
    mission_name: str = None,
    user_id: str = None,
    session_id: str = None,
    task_id: Optional[str] = None,
) -> Dict:
    try:
        # 1. 加载项目
        results = {'status': 'failed', 'task_id': task_id}
        if not gpx_file:
            results['error'] = "gpx_file 不能为空"
            return results
        if not task_id:
            results['error'] = "task_id 不能为空，且将用于新 gpx 文件命名"
            return results
        src = Path(gpx_file)
        if not src.exists():
            results['error'] = f"gpx 文件不存在: {gpx_file}"
            return results
        if src.suffix.lower() != '.gpx':
            results['error'] = f"输入文件必须是 .gpx: {gpx_file}"
            return results
        gpx = G2sc.G2Project(gpxfile=gpx_file)
        phases = gpx.phases() 
        if not phases:
            results['error'] = "GPX项目中未找到任何晶体结构"
            return results
        
        refined_cif_files = []
        for i, phase in enumerate(phases):
            phase_name = getattr(phase, 'name', f'Phase_{i+1}')
            safe_phase_name = str(phase_name).replace(' ', '_')
            fallback_cif = src.with_name(f"{safe_phase_name}_{task_id}.cif")
            try:
                phase.export_CIF(str(fallback_cif))
                refined_cif = str(fallback_cif)
            except Exception as e:
                results['error'] = f"相 {phase_name} 的CIF导出失败: {str(e)}"
                continue
            if refined_cif:
                refined_cif_files.append(refined_cif)
        
        results['status'] = 'success'
        results['exported_cif_files'] = refined_cif_files
    except Exception as e:
        results['error'] = f"导出cif文件失败: {str(e)}"
    print(f"CIF导出结果: {results}")
    return results


def recommend_ion_positions(
    gpx_file: str = None,
    task_id: Optional[str] = None,
    user_id: str = None,
    session_id: str = None,
    mission_name: str = None,
    phase_ref: Union[str, int, None] = None,
    hist_ref: Union[str, int, None] = None,
    map_type: str = 'delt-F',
    cutoff: float = None,
    grid_step: float = None,
    top_n: int = 20,
    include_negative: bool = False,
    unique_symmetry: bool = True,
) -> Dict:
    """
    使用 GSAS-II 的差值傅里叶图（Difference Fourier Map）推荐可能的离子占位点。

    该工具会在指定的 gpx 项目中，选择目标 phase 与 histogram，计算 Fourier map，
    搜索局部峰位，并按峰强排序输出候选分数坐标。可选地包含负峰、去除对称等价峰。

    Mission / Task 语义约定（与其他工具一致）：
        - mission_name 表示一个完整的大任务（用户的总体目标）。
        - task_id 表示该 mission 下的一次具体子任务/步骤。
        - 同一个 mission 的多轮调用，mission_name 应保持一致。
        - task_id 必须唯一，并用于输出文件命名。

    Args:
        gpx_file: str
            输入的 GSAS-II 项目文件路径（.gpx）。
            必填，且必须存在。

        task_id: Optional[str]
            当前子任务 ID。
            必填，用于输出文件命名（例如 `{task_id}_ion_positions.csv`）。

        user_id: str
            用户 ID，用于输出目录分层。
            可选；未提供时输出目录使用 `anonymous`。

        session_id: str
            对话 session ID，用于输出目录分层。

        mission_name: str
            大任务名称，用于输出目录分层。
            可选；未提供时输出目录使用 `default`。

        phase_ref: Union[str, int, None]
            目标相选择器。可选，默认使用第一个 phase。
            支持以下格式：
            1) int 索引（0-based）
            2) 数字字符串索引（如 "0"）
            3) phase 名称（如 "Phase_1"）
            4) phase id / ranId 字符串

        hist_ref: Union[str, int, None]
            目标 histogram 选择器。可选，默认使用第一个 histogram。
            支持格式与 phase_ref 相同：索引、名称、id、ranId。

        map_type: str
            Fourier map 类型，默认 `delt-F`。
            支持值：`Fobs`、`Fcalc`、`delt-F`、`2*Fo-Fc`、`Patterson`。
            各类型含义：
            - `Fobs`：仅由观测结构因子 Fo 构建的电子/核密度图，反映实验数据主导的实际密度分布。
            - `Fcalc`：仅由模型计算结构因子 Fc 构建的密度图，反映当前结构模型“理论上应有”的密度分布。
            - `delt-F`：差值图（Fo-Fc）。正峰常提示模型缺失密度（可能漏原子/漏占位），负峰常提示模型过量密度（占位偏高或原子位置不合理）。
            - `2*Fo-Fc`：加权差值图（2Fo-Fc），通常用于查看“接近实验又受模型约束”的整体密度连续性，常用于人工判读结构合理性。
            - `Patterson`：Patterson 自相关图（基于强度），强调原子间矢量信息；对重原子定位和初始结构线索有帮助，但不给出直接原子相位信息。
            选型建议：
            - 做离子补位/缺位搜索：优先 `delt-F`（本工具默认）。
            - 查看整体密度连贯性：可用 `2*Fo-Fc`。
            - 做重原子矢量线索分析：可尝试 `Patterson`。

        cutoff: float
            峰搜索阈值。可选。
            单位/量纲说明：
            - `cutoff` 本身为相对阈值，单位是 `%`（相对于当前 map 的最大峰值 `rhoMax`）。
            - 实际判峰阈值计算为：`contLevel = cutoff * rhoMax / 100`。
            - 因此 `cutoff=50` 表示仅保留高于最大峰值 50% 的峰。
            - 传入时：覆盖 map 的 `cutOff`。
            - 不传时：使用 GSAS-II 默认值。

        grid_step: float
            网格步长。可选。
            单位/量纲说明：
            - 单位为 `Å`（埃）。
            - 在 GSAS-II 中其与 map 分辨率关系为：`dmin = 2 * GridStep`，因此 `GridStep` 可理解为最小 d 间距阈值的一半。
            - 数值越小，网格越细、计算更慢；数值越大，网格越粗、计算更快。
            - 传入时：覆盖 map 的 `GridStep`。
            - 不传时：使用 GSAS-II 默认值。

        top_n: int
            返回候选峰数量上限，默认 20。
            - `top_n > 0` 时截断为前 N 个。
            - 其他情况按实际峰数量返回。

        include_negative: bool
            是否同时搜索负峰，默认 False。
            仅在 map_type 为 `delt-F` 或 `2*Fo-Fc` 时生效。

        unique_symmetry: bool
            是否去除对称等价峰，默认 True。

    Returns:
        dict，统一返回结构如下：

        成功时：
        {
            "status": "success",
            "task_id": str,
            "phase_name": str,              # 实际参与计算的相名
            "histogram_name": str,          # 实际参与计算的直方图名
            "map_type": str,                # 实际 map 类型
            "grid_step": float,             # 实际使用的网格步长
            "cutoff": float,                # 实际使用的峰阈值
            "candidate_count": int,         # 最终候选数量
            "recommended_positions": [      # 候选列表（按峰强降序）
                {
                    "rank": int,            # 排名（从 1 开始）
                    "kind": str,            # "positive" 或 "negative"
                    "mag": float,           # 峰强
                    "x": float,             # 分数坐标 x
                    "y": float,             # 分数坐标 y
                    "z": float,             # 分数坐标 z
                    "dzero": float,
                    "dcent": float
                }
            ],
            "csv_path": str,                # CSV 明细文件路径
            "report_path": str              # TXT 报告文件路径
        }

        失败时：
        {
            "status": "failed",
            "task_id": str,
            "error": str                    # 错误信息
        }

    输出文件说明：
        - CSV: `{task_id}_ion_positions.csv`，列为
          `rank, kind, mag, x, y, z, dzero, dcent`
        - TXT: `{task_id}_ion_positions.txt`，包含 phase/histogram/map 参数与候选摘要。
        - 输出目录：`work_dir/{user_id}/{session_id}/{mission_name}/`
    """
    try:
        results = {'status': 'failed', 'task_id': task_id}
        if not gpx_file:
            results['error'] = 'gpx_file 不能为空'
            return results
        if not task_id:
            results['error'] = 'task_id 不能为空，且将用于输出文件命名'
            return results

        src = Path(gpx_file)
        if not src.exists():
            results['error'] = f'gpx 文件不存在: {gpx_file}'
            return results
        if src.suffix.lower() != '.gpx':
            results['error'] = f'输入文件必须是 .gpx: {gpx_file}'
            return results

        gpx = G2sc.G2Project(gpxfile=str(src))
        hists = gpx.histograms()
        phases = gpx.phases()
        if not hists:
            results['error'] = 'GPX项目中未找到任何 histogram 数据'
            return results
        if not phases:
            results['error'] = 'GPX项目中未找到任何晶体结构'
            return results

        hist, hist_idx = _resolve_hist_obj(hists, hist_ref)
        phase, phase_idx = _resolve_phase_obj(phases, phase_ref)
        phase_name = getattr(phase, 'name', None) or f'Phase_{phase_idx + 1}'
        hist_name = getattr(hist, 'name', None) or f'Histogram_{hist_idx + 1}'

        refl_lists = hist.reflections()
        refl_data = refl_lists.get(phase_name)
        if refl_data is None:
            if len(refl_lists) == 1:
                refl_data = next(iter(refl_lists.values()))
            else:
                for key, value in refl_lists.items():
                    if str(key).strip().lower() == str(phase_name).strip().lower():
                        refl_data = value
                        break
        if refl_data is None:
            results['error'] = f'未找到 phase {phase_name!r} 对应的反射列表'
            return results

        generalData = phase.data['General']
        if generalData.get('Modulated'):
            results['error'] = '当前工具仅支持普通 3D Fourier map，暂不支持调制结构的 4D map'
            return results

        mapData = generalData.setdefault('Map', copy.deepcopy(G2elem.mapDefault))
        mapData['MapType'] = map_type
        mapData['RefList'] = hist_name
        if cutoff is not None:
            mapData['cutOff'] = float(cutoff)
        else:
            mapData.setdefault('cutOff', G2elem.mapDefault['cutOff'])
        if grid_step is not None:
            mapData['GridStep'] = float(grid_step)
        else:
            mapData.setdefault('GridStep', G2elem.mapDefault['GridStep'])

        valid_map_types = {'Fobs', 'Fcalc', 'delt-F', '2*Fo-Fc', 'Patterson'}
        if map_type not in valid_map_types:
            results['error'] = f'不支持的 map_type: {map_type}，可选值为 {sorted(valid_map_types)}'
            return results

        G2mth.FourierMap(phase.data, refl_data)
        peaks, mags, dzeros, dcents = G2mth.SearchMap(generalData, {})
        if include_negative and mapData.get('MapType') in ['delt-F', '2*Fo-Fc']:
            npeaks, nmags, ndzeros, ndcents = G2mth.SearchMap(generalData, {}, Neg=True)
            peaks = np.concatenate((peaks, npeaks))
            mags = np.concatenate((mags, nmags))
            dzeros = np.concatenate((dzeros, ndzeros))
            dcents = np.concatenate((dcents, ndcents))

        if len(peaks) == 0:
            results['error'] = '未在差值傅里叶图中找到满足阈值的峰位'
            return results

        map_peaks = np.concatenate((mags, peaks, dzeros, dcents), axis=1)
        order = np.argsort(map_peaks[:, 0])[::-1]
        map_peaks = map_peaks[order]

        if unique_symmetry:
            dedup_data = {'General': generalData, 'Map Peaks': map_peaks.tolist()}
            unique_idx = G2mth.PeaksEquiv(dedup_data, list(range(len(map_peaks))))
            map_peaks = map_peaks[unique_idx]

        if top_n and top_n > 0:
            map_peaks = map_peaks[:top_n]

        save_dir = TMP_DIR / f"{user_id or 'anonymous'}" / f"{session_id or 'default'}" / f"{mission_name or 'default'}"
        save_dir.mkdir(parents=True, exist_ok=True)
        csv_path = save_dir / f"{task_id}_ion_positions.csv"
        txt_path = save_dir / f"{task_id}_ion_positions.txt"

        recommendations = []
        with open(csv_path, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(['rank', 'kind', 'mag', 'x', 'y', 'z', 'dzero', 'dcent'])
            for rank, row in enumerate(map_peaks, start=1):
                mag = float(row[0])
                xyz = [float(v) for v in row[1:4]]
                dzero = float(row[4])
                dcent = float(row[5])
                kind = 'negative' if mag < 0 else 'positive'
                writer.writerow([rank, kind, mag, *xyz, dzero, dcent])
                recommendations.append({
                    'rank': rank,
                    'kind': kind,
                    'mag': mag,
                    'x': xyz[0],
                    'y': xyz[1],
                    'z': xyz[2],
                    'dzero': dzero,
                    'dcent': dcent,
                })

        with open(txt_path, 'w', encoding='utf-8') as f:
            f.write(f'phase: {phase_name}\n')
            f.write(f'histogram: {hist_name}\n')
            f.write(f'map_type: {map_type}\n')
            f.write(f'grid_step: {mapData.get("GridStep")}\n')
            f.write(f'cutoff: {mapData.get("cutOff")}\n')
            f.write(f'unique_symmetry: {unique_symmetry}\n')
            f.write(f'include_negative: {include_negative}\n')
            f.write(f'candidate_count: {len(recommendations)}\n')
            f.write('\n')
            for item in recommendations:
                f.write(
                    f"{item['rank']:>3d}  {item['kind']:<8s}  mag={item['mag']:.4f}  "
                    f"xyz=({item['x']:.5f}, {item['y']:.5f}, {item['z']:.5f})  "
                    f"dzero={item['dzero']:.4f}  dcent={item['dcent']:.4f}\n"
                )

        results['status'] = 'success'
        results['phase_name'] = phase_name
        results['histogram_name'] = hist_name
        results['map_type'] = map_type
        results['grid_step'] = mapData.get('GridStep')
        results['cutoff'] = mapData.get('cutOff')
        results['candidate_count'] = len(recommendations)
        results['recommended_positions'] = recommendations
        results['csv_path'] = str(csv_path)
        results['report_path'] = str(txt_path)
    except Exception as e:
        results = {'status': 'failed', 'task_id': task_id, 'error': f'差值傅里叶推荐失败: {str(e)}'}

    print(f"差值傅里叶推荐结果: {results}")
    return results


def refine(
    gpx_file: str = None,                 # 可选的已有gpx项目文件路径
    refine_config: Dict = {},          # 高级配置字典,支持所有GSAS-II精修参数
    user_id: str = None,
    session_id: str = None,
    mission_name: str = None,
    task_id: Optional[str] = None,                    # 由框架自动注入用于日志记录和输出文件命名
) -> Dict:
    """
    使用GSAS-II的API接口进行中子衍射数据精修任务并返回结果信息

    Mission / Task 语义约定（给大模型）：
        - mission_name 表示一个完整的大任务（用户的总体目标）。
        - task_id 表示该 mission 下的一次具体子任务/步骤。
        - 一个 mission 通常需要多次调用工具（多个 task）才能完成。
        - 同一个 mission 的多轮调用，mission_name 应保持一致。
        - 继续轮询同一个未完成 task 时，必须同时传入相同 mission_name 与 task_id。

    Args:
        gpx_file: 可选,已有的GSAS-II项目文件(.gpx)路径,如果提供则加载现有项目继续精修
        refine_config: 配置字典,支持很多GSAS-II精修参数，详细说明见下文
        user_id: 用户ID，字符串，标识当前用户身份，用于日志记录和权限控制
        session_id: 线程ID，字符串，标识当前对话线程，用于日志记录和权限控制
        mission_name: 大任务名称。
        task_id: mission 内的子任务ID。开启新子任务时可传 None 或不传，不传会自动生成。
         
    returns:
        字典{
            "state":字符串，当前精修任务状态，通常为 "completed"；在超时场景下可能为 "running"，
            "task_id":字符串，当前子任务ID，供后续轮询使用，
            'rwp': 当前Rwp值,
            'plot_path': 生成的fit plot图路径。蓝色线是实验谱线，红色线是计算谱线，绿色线是背景，黑色线是拟合差值谱线，左右两侧竖虚线是Limits范围，图下方的竖线簇是各相计算的每个衍射峰的位置。
            'refinement_log': 精修过程中的日志信息,
            'new_gpx_path': 精修后拿到的新gpx项目文件路径,
            'main_lst_content': 精修生成的lst文件内容文本,
            'lst_path': 精修生成的lst文件路径,
            'reflection_table_path': 精修生成的反射表格文件路径（csv格式）,
             "error":字符串，错误信息（仅当status为'failed'时存在）
        }

    该工具的启用模式：
        - 传入gpx文件路径，以及需要精修的参数，工具会加载该gpx项目继续精修，并返回精修结果和生成的文件路径等信息。
        - 由于每次精修后都会产生一个新gpx文件，之前的gpx文件也会保留，这意味着你可以拒绝不好的精修效果，随时回退到之前的状态，对之前的gpx文件进行不同参数的精修尝试。
    
    
    refine_config的内容和结构：
        它是一个字典，键是你能够精修的所有参数名称，键值的格式如下
        | 参数 | 类型 | 默认值 | 说明 |
        | `refine_cycle` | int | 1 | 精修循环次数 (Refine Cycle) |
        | `refine_background` | bool | False | 是否精修背景 |
        | `background_coeffs` | int | 3 | 背景函数系数数量 |
        | `remove_fixed_background` | bool | False | 是否在精修前清除固定背景文件绑定（background PWDR），并同时删除对应背景 histogram（通常是 Autobkg） |
        | `refine_scale` | bool | False | scale |
        | `refine_cell` | bool | False | 晶胞参数 (a, b, c, α, β, γ)，启用则在精修中调整晶胞 |
        | `initial_cell` | dict/list | None | 在精修前设置晶胞初始值（见下文详细说明） |
        | `refine_absorption` | bool | False | 吸收，用于修正由吸收导致的衍射峰强度系统性偏差 |
        | `initial_absorption` | float/dict | None | 在精修前设置吸收参数初始值；支持单值、`all/default` 或按 histogram 名称设置 |
        | `refine_instrument_alpha` | bool | False | 仪器参数alpha(仪器参数中影响最大的一个) | 
        | `refine_instrument_beta` | bool | False | 仪器参数beta-0, beta-1, beta-q |
        | `refine_instrument_sig` | bool | False | 仪器参数sig-0, sig-1, sig-2, sig-q |
        | `refine_instrument_x` | bool | False | 仪器参数X |
        | `refine_instrument_y` | bool | False | 仪器参数Y |
        | `refine_instrument_z` | bool | False | 仪器参数Z |
        | `set_constraints` | list[dict] | 无 | 约束配置列表，详见下文说明 |
        | `refine_atoms` | dict | {} | 要精修的原子标签字典, 例如: {'Fe1':'XU' , 'O2': 'XUF'}或{'all': 'XU'},一定注意参数打开后不会自动关闭，关闭需要你手动传 `close_atoms` |
        | `close_atoms` | dict | {} | 要关闭精修的原子标签字典, 例如: {'Fe1':'XU' , 'O2': 'XUF'}或{'all': 'XU'}|
        注意：'X' 为位置，'U' 为等效各向同性温度因子，'F' 为占位率。例如 'XU' 表示同时精修位置和 U。
        | `refine_size` | bool | False | 晶粒尺寸,一般不修,除非你有特殊需求 |
        | `refine_mustrain` | bool | False | 微应变,一般不修,除非你有特殊需求|
        | `refine_preferred_orientation` | bool | False | 择优取向精修开关（对应 HAP 的 `Pref.Ori.`） |
        | `preferred_orientation_model` | str/dict/list | "MD" | 择优取向模型，建议 `MD`（March-Dollase）；支持按相设置 |
        | `initial_preferred_orientation` | float/dict/list | None | 择优取向初值（MD 系数），支持按相设置 |
        | `preferred_orientation_axis` | list/dict/list-of-list | None | 择优取向轴向 `[h,k,l]`，支持按相设置 |
        | `initial_mustrain` | float/list/dict | None | 在精修前设置微应变初始值，仅支持isotropic模式（见下文详细说明） |
        | `initial_atom_uiso` | dict/list/float | None | 在精修前设置原子热无序因子(Uiso)初始值，支持多种格式（见下文详细说明） |
        | `initial_atom_occupancy` | dict/list/float | None | 在精修前设置原子占有率初始值，支持多种格式（见下文详细说明） |
        | `refine_phase_fraction` | bool | False | 相分数, 仅多相精修中需要 |
        | `spacegroup` | str/dict/list | None | 在精修前切换空间群；支持单值应用到所有相，或按相名/索引分别指定 |
        | `initial_limits` | dict | None | 在精修前设置TOF范围限制的初始值，格式示例: {'histogram_name': [min_tof, max_tof]}，单位微秒 |

        参数 `initial_cell`用途与使用方法：
            - 目的：在执行任何晶胞精修之前设置相（phase）的初始晶胞数值，避免直接修改核心.
            - 单位与空间：GSAS 使用实空间晶胞参数（a, b, c 单位 Å；α, β, γ 单位为度），不是倒空间参数。
            - 支持格式：
                1. 列表（按相索引）：例如 `[{'a':5.12,'b':5.12,'c':7.23,'alpha':90,'beta':90,'gamma':120}, ...]`，第 0 项对应第 1 个 phase。
                2. 字典（按相名或索引）：例如 `{'Phase_1': {'a':...}, 0: {'a':...}}`，支持相名或数字索引（或索引的字符串形式）。
            - 行为：如果 `initial_cell` 存在，代码会在精修前把实空间参数写入 `phase.data['General']['Cell']`，采用 GSAS 内部期望的格式：
                `[refine_flag, a, b, c, alpha, beta, gamma, volume]`。
                其中 `refine_flag` 保留原先的布尔值（或设为 False），`volume` 会自动计算并写入（若能计算）。
            - 同时，如果 `refine_cell` 为 True，脚本会尝试调用 `phase.set_refinements({"Cell": True})` 来开启晶胞精修标志（若该方法可用）。

        参数 `initial_absorption`用途与使用方法：
            - 目的：在执行任何吸收相关精修前，为当前直方图写入吸收参数初始值。
            - 支持格式：
                1. 单个数值：例如 `0.12`，应用到当前直方图。
                2. 字典（全局）：例如 `{'all': 0.12}` 或 `{'default': 0.12}`。
                3. 字典（按 histogram 名）：例如 `{'PWDR xxx': 0.15}`。
                4. 带 `histograms` 子字典：例如 `{'histograms': {'PWDR xxx': 0.15}, 'all': 0.12}`。
            - 行为：如果 `initial_absorption` 存在，代码会在精修前把值写入 `hist.data['Sample Parameters']['Absorption']` 的第一个位置，并保留原有精修标志位；若不存在该键则自动创建为 `[value, False]`。
            - 与 `refine_absorption` 的关系：`initial_absorption` 仅设置初值；是否参与精修由 `refine_absorption` 决定。

        参数 `initial_mustrain`用途与使用方法：
            - 目的：在执行微应变精修之前设置相（phase）的初始微应变数值。当拟合的衍射峰明显比实验数据宽，且无论如何精修都无法解决时，可将此值设为0来消除微应变的影响。
            - 仅支持isotropic模式（单一数值），单位为 μstrain（微应变）。
            - 支持格式：
                1. 单个数值：例如 `1000.0`，适用于单相或所有相使用相同微应变值。
                2. 列表（按相索引）：例如 `[1000.0, 1200.0]`，第 0 项对应第 1 个 phase。
                3. 字典（按相名或索引）：例如 `{'Phase_1': 1000.0, 0: 1200.0}`，支持相名或数字索引。
            - 行为：如果 `initial_mustrain` 存在，代码会在精修前把微应变参数写入 `gpx.data['Histograms'][hist.name]['Mustrain']`，仅修改参数值，保留原有的精修标志。
            - 特殊用法：当设置 `initial_mustrain: 0` 时，可以消除微应变对衍射峰宽的贡献，用于诊断衍射峰过宽的原因。

        参数 `initial_atom_uiso`用途与使用方法：
            - 目的：在执行精修之前设置相（phase）的原子热无序因子（Uiso）初始值。这对以下情况特别有用：
                当absorption和scale参数高度关联（一个增加另一个必须减小）导致精修卡顿时，将Uiso设为0来隔离这个问题。
            - 单位：Ų（平方埃），典型范围 0.005-0.15。
            - 支持格式：
                1. 字典（按原子标签）：例如 `{'Si': 0.012, 'O': 0.015, 'Fe': 0.010}`，推荐用于简单情况。
                2. 单个数值：例如 `0.012`，应用于所有原子，最简单的方式。

        参数 `initial_atom_occupancy`用途与使用方法：
            - 目的：在执行精修之前设置相（phase）的原子占有率初始值，便于从指定的占位模型开始精修。
            - 典型范围：通常在 0 到 1 之间，但这里仅负责写入初值，不额外施加范围约束。
            - 支持格式：
                1. 字典（按原子标签）：例如 `{'Ni1': 0.95, 'Li1': 0.05}`。
                2. 单个数值：例如 `1.0`，应用于所有原子。

        参数 `set_constraints` 的配置格式与使用方法：
            约束配置，list[dict] 格式，示例如下：
                [
                    {"type": "eqn", "vars": [{"phase": "Phase_1", "atom": "Ni_Li", "parameter": "occupancy"},{"phase": "Phase_1", "atom": "Li1", "parameter": "occupancy"}],"mults": [1,1], "total": 1},
                    {"type": "equiv", "vars": [{"phase": "Phase_1", "atom": "Ni_Li", "parameter": "occupancy"},{"phase": "Phase_1", "atom": "Li_Ni", "parameter": "occupancy"}],"mults": [1,1]}
                ]
                eqn: 线性方程约束: mult1*var1 + mult2*var2 + ... = total
                equiv: 等值约束: mult1*var1 = mult2*var2 = ...
            一定注意，如果你传了set_constraints参数，工具会覆盖式更新（覆盖而非追加）所有约束，也就是说之前的约束会被清空，只有你在set_constraints里传的约束会被保留，所以请务必每次都把你想保留的约束完整传入。
            如果你想保持现有约束不动，直接不传set_constraints参数即可。
            此外，约束要想生效，必须同时打开相关参数的精修开关（例如占有率约束需要 refine_atoms 中对应原子参数包含 'F'），否则约束虽然被设置了但不会在精修中起作用。

        参数 `spacegroup` 的配置格式与使用方法：
            - 目的：在本次精修开始前切换相的空间群。
            - 支持格式：
                1. 空间群编号：`1-230`（推荐，最稳妥），例如 `14`。
                2. 字符串符号：例如 `'P 21/c'`，将应用到所有相。
                3. 字典：例如 `{'all': 1}` 或 `{'Phase_1': 'P 21/c', 1: 62}`。
                4. 列表：例如 `['P 21/c', 62]`，按相索引对应。
            - 行为：切换后会自动按新空间群更新每个原子的 `site sym` 与 `mult`，避免元数据不一致。


    """
    print("精修配置 (展开):\n" + pprint.pformat(refine_config, width=120))

    try:
        # 1. 加载项目
        results = {'status': 'failed', 'task_id': task_id}
        if not gpx_file:
            results['error'] = "gpx_file 不能为空"
            return results
        if not task_id:
            results['error'] = "task_id 不能为空，且将用于新 gpx 文件命名"
            return results
        src = Path(gpx_file)
        if not src.exists():
            results['error'] = f"gpx 文件不存在: {gpx_file}"
            return results
        if src.suffix.lower() != '.gpx':
            results['error'] = f"输入文件必须是 .gpx: {gpx_file}"
            return results
        dst = src.with_name(f"{task_id}.gpx")
        shutil.copy2(src, dst)
        gpx = G2sc.G2Project(gpxfile=str(dst))
        hists = gpx.histograms()
        phases = gpx.phases() 
        if not hists:
            results['error'] = "GPX项目中未找到任何histogram数据"
            return results
        if not phases:
            results['error'] = "GPX项目中未找到任何晶体结构"
            return results
        hist = hists[0]  # 使用第一个histogram

        print(f"  ✓ 成功加载项目")
        print(f"  - Histogram数量: {len(hists)}")
        print(f"  - Phase数量: {len(phases)}")
        print(f"  - 使用Histogram: {hist.name}")
      
        # 2. 执行一次性精修流程
        print("开始一次性精修流程...")

        rwp, refinement_log = flexible_neutron_refinement(
            gpx, hist, phases, task_id, user_id,session_id, mission_name,
            refine_config=refine_config
        )
        
        # 3. 导出结果
        try:
            results['status'] = 'success'
            results['rwp'] = rwp
            results['plot_path'] = generate_fit_plot(hist,phases, user_id, session_id, mission_name, task_id)
            results['refinement_log'] = refinement_log
            save_dir = TMP_DIR / f"{user_id}"/f"{session_id}"/f"{mission_name}"
            save_dir.mkdir(parents=True, exist_ok=True)
            results['new_gpx_path'] = str(dst)
            lst_file = dst.with_name(f"{task_id}.lst")
            results['lst_path'] = str(lst_file)
            results['main_lst_content'] = prune_lst_keep_main(lst_file) if lst_file.exists() else ''
            refl_csv, refl_rows = export_reflection_table(gpx, user_id, session_id, mission_name, task_id)
            results['reflection_table_path'] = refl_csv

        except Exception as e:
            print(f"导出结果时遇到问题: {str(e)}")
            # 创建基本结果，确保能显示精修参数
            results = {
                'status': 'failed',
                'error': '结果导出失败: ' + str(e) + '\n' + refinement_log
            }
        
        if results['status'] == 'success':
            print("="*50)
            print(f"精修完成! 最终Rwp: {results['rwp']:.2f}%")
            print("="*50)

    except Exception as e:
        print("!"*50)
        print(f"精修失败: {str(e)}")
        print("!"*50)
        results = {
            'status': 'failed',
            'error': str(e)
        }
    
    print("精修结果:\n" + "\n".join(f"{k}: {v}" for k, v in results.items()))
    return results

if __name__ == '__main__':
    mcp=FastMCP(
    name="RefineToolsGSASII",
    instructions=(
        "A tool for refining neutron diffraction data and generating refined structure files. "
        "Mission/task rule: mission_name identifies one high-level mission and should stay the same "
        "across multiple calls for one overall goal; task_id identifies a sub-task/step in that mission. "
        "To continue polling the same unfinished task, pass the same mission_name and task_id."
    ),
    port = 42501, # for transport = "sse" to start a server-sent events (SSE) server
    )

    initialize_runner = ProcessPollingMcpToolBase(
    timeout_sec=60.0,
    poll_interval=1.0,
    target_func=initialize_project,
    )
    
    refine_runner = ProcessPollingMcpToolBase(
    timeout_sec=60.0,
    poll_interval=1.0,
    target_func=refine,
    )

    set_atoms_runner = ProcessPollingMcpToolBase(
        timeout_sec=60.0,
        poll_interval=1,
        target_func=set_atoms,
    )

    SetConstraints_runner = ProcessPollingMcpToolBase(
        timeout_sec=60.0,
        poll_interval=1,
        target_func=set_constraints,
    )

    SetRestraints_runner = ProcessPollingMcpToolBase(
        timeout_sec=60.0,
        poll_interval=1,
        target_func=set_restraints,
    )

    export_cifs_runner = ProcessPollingMcpToolBase(
        timeout_sec=60.0,
        poll_interval=1,
        target_func=export_cifs,
    )

    recommend_ion_positions_runner = ProcessPollingMcpToolBase(
        timeout_sec=60.0,
        poll_interval=1,
        target_func=recommend_ion_positions,
    )

    initialize_runner.register_mcp_tool(mcp, name="InitializeProject")
    refine_runner.register_mcp_tool(mcp, name="Refine")
    set_atoms_runner.register_mcp_tool(mcp, name="SetAtoms")
    # SetConstraints_runner.register_mcp_tool(mcp, name="SetConstraints")
    SetRestraints_runner.register_mcp_tool(mcp, name="SetRestraints")
    export_cifs_runner.register_mcp_tool(mcp, name="ExportCIFs")
    recommend_ion_positions_runner.register_mcp_tool(mcp, name="RecommendIonPositions")

    mcp.run(transport="sse")

    # cif_files = ['/home/wangh/rongzai-agent/agentWorkSpace/wanghao96/downloads/1529155.cif']
    # instrument_file = '/home/wangh/rongzai-agent/agentWorkSpace/wanghao96/downloads/bank4.instprm'
    # gsa_file = '/home/wangh/rongzai-agent/agentWorkSpace/wanghao96/downloads/MPI_RUN0027332_BANK4.gsa'

    
    # result1 = initialize_project(
    #         neutron_data=gsa_file,
    #         neutron_inst=instrument_file,
    #         cif_files=cif_files,
    #         task_id="test_task_001",
    #         user_id="user_123",
    #         mission_name="test_mission_refinement"
    #     )
    
    # result2 = set_atoms(
    #     gpx_file=result1['gpx_path'],
    #     task_id="test_task_002",
    #     user_id="user_123",
    #     mission_name="test_mission_refinement",
    #     add_atoms_config=[
    #         {"phase": "Phase_1", "element": "Ni", "label": "Ni_Li", "x": 0.0, "y": 0.0, "z": 0.0, "occ": 0.05, "uiso": 0.01},
    #         {"phase": "Phase_1", "element": "Li", "label": "Li_Ni", "x": 0.0, "y": 0.0, "z": 0.5, "occ": 0.05, "uiso": 0.01}
    #     ],
    #     remove_atoms_config=[]
    # )

    # result_restraints = set_restraints(
    #     gpx_file=result2['new_gpx_path'],
    #     task_id="test_task_003",
    #     user_id="user_123",
    #     mission_name="test_mission_refinement",
    #     restraints_config=[
    #         {
    #             "type": "bond",
    #             "phase": "Phase_1",
    #             "weight": 1000,
    #             "rules": [
    #                 {
    #                     "origin": {"labels": ["Ni_Li"]},
    #                     "target": {"elements": ["O"]},
    #                     "distance": 2.10,
    #                 }
    #             ],
    #         }
    #     ],
    # )
    # # print("set_restraints result:\n" + pprint.pformat(result_restraints, width=120))
    
    # # # result = addOrChange_constraints(
    # # #     gpx_file=result['new_gpx_path'],
    # # #     task_id="test_task_003",
    # # #     user_id="user_123",
    # # #     mission_name="test_mission_refinement",
    # # #     constraints_config=[
    # # #         {"type": "eqn", "vars": [{"phase": "Phase_1", "atom": "Ni_Li", "parameter": "occupancy"},{"phase": "Phase_1", "atom": "Li1", "parameter": "occupancy"}],"mults": [1,1], "total": 1},
    # # #         {"type": "eqn", "vars": [{"phase": "Phase_1", "atom": "Li_Ni", "parameter": "occupancy"},{"phase": "Phase_1", "atom": "Ni1", "parameter": "occupancy"}],"mults": [1,1], "total": 0.25},
    # # #     ]
    # # # )
    
    # result3 = refine(
    #         gpx_file=result_restraints['new_gpx_path'],
    #         task_id="test_task_004",
    #         user_id="user_123",
    #         mission_name="test_mission_refinement",
    #         refine_config={
    #             "refine_atoms": {"Ni_Li": "U"},
    #         }
    #     )
    
    # # # result = refine(
    # # #         gpx_file=result['new_gpx_path'],
    # # #         task_id="test_task_005",
    # # #         user_id="user_123",
    # # #         mission_name="test_mission_refinement",
    # # #         refine_config={
    # # #             "refine_atoms": {"Ni_Li": ""},
    # # #             "initial_atom_occupancy": {"Ni_Li": 0.05, "Li_Ni": 0.05}
    # # #         }
    # # #     )
    
    # # result4 = export_cifs(
    # #     gpx_file=result3['new_gpx_path'],
    # #     task_id="test_task_006",
    # #     user_id="user_123",
    # #     mission_name="test_mission_refinement",
    # # )

    # # result5 = refine(
    # #         gpx_file=result3['new_gpx_path'],
    # #         task_id="test_task_007",
    # #         user_id="user_123",
    # #         mission_name="test_mission_refinement",
    # #         refine_config={
    # #             "close_atoms": {"Ni_Li": "U"}
    # #         }
    # #     )
    
    # # result6 = export_cifs(
    # #     gpx_file=result5['new_gpx_path'],
    # #     task_id="test_task_008",
    # #     user_id="user_123",
    # #     mission_name="test_mission_refinement"
    # # )

    # result7 = recommend_ion_positions(
    #     gpx_file=result3['new_gpx_path'],
    #     task_id="test_task_009",
    #     user_id="user_123",
    #     mission_name="test_mission_refinement",
    #     map_type="delt-F",
    #     cutoff=30.0,
    #     grid_step=0.25,
    #     unique_symmetry=True,
    #     include_negative=False,
    #     hist_ref=None,
    #     phase_ref=None,
    # )
    # print("recommend_ion_positions result:\n" + pprint.pformat(result7, width=120))

    
