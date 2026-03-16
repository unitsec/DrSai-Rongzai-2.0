import numpy as np
import json
from bumps.names import *
from sasmodels.data import load_data
from sasmodels.core import load_model
from sasmodels.bumps_model import Model, Experiment


# 设计利用sasView拟合实验散射谱的函数，拟合参数按需开放，可以在拟合过程中选择关闭和开启，或者修改参数数值。
# 输入参数：不同模型有的参数值不一样多，

class SasViewFit:
    def __init__(self, data_path, shape, para, cut_off=1e-6, fit_method='lm'):
        self.radial_data = load_data(data_path)
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
        if len(problem.getp()) == 0:
            print("最少需要一个参数进行拟合")
            return None
        fit(problem, method=self.fit_method)

        pars = self.model.parameters()
        Iq_model = M.theory()
        q = self.radial_data.x
        Iq_data = self.radial_data.y
        Ierr = self.radial_data.dy

        return q, Ierr, Iq_data, Iq_model, pars
    
    def fit_para(self):
        for key in self.para["para"]:
            param = getattr(self.model, key)
            if self.para["para"][key]["fit"]:
                param.range(0, np.inf)
            else:
                pass
    
    def mapping_para(self):
        para = {}
        for key, value in self.para["para"].items():
            # print(key)
            para[key] = self.para["para"][key]["value"]
        for key, value in self.para["statu"].items():
            para[key] = self.para["statu"][key]
        return para
    
def check_para(shape, para, config):
    if shape in config:
        if set(config[shape]["para"].keys()) == set(para["para"].keys()) and set(config[shape]["statu"].keys()) == set(para["statu"].keys()):
            return True
    else:
        return False
    return False


    
if __name__ == '__main__':
    data_path = '/home/dur/zhongjiajun/rozai_agent/tools/sasview/IQ_39191_SubEB_PC_0.dat'
    shape = 'hollow_cylinder'
    para = {
        "para":{
            "scale": {"fit": True, "value": 0.08},
            "radius": {"fit": False, "value": 105},
            "radius_pd": {"fit": False, "value": 0.0},
            "thickness": {"fit": False, "value": 105},
            "thickness_pd": {"fit": False, "value": 0.0},
            "length": {"fit": False, "value": 105},
            "length_pd": {"fit": False, "value": 0.0},
            "sld": {"fit": False, "value": 1},
            "sld_solvent": {"fit": False, "value": 4},
            "background": {"fit": False, "value": 0}
        },
        "statu":{
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
    q, Ierr, Iq_data, Iq_model, pars = svf.fit()
    for name, param in pars.items():
        if name in para["para"]:
            para["para"][name]["value"] = param.value
        if name in para["statu"]:
            para["statu"][name] = param.value
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