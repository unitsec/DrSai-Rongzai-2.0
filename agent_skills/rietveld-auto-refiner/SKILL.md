---
name: rietveld-auto-refiner
description: 面向中子粉末衍射 Rietveld 自动精修的执行技能。用于获取精修所需文件的路径，并按轮次调用精修工具完成迭代优化。
---

# Rietveld Auto Refiner

## 适用场景

- 用户要求你进行中子粉末衍射 Rietveld 精修。
- 用户只提供了谱仪、RUN、探测器、文件名，而没有提供完整绝对路径。
- 用户要求继续上一轮精修并调整参数。

## 工具与职责

1. find_refinement_files_tool
   - 用于定位精修需要的输入文件。
   - 目标输出字段：gsa_path、instprm_path、cif_paths。

2. run_rietveld_refinement_tool
   - 用于执行一次 GSAS-II 精修。
   - 接收 refine_params、gpx_path、refine_idx 等参数，用于连续多轮精修。

## 标准流程

1. 判断输入是否完整
   - 若用户已给出 gsa_path、instprm_path、cif_paths 的绝对路径，直接进入第 3 步。
   - 否则进入第 2 步。

2. 先定位文件
   - 调用 find_refinement_files_tool。
   - 如果返回 status=error：
     - 用一句话说明缺失项。
     - 明确告诉用户下一步需要补充什么。
     - 结束本轮，不要盲目发起精修。
   - 如果返回 status=success：记录返回的 gsa_path、instprm_path、cif_paths。

3. 执行一轮精修
   - 调用 run_rietveld_refinement_tool。
   - 若返回失败：先解释失败原因，再给出可执行的修复建议。
   - 若返回成功：提取并总结关键结果，再判断是否建议继续下一轮。

4. 迭代策略
   - 每次只增加少量精修自由度，避免一次开放过多参数。
   - 若出现明显恶化或异常，优先建议回退到上一轮可用状态。
   - 如果指标已稳定且改进不显著，建议暂停并请用户确认是否继续。

## 回复模板建议

每轮结果建议按以下结构输出：

1. 本轮输入
   - gpx_path / refine_idx / 关键 refine_params

2. 本轮结果
   - status
   - 关键指标变化
   - 主要告警或异常

3. 决策
   - 是否接受本轮结果
   - 下一轮建议调整参数

## 限制

- 不要捏造文件路径或工具返回值。
- 无法确定时优先请求补充信息，而不是编造答案。
- 一次只处理一个探测器数据，避免在同一轮混入多个探测器。
