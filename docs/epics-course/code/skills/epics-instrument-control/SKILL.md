---
name: epics-instrument-control
description: 通过 EPICS PV 控制实验设备（电机、温控、快门、探测器），并自主完成扫描/寻峰/变温测量等实验动作。当用户要求"动设备/调温度/扫角度/找峰/跑一次实验"，或需要在散裂中子源谱仪上自主驱动实验时，必须先阅读本 skill。
---

# EPICS 实验设备控制（智能体自主实验）

## 工具

MCP 服务 `EPICS Instrument Tools`（本仓库 `docs/epics-course/code/c08_mcp_server.py`）。
该服务下的工具：`describe_instrument`、`self_check`、`snapshot`、`read_channel`、
`write_channel`、`hold_temperature`、`move_axis`、`run_scan`、`emergency_stop`、`get_progress`。

连接方式（两种都可以，按部署环境选）：

- stdio（本机、由智能体拉起进程）：`python <repo>/docs/epics-course/code/c08_mcp_server.py`
- SSE（常驻服务）：
  - type: sse
  - url: http://127.0.0.1:42521/sse
  - 启动命令：`python c08_mcp_server.py --sse --port 42521`

## 硬性约束（违反会伤害设备或样品，也会让你的结论不可信）

1. **先读后写**：任何写操作前先 `describe_instrument` + `snapshot`，
   不要凭记忆或猜测使用通道名、单位、限值。
2. **每个写操作必须给 reason**，写清实验意图。没有 reason 会被直接拒绝。
3. **用动作类工具，不要拼裸 PV**：要温度就 `hold_temperature`，
   要角度就 `move_axis`，要扫描就 `run_scan`。
   这些工具会自己等待到位并返回证据（actual、elapsed_s）。
4. **被拒绝不是故障**：`ok=false` 时读 `rule` 和 `hint`，调整参数重试，
   或向用户报告。**永远不要试图绕过安全层，也不要把被拒绝说成已完成。**
5. **不确定就急停**：读数异常、设备无响应、结果不符合物理直觉，
   立刻 `emergency_stop`，然后报告。
6. **长任务必须转述进展**：`hold_temperature` / `run_scan` 可能跑几分钟，
   期间用 `get_progress` 把最新一行讲给用户，不要让用户面对静默。
7. **如实报告**：只报告工具实际返回的数据；没测到的就说没测到。
   报告里要有：做了什么、实际数值、数据文件路径、异常与不确定度。

## 标准流程

### A. 上机前（每次都做）

1. `self_check`。如果 `problems` 非空 → 停止，把问题原文报给用户。
2. `snapshot`，记住当前温度、角度、联锁状态（`heater_enable`、`over_temp`）。

### B. 调温

1. `hold_temperature(target=60, tolerance=0.5, reason="...")`。
2. 成功判据是返回里的 `actual` 落在 `target ± tolerance` 内，而不是"我发过命令了"。
3. 失败（超时）时先算一下物理上限：环境温度 + 满功率可达到的温升。
   如果目标温度本身就超过能力，告诉用户，不要反复重试。

### C. 扫描 + 寻峰（本 skill 最常用）

1. **两段式扫描**，不要一次就把步长设到最小：
   - 粗扫：`run_scan(start=10, stop=30, step=2, reason="粗扫找峰")`
   - 从返回的 `peak.peak_x_refined`（或 `peak_x`）取峰位 P；
   - 细扫：`run_scan(start=P-1.5, stop=P+1.5, step=0.25, samples=2, reason="细扫")`
2. 点数和时间预算：点数 = (stop-start)/step + 1，单点耗时 ≈ 0.3~2 s
   （取决于到位速度和 dwell）。**单次扫描不要超过 500 点**；
   宁可先用大步长看形状，再逐步加密。
3. 报告峰位时要说明用的是哪个估计：`peak_x_refined`（抛物线插值）
   还是 `centroid_x`（质心）；两者差异大说明峰形不对称，值得说明。
4. 每次扫描都会自动落盘 CSV（`data_file`），报告里要给出路径。

### D. 变温/变角度组合实验

对每个实验点，按顺序做：`move_axis` → `hold_temperature` → `read_channel`/`run_scan`，
记录"设定值、实际值、读数、时间戳"。**每个点都要有实际值**，
因为"设定 45℃"和"实际 44.1℃"在论文里不是一回事。

### E. 收尾

1. 实验结束调用 `emergency_stop("实验结束")`，让设备回到安全状态。
2. 报告里附上审计日志路径（`describe_instrument` 里可以看到配置，
   日志默认在 `logs/audit.jsonl`），方便人工复核每一步。

## 常见失败与处理

| 现象 | 原因 | 怎么办 |
|---|---|---|
| `rule: ch1-只读` | 写了测量通道 | 测量值不能写；改设定值通道或执行器通道 |
| `rule: ch2-范围` | 超出登记限值 | 检查单位（℃ / K / deg / rad）和量级 |
| `rule: ch3-单步` | 一次变化太大 | 分几步走；或说明理由请求人工授权 |
| `rule: ch4-联锁` | 前提条件没满足（如没给运行许可） | 先满足 `requires` 里的条件，再重试 |
| `rule: ch5-速率` | 写得太频繁 | 不要用写 PV 的方式刷数据；用订阅/扫描工具 |
| `hold_temperature` 超时 | 目标超能力、环境扰动、控制器没起 | 检查目标是否可达；确认常驻 PID 服务在跑 |
| 峰位明显不对 | 粗扫步长太大、峰落在扫描范围外、轴没到位 | 扩大范围重扫；检查 `theta_rb` 是否真的到位 |
| 设备无响应 | 串口/IOC 掉了 | `self_check` 看 `UNREACHABLE`；报告用户，不要盲目重试 |
