---
name: pdf-refine
description: PDF refinement skill for pair distribution function fitting using the PdfFit engine. Also covers obtaining and processing files required for refinement. This skill must be read first whenever the user requests PDF data refinement. This skill should be elevated to a system prompt to guide your refinement-related decisions and operations continuously.
---

# PDF Auto Refiner

## 工具

1. MCP 服务 PDFRefinementTools。该服务下面的工具有 InitializeProject、Refine、ExportCIFs 三个工具。
   - InitializeProject：使用原始 PDF 数据和结构文件创建项目快照。
   - Refine：执行一次 PDF 精修并输出新快照、拟合结果与图像。
   - ExportCIFs：从已有精修快照中收集导出的结构文件。

## 标准流程

如果你认为用户是希望你自己先把数据修好，那么你要按照下面的流程，不停地进行 FunctionCall，直到严格执行完步骤 4。

1. 定位精修所需文件
   - 要开始 PDF 精修，首先必须有 PDF 数据文件（.txt/.dat，名称通常带有pdf，第一列通常为 r），以及至少一个结构文件（通常是 .cif）。
   - 用户可能上传文件，或者让你用之前上传过的文件。优先使用这些文件。
   - 如果有文件没有上传，需要你根据用户提供的信息去数据库中获取。用户可能会说类似“帮我把 BL16 的 RUN0023568 的 PDF 数据修好，cif 文件是 Si.cif”这类话，你需要从中提取谱仪名称（BL16）和 RUN 号（RUN0023568），然后去数据库中寻找对应文件。
      - 去数据库获取 PDF 数据文件，需要用户提供：谱仪名称（如 BL16）、RUN 号（如 RUN0023568），以及可用于定位数据文件的名称信息（如 Gr_Hermite）。
      - 去数据库获取 .cif 文件，需要用户提供：谱仪名称（如 BL16）、RUN 号（如 RUN0023568）。
      - 如果用户给的信息不完整，询问用户。
      - 数据库的位置：~/rongzai-agent/datasets/，数据库下常见结构如下：
         datasets/
         |--BLXXX/
         |   |--RUNdataFiles/
         |      |--RUNXXX/
         |         |--cifFiles/
         |         |   |--XXX.cif
         |         |--XXX.txt
         |         |--XXX.dat
         |--BLYYY/
        其中 XXX、YYY 代表不同谱仪号、RUN 号或者其他文件名，你需要根据用户提供的信息自行寻找。
   - 如果缺失任何关键文件，明确告知用户并结束本轮。
   - 这一步的目的是：你要明确获取到所需文件的文件路径，并且这些文件必须是绝对路径。

2. 检查并连接 MCP 工具
   - 你需要确保已经连接上 PDFRefinementTools 的 MCP 服务，并且已经拿到该服务暴露出的工具说明。
   - 如果当前还没有可用的 PDFRefinementTools MCP 代理工具，或者你无法确认该服务是否已经连接：
      - 根据下列连接信息连接 MCP 服务：
         - type: sse
         - url: http://127.0.0.1:42502/sse
      - 连接成功后，认真阅读该 MCP 服务返回的工具名称和 description，确认工具用法。
   - 如果已经存在可用的 PDFRefinementTools MCP 代理工具：继续后续步骤。

3. 开始自动迭代精修（严格执行）
   step1: 初始化
      - 当根据用户提供的信息拿到原始数据后，先调用 InitializeProject 创建项目快照（project_path），再执行下一步。
      - 注意！初始化不能出现任何错误或警告！否则必须解决后重新初始化！可以通过直接修改cif文件等方式来解决。

   step2: 基础参数与范围检查
      - 检查并设置合理的 qmax、rmin、rmax、实验温度、pdf_source（N/X，如果用户未提供则默认使用N）。
      - r 区间过宽可能导致噪声干扰，过窄可能丢失结构信息。优先使用对拟合敏感、峰形清晰的主区间（通常从低 r 的有效起点到中高 r 的稳定区间）。

   step3: 先做“峰位/尺度”精修，再放开一些非结构参数，为后续的结构精修做个铺垫。
      - 先放开晶胞参数与pscale。
      - 目标是优先把主要峰位和整体幅度对齐。峰位若未对齐，后续参数开放会失效或发散。
      - 在峰位和尺度基本稳定后，再考虑逐步开放 各相的delta1、delta2、原子热参数（u11，u22,u33）,全局的qdamp、qbroad也可以试试。
      - 一次最多新增一个参数组，观察是否改善；若发散或出现奇异矩阵，立即回退到上一步稳定配置。
      
   step4: 结构精修（最重要步骤）
      - 放开占位率、原子位置等结构参数进行精修。注意要一次开放少量甚至一个原子的精修，避免精修发散。逐渐把能修的原子都修一遍。
      - 若出现 rw 明显变差或拟合图异常，立即回退并调整策略。

   注意：
      - 
      - 每次你执行一步精修前，要先告诉用户当前精修状态、接下来开放哪些参数、做出该决策的理由，然后自行执行。
      - 迭代阶段不要额外执行“传图给用户”的动作。工具返回中的图片路径会由系统自动展示给用户。
      - 精修是复杂决策过程，容易落入局部最小或发散。你必须反复检查每次 Refine 的输出（rw、参数变化、拟合图），不合适就回退并调整策略。

## 专家知识
精修是一个很复杂的决策过程，稍有不慎就会落入局部最小或者发散。下面是 PDF 精修专家知识，你精修的时候需要参考：

1. 能放开的参数非常多，从少到多，直到全部放开稳定收敛。
2. 以“图像细节 + rw 数值 + 参数物理合理性”三者共同判断结果，不能只看 rw。
3. 参数开放顺序遵循“由少到多、由稳到险”：先 lattice/pscale，再 delta1/delta2，再其他参数。
4. 每一步只新增一个参数组最稳妥。一次开放过多参数会显著增加奇异矩阵和发散风险。
5. 若出现 refinement failed、Singular matrix 或 rw 明显变差，立刻回退到上一个稳定快照并改策略。
6. 差值谱（Difference）中若有明显系统峰形，说明仍存在结构或仪器相关失配，需要继续迭代。
7. 参数数值必须物理合理：占位率在 [0,1]、热参数不应为负、晶格参数不可偏离常识范围。
8. Refine 使用严格 schema：refine_config 仅通过 paramsCurrentValue、parameter_controls 两部分控制。
9. parameter_controls 的执行顺序有意义，后面的控制项可覆盖前面的约束状态；必要时用 bind/unbind 显式管理参数关联。
10. 当用户明确给出目标（例如 rw < 0.14）时，以达标为主，但不能牺牲参数物理合理性。
