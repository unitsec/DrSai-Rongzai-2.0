# 附录 F　配套代码清单（打印版索引）

> 代码在仓库的 `docs/epics-course/code/` 下，**24 个文件，全部在 EPICS Base 7.0.10 上实测运行过**。
> 本附录是"打印版索引"——纸上看到某个工具想上手时，按这里的命令跑即可。

---

## F.1 三条命令跑起来

```bash
cd docs/epics-course/code

bash 01_env_check.sh          # 环境自检：缺什么、怎么补
bash runlab.sh start sim      # 一键起整套虚拟实验站（IOC + 恒温箱 + 探测器 + PID + 板子仿真）
bash runlab.sh status         # 看谁在跑，并抽查 PV
```

其他常用：`bash runlab.sh logs`（跟踪日志）、`bash runlab.sh stop`（收工，会先把加热写回 0）。

---

## F.2 文件清单

### 基础设施

| 文件 | 作用 | 验证状态 |
|---|---|---|
| `01_env_check.sh` | 检查 EPICS 工具、Python 库、编译工具、CA 环境变量 | ✅ 实测（13 项全过） |
| `runlab.sh` | 一键起停整套实验站；启动顺序、停止顺序、PID 管理 | ✅ 实测（start/status/stop） |
| `softioc/st.cmd` | IOC 启动脚本（`dbLoadRecords` + `iocInit` + `dbl`） | ✅ 实测 |
| `softioc/db/hello.db` | 第 3 章：第一个数据库（3 条记录） | ✅ 实测 |
| `softioc/db/lab.db` | 第 4 章：完整实验站（18 条记录：`ai/ao/bi/bo/calc/mbbi/mbbo/longin/waveform/string*`） | ✅ 实测（含两处报错复现） |

### 第 5 章　客户端

| 文件 | 作用 | 命令示例 | 验证状态 |
|---|---|---|---|
| `pv_utils.py` | 可靠 PV 封装：连接超时、`put_checked` 读回重试、`wait_within` 等到位、订阅过滤占位事件 | 被全部脚本引用 | ✅ 实测 |
| `c05_pv_explore.py` | 打印一个 PV 的全部自描述信息（单位/限值/报警/枚举/扫描） | `python c05_pv_explore.py LAB:TempRaw` | ✅ 实测 |
| `c05_monitor_log.py` | 订阅若干 PV 落 CSV（带时间戳、flush、优雅退出） | `python c05_monitor_log.py LAB:TempRaw --seconds 20` | ✅ 实测（81 次事件） |
| `c05_scan_demo.py` | 扫描 + 画图 + 找峰（五步骨架：写→到位→稳定→取数→记录） | `python c05_scan_demo.py --start 15 --stop 25 --step 1` | ✅ 实测（误差 0.016°） |
| `scan_utils.py` | 找峰/质心/FWHM/扫描摘要（供第 5、8、10 章共用） | 被引用 | ✅ 实测 |

### 第 6 章　闭环与 PID

| 文件 | 作用 | 命令示例 | 验证状态 |
|---|---|---|---|
| `c06_thermal_plant.py` | 虚拟恒温箱（一阶热模型，含传感器噪声、环境漂移） | `python c06_thermal_plant.py` | ✅ 实测（与理论吻合） |
| `c06_detector_sim.py` | 虚拟探测器（高斯峰 + 本底 + 噪声） | `python c06_detector_sim.py --peak 20` | ✅ 实测 |
| `pid.py` | PID：真实 dt 积分、抗饱和、测量值微分、微分滤波、继电整定 | 被引用 | ✅ 实测 |
| `c06_run_closed_loop.py` | 阶跃 / 闭环 / 自动整定三种模式，出报告 + 出图 | `python c06_run_closed_loop.py --mode closed --setpoint 60` | ✅ 实测 |
| `c06_pid_service.py` | **常驻** PID 控制器（唯一写入者 + 联锁 + 看门狗 + CSV 日志） | `python c06_pid_service.py --kp 2 --ki 0.08` | ✅ 实测 |

### 第 7 章　硬件

| 文件 | 作用 | 命令示例 | 验证状态 |
|---|---|---|---|
| `c07_pv_bridge.py` | 串口 ↔ EPICS PV 桥（caproto 造的 CA 服务端），含标定、滤波、失联检测、`--sim` 仿真设备 | `python c07_pv_bridge.py --sim` / `--port /dev/ttyUSB0` | ✅ 实测（仿真模式全流程） |
| `hardware/esp32_firmware/main.py` | ESP32 MicroPython 固件：行协议 + 范围检查 + **看门狗** | 拷进开发板 | ⚠️ 需真硬件验证 |
| `hardware/wiring.md` | 采购清单、安全底线、接线表、分步验证表、故障速查 | 阅读 | 📋 清单 |

### 第 8 章　智能体（核心）

| 文件 | 作用 | 命令示例 | 验证状态 |
|---|---|---|---|
| `c08_instrument.json` | **仪器画像**：通道、单位、限值、单步幅度、联锁、安全状态 | 被全部第 8 章代码读取 | ✅ 实测 |
| `c08_safety.py` | **安全层**：白名单/范围/取值/单步/联锁/速率/总量六道检查 + 审计日志 + 急停 | 被引用 | ✅ 16/16 测试通过 |
| `c08_tools.py` | **语义工具**（10 个）：`describe_instrument` / `self_check` / `snapshot` / `read_channel` / `write_channel` / `hold_temperature` / `move_axis` / `run_scan` / `emergency_stop` / `get_progress` | `python c08_tools.py --self-check` | ✅ 16/16 测试通过 |
| `tools_test.py` | 安全与工具的自动化测试（**故意去撞每一条规则**） | `python tools_test.py` | ✅ 16/16 通过 |
| `c08_mcp_server.py` | MCP 服务（stdio / SSE），暴露上面 10 个工具 | `python c08_mcp_server.py --sse --port 42521` | ✅ 官方 mcp 客户端实测连通 |
| `c08_agent_demo.py` | 智能体自主实验闭环（`script` 策略无需大模型；`llm` 策略接真模型） | `python c08_agent_demo.py --policy script` | ✅ 实测完整跑通 |
| `c08_bluesky_demo.py` | 同一件事用 ophyd/bluesky 写（正统路线迁移样例） | `python c08_bluesky_demo.py` | ✅ 实测 9 点扫描 |
| `skills/epics-instrument-control/SKILL.md` | 蓉仔 skill：智能体的行为约定与标准流程 | 复制进 `agent_skills/` | 📋 可直接使用 |

### 本书构建工具

| 文件 | 作用 |
|---|---|
| `tools/build_docx.py` | 把本目录的 Markdown 重新构建成打印用 DOCX/PDF（改完章节后重跑即可） |

---

## F.3 运行时生成的目录

```
logs/    audit.jsonl（审计日志）、progress.jsonl（长任务进度）、pid_service.csv、各服务日志
data/    scan_*.csv（扫描数据）、report_*.json（实验报告）
.run/    各服务 PID 文件
```

**这三个目录都可以随时删掉重建**（`bash runlab.sh stop && rm -rf .run logs data`）。

---

## F.4 关键环境变量

| 变量 | 用途 | 默认 |
|---|---|---|
| `PYTHON` | 用哪个解释器起服务 | `python3` |
| `SOFTIOC` | 用哪个 IOC 可执行文件 | 自动找 `softIocPVA` → `softIoc` |
| `EPICS_PREFIX` | PV 前缀 | `LAB:` |
| `EPICS_INSTRUMENT_CONFIG` | 仪器画像路径 | `c08_instrument.json` |
| `EPICS_INSTRUMENT_DRY_RUN` | `1` = 演习模式（检查照做、不真写） | `0` |
| `EPICS_MCP_HOST` / `EPICS_MCP_PORT` | MCP 服务（SSE）监听地址 | `127.0.0.1` / `42521` |
| `EPICS_CA_ADDR_LIST` | **建议留空**（见附录 C 第 21 条那个大坑） | 空 |
| `MPLCONFIGDIR` | matplotlib 缓存目录（无家目录的服务器上需要设置） | — |

---

## F.5 重建这份文档

```bash
cd docs/epics-course
python tools/build_docx.py            # 生成 DOCX + PDF
python tools/build_docx.py --check    # 只自检（链接是否失效）
```

改完任意一章的 Markdown 后重跑一遍即可。**文档和代码同源、可重建**——
这也是第 9.6 节"配置即代码"的实践。
