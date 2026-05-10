import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
# set the path to fine the data files and the library files for Z-Rietveld
os.environ['ZRT_DATA_DIR'] = str(PROJECT_ROOT / 'Z-Rietveld' / 'Z-Rietveld' / 'utility_atomic_data')
os.environ['ZRT_LIB_DIR'] = str(PROJECT_ROOT / 'Z-Rietveld' / 'lib')
sys.path.insert(0, str(PROJECT_ROOT))

# difPath = str(PROJECT_ROOT / "testData" / "RUN0000410_groupMA.histogramIgor")
# cifPath = str(PROJECT_ROOT / "testData" / "run410-zeolyte.cif")
# instruPath = str(PROJECT_ROOT / "testData" / "MA.zDiffractometer")


difPath = str(PROJECT_ROOT / "testData" / "RUN0001094_groupBM.histogramIgor")
cifPath = str(PROJECT_ROOT / "testData" / "LNO.cif")
instruPath = str(PROJECT_ROOT / "testData" / "BM.zDiffractometer")


import refineTool

# Generate ZRTn file
ZRTnFilePath = refineTool.genZRTn(difPath, instruPath,[cifPath])
print("ZRTn file generated at:", ZRTnFilePath)

# -----------------------------------------------------------------------------
# refineTool.refine(ZRTnFilePath, cfg) 的 cfg 说明
# -----------------------------------------------------------------------------
# 1. 所有值目前都以“字符串”形式传入。
#    例如：
#    "true" / "false" / "6" / "1.0,0,0,0,0,0"
#
# 2. 常用键：
#    - "scaleVary":
#        是否精修整体 scale。等价新名字是 "refine_scale"。
#    - "scaleInit":
#        scale 初值。
#    - "varyBackground":
#        是否精修背景。等价新名字是 "refine_background"。
#    - "bgOrder":
#        背景多项式项数。等价新名字是 "background_coeffs"。
#    - "bgInit":
#        背景初值，逗号分隔。
#    - "bgMode":
#        背景类型，目前示例主要用 "legendre"。
#    - "maxCycle":
#        最大迭代次数。等价新名字是 "refine_cycle"。
#    - "refine_cell":
#        是否精修晶胞参数（推荐使用该新名字，旧名字 "varyLattice" 仍兼容）。
#        设为 "true" 后，会按空间群所属晶系自动应用晶胞约束，不需要手动判断：
#        - Cubic: a=b=c，角度固定
#        - Tetragonal/Hexagonal: a=b，角度按晶系固定
#        - Orthorhombic: a,b,c 独立，角度固定
#        - Monoclinic/Triclinic: 按晶系规则自动决定可变参数
#        注意：若同时设置 "space_group"，则按新的空间群规则生效。
#    - "varyMassRatio":
#        是否精修相比例。等价新名字是 "refine_phase_fraction"。
#    - "varyOccupancy":
#        是否精修占位。等价新名字是 "refine_occupancy"。
#    - "refine_position":
#        是否精修原子坐标。
#    - "refine_atoms":
#        针对指定原子单独控制精修内容。
#        例："{'Fe1':'XU','O2':'YZF','all':'U'}"
#        支持的标记：
#        - X/Y/Z: 分别控制坐标 x/y/z（可组合）
#        - U 或 UISO: 精修各向同性 Uiso（AtomicDisplacement12）
#        - F 或 OCC/OCCUPANCY: 精修占有率
#        兼容旧写法：只有 X（如 "XU"）时，按历史行为等价于 XYZ 全开。
#
# 3. 空间群修改：
#    - "space_group":
#        值是“相名称 -> Z-Rietveld 内部空间群字符串”的字典。
#        例："{'run410-zeolyte':'203_2'}"
#        这里的相名称来自 CIF 文件名 stem，即 run410-zeolyte.cif -> run410-zeolyte。
#
# 4. 吸收修正配置：
#    - "absorption":
#        值是“数据标签(通常是 histogram label) -> 配置对象”的字典。
#        例：
#        "{'A': {'refine': True, 'function': 'spherical'}}"
#
#        子键说明：
#        - refine:
#            true/false，是否打开该条数据的吸收相关精修。
#            当前实现中，真正被设为 Vary/Fixed 的参数是 PowderDensity。
#        - function:
#            吸收函数类型，可选：
#            "none"
#            "cylindrical"
#            "spherical"
#            "plate"
#            "flatplate"
#            "lobanov1"
#            "lobanov2"
#
#            其中源码内部名称对应关系为：
#            cylindrical -> Type 1
#            spherical   -> Type 2
#            plate       -> Type 3
#            flatplate   -> Type 4
#            lobanov1    -> Type 5 (源码内部名 LobanovSmallerThan3)
#            lobanov2    -> Type 6 (源码内部名 LobanovBiggerThan3)
#
#        - absorption:
#            可选，直接设置吸收参数值。
#        - powder_density / powderDensity:
#            可选，设置 PowderDensity 初值。
#
#        还支持默认项：
#        "{'_default': {'refine': False, 'function': 'cylindrical'},
#           'A': {'refine': True, 'function': 'lobanov1'}}"
#
# 5. 一个较完整的 cfg 示例：
#    cfg = {
#        "scaleVary": "true",
#        "bgOrder": "6",
#        "bgInit": "1.0,0,0,0,0,0",
#        "bgMode": "legendre",
#        "refine_cell": "true",
#        "space_group": "{'run410-zeolyte':'203_2'}",
#        "absorption": "{'A': {'refine': True, 'function': 'lobanov1', 'powder_density': 1.2}}"
#    }
# -----------------------------------------------------------------------------

# Configure refinement parameters in a dictionary
cfg1 = {
    "scaleVary": "true",
    "varyBackground": "false"
}

# cfg = {
#     "scaleVary": "true",
#     "scaleInit": "1.0",
#     "bgOrder": "6",
#     "bgInit": "1.0,0,0,0,0,0",
#     "bgMode": "legendre",
#     "refine_profile_level0": "false",
#     "refine_profile_level1": "false",
#     "refine_profile_level2": "false",
#     "refine_profile_alpha": "false",
#     "refine_profile_beta0": "false",
#     "refine_profile_beta1": "false",
#     "refine_profile_r0": "false",
#     "varyMassRatio": "false",
#     "refine_cell": "true",
#     "varyOccupancy": "false",
#     "adpVaryThreshold": "0.0",
#     "maxCycle": "10"
# }

# Perform refinement
# refineTool.refine(ZRTnFilePath, cfg1)


cfg2 = {
    "scaleVary": "true",
    "varyBackground": "true",
    "bgOrder": "6",
    "bgInit": "1.0,0,0,0,0,0",
    "bgMode": "legendre",
    # "space_group": "{'run410-zeolyte': '203_2'}",
    # Histogram label is usually "A" in this test flow.
    # 这里演示针对 A 这条数据打开吸收精修，并使用球形吸收模型。
    "absorption": "{'A': {'refine': True, 'function': 'plate'}}"
}

refineTool.refine(ZRTnFilePath, cfg2)
# refineTool.autoRefine(ZRTnFilePath)

cfg3 = {
    "scaleVary": "true",
    "varyBackground": "true",
    "bgOrder": "6",
    "bgInit": "1.0,0,0,0,0,0",
    "bgMode": "legendre",
    # "space_group": "{'run410-zeolyte': '203_2'}",
    # Histogram label is usually "A" in this test flow.
    # 这里演示针对 A 这条数据打开吸收精修，并使用球形吸收模型。
    "absorption": "{'A': {'refine': True, 'function': 'plate'}}",
    "refine_cell": "false"
}

refineTool.refine(ZRTnFilePath, cfg3)