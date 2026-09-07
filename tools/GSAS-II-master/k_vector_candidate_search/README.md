# GSAS-II 候选 k-vector 搜索封装

这个目录提供了一个无 GUI 的小封装，用来调用 GSAS-II 内置的 `GSASII/k_vector_search.py`。适用场景是：已经有母相核结构 CIF 文件和实验观测到的 extra/satellite peaks，希望从这些峰位搜索候选磁传播矢 `k`。

## 文件说明

- `k_vector_candidate_search.py`：Python 封装，核心函数是 `search_candidate_k_vectors(...)`。
- `README.md`：当前使用说明。

## 依赖

这个封装直接复用旁边的 GSAS-II 源码树。运行环境需要安装 GSAS-II k-vector 搜索依赖，主要包括：

- `numpy`
- `scipy`
- `ase`，用于从 CIF 文件读取晶胞和原子坐标
- `seekpath`
- `spglib`，通常会随 `seekpath` 一起安装

如果要使用 `option=2` 做完整第一布里渊区网格搜索，还需要 GSAS-II 编译好的 `kvec_general` 扩展。本 checkout 里已有 Linux/Python 3.11 版本：`GSASII/bin/kvec_general.cpython-311-x86_64-linux-gnu.so`。

如果当前环境缺少 `seekpath`，可以安装到实际运行该封装的 Python 环境中：

```bash
conda install -n <env-name> -c conda-forge seekpath
```

或：

```bash
python -m pip install seekpath
```

注意：`option=2` 需要 Python 版本和平台与 `GSASII/bin/kvec_general.cpython-311-x86_64-linux-gnu.so` 匹配。当前这个二进制对应 Linux + Python 3.11；如果使用 Python 3.12 或其他平台，需要重新编译 `kvec_general`，或者只使用 `option=0/1`。

## 推荐用法：直接传 CIF 文件

如果从这个目录外部调用，先把本目录加入 `PYTHONPATH`。例如在仓库根目录下运行：

```bash
PYTHONPATH=tools/GSAS-II-master/k_vector_candidate_search python your_script.py
```

Python 示例：

```python
from k_vector_candidate_search import search_candidate_k_vectors

candidates = search_candidate_k_vectors(
    cif_path="/path/to/parent_phase.cif",
    satellite_d_spacings=[4.21, 3.18, 2.67],
    threshold=0.001,
    option=0,
)

for item in candidates:
    print(item)
```

传入 `cif_path` 后，封装会自动从 CIF 中读取：

- 晶胞矩阵 `cell`
- 原子分数坐标 `positions`
- 原子类型 `atom_types`
- Bravais lattice symbol `bravais_symbol`，如果调用时没有手动传入，会用 `seekpath` 从结构中推断

如果你希望覆盖自动推断的 Bravais symbol，也可以显式传入：

```python
candidates = search_candidate_k_vectors(
    cif_path="/path/to/parent_phase.cif",
    bravais_symbol="hR",
    satellite_d_spacings=[4.21, 3.18, 2.67],
)
```

返回结果是字典列表，每个字典包含：

- `kx`, `ky`, `kz`：候选传播矢，使用 conventional reciprocal cell setting。
- `indicator_distance`：GSAS-II 用来排序的归一化匹配指标，基于 delta d / d；越小越好。
- `average_delta_d`：平均 d-spacing 绝对偏差。
- `max_delta_d`：最大 d-spacing 绝对偏差。
- `setting`：当前固定为 `conventional`。

GSAS-II 会按 `indicator_distance` 排序候选结果。如果最佳候选已经满足 `threshold`，会提前返回一个候选；否则最多返回 10 个候选。

## 输入参数

### `cif_path`

推荐传入母相核结构 CIF 文件路径：

```python
cif_path="/path/to/parent_phase.cif"
```

提供 `cif_path` 后，通常不需要再手动传 `cell`、`lattice_parameters`、`positions`、`atom_types` 或 `numbers`。

### `bravais_symbol`

这是 GSAS-II/seekpath 使用的 Bravais lattice symbol，例如：

- `P`
- `cF`, `oF`
- `cI`, `tI`, `oI`
- `hR`
- `oC`, `oA`, `mC`

如果提供了 `cif_path`，这个参数可以省略，封装会用 `seekpath` 自动推断。在 GSAS-II GUI 中，这个量通常由母相空间群的 lattice type 和 crystal system 推导得到。

### 母相晶胞

如果不使用 `cif_path`，可以手动提供晶胞参数：

```python
lattice_parameters=[a, b, c, alpha, beta, gamma]
```

其中角度单位是 degree。

也可以直接提供 3x3 晶格矢量矩阵：

```python
cell=[
    [ax, ay, az],
    [bx, by, bz],
    [cx, cy, cz],
]
```

不使用 `cif_path` 时，二者提供一个即可。

### 原子坐标和原子编号

如果不使用 `cif_path`，需要手动提供 `positions`。它是母相晶胞中的分数坐标：

```python
positions=[
    [x1, y1, z1],
    [x2, y2, z2],
]
```

然后提供 `numbers` 或 `atom_types` 二选一。使用 `cif_path` 时，这些会从 CIF 自动读取。

如果已经有 seekpath 需要的整数编号，可以直接传：

```python
numbers=[1, 1, 2, 2]
```

如果只有元素/原子类型标签，可以传：

```python
atom_types=["Mn", "Mn", "O", "O"]
```

封装会把重复的 atom type 自动映射成整数 ID。`positions` 的数量必须与 `numbers` 或 `atom_types` 的数量一致。

### Satellite Peaks

`satellite_d_spacings` 是观测到的 extra/satellite peaks 的 d-spacing 列表：

```python
satellite_d_spacings=[4.21, 3.18, 2.67]
```

如果你的峰位现在是 2theta 或 TOF，需要先用对应的 GSAS-II instrument 参数转换成 d-spacing，再传给这个函数。

### Nuclear HKLs

`nuclear_hkls` 是母相核结构反射的 hkl 列表。如果不传，封装会使用 GSAS-II GUI 中同样的默认值：每个方向从 0 到 5 的非负 hkl 网格，共 216 个 hkl。

也可以手动传入自己的反射列表：

```python
nuclear_hkls=[
    [0, 0, 1],
    [0, 1, 0],
    [1, 0, 0],
    [1, 1, 0],
]
```

## 搜索模式

用 `option` 控制搜索范围：

- `option=0`：只搜索高对称 k 点，最快。
- `option=1`：搜索高对称 k 点和高对称路径。
- `option=2`：在第一布里渊区做 general grid search，最慢，可能需要数小时。

`option=2` 时可以设置网格步长和并行进程数：

```python
candidates = search_candidate_k_vectors(
    bravais_symbol="P",
    lattice_parameters=[10, 10, 10, 90, 90, 90],
    positions=[[0, 0, 0]],
    atom_types=["Mn"],
    satellite_d_spacings=[4.21, 3.18],
    threshold=0.001,
    option=2,
    kstep=[0.01, 0.01, 0.01],
    processes=8,
)
```

## 辅助函数

封装还提供了几个小工具函数：

- `parse_cif_structure(cif_path, bravais_symbol=None)`：从 CIF 读取晶胞、分数坐标、原子类型，并推断或使用给定的 Bravais symbol。
- `lattice_parameters_to_vectors(lattice_parameters)`：把 `[a, b, c, alpha, beta, gamma]` 转成 GSAS-II 使用的 3x3 lattice vectors。
- `atom_types_to_numbers(atom_types)`：把原子类型标签映射为 seekpath 需要的整数 ID。
- `generate_nuclear_hkls(max_index=5)`：生成默认 hkl 网格。

## 注意事项

- 这个函数只负责从 satellite peaks 搜索候选传播矢 `k`，不是完整磁结构解析。
- 得到候选 `k` 后，后续仍需要结合磁对称性分析和 refinement，例如 k-SUBGROUPSMAG 或 GSAS-II 磁相精修流程。
- 封装返回的是 conventional reciprocal cell setting 下的 k-vector；GSAS-II 内部搜索使用 primitive setting，封装已经按 GUI 逻辑做了转换。