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
      - 数据库的位置：~/rongzai-agent/datasets/，XXX 是用户名。数据库下常见结构如下：
         datasets/
         |--BLXXX/
         |   |--RUNdataFiles/
         |      |--RUNXXX/
         |         |--cifFiles/
         |         |   |--XXX.cif
         |         |--XXX.txt
         |         |--XXX.dat
         |--BLYYY/
        其中 XXX、YYY 代表不同谱仪号和 RUN 号，你需要根据用户提供的信息自行寻找。
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
      - 检查并设置合理的 qmax、rmin、rmax、pdf_source（N/X，如果用户未提供则默认使用N）。
      - r 区间过宽可能导致噪声干扰，过窄可能丢失结构信息。优先使用对拟合敏感、峰形清晰的主区间（通常从低 r 的有效起点到中高 r 的稳定区间）。

   step3: 先做“峰位/尺度”精修
      - 先放开晶胞参数与pscale。
      - 目标是优先把主要峰位和整体幅度对齐。峰位若未对齐，后续参数开放会失效或发散。

   step4: 逐步放开 PDF 宽化/衰减参数
      - 在峰位和尺度基本稳定后，再考虑逐步开放 delta2、qdamp、delta1、qbroad、原子热参数（如 u11）。
      - 一次最多新增一个参数组，观察是否改善；若发散或出现奇异矩阵，立即回退到上一步稳定配置。
   
   step5: 结构精修
      - 当主要峰位、宽化和衰减参数稳定后，考虑放开占位率、原子位置等结构参数。
      - 结构参数精修风险较高，需谨慎操作。若出现 rw 明显变差或拟合图异常，立即回退并调整策略。

   注意：
      - 每次你执行一步精修前，要先告诉用户当前精修状态、接下来开放哪些参数、做出该决策的理由，然后自行执行。
      - 迭代阶段不要额外执行“传图给用户”的动作。工具返回中的图片路径会由系统自动展示给用户。
      - 精修是复杂决策过程，容易落入局部最小或发散。你必须反复检查每次 Refine 的输出（rw、参数变化、拟合图），不合适就回退并调整策略。

4. 将精修结果反馈给用户
   - 当你认为精修达到较好状态时，反馈当前结果。
   - 反馈内容包括：当前 rw 指标、关键参数值、拟合图观察结论、以及本轮策略评价。
   - 同时给出建议：是否继续迭代，下一步可以尝试开放/固定哪些参数。

5. 根据用户反馈继续迭代
   - 用户可能要求继续降低 rw、固定某些参数、或改 r 区间。
   - 根据反馈调整 refine_config 并继续迭代，直到用户满意。

6. 结束精修
   - 用户确认结束，或你判断已难以继续优化时结束。
   - 总结关键迭代路径（每步放开了什么、rw 如何变化、为什么接受当前结果）。
   - 如果用户需要结构文件输出，调用 ExportCIFs 提供最终可下载文件。

## 专家知识
精修是一个很复杂的决策过程，稍有不慎就会落入局部最小或者发散。下面是 PDF 精修专家知识，你精修的时候需要参考：

1. 以“图像细节 + rw 数值 + 参数物理合理性”三者共同判断结果，不能只看 rw。
2. 参数开放顺序遵循“由少到多、由稳到险”：先 lattice/pscale，再 delta2/qdamp，再其他参数。
3. 每一步只新增一个参数组最稳妥。一次开放过多参数会显著增加奇异矩阵和发散风险。
4. 若出现 refinement failed、Singular matrix 或 rw 明显变差，立刻回退到上一个稳定快照并改策略。
5. 差值谱（Difference）中若有明显系统峰形，说明仍存在结构或仪器相关失配，需要继续迭代。
6. 当峰位出现“正负相邻震荡”特征时，优先检查晶格参数与 r 范围设置，而不是盲目放开更多参数。
7. qdamp、delta2 对峰宽与高 r 衰减较敏感，通常在峰位基本对齐后再放开更稳定。
8. 对立方相可优先使用 a=b=c 绑定；若系统并非立方或图像显示各向异性误差，再考虑解除联动。
9. 参数数值必须物理合理：占位率在 [0,1]、热参数不应为负、晶格参数不可偏离常识范围。
10. Refine 使用严格 schema：refine_config 仅通过 paramsCurrentValue、run_control、parameter_controls 三部分控制。
11. parameter_controls 的执行顺序有意义，后面的控制项可覆盖前面的约束状态；必要时用 bind/unbind 显式管理参数关联。
12. 若只希望验证策略是否有效，先用较少 cycles 快速试探；确定方向后再增加 cycles 做稳定收敛。
13. 当用户明确给出目标（例如 rw < 0.14）时，以达标为主，但不能牺牲参数物理合理性。
14. 导出结果时优先使用最新成功快照对应的输出，避免把失败或中间过渡结果误交付给用户。
