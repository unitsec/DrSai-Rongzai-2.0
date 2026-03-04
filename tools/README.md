tools 工具目前包括主要工具：

一.执行GSASII精修：
    1.定位指定路径：cd rozai_agent/tools/GSAS-II-master/GSASII/
    2.激活环境：conda activate gsas2_py311
    3.运行脚本：python 0neutron_gsas_refine.py
        # 输入文件路径
        input_dir = "/aifs/user/home/liqingmeng/rozai_agent/tools/GSAS-II-master/GSASII/RUN0021905"
        output_dir = "/aifs/user/home/liqingmeng/rozai_agent/tools/GSAS-II-master/GSASII/RUN0021905/gsas_output"
        
        # 执行中子精修（完整精修流程）
        results = neutron_refinement(
            neutron_data=os.path.join(input_dir, "RUN0021905_group5.gsa"),
            neutron_inst=os.path.join(input_dir, "RT_GROUP5.instprm"),
            cif_files=[os.path.join(input_dir, "mp-149.cif")],
            output_dir=output_dir,
            refine_scale=True,          # 精修标度因子
            refine_absorption=True,     # 根据需要启用吸收精修
            use_constraints=True,       # 使用约束
        )
    4.输出精修后的cif文件和其他精修。
        结果保存路径：/aifs/user/home/liqingmeng/rozai_agent/tools/GSAS-II-master/GSASII/RUN0021905/gsas_output
        精修后cif文件为：mp-149_gsas_refined.cif

二.执行PDF精修，指定GSAS精修后输出的cif文件：
    1.定位指定路径：cd rozai_agent/tools/pdf_refinement/
    2.激活环境：conda activate diffpy.pdffit2_env
    3.运行脚本：python 0pdf_refine.py
        data_file = "/aifs/user/home/liqingmeng/rozai_agent/tools/GSAS-II-master/GSASII/RUN0021905/Si_PDF.txt"
        structure_file = "/aifs/user/home/liqingmeng/rozai_agent/tools/GSAS-II-master/GSASII/RUN0021905/gsas_output/mp-149_gsas_refined.cif"
        output_dir = "output"  # 输出目录
    4.输出pdf精修后的cif图和其他精修结果。
        结果保存路径：/aifs/user/home/liqingmeng/rozai_agent/tools/pdf_refinement/output
        精修后cif文件为：pdf_refinement_refined.cif

三.晶体结构3D视图脚本，指定GSAS精修/PDF精修后输出的cif文件：
    1.定位指定路径：rozai_agent/tools/cif_3D_visualization/
    2.激活环境：conda activate drsai    或者  conda activate gsas2_py311
    3.运行脚本：python cif_3D_visualization.py
        # 示例1: 处理当前目录下所有CIF文件
        # cif_3D_visualization("./", "./output_images")
        
        # 示例2: 处理单个CIF文件
        cif_3D_visualization("/aifs/user/home/liqingmeng/rozai_agent/tools/GSAS-II-master/GSASII/RUN0021905/gsas_output/mp-149_gsas_refined.cif", "./output_images")
        cif_3D_visualization("/aifs/user/home/liqingmeng/rozai_agent/tools/pdf_refinement/output/Si_PDF_refined.cif", "./output_images")
    
        
        # 示例3: 处理文件列表
        # cif_3D_visualization(["file1.cif", "file2.cif"], "./output_images")
    4.输出3D可视化png。