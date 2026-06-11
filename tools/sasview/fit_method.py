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

## tools的开发，和前面的fit功能分离。
## 增加计算SLD的功能函数
import periodictable as pt
def calculate_sld(formula, density, neutron_wavelength=1.8, xray_wavelength=None):
    """
    计算给定化学式和密度的物质的中子SLD。
    :param formula: 化学式字符串，例如 'H2O'
    :param density: 密度，单位 g/cm³
    :param neutron_wavelength: 中子波长，单位 Å（默认 1.8 Å）
    :param xray_wavelength: X射线波长，单位 Å（暂不使用）
    :return: SLD，单位 10⁻⁶ Å⁻²
    """
    try:
        formula_obj = pt.formula(formula, density=density)
        sld = formula_obj.neutron_sld(wavelength=neutron_wavelength)

        number_density = density / formula_obj.mass * pt.constants.avogadro_number  # 分子数密度, 1/cm^3

        # 吸收截面采用热中子(1.798 A)截面的 1/v 近似: sigma_a(lambda)=sigma_a,th*(lambda/1.798)
        thermal_wavelength = 1.798  # A
        incoherent_barn = sum(atom.neutron.incoherent * fraction for atom, fraction in formula_obj.atoms.items())
        coherent_barn = sum(atom.neutron.coherent * fraction for atom, fraction in formula_obj.atoms.items())
        absorption_barn = sum(
            atom.neutron.absorption * (neutron_wavelength / thermal_wavelength) * fraction
            for atom, fraction in formula_obj.atoms.items()
        )
        total_barn = coherent_barn + incoherent_barn + absorption_barn

        incoherent_cross_section = incoherent_barn * 1e-24 * number_density
        absorption_cross_section = absorption_barn * 1e-24 * number_density
        neutron_1e_length = 1.0 / (total_barn * 1e-24 * number_density)

        print(f"Neutron Incoherent Cross-section: {incoherent_cross_section:.4f} cm⁻¹")
        print(f"Neutron Absorption Cross-section: {absorption_cross_section:.4f} cm⁻¹")
        print(f"Neutron 1/e Length: {neutron_1e_length:.4f} cm")
        result = {
            "sld": sld,
            "incoherent_cross_section": incoherent_cross_section,
            "absorption_cross_section": absorption_cross_section,
            "neutron_1e_length": neutron_1e_length
        }
        return result  # 返回第一个元素的SLD
    except Exception as e:
        print(f"计算SLD时发生错误: {e}")
        return None

    
if __name__ == '__main__':
    test = 'sld'  # 'fit' or 'sld'
    if test == 'fit':
        # 测试fit功能，使用sasmodels中hollow_cylinder模型的参数结构，拟合IQ_39191_SubEB_PC_0.dat数据，拟合结果保存为png图片。
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
    elif test == 'sld':
        # 测试计算SLD功能，计算水的中子SLD，密度为1g/cm³。
        density = 1.0  # g/cm³
        formula = 'H2O'
        neutron_wavelength = 1.8  # 中子波长，单位 Å，可在此修改
        result = calculate_sld(formula, density, neutron_wavelength=neutron_wavelength)
        if result is not None:
            print(f"水的中子 SLD: {result['sld']:.4f} × 10⁻⁶ Å⁻²")
            print(f"水的中子 Incoherent Cross-section: {result['incoherent_cross_section']:.4f} cm⁻¹")
            print(f"水的中子 Absorption Cross-section: {result['absorption_cross_section']:.4f} cm⁻¹")
            print(f"水的中子 1/e Length: {result['neutron_1e_length']:.4f} cm")
    else:
        print("请选择测试类型：'fit' 或 'sld'")