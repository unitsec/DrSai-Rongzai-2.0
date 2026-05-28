"""
从 sasmodels 提取所有模型信息并按 model.json 格式保存
使用 Model.state() 获取真实默认参数值，与 generate_config.py 方式一致
"""

import json
import os
import traceback

from sasmodels.core import load_model, list_models
from sasmodels.bumps_model import Model


def extract_model_parameters(model_name: str):
    """
    使用 Model.state() 提取参数的真实默认值，输出格式与 model.json 一致。

    Returns:
        dict: {"para": {...}, "dispersity": {...}}，失败时返回 None
    """
    try:
        kernel = load_model(model_name)
        model = Model(kernel)
        state = model.state()   # {param_name: value, ...}

        para_dict = {}
        dispersity_dict = {}

        for name, value in state.items():
            # up_frac_i 及之后的参数（磁/极化参数）不需要
            # if name == 'up_frac_i':
            #     break
            # _pd_type / _pd_n / _pd_nsigma 归入 dispersity
            if name.endswith('_pd_type'):
                dispersity_dict[name] = value
            elif name.endswith('_pd_n') or name.endswith('_pd_nsigma'):
                dispersity_dict[name] = int(value) if isinstance(value, float) and value == int(value) else value
            else:
                # 普通参数 + _pd 参数全部放入 para
                try:
                    float_val = float(value)
                except (TypeError, ValueError):
                    float_val = value
                para_dict[name] = {"fit": False, "value": float_val}

        # scale 默认 fit=True
        if 'scale' in para_dict:
            para_dict['scale']['fit'] = True

        return {"para": para_dict, "dispersity": dispersity_dict}

    except Exception as e:
        print(f"  ✗ {model_name} 提取失败: {e}")
        traceback.print_exc()
        return None

def generate_model_list_json(output_path: str):
    """
    遍历 sasmodels 全部可用模型，生成 model_list.json。
    """
    all_models = list_models()
    print(f"共发现 {len(all_models)} 个模型\n")

    model_config = {}
    model_names = []
    failed = []

    for model_name in all_models:
        model_names.append(model_name)
        model_config['model_name'] = model_names

    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(model_config, f, indent=4, ensure_ascii=False)

    print(f"\n✓ 已保存到: {output_path}")
    print(f"成功: {len(model_config)} 个，失败: {len(failed)} 个")
    if failed:
        print(f"失败列表: {failed}")

    return model_config


def generate_model_json(output_path: str):
    """
    遍历 sasmodels 全部可用模型，生成 model.json。
    """
    all_models = list_models()
    print(f"共发现 {len(all_models)} 个模型\n")

    model_config = {}
    failed = []

    for model_name in all_models:
        print(f"处理: {model_name} ...", end=" ", flush=True)
        params = extract_model_parameters(model_name)
        if params:
            model_config[model_name] = params
            print(f"✓ ({len(params['para'])} 个参数)")
        else:
            failed.append(model_name)

    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(model_config, f, indent=4, ensure_ascii=False)

    print(f"\n✓ 已保存到: {output_path}")
    print(f"成功: {len(model_config)} 个，失败: {len(failed)} 个")
    if failed:
        print(f"失败列表: {failed}")

    return model_config


if __name__ == '__main__':
    script_dir = os.path.dirname(os.path.abspath(__file__))
    output_path = os.path.join(script_dir, 'model_list.json')

    print("=" * 60)
    print("sasmodels 全模型参数提取工具")
    print("=" * 60 + "\n")

    model_config = generate_model_list_json(output_path)

    print("\n" + "=" * 60)
    print("示例：sphere 模型的配置")
    print("=" * 60)
    if 'sphere' in model_config:
        print(json.dumps(model_config['sphere'], indent=2))
