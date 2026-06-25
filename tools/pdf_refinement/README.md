diffpy.pdffit2 - PDF精修工具
简介
    diffpy.pdffit2 是一个用于原子对分布函数(PDF)计算和精修的Python工具包。它提供了从晶体结构模型计算PDF并进行结构精修的功能，是PDFgui软件的计算引擎。

系统要求
    支持的操作系统：Windows、macOS(非Arm64)、Linux
    支持的Python版本：3.11、3.12、3.13

安装指南
推荐安装方法（使用Miniconda）
1.首先安装 Miniconda
2.添加conda-forge频道：
    conda config --add channels conda-forge
3.创建并激活conda环境：
    conda create -n diffpy.pdffit2_env diffpy.pdffit2
    conda activate diffpy.pdffit2_env
4.验证安装是否成功：
    python -c "import diffpy.pdffit2; print(diffpy.pdffit2.__version__)"
