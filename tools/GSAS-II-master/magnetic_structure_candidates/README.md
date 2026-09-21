# GSAS-II 磁空间群筛选与初始磁结构封装

这个目录提供一个无 GUI 的 Python 接口，用于根据：

- 母相结构 CIF
- 一个或多个传播矢 k
- 指定的磁性元素类型或原子标签

调用 Bilbao Crystallographic Server 的 `k-SUBGROUPSMAG`，筛选候选磁空间群，并使用 GSAS-II 生成可继续接入精修流程的初始 magnetic phase。

封装会从 CIF 读取顺磁母相空间群，并将该空间群的完整对称操作提交给 Bilbao。`k-SUBGROUPSMAG` 在 `gmag` 模式下把它作为顺磁灰群 `G1'`，再结合传播矢 k 枚举允许的磁子群。因此顺磁相空间群已经是 Bilbao 服务端第一层、也是最重要的一层约束。

## 文件

- `magnetic_structure_candidates.py`：核心封装。
- `README.md`：使用说明。

## 重要说明

磁空间群枚举来自 Bilbao 在线服务，因此运行时必须能访问：

```text
https://cryst.ehu.es/
```

如果 Bilbao 无响应或网络受限，函数会抛出 `ConnectionError`。CIF 解析、磁性位点筛选和 magnetic phase 构造在本地使用 GSAS-II 源码完成。

GSAS-II 当前源码中使用的旧地址带有 `www`，但服务器证书只覆盖 `cryst.ehu.es`。本封装已经在运行时改用不带 `www` 的有效 HTTPS 地址；不会修改 GSAS-II 原始源码。

## 环境依赖

建议使用 GSAS-II 的 Python 3.11 环境：

```bash
conda activate gsas2_py311
python -m pip install requests PyCifRW numpy
```

本封装直接加载相邻目录中的 GSAS-II 源码，不需要把 GSAS-II 安装成 pip 包。

## 基本用法

从仓库根目录运行脚本时，将当前封装目录加入 `PYTHONPATH`：

```bash
PYTHONPATH=tools/GSAS-II-master/magnetic_structure_candidates \
conda run -n gsas2_py311 python your_script.py
```

Python 示例：

```python
from magnetic_structure_candidates import search_magnetic_structure_candidates

candidates = search_magnetic_structure_candidates(
    cif_path="/path/to/parent_structure.cif",
    k_vectors=[[0, 0, 0]],
    magnetic_atom_types=["Mn"],
    exact_wyckoff_orbit=True,
    initial_moment=1.0,
)

for candidate in candidates:
    print(candidate["magnetic_space_group"])
    print(candidate["transformation_matrix"])
    for site in candidate["magnetic_sites"]:
        print(site)
```

## 核心函数

```python
search_magnetic_structure_candidates(
    cif_path,
    k_vectors,
    magnetic_atom_types=None,
    magnetic_atom_labels=None,
    *,
    use_whole_star=False,
    landau_only=False,
    maximal_only=False,
    all_have_moment=False,
    max_unique=100,
    wyckoff_position_type=None,
    preserve_parent_wyckoff_type=False,
    exact_wyckoff_orbit=False,
    initial_moment=1.0,
    kept_only=True,
)
```

### `cif_path`

母相核结构 CIF 路径。封装使用 GSAS-II 自带的 `CIFPhaseReader` 读取：

- 母相空间群 `SGData`
- 晶胞参数
- 独立原子坐标
- 原子类型和标签

如果一个 CIF 包含多个结构 block，GSAS-II reader 可能需要 GUI 选择。目前无 GUI 封装最适合只有一个结构 block 的 CIF。

### `k_vectors`

一到三个传播矢：

```python
k_vectors=[[0, 0, 0]]
```

也可以传分数字符串，推荐对特殊 k 点使用这种形式：

```python
k_vectors=[["1/2", "0", "1/2"]]
```

浮点数会被转换成分母不超过 24 的有理数。例如 `0.5` 会转换成 `1/2`。

多个 k-vector 示例：

```python
k_vectors=[
    ["1/2", "0", "0"],
    ["0", "1/2", "0"],
]
```

注意：Bilbao 的 Landau 筛选不能与多个 k-vector 同时使用。

### 磁性原子选择

可以按元素/原子类型选择：

```python
magnetic_atom_types=["Mn", "Fe"]
```

也可以按 CIF 中的 atom label 精确选择：

```python
magnetic_atom_labels=["Mn1", "Mn2"]
```

两种条件同时提供时采用并集。

如果两者都不提供，封装会自动选择 GSAS-II 有 magnetic form factor 的非氧原子。显式选择没有 GSAS-II magnetic form factor 的元素时会抛出 `ValueError`，避免生成空磁相。

### 筛选选项

- `use_whole_star=True`：让 Bilbao 使用完整 k-star。
- `landau_only=True`：使用 Landau transition 条件筛选。
- `maximal_only=True`：只保留 maximal magnetic subgroup。
- `all_have_moment=True`：要求所有选中的磁性位点都允许非零磁矩。
- `max_unique=100`：允许的最大独立磁位点数量。
- `wyckoff_position_type="general"`：只保留磁性原子在候选磁群中处于一般 Wyckoff 位点的候选。
- `wyckoff_position_type="special"`：只保留磁性原子在候选磁群中处于特殊 Wyckoff 位点的候选。
- `preserve_parent_wyckoff_type=True`：要求每个磁性原子在候选磁群中的 general/special 类型与母相一致。
- `exact_wyckoff_orbit=True`：对完整磁性 Wyckoff 轨道枚举磁矩模型，用 spglib 识别精确磁稳定子群，再与 Bilbao 候选按 BNS 编号匹配并去重。这是复现 Bilbao 网页 Wyckoff 轨道筛选的严格模式。
- `kept_only=True`：只返回通过本地位点检查的候选；设为 `False` 可查看未通过候选。

### Wyckoff 位点类型筛选

GSAS-II 的 Bilbao 请求字典中虽然存在 `wyckoffstrain` 字段，但当前 GSAS 专用 `k-SUBGROUPSMAG` 接口没有接收 CIF 原子坐标，而且实测向该字段提交 `2a`、`general`、`special`、`yes` 等值均不会改变 Bilbao 返回结果。因此本封装没有把未验证的字符串直接当作服务端筛选条件。

`wyckoff_position_type` 和 `preserve_parent_wyckoff_type` 使用 CIF 中的实际磁性原子坐标做 general/special 类型粗筛：

1. 用 GSAS-II `SytSym` 计算磁性原子在母相中的轨道 multiplicity。
2. 计算母相一般位置的 multiplicity，判断该磁性位点属于 `general` 或 `special`。
3. 对每个 Bilbao 候选变换磁性原子坐标。
4. 在候选磁群下重新计算 multiplicity 和 Wyckoff position type。
5. 按 `wyckoff_position_type` 或 `preserve_parent_wyckoff_type` 淘汰候选。

这个粗筛使用真实坐标，但不等价于 Bilbao 网页的完整 Wyckoff 磁轨道稳定子群筛选。以仓库中的 `HABSRefine.cif`、`k=(0,0,0)` 和 Mn 位点为例，原有磁矩允许性筛选返回 130 个候选；启用 `preserve_parent_wyckoff_type=True` 后剩余 113 个。

要复现网页中 `2a:(0,0,0)` 得到的 14 个结果，应使用：

```python
candidates = search_magnetic_structure_candidates(
    cif_path="tools/GSAS-II-master/tests/kSearchTest/HABSRefine.cif",
    k_vectors=[[0, 0, 0]],
    magnetic_atom_types=["Mn"],
    exact_wyckoff_orbit=True,
    initial_moment=1.5,
)
```

严格模式会：

1. 用 ASE 将 CIF 中选定的独立磁性位点展开成完整 Wyckoff 轨道。
2. 枚举轨道上非零磁矩的方向、相对符号和幅值比。
3. 用 spglib 计算每个磁矩模型的精确 UNI/BNS 磁空间群。
4. 从 Bilbao GSAS 响应中恢复 GSAS-II parser 原本丢弃的规范 BNS 编号。
5. 按 BNS 编号与精确稳定子群取交集，并折叠共轭设置产生的重复项。
6. 使用对应的代表磁矩模型初始化 magnetic phase，保留不同位点间的方向、符号和幅值比例。

当前严格模式支持 `k=(0,0,0)`，并支持展开后最多两个磁性轨道位置。其他 k-vector 或更高 multiplicity 轨道会明确抛出 `NotImplementedError`，不会静默退回不等价的近似筛选。

对 `HABSRefine.cif` 的 Mn `2a:(0,0,0)`，严格模式实测返回以下 14 个 BNS 群：

```text
194.270  194.268  164.89
63.464   63.463   63.462   63.457
15.89    15.85
12.62    12.58
11.54    11.50
2.4
```

## 返回结果

函数返回候选字典列表。每个候选包含：

```python
{
    "paramagnetic_space_group": "P 63/m m c",
    "paramagnetic_gray_group": "P 63/m m c 1'",
    "magnetic_space_group": "...",
    "bns_symbol": "...",
    "gid": 1,
    "is_base_candidate": True,
    "keep": True,
    "transformation_matrix": [[...], [...], [...]],
    "origin_shift": [...],
    "cell": [a, b, c, alpha, beta, gamma, volume],
    "unique_tested_site_count": 2,
    "magnetic_sites": [...],
    "orbit_stabilizer_model": {...},
    "phase": {...},
}
```

- `paramagnetic_space_group`：从输入 CIF 读取的普通母相空间群。
- `paramagnetic_gray_group`：Bilbao 磁子群搜索所对应的顺磁灰群 `G1'`。
- `magnetic_space_group`：该顺磁灰群在给定 k-vector 下的候选磁子群。

### `magnetic_sites`

每个磁位点包含：

```python
{
    "label": "Mn1",
    "atom_type": "Mn",
    "fractional_coordinates": [0.0, 0.0, 0.0],
    "magnetic_site_symmetry": "...",
    "wyckoff_position_type": "special",
    "multiplicity": 2,
    "general_multiplicity": 24,
    "parent_wyckoff_position_type": "special",
    "parent_multiplicity": 2,
    "parent_general_multiplicity": 24,
    "parameter_ids": [1, 0, 0],
    "multipliers": [1.0, 0.0, 0.0],
    "child_basis_constraint_relations": [[1.0, 0.0, 0.0]],
    "parent_basis_constraint_relations": [[1.0, 1.0, 0.0]],
    "initial_moment": [1.0, 0.0, 0.0],
}
```

`parameter_ids` 和 `multipliers` 来自 GSAS-II 的 `GetCSpqinel`，表示该磁位点允许的独立磁矩分量以及分量之间的对称关系。

`parameter_ids`、`multipliers`、`initial_moment` 和 phase 中的 `AMx/AMy/AMz` 都使用候选磁子相的晶轴基底。`child_basis_constraint_relations` 将这个约束写成显式向量；`parent_basis_constraint_relations` 则通过 Bilbao 晶胞变换矩阵将同一约束换回 CIF 母相晶轴基底。

例如 `HABSRefine.cif` 的 `Cmcm (63.457)` 候选只有一个独立 Mn 磁位点：

```text
Cmcm 子相基底：  (M, 0, 0)
P63/mmc 母相基底：(M, M, 0)
```

两种写法描述的是同一个磁矩自由度，不能把母相基底下的 `(M,M,0)` 数值直接写入 Cmcm 子相的 `AMx/AMy/AMz`。

`wyckoff_position_type` 是候选磁群中的 general/special 类型；带 `parent_` 前缀的字段描述 CIF 母相中的位点类型和 multiplicity。

`orbit_stabilizer_model` 在严格模式下记录 spglib UNI/BNS 编号、完整磁轨道坐标以及用于生成初始结构的代表磁矩模式。

### `phase`

`phase` 是 GSAS-II phase 字典，已经完成：

- 磁空间群设置
- 晶胞和原子坐标变换
- 非磁原子过滤
- 磁位点筛选
- `Mx/My/Mz` 字段初始化
- magnetic phase 类型设置

它可以作为后续 GPX/GSASIIscriptable 集成的输入，但这个封装不会自动创建完整 GPX 工程，也不会自动添加 histogram 或 refinement constraints。

## 初始磁矩

`initial_moment` 只提供一个精修起点，不代表磁矩预测结果。封装会找到该位点第一个允许的独立磁矩自由度，并按 GSAS-II 给出的 multiplier 写入初值：

```python
initial_moment=1.5
```

例如某个位点允许三个独立方向时，初值可能是：

```text
[1.5, 0.0, 0.0]
```

如果对称性要求两个分量相关，初值会按照 `multipliers` 同时赋值。磁矩大小和具体磁畴仍需要结合衍射数据进一步判断和精修。

## 本地验证

仓库中的 Mn CIF 可以用于验证本地 CIF 解析和 magnetic phase 构造：

```text
tools/GSAS-II-master/tests/kSearchTest/HABSRefine.cif
```

完整候选搜索还要求 Bilbao 服务在线。若服务器暂时超时，本地验证通过并不代表远程枚举已经执行。

## 相关 GSAS-II 源码

- `GSASII/SUBGROUPS.py`：Bilbao `k-SUBGROUPSMAG` 请求和响应解析。
- `GSASII/GSASIIpwdGUI.py`：候选磁空间群及磁位点筛选流程。
- `GSASII/GSASIIlattice.py`：磁群对象、晶胞和原子变换。
- `GSASII/GSASIIspc.py`：磁 site symmetry 和允许磁矩分量。