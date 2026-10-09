import json
from pathlib import Path
import logging
import os
import traceback
import uuid
import numbers
from matplotlib import pyplot as plt
from mcp.server.fastmcp import FastMCP
import numpy as np

from fit_method import SasViewFit, check_para, build_default_para
from src.core.tools.mcp_task_runner import ProcessPollingMcpToolBase


from dotenv import load_dotenv
here = Path(__file__).parent.parent.parent.absolute()
print(f"Loading environment variables from: {here / '.env'}")
load_dotenv(dotenv_path=os.path.join(here, ".env"), override=True)
TMP_DIR = Path(os.getenv("WORKDIR", ""))
print(f"Using WORKER_SPACE: {TMP_DIR}")

def _as_1d(arr):
    a = np.asarray(arr)
    return a.reshape(-1)


def _build_changed_para_view(para: dict, default_para: dict) -> dict:
    """仅保留相对默认初始化参数发生变化的内容。"""
    current_para = para.get('para', {}) if isinstance(para, dict) else {}
    current_dispersity = para.get('dispersity', {}) if isinstance(para, dict) else {}

    default_para_block = default_para.get('para', {}) if isinstance(default_para, dict) else {}
    default_dispersity_block = default_para.get('dispersity', {}) if isinstance(default_para, dict) else {}

    changed_para = {
        key: value
        for key, value in current_para.items()
        if key not in default_para_block or value != default_para_block.get(key)
    }
    changed_dispersity = {
        key: value
        for key, value in current_dispersity.items()
        if key not in default_dispersity_block or value != default_dispersity_block.get(key)
    }

    return {
        'para': changed_para,
        'dispersity': changed_dispersity,
    }


def _format_para_values_for_output(para_view: dict) -> dict:
    """将输出中的 value 字段格式化为科学计数法（两位小数）。"""
    if not isinstance(para_view, dict):
        return para_view

    formatted = {'para': {}, 'dispersity': para_view.get('dispersity', {})}
    para_block = para_view.get('para', {})

    for key, cfg in para_block.items():
        if not isinstance(cfg, dict):
            formatted['para'][key] = cfg
            continue

        cfg_copy = dict(cfg)
        value = cfg_copy.get('value')
        error = cfg_copy.get('error')
        if isinstance(value, numbers.Real) and not isinstance(value, bool):
            cfg_copy['value'] = f"{float(value):.2e}"
        if isinstance(error, numbers.Real) and not isinstance(error, bool):
            cfg_copy['error'] = f"{float(error):.2e}"
        formatted['para'][key] = cfg_copy

    return formatted


def _get_default_para_if_supported(model: str) -> tuple[dict | None, str | None]:
    """校验模型是否受 sasmodels 支持，并返回默认参数或错误信息。"""
    try:
        return build_default_para(model), None
    except Exception as exc:
        return None, f'不支持的 model: {model}。请传入 SasView/sasmodels 支持的模型名称。原始错误: {exc}'

def export_fit_results(q, Ierr, I_data, I_fit_data, pars, residual, title, output_dir, fit_idx, shape, data_stem="data"):
    result_data = {}
    thread_id = uuid.uuid4()
    #########保存拟合图片############
    # 统一转为1D数组，容错处理None
    q = _as_1d(q) if q is not None else np.array([])
    Ierr = _as_1d(Ierr) if Ierr is not None else None
    I_data = _as_1d(I_data) if I_data is not None else np.array([])
    I_fit_data = _as_1d(I_fit_data) if I_fit_data is not None else np.array([])
    residual = _as_1d(residual) if residual is not None else np.array([])

    # 使用matplotlib绘制到内存PNG
    fig, axs = plt.subplots(2, 1, sharex=True, figsize=(5, 4), gridspec_kw={'height_ratios': [2, 1]})
    # 上：实验数据 + 拟合
    if Ierr is not None and Ierr.size == I_data.size:
        axs[0].errorbar(q, I_data, yerr=Ierr, fmt='o', label='Experiment', capsize=3, zorder=1)
    else:
        axs[0].plot(q, I_data, 'o', label='Experiment', zorder=1)
    axs[0].plot(q, I_fit_data, '-', label='Fit Curve', zorder=2)
    axs[0].set_xscale('log')
    axs[0].set_yscale('log')
    axs[0].set_ylabel("I")
    axs[0].legend(loc='best')
    axs[0].set_title(title)

    # 下：残差
    axs[1].plot(q, residual, 'r.', label='residual')
    axs[1].axhline(0, color='gray', lw=1)
    axs[1].set_xlabel("q")
    axs[1].set_ylabel("res.")
    axs[1].legend(loc='best')

    fig.tight_layout()
    plot_path = os.path.join(output_dir, f'{data_stem}_{shape}_fit_{fit_idx}.png')
    fig.savefig(plot_path, format='png', dpi=400, bbox_inches='tight')
    plt.close(fig)
    result_data['plot_path'] = plot_path

    # 假设上述变量已准备好（q, I_data, Ierr, I_fit_data, residual）
    # 如果 Ierr 为 None，就用全0或 NAN 填充：
    if Ierr is None:
        Ierr = np.full_like(I_data, np.nan)
    # 合并为二维数组，按列排列
    # 注意都要为 1D，且长度一致
    saved_data = np.column_stack([q, I_data, Ierr, I_fit_data, residual])
    # 设置保存路径
    dat_path = os.path.join(output_dir, f'{data_stem}_{shape}_fit_{fit_idx}.dat')
    # 设置标题字符串
    header = 'q\tI_data\tIerr\tI_fit_data\tresidual'
    # 保存为 .dat 文件
    np.savetxt(dat_path, saved_data, header=header, fmt='%.8g', delimiter='\t', comments='')
    result_data['plot_data_path'] = dat_path
    ####################################
    #########保存拟合结果############
    # 保存拟合结果到json文件
    json_path = os.path.join(output_dir, f'{data_stem}_{shape}_fit_{fit_idx}.json')
    with open(json_path, 'w') as f:
        json.dump(pars, f, indent=4)
    result_data['json_path'] = json_path
    return result_data

def refine_data(data_path: str = None,
                json_path: str = None, 
                shape: str = None,
                fit_params: dict = None,
                fit_idx: int = None,
                output_dir: str = None,
                return_full_para: bool = False,
                default_para: dict | None = None):
    '''
    
    '''    
    # 创建输出目录
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    fit_idx = fit_idx if fit_idx is not None else 0

    with open(json_path, 'r') as f:
        para = json.load(f)
    try:
        fit_params = fit_params or {}
        # 兼容两种入参：新格式 {'para': {...}, 'dispersity': {...}} 与旧格式 {param_name: {...}}
        if isinstance(fit_params.get('para'), dict) or isinstance(fit_params.get('dispersity'), dict):
            param_updates = fit_params.get('para') or {}
            dispersity_updates = fit_params.get('dispersity') or {}
        else:
            param_updates = fit_params
            dispersity_updates = {}

        if not isinstance(param_updates, dict):
            param_updates = {}
        if not isinstance(dispersity_updates, dict):
            dispersity_updates = {}

        para.setdefault('dispersity', {})

        has_param_adjustment = False
        for cfg in param_updates.values():
            if not isinstance(cfg, dict):
                continue
            if cfg.get('fit') or cfg.get('value') is not None:
                has_param_adjustment = True
                break

        # 如果本轮没有任何参数被修正，默认开放 scale（或混合模型的 A_scale）进行拟合
        if not has_param_adjustment:
            param_updates = dict(param_updates)
            # 找第一个可用的 scale 参数：优先顶层 scale，其次 A_scale 等前缀 scale
            default_scale_key = None
            for candidate in ['scale'] + sorted(k for k in para.get('para', {}) if k.endswith('_scale')):
                if candidate in para.get('para', {}):
                    default_scale_key = candidate
                    break
            if default_scale_key:
                scale_cfg = param_updates.get(default_scale_key, {})
                if not isinstance(scale_cfg, dict):
                    scale_cfg = {}
                scale_cfg['fit'] = True
                if 'value' not in scale_cfg:
                    scale_cfg['value'] = None
                param_updates[default_scale_key] = scale_cfg

        # 根据fit_params设置para中需要修改的参数
        for key in para['para']:
            if key in param_updates:
                cfg = param_updates[key]
                para['para'][key]['fit'] = cfg.get('fit', False)
                if cfg.get('value') is not None:
                    para['para'][key]['value'] = cfg['value']
                # _pd 参数支持额外的 dispersity 子项：type / n / nsigma
                if key.endswith('_pd'):
                    disp_cfg = dispersity_updates.get(key, {})
                    if not isinstance(disp_cfg, dict):
                        disp_cfg = {}
                    if 'type' in cfg:
                        para['dispersity'][f'{key}_type'] = cfg['type']
                    if 'n' in cfg:
                        para['dispersity'][f'{key}_n'] = cfg['n']
                    if 'nsigma' in cfg:
                        para['dispersity'][f'{key}_nsigma'] = cfg['nsigma']
                    if 'type' in disp_cfg:
                        para['dispersity'][f'{key}_type'] = disp_cfg['type']
                    if 'n' in disp_cfg:
                        para['dispersity'][f'{key}_n'] = disp_cfg['n']
                    if 'nsigma' in disp_cfg:
                        para['dispersity'][f'{key}_nsigma'] = disp_cfg['nsigma']
            else:
                para['para'][key]['fit'] = False

        # 允许直接通过 dispersity 扁平键更新，如 {'radius_pd_type': 'gaussian'}
        for d_key, d_val in dispersity_updates.items():
            if isinstance(d_val, dict):
                continue
            if d_key in para['dispersity']:
                para['dispersity'][d_key] = d_val
                
        # print(f"拟合参数：{para}")
        # 检查输入参数格式与形状参数配置是否匹配，现在para由json记录，不会出错，所以注释掉检测
        # config_path=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'model.json')
        # with open(config_path, 'r') as f:
        #     config = json.load(f)
        # if not check_para(shape, para, config):
        #     print("输入参数格式与形状参数配置不匹配")
        
        # 开始拟合
        print(f"开始拟合，参数配置：{para}")
        svf = SasViewFit(data_path, shape, para)
        q, Ierr, Iq_data, Iq_model, pars, fit_result = svf.fit()

        # 组织拟合结果
        for name, param in pars.items():
            if name in para["para"]:
                para["para"][name]["value"] = param.value
                para["para"][name]["error"] = None
                if para["para"][name]["fit"]:
                    for idx, value in enumerate(fit_result['x']):
                        if value == param.value:
                            para["para"][name]["error"] = fit_result['dx'][idx]
            if name in para["dispersity"]:
                para["dispersity"][name] = param.value
        print(f"拟合参数：{_format_para_values_for_output(para)}")
        # print(f"拟合结果：{_build_changed_para_view(para, default_para or {})}")
        residual = (Iq_data - Iq_model) / Ierr
        data_length = len(q)
        chisquare = sum(residual ** 2)/(data_length-3)
        print(f"拟合结果的χ²值: {chisquare}")

        #########保存拟合结果#
        data_stem = Path(data_path).stem
        result_data = export_fit_results(q, Ierr, Iq_data, Iq_model, para, residual, f"model:'{shape}'-chi2:{chisquare:.2f}-fitID:{fit_idx}", output_dir, fit_idx, shape=shape, data_stem=data_stem)

        # 输出要重新组织
        result_data['data_path'] = data_path
        result_data['shape'] = shape
        para_view = para if return_full_para else _build_changed_para_view(para, default_para or {})
        result_data['para'] = _format_para_values_for_output(para_view)
        result_data['chisquare'] = chisquare
        result_data['fit_idx'] = fit_idx
        return result_data

    except Exception as e:
        raise

def sasview_fit(
    data_path: str = None,
    json_path: str = None,
    model: str = None,
    fit_params: dict = None,
    user_id: str = None,
    mission_name: str = "",
    session_id: str = None,
    task_id: int | str = 0,
) -> dict:
    """
    使用 SasView 的fit功能，对小角散射数据进行拟合并返回结果。

    Mission / Task 概念约定（给大模型）：
        - mission_name 表示一个完整的大任务。
        - task_id 表示该 mission 中某一次具体拟合步骤/子任务。
        - 一个 mission 往往需要多次调用本工具（多个 task）才能完成。
        - 因此，同一个 mission 的多轮调用，应持续使用同一个 mission_name。
        - 在同一个 mission 内，每一轮可使用不同 task_id（例如 0,1,2...）表示阶段推进。
        - 继续轮询同一个未完成 task 时，必须同时传入相同 mission_name 和 task_id。
        - 开始新的子任务时，可在同一 mission_name 下使用新的 task_id。

    Args:输入时需要提供的参数
        data_path: 小角散射数据文件的绝对路径。
        json_path: 包含拟合参数结果的文件绝对路径。初始化时和每次model改变时应该没有拟合结果 ，在进行多轮调用时，由前一次工具输出决定。
        model: 几何模型名称（如 单一模型: guinier_porod、sphere等；复合模型: sphere+cylinder等）。
        fit_params: 决定本轮拟合的参数开放情况。通过fit参数/bool型，决定是否开放拟合，或修改value参数，变化初始值。未出现在 fit_params 中的参数将取消拟合，你认为不需要拟合的参数可以不出现在fit_params中。
        user_id: 用户 ID，用于输出目录组织。
        session_id: 会话 ID，用于输出目录组织（如果有的话）。
        mission_name: mission 名称（必填，建议全程保持不变）；用于标识同一个大任务。
        task_id: 任务轮次索引/标识。初始化和每次model改变时，建议设置为0；
                 使用相同model继续拟合时，按上次返回的 task_id+1。

    Args中 fit_params 的介绍：fit_params中的键和value，与model是对应的，同一个model，fit_params中可开放的参数是不变的，通过初始化后得到。
        格式如下：
        fit_params={"para":{key: {"fit": bool, "value": number},...}, "dispersity":{key: value,...}}，
        其中para块中的key范围由初始化参数确定，value是一个字典，可以包含fit/bool型和value/数值型两个字段；dispersity块中的key是多分散函数的控制参数（如*_pd_n, *_pd_type），value是对应的参数内容。
        *_pd_type参数含义：Distribution function type for polydispersity。内容范围：uniform、rectangular、gaussian、boltzmann、lognormal、schulz、array。
        *_pd_n参数含义：Number of points for polydispersity distribution。
        *_pd_nsigma参数含义：Number of standard deviations to include for each type of polydispersity。

    Returns:
        该函数作为 ProcessPollingMcpToolBase 的业务函数，返回：
        {
            'description': '由工具sasview_fit执行拟合后返回的结果。',
            'status': 'success' | 'failed',
            'content': {
                'data_path': 原始数据路径,
                'json_path': 参数文件路径,
                'model': 使用的模型名,
                'fit_params': 拟合后的参数,
                'chisquare': 最终拟合 χ²,
                'plot_path': 拟合图路径,
                'plot_data_path': 拟合曲线导出数据路径,
                'output_dir': 输出目录,
                'mission_name': 任务名,
                'task_id': 拟合轮次索引
            } 或字符串（失败时）
        }
    }
    """
    try:
        print(f"sasview_fit called with data_path={data_path}, json_path={json_path}, model={model}, fit_params={fit_params}, user_id={user_id}, mission_name={mission_name}, task_id={task_id}")

        # 与 GSAS 工具保持一致：按 user_id/mission_name 组织输出目录
        user_key = user_id or 'default_user'
        mission_key = mission_name or 'default_mission'
        session_key = session_id or 'default_session'
        output_dir = TMP_DIR / f"{user_key}_{session_key}_{mission_key}"  # 用下划线连接，避免路径过长或特殊字符问题
        output_dir.mkdir(parents=True, exist_ok=True)

        if not data_path or not os.path.exists(data_path):
            return {
                'description': '由工具sasview_fit执行拟合后返回的结果。',
                'status': 'failed',
                'content': f'数据文件不存在: {data_path}'
            }

        if not model:
            return {
                'description': '由工具sasview_fit执行拟合后返回的结果。',
                'status': 'failed',
                'content': '缺少必填参数 model。'
            }

        if not mission_name:
            return {
                'description': '由工具sasview_fit执行拟合后返回的结果。',
                'status': 'failed',
                'content': '缺少必填参数 mission_name。请保持 mission_name 在同一任务轮询中一致。'
            }

        # 始终构造一次默认参数，用于非初始化场景下输出差异
        default_para, model_error = _get_default_para_if_supported(model)
        if model_error:
            return {
                'description': '由工具sasview_fit执行拟合后返回的结果。',
                'status': 'failed',
                'content': model_error,
            }

        # 若未提供 json_path，则基于模型生成默认参数
        is_init_run = not json_path or not os.path.exists(json_path)
        if is_init_run:
            initial_para = default_para
            json_path = str(output_dir / f'init_{model}_params.json')
            with open(json_path, 'w') as _f:
                json.dump(initial_para, _f, indent=4)
            print(f'初始参数已生成并保存: {json_path}')

        result = refine_data(
            data_path=data_path,
            json_path=json_path,
            shape=model,
            fit_params=fit_params,
            fit_idx=task_id,
            output_dir=str(output_dir),
            return_full_para=is_init_run,
            default_para=default_para,
        )
        print(f"sasview_fit result: {result}")
        if is_init_run:
            return {
                'description': f'sasview_fit工具已成功生成初始化参数文件: {json_path}。',
                'status': 'success',
                'content': {
                    'data_path': result.get('data_path'),
                    'json_path': result.get('json_path'),
                    'model': result.get('shape'),
                    'fit_params': result.get('para'),
                    'chisquare': result.get('chisquare'),
                    'plot_path': result.get('plot_path'),
                    'plot_data_path': result.get('plot_data_path'),
                    'output_dir': str(output_dir),
                    'mission_name': mission_name,
                    'task_id': result.get('fit_idx'),
                }
            }
        else:
            return {
                'description': '由工具sasview_fit执行拟合后返回的结果。',
                'status': 'success',
                'content': {
                    'data_path': result.get('data_path'),
                    'json_path': result.get('json_path'),
                    'model': result.get('shape'),
                    'fit_params': result.get('para'),
                    'chisquare': result.get('chisquare'),
                    'plot_path': result.get('plot_path'),
                    'plot_data_path': result.get('plot_data_path'),
                    'output_dir': str(output_dir),
                    'mission_name': mission_name,
                    'task_id': result.get('fit_idx'),
                }
            }
    except Exception:
        return {
            'description': '由工具sasview_fit执行拟合后返回的结果。',
            'status': 'failed',
            'content': traceback.format_exc(),
        }

refine_tools_runner = ProcessPollingMcpToolBase(
    timeout_sec=60.0,
    poll_interval=1.0,
    target_func=sasview_fit,
    # param_mapping auto-derived from sasview_fit's signature
)

mcp=FastMCP(
    name="SasViewTools",
    instructions=(
        "Tools for analysis small-angle neutron scattering (SANS) data using SasView core function. "
        "Every tool call must include session_id, using the current Agent session_id exactly as provided "
        "in the system prompt; never omit it, use a default value, or invent a new value. "
        "Important calling rule: mission_name is a high-level mission identifier and can stay the same "
        "across multiple tool calls; task_id identifies a sub-task/step inside that mission. "
        "Use the same mission_name for the same overall user goal, and advance task_id per step."
    ),
    port=int(os.getenv("MCP_SASVIEW_REFINE_PORT", "42503")),
    )

refine_tools_runner.register_mcp_tool(mcp, name="sasview_fit")

if __name__ == '__main__':
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == 'test':
        # ----------------------------------------------------------------
        # 本地测试入口：无需启动 agent，直接调用 refine_data 验证混合模型拟合
        # 用法：python 0sasview_refine.py test
        # ----------------------------------------------------------------
        import tempfile

        _DATA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                  'IQ_39191_SubEB_PC_0.dat')
        _OUTPUT_DIR = os.path.join(tempfile.gettempdir(), 'sasview_test_output')

        # ---------- 测试1：单模型（hollow_cylinder），与现有逻辑保持一致 ----------
        print("\n========== 测试1: 单模型 'hollow_cylinder' ==========")
        res1 = sasview_fit(
            data_path='/home/dur/rongzai-agent/agentWorkSpace/zhongjiajun@ihep.ac.cn/downloads/my data 20251123.dat',
            json_path='/home/dur/rongzai-agent/tools/tmp/zhongjiajun@ihep.ac.cn/sans_fit_hollowcyl_20251123/my data 20251123_hollow_cylinder_fit_3.json',
            model='hollow_cylinder',
            fit_params={'scale': {'fit': True}, 'background': {'fit': True}, 'radius': {'fit': True, 'value': 120.0}, 'thickness': {'fit': True, 'value': 10.0}, 'length': {'fit': True, 'value': 2000.0}},
            user_id='zhongjiajun@ihep.ac.cn',
            mission_name='sans_fit_hollowcyl_20251123',
            task_id=4
            
        )
        if res1.get('status') != 'success':
            print(f"[FAIL] {res1.get('content')}")
        else:
            c1 = res1.get('content', {})
            print(f"[OK] chi²={c1.get('chisquare', 'N/A'):.4f}")
            print(f"     plot -> {c1.get('plot_path')}")
            print(f"     json -> {c1.get('json_path')}")

        # ---------- 测试2：混合模型（sphere+cylinder），不提供 json_path ----------
        print("\n========== 测试2: 混合模型 sphere+cylinder ==========")
        res2 = sasview_fit(
            data_path=_DATA_PATH,
            json_path=None,           # 无预设 json，动态构建混合模型默认参数
            model='sphere+cylinder',
            fit_params={
                'scale':       {'fit': True,  'value': 1.0},
                'background':  {'fit': True,  'value': 0.001},
                'A_scale':     {'fit': True,  'value': 0.5},
                'A_radius':    {'fit': True,  'value': 50.0},
                'A_sld':       {'fit': False, 'value': 1.0},
                'A_sld_solvent':{'fit': False,'value': 6.0},
                'B_scale':     {'fit': True,  'value': 0.5},
                'B_radius':    {'fit': False, 'value': 20.0},
                'B_length':    {'fit': True,  'value': 200.0},
                'B_sld':       {'fit': False, 'value': 4.0},
                'B_sld_solvent':{'fit': False,'value': 1.0},
            },
            user_id='test_user',
            mission_name='sasview_test',
            task_id='local_test_2',
        )
        if res2.get('status') != 'success':
            print(f"[FAIL] {res2.get('content')}")
        else:
            c2 = res2.get('content', {})
            print(f"[OK] chi²={c2.get('chisquare', 'N/A'):.4f}")
            print(f"     plot -> {c2.get('plot_path')}")
            print(f"     json -> {c2.get('json_path')}")

        # ---------- 测试3：混合模型，提供 json_path ----------
        print("\n========== 测试3: 混合模型 sphere+cylinder（从 json 加载参数） ==========")
        import tempfile as _tf
        _mixed_para = build_default_para('sphere+cylinder')
        _mixed_para['para']['A_scale']['fit'] = True
        _mixed_para['para']['A_radius']['fit'] = True
        _mixed_para['para']['B_scale']['fit'] = True
        _mixed_para['para']['B_length']['fit'] = True
        _mixed_para['para']['background']['fit'] = True
        _tmp_json = os.path.join(_OUTPUT_DIR, 'mixed_model_test.json')
        os.makedirs(_OUTPUT_DIR, exist_ok=True)
        with open(_tmp_json, 'w') as _f:
            json.dump(_mixed_para, _f, indent=4)
        print(f"  临时参数 json 已写入: {_tmp_json}")

        res3 = refine_data(
            data_path=_DATA_PATH,
            json_path=_tmp_json,
            shape='sphere+cylinder',
            fit_params={},            # 使用 json 中已设好的 fit 标志
            fit_idx=2,
            output_dir=_OUTPUT_DIR,
        )
        print(f"[OK] chi²={res3.get('chisquare', 'N/A'):.4f}")
        print(f"     plot -> {res3.get('plot_path')}")

    else:
        mcp.run(transport='sse')