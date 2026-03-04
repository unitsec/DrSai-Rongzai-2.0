# @mcp_tools/GSASII 

## 一、安装和配置GSASII：

https://gsas-ii.readthedocs.io/en/latest/index.html

1. 从以下链接拉取开源主源代码：
https://github.com/AdvancedPhotonSource/GSAS-II/

2. 从以下链接下载系统所需的bin文件，解压后重命名为“bin”并复制到“./GSAS-II-master/GSASII”文件夹下:

https://tooomm.github.io/github-release-stats/?username=AdvancedPhotonSource&repository=GSAS-II-buildtools

2.5. 在用GSASII生成磁结构数据的时候报错，原因来源是GSASIIstrIO.py中1657-1659行如下：
    for itm,text in enumerate(SGtable):
        opr = text.split(')')[1]
        newTable += ['(%2d)%s   %2d'%(itm+1,opr,int(SpnFlp[itm])),]
    修改为
    for itm,text in enumerate(SGtable):
        opr = text.split(')')[1]
        try:
            flip = int(SpnFlp[itm])
        except Exception:
            # 兜底：从操作字符串的最后一段解析 x,y,z,±1
            tail = opr.split(',')[-1].strip().replace(' ', '')
            flip = 1 if tail == '-1' else 0
            newTable += ['(%2d)%s %d' % (itm + 1, opr, flip), ]
    后问题解决


3. 创建环境gsas2_py311,并激活

4. 使用pip install安装以下包
```text
python == 3.11
numpy==1.26.4 
scipy==1.11.4
wxPython==4.2.2 # 可选的，或者使用conda安装
PyCifRW==4.4.6
PyOpenGL==3.1.9
matplotlib==3.7.5
```

## 二、使用@mcp_tools

“./GSAS-II-master/GSASII”文件夹下包含“mcp_gsas2.py”，包含多个mcp工具：
1. 提供晶体结构文件、仪器参数文件和计算参数，模拟出XRD图谱并绘制图表。

```python
@mcp.tool()
def simulate_and_plot_xrd(cif_file, instrument_params, 
                            two_theta_range, num_points, 
                            output_prefix, save_plot):
    """模拟XRD图谱并绘制图表
    Args:
        cif_file (str): CIF文件路径
        instrument_params (str): 仪器参数文件路径
        two_theta_range (tuple): 2theta范围 (min, max)
        num_points (int): 数据点数
        output_prefix (str): 输出文件前缀
        save_plot (bool): 是否保存图表
    Returns:
        dict: 包含模拟结果和图表文件的路径
        {
            "status": "success",
            "project_file": str,  # 项目文件路径
            "data_file": str,  # 数据文件路径
            "reflections_file": str,  # 反射文件路径
            "plot_file": str,  # 图表文件路径（如果保存）
            "x_data": list,  # x数据
            "y_data": list,  # y数据
            "error": str  # 错误信息（如果有的话）
        }
        """
```

2. 提供实验图像和控制文件后，积分出图像文件的强度数据，并保存在DATA_DIR文件中。

```python
@mcp.tool()
def integrate_single_image(
    image_file: str,
    image_control_file: str,
    output_dir: str = "custom_results"
) -> Dict[str, Any]:
    """
    处理单个XRD图像文件并生成积分数据（输出TXT格式）
    
    Args:
        image_file: 图像文件名(需在DATA_DIR中)
        image_control_file: 图像控制文件名(需在DATA_DIR中)
        output_dir: 输出目录名称(默认:"custom_results")
        
    Returns:
        {
            "status": "success"/"error",
            "input_image": 输入图像路径,
            "output_files": 生成的TXT文件列表,
            "gpx_file": 项目文件路径,
            "processing_time": 处理耗时(秒),
            "output_path": 输出目录绝对路径,
            "error": 错误信息(如有)
        }
    """
```
3. 提供中子或X射线粉末衍射数据精修，输入衍射文件和仪器参数，通过设置use_xray或use_neutron来选择X射线或中子，两个都设置为True时，跨装置同时精修中子和X射线粉末衍射数据。设置精修轮次后，运行输出精修结果。

'''python
@mcp.tool()
def powder_refinement(
    xray_data=None, 
    xray_inst=None, 
    neutron_data=None, 
    neutron_inst=None, 
    cif_files=None, 
    output_dir='output',
    use_xray=True,
    use_neutron=True,
    refine_background_cycles=1,
    refine_cell_cycles=1,
    refine_hap_cycles=1,
    refine_size_strain_cycles=1,
    refine_sample_atoms_cycles=1,
    refine_instrument_cycles=1
):
    """
    粉末衍射数据精修主函数（支持多相）
    
    参数:
        xray_data (str): X射线数据文件路径
        xray_inst (str): X射线仪器参数文件路径
        neutron_data (str): 中子数据文件路径
        neutron_inst (str): 中子仪器参数文件路径
        cif_files (list): CIF结构文件路径列表
        output_dir (str): 输出目录路径
        use_xray (bool): 是否使用X射线数据 (默认True)
        use_neutron (bool): 是否使用中子数据 (默认True)
        refine_*_cycles (int): 各精修步骤的执行次数，0表示不执行
        
    返回:
        dict: 包含精修结果的字典，包括:
            - rwp_values: 各数据的Rwp值
            - cell_parameters: 各相的晶胞参数
            - phase_fractions: 各相的相分数
            - output_files: 生成的文件列表
    """
'''
## 启动FastAPI服务

```bash
bash run.sh
```