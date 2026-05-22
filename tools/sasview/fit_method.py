import numpy as np
import json
from bumps.names import *
from sasmodels.data import load_data
from sasmodels.core import load_model
from sasmodels.bumps_model import Model, Experiment


# 设计利用sasView拟合实验散射谱的函数，拟合参数按需开放，可以在拟合过程中选择关闭和开启，或者修改参数数值。
# 输入参数：不同模型有的参数值不一样多，

def build_default_para(model_name: str) -> dict:
    """
    动态从 sasmodels 获取模型（含混合模型）的默认参数结构。
    返回格式与 model.json 中每个条目一致：{"para": {...}, "dispersity": {...}}
    """
    kernel = load_model(model_name)
    model = Model(kernel)
    state = model.state()

    para_dict = {}
    dispersity_dict = {}

    for name, value in state.items():
        if name.endswith('_pd_type'):
            dispersity_dict[name] = value
        elif name.endswith('_pd_n') or name.endswith('_pd_nsigma'):
            dispersity_dict[name] = int(value) if isinstance(value, float) and value == int(value) else value
        else:
            try:
                float_val = float(value)
            except (TypeError, ValueError):
                float_val = value
            para_dict[name] = {"fit": False, "value": float_val, "error": None}

    # scale 默认 fit=True
    if 'scale' in para_dict:
        para_dict['scale']['fit'] = True

    return {"para": para_dict, "dispersity": dispersity_dict}


class SasViewFit:
    def __init__(self, data_path, shape, para, cut_off=1e-6, fit_method='lm'):
        self.radial_data = load_data(data_path)
        q = self.radial_data.x
        self.rmin = 2 * np.pi / max(q)
        self.rmax = 2 * np.pi / min(q)
        # print(radial_data)
        self.kernel = load_model(shape)
        self.para = para
        self.cut_off = cut_off
        self.fit_method = fit_method
        
        origin_para = self.mapping_para()
        self.model = Model(self.kernel, **origin_para)

    def fit(self):
        self.fit_para()
        M = Experiment(data=self.radial_data, model=self.model, cutoff=self.cut_off)
        problem = FitProblem(M)
        result = fit(problem, method=self.fit_method)

        pars = self.model.parameters()
        Iq_model = M.theory()
        q = self.radial_data.x
        Iq_data = self.radial_data.y
        Ierr = self.radial_data.dy

        return q, Ierr, Iq_data, Iq_model, pars, result
    
    def fit_para(self):
        for key in self.para["para"]:
            param = getattr(self.model, key)
            if self.para["para"][key]["fit"]:
                param.range(*param.limits)
    
    def mapping_para(self):
        para = {}
        for key, value in self.para["para"].items():
            # print(key)
            para[key] = self.para["para"][key]["value"]
        for key, value in self.para["dispersity"].items():
            para[key] = self.para["dispersity"][key]
        return para
    
def check_para(shape, para, config):
    # 混合模型（含 + 或 @ 的组合模型）不做静态校验，依赖 sasmodels 运行时检查
    if '+' in shape or '@' in shape:
        return True
    if shape in config:
        if set(config[shape]["para"].keys()) == set(para["para"].keys()) and set(config[shape]["dispersity"].keys()) == set(para["dispersity"].keys()):
            return True
    else:
        return False
    return False

    
if __name__ == '__main__':
    data_path = '/home/dur/zhongjiajun/rozai_agent/tools/sasview/IQ_39191_SubEB_PC_0.dat'
    shape = 'hollow_cylinder'
    para = {
        "para":{
            "scale": {"fit": True, "value": 0.08, 'error':None},
            "radius": {"fit": False, "value": 105, 'error':None},
            "radius_pd": {"fit": False, "value": 0.0, 'error':None},
            "thickness": {"fit": True, "value": 105, 'error':None},
            "thickness_pd": {"fit": False, "value": 0.0, 'error':None},
            "length": {"fit": False, "value": 105, 'error':None},
            "length_pd": {"fit": False, "value": 0.0, 'error':None},
            "sld": {"fit": False, "value": 1, 'error':None},
            "sld_solvent": {"fit": False, "value": 4, 'error':None},
            "background": {"fit": True, "value": 0, 'error':None}
        },
        "dispersity":{
            "radius_pd_type": "lognormal",
            "radius_pd_n": 35,
            "radius_pd_nsigma": 8,
            "thickness_pd_type": "lognormal",
            "thickness_pd_n": 35,
            "thickness_pd_nsigma": 8,
            "length_pd_type": "lognormal",
            "length_pd_n": 35,
            "length_pd_nsigma": 8
        }
    }

    # 拟合部分
    config_path='/home/dur/zhongjiajun/rozai_agent/tools/sasview/model.json'
    with open(config_path, 'r') as f:
        config = json.load(f)
    if not check_para(shape, para, config):
        print("输入参数格式与形状参数配置不匹配")
        exit()
    svf = SasViewFit(data_path, shape, para)
    q, Ierr, Iq_data, Iq_model, pars, fit_result = svf.fit()
    print(f"拟合参数: {fit_result['dx']}")
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
    print(para)
    residual = (Iq_data - Iq_model) / Ierr
    data_length = len(q)
    chisquare = sum(residual ** 2)/(data_length-3)
    print(f"拟合结果的χ²值: {chisquare}")
    title = f"{shape} Fitting"

    # 绘图部分
    import matplotlib.pyplot as plt
    from io import BytesIO

    # 使用matplotlib绘制到内存PNG
    fig, axs = plt.subplots(2, 1, sharex=True, figsize=(5, 4), gridspec_kw={'height_ratios': [2, 1]})
    # 上：实验数据 + 拟合
    if Ierr is not None and Ierr.size == Iq_data.size:
        axs[0].errorbar(q, Iq_data, yerr=Ierr, fmt='o', label='Experiment', capsize=3, zorder=1)
    else:
        axs[0].plot(q, Iq_data, 'o', label='Experiment', zorder=1)
    axs[0].plot(q, Iq_model, '-', label='Fit Curve', zorder=2)
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

    buf = BytesIO()
    fig.savefig(buf, format='png', dpi=400, bbox_inches='tight')
    
    plt.close(fig)  # 关闭图形，释放内存

    # 保存到文件
    save_path = '/home/dur/zhongjiajun/rozai_agent/tools/sasview/fit_result.png'
    with open(save_path, 'wb') as f:
        f.write(buf.getvalue())
    print(f"图片已保存到: {save_path}")