# 从零到智能体自主实验：EPICS 中文实践教程

> 写给**有软件/数据分析基础、没有硬件知识**的人。
> 目标不是"了解 EPICS"，而是**能把硬件（含散裂中子源的设备）接进智能体，让它自主驱动实验**。
>
> 全部 17 篇文档 + 一套**实测可运行**的代码，一路从"第一个 PV"做到
> "智能体自主完成粗扫→细扫→变温→出报告"。

---

## 🚀 快速开始（10 分钟看到结果）

```bash
# 1) 装 EPICS（一条命令，Windows/macOS/Linux 都有包）
conda create -n epics -c conda-forge epics-base -y
conda activate epics

# 2) 装 Python 依赖
pip install pyepics caproto pyserial matplotlib numpy mcp

# 3) 进代码目录，自检
cd docs/epics-course/code
bash 01_env_check.sh

# 4) 一键启动"虚拟实验站"（IOC + 恒温箱 + 探测器 + PID 控制器 + 开发板仿真）
bash runlab.sh start sim
bash runlab.sh status

# 5) 读、写、订阅
caget LAB:TempRaw LAB:Power LAB:Counts
caput LAB:RunPermit 1 && caput LAB:SetPointTemp 45
camonitor LAB:TempRaw

# 6) 跑安全测试（16 项，应该全过）
python tools_test.py

# 7) 让"智能体"自主完成一次实验（不需要大模型 API）
python c08_agent_demo.py --policy script

# 8) 收工
bash runlab.sh stop
```

> **不需要任何硬件**就可以走完 1~6 步和第 8 章的全部内容
> （仿真桥 `--sim` 会顶替开发板）。

---

## 📚 目录

**入门**
- [第 0 章　导读：这本书怎么用](00-导读-这本书怎么用.md)
- [第 1 章　软件工程师眼中的硬件](01-软件工程师眼中的硬件.md)
- [第 2 章　EPICS 是什么：一张图看懂](02-EPICS是什么-一张图看懂.md)

**动手**
- [第 3 章　搭实验台：30 分钟跑起第一个 IOC](03-搭实验台-30分钟跑起第一个IOC.md)
- [第 4 章　记录与数据库：EPICS 的日常](04-记录与数据库-EPICS的日常.md)
- [第 5 章　Python 客户端：从 `caget` 到可靠读写](05-Python客户端-从caget到可靠读写.md)
- [第 6 章　第一个闭环：虚拟恒温箱与 PID](06-第一个闭环-虚拟恒温箱与PID.md)
- [第 7 章　真硬件：两百多块钱的实验台](07-真硬件-两百元实验台.md)

**核心（你的真正目标）**
- [第 8 章　智能体驱动实验：从 MCP 到自主闭环](08-智能体驱动实验-从MCP到自主闭环.md) ⭐
- [第 9 章　安全与工程化：散裂级别的现实](09-安全与工程化-散裂级别的现实.md) ⭐
- [第 10 章　综合实战：自己的"自动寻峰衍射仪"](10-综合实战-自动寻峰衍射仪.md)

**手册**
- [附录 A　速查表](附录A-速查表.md)
- [附录 B　术语表（中英对照）](附录B-术语表.md)
- [附录 C　踩坑清单（40 条）](附录C-踩坑清单.md)
- [附录 D　资料精读指南（你手上那批官方文档怎么读）](附录D-资料精读指南.md)
- [附录 E　90 天路线图：从零到接入散裂设备](附录E-90天路线图.md)
- [附录 F　配套代码清单（打印版索引）](附录F-配套代码清单.md)

**单文件全文**：[EPICS从零到智能体自主实验-完整书稿.md](EPICS从零到智能体自主实验-完整书稿.md)（方便全文检索）

**打印版**：[EPICS从零到智能体自主实验-完整书稿.docx](EPICS从零到智能体自主实验-完整书稿.docx)（Word 可编辑、**A4 / 104 页 / 目录带真实页码并可 Ctrl+点击跳转 / 页眉显示当前章节**）／[同名 PDF](EPICS从零到智能体自主实验-完整书稿.pdf)（直接打印）。
改完章节后用 `python tools/build_docx.py` 一键重建（脚本会自动重算目录页码）。

---

## 🧩 配套代码（[`code/`](code/)）

| 文件 | 作用 | 验证状态 |
|---|---|---|
| [`01_env_check.sh`](code/01_env_check.sh) | 环境自检：缺什么、怎么补 | ✅ 实测 |
| [`runlab.sh`](code/runlab.sh) | 一键起停整套虚拟实验站 | ✅ 实测（start/status/stop） |
| [`softioc/db/hello.db`](code/softioc/db/hello.db) | 第一个数据库 | ✅ softIoc/softIocPVA 实测 |
| [`softioc/db/lab.db`](code/softioc/db/lab.db) | 完整实验站：18 条记录，含联锁、闭环、限值、枚举、波形 | ✅ 实测（含两处报错复现） |
| [`pv_utils.py`](code/pv_utils.py) | 可靠 PV 封装：连接、读回确认、等待到位、订阅 | ✅ 实测（pyepics 3.5.10） |
| [`c05_pv_explore.py`](code/c05_pv_explore.py) | 一条命令看清 PV 的全部元数据 | ✅ 实测 |
| [`c05_monitor_log.py`](code/c05_monitor_log.py) | 订阅落 CSV（含占位事件过滤） | ✅ 实测（81 次事件记录） |
| [`c05_scan_demo.py`](code/c05_scan_demo.py) | 扫描 + 画图 + 找峰 | ✅ 实测（峰位误差 0.016°） |
| [`c06_thermal_plant.py`](code/c06_thermal_plant.py) | 虚拟恒温箱（一阶热模型） | ✅ 实测（与理论值吻合） |
| [`pid.py`](code/pid.py) | PID（抗饱和 + 微分滤波 + 继电整定） | ✅ 实测 |
| [`c06_run_closed_loop.py`](code/c06_run_closed_loop.py) | 阶跃/闭环/自动整定三种模式 | ✅ 实测 |
| [`c06_pid_service.py`](code/c06_pid_service.py) | 常驻 PID 控制器（带联锁与看门狗） | ✅ 实测 |
| [`c07_pv_bridge.py`](code/c07_pv_bridge.py) | 串口 ↔ EPICS PV 桥（caproto），含 `--sim` | ✅ 实测（仿真模式全流程） |
| [`hardware/esp32_firmware/main.py`](code/hardware/esp32_firmware/main.py) | ESP32 MicroPython 固件（行协议 + 看门狗） | ⚠️ 需真硬件验证 |
| [`hardware/wiring.md`](code/hardware/wiring.md) | 采购清单 + 接线表 + 分步验证 | 📋 清单 |
| [`c08_instrument.json`](code/c08_instrument.json) | 仪器画像（通道/限值/联锁/安全状态） | ✅ 实测 |
| [`c08_safety.py`](code/c08_safety.py) | 安全层（六道检查 + 审计日志 + 急停） | ✅ 16/16 测试通过 |
| [`c08_tools.py`](code/c08_tools.py) | 仪器语义工具（10 个） | ✅ 16/16 测试通过 |
| [`tools_test.py`](code/tools_test.py) | 安全与工具的自动化测试 | ✅ 16/16 通过 |
| [`c08_mcp_server.py`](code/c08_mcp_server.py) | MCP 服务（stdio + SSE） | ✅ 官方 mcp 客户端实测连通 |
| [`c08_agent_demo.py`](code/c08_agent_demo.py) | 智能体自主实验闭环（`script`/`llm` 两种策略） | ✅ 实测完整跑通 |
| [`c08_bluesky_demo.py`](code/c08_bluesky_demo.py) | 同一件事用 ophyd/bluesky 写（正统路线） | ✅ 实测 9 点扫描 |
| [`scan_utils.py`](code/scan_utils.py) | 找峰/质心/FWHM/扫描摘要 | ✅ 实测 |
| [`skills/epics-instrument-control/SKILL.md`](code/skills/epics-instrument-control/SKILL.md) | 蓉仔 skill：智能体的行为约定 | 📋 可直接放进 `agent_skills/` |

**实测环境**：Linux x86_64、EPICS Base **7.0.10**（源码编译版 + conda-forge 7.0.10.0 版）、
Python 3.12、pyepics 3.5.10、caproto 1.3.0、p4p、ophyd/bluesky、mcp 1.17.0。

---

## 🎯 学完你能做什么

- 看懂散裂中子源给你们的任意一份 PV 清单，并写出对应的 `.db` / `instrument.json`；
- 用 Python 可靠地驱动设备（写 → 读回确认 → 等到位 → 记录证据）；
- 做温度/角度闭环，并知道怎么判断"控得好不好"；
- 用两百多块钱的开发板搭一台真设备，接成 EPICS PV；
- **让智能体安全地自主做实验**：安全层六道检查、语义工具、MCP、skill、审计日志；
- 拿出一份能让控制组和安全员放心的评审材料。

---

## ⚠️ 关于安全（重要）

- 本书所有**硬件练习只用低压直流**（5V/12V）。**不要接 220V 市电。**
- 涉及散裂中子源真实设备时：**智能体可以自主做"科学决策"，不可以自主做"安全决策"**。
  详见第 9 章。
- **软件急停不是安全功能**，物理急停才是。任何自动化都不能替代安全联锁与操作规程。

---

## 🤝 怎么用这份材料

- **自学**：按第 0 章的三种节奏走，90 天路线见附录 E。
- **带新人**：第 1~5 章可以直接当培训课（大约 3 次 × 2 小时）。
- **当团队规范**：`c08_instrument.json` + 安全层 + `tools_test.py` + skill
  这一套可以直接搬到你们的真实项目里。
- **对自己的设备做一遍**：把第 10 章的映射表（10.7 节）当成迁移检查单。
