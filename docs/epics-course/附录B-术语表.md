# 附录 B　术语表（中英对照）

> 开会时听到这些词不再茫然。★ 表示"你必须能主动说出来"。

## B.1 EPICS 核心

| 英文 | 中文 | 一句话解释 |
|---|---|---|
| ★ EPICS | 实验物理与工业控制系统 | 一套用于大型实验装置的控制系统软件框架 |
| ★ IOC | 输入输出控制器 | 常驻进程，管着一批 PV 和设备通信 |
| ★ PV (Process Variable) | 过程变量 | 一个有名字、有类型、有元数据的值；`记录名.字段名` |
| ★ Record | 记录 | PV 背后的数据结构与处理逻辑（`ai`/`ao`/`calc`…） |
| Field | 字段 | 记录的一个属性（`VAL`/`EGU`/`DRVL`…） |
| ★ Database (`.db`) | 数据库文件 | 声明"这个 IOC 里有哪些记录"的文本文件 |
| ★ Channel Access (CA) | 通道访问 | EPICS 3 时代的访问协议（UDP 搜索 + TCP 数据） |
| ★ pvAccess | — | EPICS 7 的新协议，支持结构化数据、高吞吐 |
| ★ CA Gateway | CA 网关 | 跨网段访问 PV 的受控通道（安全隔离的关键） |
| softIoc | 软 IOC | 只有软件记录、不连硬件的 IOC；学习与测试用 |
| softIocPVA | — | 同时提供 CA 与 pvAccess 的 soft IOC |
| iocsh | IOC 交互命令行 | IOC 的 shell，`dbl`/`dbpr` 在这里用 |
| ★ `.dbd` | 数据库定义文件 | 定义记录类型、字段、设备支持 |
| DTYP / Device Support | 设备支持 | "这条记录的值从哪个硬件、用什么方式来" |
| asyn | 异步驱动框架 | EPICS 里一切串口/网络/GPIB 驱动的地基 |
| StreamDevice | 流设备 | 用文本协议文件（`.proto`）驱动仪器 |
| Device driver | 设备驱动 | 真正和硬件说话的那层代码 |
| SCAN | 扫描方式 | 记录何时被处理（Passive/Periodic/I/O Intr/Event） |
| FLNK | 前向链接 | 处理完我接着处理它 |
| CP / CPP | 处理链接 | 源记录处理时也处理本记录 |
| I/O Intr | 中断扫描 | 设备上报时才处理（真硬件首选） |
| ★ Access Security (ASG) | 访问安全 | IOC 侧"谁能写哪个 PV"的规则 |
| Autosave | 自动保存 | IOC 重启后恢复设定值 |
| devIocStats | IOC 统计 | IOC 自身健康监控（CPU/内存/连接数） |
| synApps | — | 常用模块的集合（motor/scaler/sscan/std/calc…） |
| areaDetector | 面探测器框架 | 相机/图像数据流 |
| motor record | 电机记录 | 位置控制（回零、限位、到位判断全都有） |
| sscan | 扫描模块 | 步进扫描、位置列表扫描 |
| Phoebus / CS-Studio | 操作员界面 | 看板、面板、报警界面 |
| `EPICS_HOST_ARCH` | 主机架构 | 决定用 `bin/<arch>/` 下哪套二进制 |

## B.2 信号与硬件

| 英文 | 中文 | 一句话解释 |
|---|---|---|
| ★ AI / AO | 模拟输入 / 模拟输出 | 连续量（电压、温度、位置） |
| ★ DI / DO（BI/BO） | 数字输入 / 数字输出 | 开关量 |
| ADC / DAC | 模数 / 数模转换 | 把电压变成数字、把数字变成电压 |
| Resolution | 分辨率 | 能分辨的最小变化（12 bit / 16 bit） |
| Accuracy vs Precision | 准确度 vs 精度 | 离真值多远 vs 重复读有多散 |
| Offset / Drift | 偏置 / 漂移 | 系统性偏差 / 随时间缓慢变化 |
| Calibration | 标定 | 用已知标准把读数映射为真值 |
| Linearity | 线性度 | 输入输出是否成正比 |
| Noise | 噪声 | 读数里的随机抖动 |
| ★ Filter | 滤波 | 让读数变稳（但引入延迟，且不消除偏差） |
| Range / Span / Full scale | 量程 / 跨度 / 满量程 | 能表示的范围；误差常写成 %FS |
| Saturation | 饱和 | 到极限上不去/下不来 |
| ★ Limit switch | 限位开关 | 撞到就报信的物理开关 |
| ★ Interlock | 互锁/联锁 | 条件不满足就禁止动作 |
| ★ Watchdog | 看门狗 | 心跳停就自动进入安全状态 |
| Deadband / Hysteresis | 死区 / 回差 | 防止在阈值附近抖动 |
| Readback (RBV) | 回读 | 设备实际值，用来验证命令 |
| Settle time | 稳定时间 | 进入容差窗口并不再离开所需时间 |
| Rise time / Overshoot | 上升时间 / 超调 | 控制性能指标 |
| Steady-state error | 静态误差 | 稳定后与设定值的差 |
| Time constant (τ) | 时间常数 | 一阶系统的响应快慢 |
| Duty cycle / PWM | 占空比 / 脉宽调制 | 用开关控制平均功率 |
| Grounding / Common ground | 接地 / 共地 | 不共地 = 神秘故障 |
| Pull-up / Pull-down | 上拉 / 下拉 | 让数字输入有确定电平 |
| Crosstalk / EMI | 串扰 / 电磁干扰 | 线缆和接地问题的主要来源 |
| SCPI | — | 仪器的文本命令标准（`*IDN?`） |
| Modbus RTU/TCP | — | 最常见的工业寄存器协议 |
| RS-232 / RS-485 | — | 串口标准；485 是多点总线 |
| TTL | — | 5V/3.3V 数字电平 |
| RTEMS / vxWorks | — | 实时操作系统，很多 IOC 跑在上面 |

## B.3 控制与实验

| 英文 | 中文 | 一句话解释 |
|---|---|---|
| ★ PID | 比例-积分-微分 | 控制器基本形式 |
| Kp / Ki / Kd | 三个增益 | 比例/积分/微分系数 |
| Windup / Anti-windup | 积分饱和 / 抗饱和 | 执行器到顶后积分继续累积导致的超调 |
| Setpoint (SP) / Process Variable (PV) | 设定值 / 测量值 | 控制回路的输入与反馈 |
| Open loop / Closed loop | 开环 / 闭环 | 有无反馈 |
| Plan | 实验计划 | Bluesky 里"一次实验"的可执行描述 |
| Run | 一次采集 | 一个 plan 执行一次产生的数据集 |
| Scan | 扫描 | 逐点改变参数并采集 |
| ★ θ-2θ scan | θ-2θ 扫描 | 衍射谱仪的标准扫描方式 |
| Counts / Count rate | 计数 / 计数率 | 探测器输出（cps = counts per second） |
| Background | 本底 | 没有信号时的计数 |
| Peak / FWHM | 峰 / 半高全宽 | 峰位与峰宽 |
| Rocking curve | 摇摆曲线 | 单晶衍射里常用的扫描 |
| Alignment | 对轴/准直 | 让样品/光路对准 |
| Sample environment | 样品环境 | 控温/控压/磁场等装置 |
| Shutter | 快门 | 控制光束通断 |
| Beamline | 束线 | 从源到实验站的一条光路 |
| Run permit | 运行许可 | 允许出束/运行的联锁信号 |
| ★ NeXus / HDF5 | — | 科学数据的标准容器格式 |
| Metadata | 元数据 | 描述数据的数据（谁、何时、什么参数） |
| Provenance | 溯源信息 | 数据是怎么产生的 |
| Reproducibility | 可复现性 | 换个人/换个时间能做出同样结果 |

## B.4 本课程自造的概念（你在别处看不到，但在工程上有用）

| 词 | 含义 |
|---|---|
| **仪器画像** | `c08_instrument.json`：把设备的通道、单位、限值、联锁写成数据 |
| **语义工具** | `set_temperature(60)` 这种"动作级"接口，而不是裸 PV 写操作 |
| **安全层** | 白名单/限值/单步/联锁/速率/审计六道检查的唯一写入口 |
| **证据（evidence）** | 动作返回里的 `actual`、`elapsed_s`、`data_file` —— 让"我说做了"变成"数据证明做了" |
| **两段式扫描** | 先粗扫定位，再在峰附近细扫 |
| **dry-run 演习** | 所有检查照做，但不真正写设备 |
| **分层防护** | 物理/设备/IOC/软件/智能体/流程 六层，每层都会失效，所以每层都要有下一层 |
