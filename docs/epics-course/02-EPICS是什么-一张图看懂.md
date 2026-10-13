# 第 2 章　EPICS 是什么：一张图看懂

> **本章目标**：建立正确的 EPICS 心智模型。读完你应该能回答：
> "IOC 和记录什么关系？CA 和 pvAccess 有什么区别？我要写代码该从哪一层下手？"
> 本章不需要安装任何东西，但建议对着图把第 1 章的四种信号代进去想一遍。

---

## 2.1 一句话版本，加一张类比表

> **EPICS = 一组常驻的、能读写硬件的"服务器进程"（IOC），加上一种把所有硬件都抽象成"带名字和类型的数据点"（PV）的约定，再加上一套跨平台、跨语言的访问协议（CA / pvAccess）。**

如果你写过 Web 后端，这张对照表能省你两天时间：

| EPICS 概念 | 你熟悉的对应物 | 说明 |
|---|---|---|
| **IOC** (Input/Output Controller) | 一个常驻微服务 | 独立进程，有状态，负责和设备说话 |
| **Record** | 数据库里的一行 / 一个对象 | 有类型、有字段、有处理逻辑 |
| **PV** (Process Variable) | 一个带类型的 REST 端点 + 推送通道 | `PV = 记录名 + 字段名`，如 `BL01:m1.VAL` |
| **.db 文件** | 声明式配置文件 / K8s CRD / Terraform | 描述"这个 IOC 里有哪些记录" |
| **DTYP / device support** | 驱动 / 适配器 | "这个记录的值从哪个硬件来、怎么来" |
| **asyn** | 异步 IO 框架（libuv/asyncio） | EPICS 里所有串口/网络/GPIB 驱动的地基 |
| **CA / pvAccess** | HTTP/1.1 vs gRPC | 两代网络协议，都能用，pvAccess 是新标准 |
| **caget / pvget** | curl | 命令行读 PV |
| **caput / pvput** | curl -X POST | 命令行写 PV |
| **camonitor / pvmonitor** | WebSocket / SSE 订阅 | 命令行订阅 PV 变化 |
| **Phoebus / CS-Studio** | Grafana / Admin 面板 | 操作员界面 |
| **Alarm (STAT/SEVR)** | Prometheus 告警 | 每个 PV 自带告警状态和等级 |
| **Access Security (ASG)** | RBAC | 谁能写哪个 PV |
| **Autosave** | etcd / 持久化 | 重启后恢复设定值 |
| **Soft IOC** | mock server / 本地 fake | 没有真硬件也能跑一个完整的 IOC |
| **IOC Shell (iocsh)** | 交互式 REPL / 容器里的 shell | 启动脚本、在线调试 |
| **`dbLoadRecords()`** | `helm install` / 加载配置 | 把 .db 文件装进运行中的 IOC |
| **`dbl`** | `kubectl get all` | 列出这个 IOC 里所有 PV |

> 💡 **心智模型的金句**：
> **IOC 是服务器，PV 是 API，`.db` 是配置，asyn/StreamDevice 是驱动，CA/pvAccess 是网络。**
> 你作为"应用层"开发者，90% 的时间在跟 **PV** 打交道，剩下 10% 在跟 **`.db` 文件**打交道。

---

## 2.2 全景架构图

```
┌─────────────────────────── 客户端层（你的地盘） ───────────────────────────┐
│  命令行:  caget caput camonitor pvget pvput pvmonitor                     │
│  GUI:     Phoebus / CS-Studio / 网页看板                                   │
│  Python:  pyepics / p4p / caproto        ← 智能体从这里接入                │
│  其他:    Matlab / LabVIEW / C / Java / EPICS 内部模块                     │
└───────────────┬───────────────────────────────────────────────────────────┘
                │  ① CA (Channel Access, 老协议, UDP 搜索 + TCP 数据)
                │  ② pvAccess (EPICS 7 新协议, 结构化数据, 高吞吐)
      ┌─────────┴─────────┐
      │  CA Gateway (可选) │  ← 生产网/开发网之间唯一的、可审计的通道
      └─────────┬─────────┘
                │
┌───────────────┴──────────────────── IOC 层（控制组的地盘，你也要会一点）───┐
│                                                                            │
│   IOC 进程 (softIoc 或编译出来的可执行文件)                                 │
│   ├── IOC Shell (iocsh)       启动脚本 st.cmd: dbLoadRecords / iocInit ...  │
│   ├── 数据库 (Database)       一堆记录: ai ao bi bo calc calcout ...        │
│   │      └── 每条记录 = 名字 + 类型 + 字段 (VAL, EGU, HOPR, SCAN, DTYP...)  │
│   ├── 记录处理 (Processing)   什么时候算、算完通知谁 (SCAN / FLNK / CP)      │
│   ├── Device Support          把"记录"和"驱动"粘起来 (devAiSoft, devMotor...)│
│   └── 驱动/通信层                                                          │
│          asyn ─ StreamDevice ─ modbus ─ motor ─ areaDetector ...            │
│                                                                            │
└───────────────┬────────────────────────────────────────────────────────────┘
                │  ③ 设备级协议
    ┌───────────┼─────────────┬──────────────┬───────────────┐
    ▼           ▼             ▼              ▼               ▼
 串口/RS485   Modbus TCP    以太网/TCP     VME/PCIe      模拟/数字 IO
  (SCPI)      (PLC)        (自定义协议)   (探测器板卡)   (ADC/DAC/光耦)
    │           │             │              │               │
    ▼           ▼             ▼              ▼               ▼
 温控仪      真空/阀门      电机控制器     中子探测器      限位/继电器
```

**这张图是本课程的地图。** 你会依次走过：客户端层（第 5 章）→ 数据库层（第 4 章）→ 设备层（第 7 章）→ 把智能体接在客户端层（第 8 章）。

---

## 2.3 三件套之一：IOC

**IOC = Input/Output Controller**，字面意思是"输入输出控制器"。实际上它就是一个**常驻的、单线程事件驱动的 C/C++ 进程**，职责是：

1. 加载一份数据库定义（哪些 PV 存在）；
2. 按需/按周期读硬件，把值放进记录；
3. 响应客户端请求（读、写、订阅）；
4. 执行记录之间的联动（报警、联锁、计算）。

关键认知：

- **IOC 不等于硬件。** 一个 IOC 可以管 500 个 PV，也可以一个硬件都不连（"soft IOC"）。
- **IOC 是"数据的主人"。** 客户端不直接碰硬件，只跟 IOC 说话。这就是为什么你在办公室写代码，也能安全地读散裂的 PV（只读时）。
- **IOC 通常跑在 Linux 或实时系统（RTEMS/vxWorks）上**，很多是嵌入式单板机，放在机柜里。你大概率不需要碰它们，但需要知道它们**可能很慢、内存很小、不能随便重启**（重启会丢失设备状态）。

---

## 2.4 三件套之二：记录与 PV（全书最重要的概念）

### PV 名 = 记录名 + 字段名

EPICS 里，**一条记录（record）** 是一个结构体；**PV** 是你访问这个结构体某个字段（field）的路径。

```
记录名:    BL01:Sample:Temp
字段名:    VAL
PV 全名:   BL01:Sample:Temp.VAL     ← 完整写法
简写:      BL01:Sample:Temp         ← 省略字段时默认就是 .VAL
```

所以下面两个是同一个东西：

```bash
caget BL01:Sample:Temp        # 默认读 .VAL
caget BL01:Sample:Temp.VAL    # 显式读 .VAL
caget BL01:Sample:Temp.EGU    # 读"工程单位"字段，比如 "degC"
caget BL01:Sample:Temp.HOPR   # 读高报警限
```

### 一条记录长什么样

以最常见的模拟输入记录 `ai` 为例（真实字段列表可以用 `dbdToHtml.pl` 或在线文档查，这里只列你天天要用的）：

```c
record(ai, "BL01:Sample:Temp") {
    field(DESC, "样品温度")
    field(EGU,  "degC")        # 工程单位
    field(DTYP, "Soft Channel")# 用哪个设备支持取值
    field(INP,  "@sim 1")      # 取值来源（硬件地址/链接）
    field(SCAN, "1 second")    # 多久处理一次
    field(PREC, "2")           # 显示几位小数
    field(HOPR, "200")         # 显示上限
    field(LOPR, "-50")         # 显示下限
    field(HIGH, "80")          # 高报警阈值
    field(HIHI, "95")          # 高高报警阈值
    field(ADEL, "0.1")         # 变化超过 0.1 才通知订阅者（死区）
}
```

**你应该从这段代码里看到的东西**：

- 名字里带 `:`，习惯上按 `系统:位置:设备:属性` 分层（具体规范问控制组，散裂一定有自己的约定）。
- `EGU` 是单位。**没有单位的温度值等于没有值。**
- `DTYP` + `INP` 一起回答"这个数从哪来"。
- `SCAN` 回答"多久更新一次"。
- `HIGH/HIHI` 说明**报警是数据模型的一部分**，不是你在 Python 里 `if temp > 80`。

### 会"算"的记录

EPICS 不只有 IO，还有计算记录。比如 `calc`：

```c
record(calc, "BL01:Sample:TempK") {
    field(INPA, "BL01:Sample:Temp")   # 读摄氏
    field(CALC, "A+273.15")           # 内置表达式语言
    field(EGU,  "K")
    field(PREC, "2")
}
```

**这就是为什么"用 EPICS"比"用一堆 Python 脚本读串口"强**：单位换算、报警判断、联锁逻辑可以放在 IOC 里，**所有客户端看到的都是同一个、已经算好的、带单位的、带报警的值**。

---

## 2.5 三件套之三：协议（CA 与 pvAccess）

EPICS 有两代网络协议，**你两个都会遇到**：

| | **Channel Access (CA)** | **pvAccess** |
|---|---|---|
| 年代 | 1990s，随 EPICS 3 诞生 | EPICS 7（2018+）引入 |
| 数据模型 | 值 + 一组固定的元数据字段 | 结构化数据（pvData），可自定义结构体 |
| 传输 | UDP 搜索 + TCP 数据 + 单独的 beacon | TCP（可配 UDP），支持批量、压缩 |
| 典型工具 | `caget` `caput` `camonitor` | `pvget` `pvput` `pvmonitor` |
| 典型 Python | `pyepics`、`caproto` | `p4p`、`pvxs` |
| 大数组/图像 | 能传但不擅长 | 擅长（areaDetector 图像走 pvAccess） |
| 现状 | **存量最大，几乎一切设备都支持** | 新项目首选，EPICS 7 里两者并存 |

实际项目里怎么办？

> **默认用 CA 那一套工具（兼容性最好、你现有的所有设备都支持）；遇到大数组、结构化数据、或者控制组明确说"我们用 pvAccess"，再切到 pvAccess。**
> 好消息：**对应用层来说，两者的用法几乎一样**——都是 `get / put / monitor`。你换协议只改一行 import。

### 客户端-服务器 vs 发布-订阅

- **CA 的搜索机制**：客户端往局域网广播"谁有 `BL01:Temp` 这个 PV？"，IOC 应答"我有，我的 IP:端口是……"。这就是为什么**跨网段必须用 CA Gateway**——广播过不去，而且你也不该让办公网直接看见控制网里的一切。
- **订阅（monitor）**：客户端告诉 IOC "这个 PV 变了就推给我"，IOC 维护订阅列表，值变化（超过 `ADEL`/`MDEL` 死区）就推送。**这是控制系统里最重要的机制**。

---

## 2.6 一个 PV 的完整生命周期

**场景**：你要把样品台加热到 60 °C。

```
你的代码                  IOC                        硬件
   │                       │                          │
   │ caput SetPoint 60     │                          │
   ├──────────────────────►│ 1. 收到写请求             │
   │                       │ 2. 写进记录字段 VAL        │
   │                       │ 3. 触发记录处理 (process)  │
   │                       │ 4. 设备支持把 VAL 换算   │
   │                       ├─────────────────────────►│ 5. 写寄存器/发串口命令
   │  ◄── put callback ────┤    (说明"我已发出去")      │
   │                       │                          │ 6. 温控仪开始加热
   │ camonitor Temp        │                          │
   ├──────────────────────►│                          │
   │                       │◄──── 周期性读取 ─────────┤ 7. 读回当前温度
   │  ◄── 25.1, 26.3 ... ──┤  (SCAN 1 second)         │
   │                       │                          │
   │ 判断 |Temp-60|<0.5 ?  │                          │
   │ 是 → 记录数据/进下一步 │                          │
```

**注意第 5→7 步之间的鸿沟**：`put` 回调只保证"命令发出去了"，**不保证温度到了**。你的代码必须自己等。

---

## 2.7 PV 命名：别自己发明规范

散裂中子源（CSNS）有成千上万个 PV，命名规范是**组织资产**。自己发明命名 = 未来对接时全部重写。

常见的命名风格（举例，**具体以控制组给你的为准**）：

```
系统: 位置:  设备:  实例  :属性
CSNS: BL01:  MC:    m1    : VAL        # 运动控制器 1 号电机当前值
CSNS: BL01:  SE:    tc1   : Temp       # 样品环境 1 号温控读温度
CSNS: BL01:  DET:   panel1: Counts     # 探测器计数
```

**你现在就该做的事**：向控制组要三样东西——
1. 《PV 命名规范》文档（哪怕只是一页 wiki）；
2. 一个真实设备的完整 PV 清单（比如一台电机 + 一个温控）；
3. 一个可以让你练手的**测试 IOC**（或在测试网段里自己跑 softIOC）。

---

## 2.8 EPICS 生态地图：哪些模块你会碰到

| 模块 | 作用 | 你什么时候会用到 |
|---|---|---|
| **EPICS Base** | IOC 核心、记录类型、CA/pvAccess 工具 | 全程 |
| **asyn** | 异步设备通信框架（串口/GPIB/TCP） | 一切自定义硬件的底座 |
| **StreamDevice** | 用文本协议文件驱动串口/网络仪器 | 接温控仪、压力计、老仪器（**极常用**） |
| **modbus** | Modbus RTU/TCP 支持 | 接 PLC、变频器、传感器 |
| **motor** | 电机记录（含回零、限位、到位判断） | 一切运动控制 |
| **scaler** | 计数器/定标器 | 探测器计数 |
| **sscan** | 扫描（步进扫描、位置列表） | "把 theta 从 10 扫到 30" |
| **areaDetector** | 面探测器/相机图像 | 成像、衍射图像 |
| **autosave** | 保存/恢复设定值 | 让 IOC 重启后记得参数 |
| **calc / busy / seq / std** | 计算、状态机、序列 | 把逻辑放进 IOC |
| **devIocStats** | IOC 自身健康监控 | 运维 |
| **pyepics / p4p / caproto** | Python 客户端 | **你的主战场** |
| **ophyd / bluesky / databroker** | 实验编排与数据采集框架（源自 NSLS-II） | **第 8 章：智能体驱动实验的正统路线** |
| **Phoebus / CS-Studio** | 操作员界面 | 人工干预、看板 |

> 🔑 **最重要的一个认知**：真正"驱动实验"的层次不是"写 PV"，而是 **Bluesky plan**（"做一次 θ-2θ 扫描"）。PV 是原子操作，plan 是实验动作。你的智能体将来应该在 plan 层工作（第 8 章详述）。

---

## 2.9 诚实回答：为什么用 EPICS，而不是我自己写个 HTTP 服务？

**用 EPICS 的理由：**

1. 已有几百种设备的驱动写好了（有的设备你可能永远不会用，但散裂的那些一定有）。
2. 多客户端并发、订阅推送、报警、命名空间、访问控制，全都是现成的。
3. 操作员界面（Phoebus）不用你写。
4. 全世界实验室都在用，出问题有人问（tech-talk 邮件列表）。
5. 你的代码和散裂现有系统的对接成本接近 0。

**但也要知道它的代价：**

1. 学习曲线陡（尤其 .db 语法和 C 风格工具链）。
2. C/C++ 工具链、交叉编译（RTEMS）对 Python 开发者不友好。
3. 协议本身**没有加密、没有认证**（靠网络隔离 + Access Security），所以不能暴露到公网。
4. 数据库语法老派，报错信息不友好。

**结论**：**用它**。但把复杂逻辑放在 Python（你的强项），把"数据主权 + 设备通信 + 报警"交给 IOC。

---

## 2.10 练习

### 练习 2-1（读代码，15 分钟）
不用安装任何东西，解释下面每条记录在干什么、它最终会暴露成哪些 PV：

```c
record(ai, "$(P)Temp") {
    field(DESC, "样品温度")
    field(EGU,  "degC")
    field(SCAN, "1 second")
    field(PREC, "2")
    field(HIHI, "95")
    field(HIGH, "80")
}
record(ao, "$(P)Heat") {
    field(DESC, "加热功率百分比")
    field(EGU,  "%")
    field(DRVL, "0")
    field(DRVH, "100")
    field(PREC, "1")
}
record(calc, "$(P)TempF") {
    field(INPA, "$(P)Temp")
    field(CALC, "A*9/5+32")
    field(EGU,  "degF")
}
record(bi, "$(P)DoorOpen") {
    field(DESC, "防护门状态")
    field(ZNAM, "关闭")
    field(ONAM, "打开")
    field(SCAN, "I/O Intr")
}
```

思考题：
1. `$(P)` 是什么？（提示：宏替换，加载时由 `dbLoadRecords` 传入。）
2. 为什么 `DoorOpen` 用 `I/O Intr` 而 `Temp` 用 `1 second`？
3. `Heat` 记录里 `DRVL/DRVH` 是给谁看的？客户端写 150 会怎样？

### 练习 2-2（画图，20 分钟）
用你第 1 章练习 1-1 的信号表，画一张你自己的"架构图"：左边是你的传感器/执行器，中间是你打算怎么读（串口？ADC？），右边是 PV 名。**这张图就是你未来两周的路线图。**

### 练习 2-3（提问作业）
向控制组要这三件事（照抄第 2.7 节）：命名规范、真实 PV 清单、测试 IOC。

---

## 2.11 本章自检

- [ ] 我能解释 IOC / 记录 / PV / `.db` 四者的关系。
- [ ] 我知道 `PV = 记录名 + 字段名`，`.VAL` 是默认字段。
- [ ] 我能说出 CA 和 pvAccess 的三个区别。
- [ ] 我知道 `caput` 成功不代表设备动作完成。
- [ ] 我能说出 Bluesky plan 和 PV 的区别（如果还不能，记住这个词，第 8 章会展开）。

**下一章**：动手。30 分钟内，在你的电脑上跑起第一个 IOC，读出、写入、订阅你的第一个 PV——**不需要任何硬件**。
