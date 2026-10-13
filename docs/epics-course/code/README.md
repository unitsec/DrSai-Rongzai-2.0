# 配套代码使用说明

> 所有代码都在 EPICS Base 7.0.10 + Python 3.12 上实测跑过。
> 遇到问题先跑 `bash 01_env_check.sh`。

## 依赖

```bash
# EPICS（二选一）
conda create -n epics -c conda-forge epics-base -y && conda activate epics
# 或者：按第 3 章路线 B 从源码编译 base-7.0.10

# Python
pip install pyepics caproto pyserial matplotlib numpy
pip install mcp                 # 第 8 章的 MCP 服务
pip install openai              # 只有要用真大模型时才需要
pip install ophyd bluesky       # 只有跑 c08_bluesky_demo.py 时才需要
```

## 三条最常用的命令

```bash
cd docs/epics-course/code

bash runlab.sh start sim     # 启动整套虚拟实验站
bash runlab.sh status        # 看状态 + 抽查 PV
bash runlab.sh stop          # 收工（先把加热写回 0 再停进程）
```

## 各文件怎么跑

| 文件 | 命令 | 说明 |
|---|---|---|
| `01_env_check.sh` | `bash 01_env_check.sh` | 环境自检 |
| `softioc/st.cmd` | `cd softioc && softIocPVA -S st.cmd` | 只起 IOC（前台，能看到 iocsh） |
| `c05_pv_explore.py` | `python c05_pv_explore.py LAB:TempRaw` | 看一个 PV 的全部元数据 |
| `c05_monitor_log.py` | `python c05_monitor_log.py LAB:TempRaw --seconds 20` | 订阅落 CSV |
| `c05_scan_demo.py` | `python c05_scan_demo.py --start 15 --stop 25 --step 1` | 扫描 + 画图 + 找峰 |
| `c06_thermal_plant.py` | `python c06_thermal_plant.py` | 虚拟恒温箱（需要 IOC 先起） |
| `c06_detector_sim.py` | `python c06_detector_sim.py` | 虚拟探测器（高斯峰） |
| `c06_pid_service.py` | `python c06_pid_service.py --kp 2 --ki 0.08` | 常驻 PID 控制器 |
| `c06_run_closed_loop.py` | `python c06_run_closed_loop.py --mode closed --setpoint 60` | 阶跃/闭环/自动整定 |
| `c07_pv_bridge.py` | `python c07_pv_bridge.py --sim` 或 `--port /dev/ttyUSB0` | 开发板 ↔ PV 桥 |
| `c08_tools.py` | `python c08_tools.py --self-check` / `--snapshot` / `--hold 45` | 语义工具直接当命令行用 |
| `tools_test.py` | `python tools_test.py` | **安全测试（16 项）** |
| `c08_agent_demo.py` | `python c08_agent_demo.py --policy script` | 智能体自主实验（无需大模型） |
| `c08_mcp_server.py` | `python c08_mcp_server.py`（stdio）或 `--sse --port 42521` | 给蓉仔接的 MCP 服务 |
| `c08_bluesky_demo.py` | `python c08_bluesky_demo.py` | ophyd/bluesky 版扫描 |

## 目录结构

```
code/
├── runlab.sh                 一键起停整套实验站
├── 01_env_check.sh           环境自检
├── pv_utils.py               可靠 PV 封装（贯穿全书）
├── pid.py                    PID 控制器
├── scan_utils.py             找峰/质心/FWHM
├── softioc/
│   ├── st.cmd                启动脚本
│   └── db/{hello.db,lab.db}  数据库
├── c05_*.py                  第 5 章：客户端与扫描
├── c06_*.py                  第 6 章：仿真设备与闭环
├── c07_pv_bridge.py          第 7 章：硬件桥
├── c08_*.py / c08_*.json     第 8 章：安全层、语义工具、MCP、智能体
├── tools_test.py             安全测试
├── hardware/                 固件与接线说明
├── skills/                   蓉仔 skill
├── logs/                     审计日志、进度日志、控制器日志（运行时生成）
├── data/                     扫描数据与报告（运行时生成）
└── .run/                     进程 PID 文件（运行时生成）
```

## 环境变量

| 变量 | 用途 | 默认 |
|---|---|---|
| `PYTHON` | 用哪个 Python 解释器起服务 | `python3` |
| `SOFTIOC` | 用哪个 IOC 可执行文件 | 自动找 `softIocPVA` → `softIoc` |
| `EPICS_PREFIX` | PV 前缀 | `LAB:` |
| `EPICS_INSTRUMENT_CONFIG` | 仪器画像路径 | `c08_instrument.json` |
| `EPICS_INSTRUMENT_DRY_RUN` | `1` = 演习模式（检查照做，不真写） | `0` |
| `EPICS_MCP_HOST` / `EPICS_MCP_PORT` | MCP 服务（SSE）监听地址 | `127.0.0.1` / `42521` |
| `EPICS_CA_ADDR_LIST` | **建议留空**（见附录 C 第 21 条） | 空 |
| `MPLCONFIGDIR` | matplotlib 缓存目录（无家目录的服务器上需要） | — |

## 排错

1. `PV 连不上` → `bash runlab.sh status`，再看 `logs/ioc.log`。
2. `pvget 超时` → 你起的是不是 `softIocPVA`？
3. `读不到某个 PV` → 是不是手工设了 `EPICS_CA_ADDR_LIST`（附录 C 第 21 条）。
4. `MCP 客户端报 Invalid JSON` → 有东西往 stdout 打印了（第 8.5 节）。
5. 全都乱了 → `bash runlab.sh stop`，`rm -rf .run logs data`，重来一遍。
