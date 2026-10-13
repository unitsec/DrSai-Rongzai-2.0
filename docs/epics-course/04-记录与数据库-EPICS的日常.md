# 第 4 章　记录与数据库：EPICS 的日常

> **本章目标**：看懂并写出真实可用的 `.db` 文件。读完你应该能：
> 拿到一份陌生人写的数据库文件，在 10 分钟内说清"哪个 PV 从哪来、什么时候更新、
> 报警在哪里、联锁怎么起作用"；并且能自己加一条新记录而不破坏现有逻辑。
>
> 本章所有结论都在 EPICS Base 7.0.10 上实测过，包括它**拒绝加载**的那两种写法。

配套代码：[`code/softioc/db/lab.db`](code/softioc/db/lab.db)（一个模拟实验站的完整数据库）。

---

## 4.1 三十秒学会 `.db` 语法

整个 EPICS 数据库语法只有两条规则：

```c
/* ① 注释这样写（C 风格）；也可以用 # 开头 */
record(记录类型, "PV名字") {
    field(字段名, "值")
    field(字段名, "值")
}
```

- 记录类型：`ai` `ao` `bi` `bo` `calc` …（第 4.2 节的表）
- PV 名字：**双引号里可以写宏**，加载时替换。
- 字段：名字在引号外，值在引号内；**末尾没有逗号，也没有分号**。

### 宏（macro）：一个文件服务一百台设备

```c
record(ai, "$(P)Temperature") {
    field(DESC, "$(SITE) $(P) 样品温度")
}
```

加载时传入宏：

```bash
# 命令行方式
softIoc -d db/lab.db -m "P=LAB:,SITE=CSNS"

# 启动脚本方式（更常用）
dbLoadRecords("db/lab.db", "P=BL01:,SITE=CSNS")
```

同一份 `.db` 用不同的 `P` 就能在 20 条束线上跑出一百个 IOC。**这是 EPICS 最重要的复用机制**：
永远不要为了某台设备去复制粘贴一份 `.db` 再手改 PV 名。

> ⚠️ 实测坑：宏用了但没传值，记录名里会留下字面的 `$(P)`，而 IOC 只会给出很难懂的报错。
> 写完 `.db` 一定跑一遍 `dbl`（列记录），肉眼确认 PV 名字对。

### 立刻看看你的数据库长什么样

在 iocsh（IOC 的交互式命令行）里：

```
dbl                       # 列出所有记录：名字 + 类型
dbpr("LAB:SetPoint", 5)   # 打印这条记录的所有字段（5 = 详细级别）
```

`dbpr` 是你在没有图形界面时最好用的排查工具——它把 IOC 内部那条记录的
每个字段都摊开给你看（包括你没在 `.db` 里写的默认值）。

---

## 4.2 记录类型全景：你只需要记住 15 个

EPICS Base 7.0.10 一共提供 34 种记录类型。实际项目里 95% 用的是下面这些：

### ① 输入类（值来自外界）

| 记录 | 用途 | 典型场景 |
|---|---|---|
| `ai` | 模拟输入（浮点） | 温度、压力、位置回读、光强 |
| `bi` | 位/布尔输入 | 限位、门开关、就绪信号 |
| `mbbi` | 多态输入（枚举） | 设备状态：Idle/Running/Error |
| `longin` | 整数输入 | 计数、编号、状态码 |
| `stringin` | 字符串输入 | 样品名、错误信息、设备 ID |
| `waveform` | 数组输入 | 波形、谱图、图像一行 |
| `aai` | 模拟数组（只读数组） | 高频采样缓冲区 |
| `subArray` | 数组切片 | 从大缓冲里取一段 |
| `int64in` | 64 位整数输入 | 大计数、时间戳 |

### ② 输出类（你把值写出去）

| 记录 | 用途 | 典型场景 |
|---|---|---|
| `ao` | 模拟输出 | 设定温度、加热功率、电机目标位置 |
| `bo` | 位输出 | 开/关快门、继电器、使能 |
| `mbbo` | 多态输出 | 选择运行模式 |
| `longout` | 整数输出 | 设定点数、选择通道号 |
| `stringout` | 字符串输出 | 下发命令、设置样品名 |

### ③ 计算类（值由 EPICS 自己算）

| 记录 | 用途 | 为什么值得学 |
|---|---|---|
| `calc` | 表达式计算 | 单位换算、线性组合、简单判断 |
| `calcout` | 计算 + 输出链接 | 既能算又能往外写、还能控制写入时机 |
| `fanout` / `dfanout` | 一对多分发 | 一次触发多个记录处理 |
| `seq` | 小型状态机/序列 | 把"先 A 再 B 再 C"写进 IOC |
| `sub` / `aSub` | 自定义子程序 | 需要 C 代码时用（你可以先跳过） |
| `compress` | 压缩/统计 | 取平均、最大最小、直方图 |
| `histogram` | 直方图 | 计数率分布 |
| `printf` | 格式化输出 | 生成文本日志 |
| `event` | 软件事件 | 手动触发一批记录 |
| `sel` | 选择器 | 从多个输入里选一个 |
| `state` | 字符串状态机 | 更复杂的流程控制 |

> 🎯 **新手路线**：先彻底掌握 `ai` `ao` `bi` `bo` `calc` `mbbi` `mbbo` `waveform` 这 8 个，
> 其余用到再查。**不要**一上来就研究 `sub`/`aSub`。

---

## 4.3 字段解剖：通用字段（所有记录都有）

| 字段 | 含义 | 新手要注意什么 |
|---|---|---|
| `DESC` | 描述 | **最多 41 字节**！中文一个字 3 字节，写超过 13 个汉字 IOC 直接拒绝加载 |
| `EGU` | 工程单位 | 不写单位等于没写文档；界面上没有单位的数字最害人 |
| `PREC` | 显示精度（小数位） | 只影响显示，不影响存储精度 |
| `HOPR` / `LOPR` | 显示上下限 | 只给操作界面用，**不是**"能写到哪" |
| `SCAN` | 什么时候处理 | 见 4.6 节，误区最多的地方 |
| `DTYP` | 用哪个设备支持 | `"Soft Channel"` = 软记录（不连硬件） |
| `INP` / `OUT` | 输入/输出链接 | 可以是硬件地址（`@asyn(...)`），也可以是另一个 PV |
| `FLNK` | 前向链接 | "处理完我，接着处理它"——用得好能省一堆周期任务 |
| `PHAS` | 处理阶段 | 同一次扫描里，先跑 PHAS 小的记录（0..9） |
| `DISV` / `DISA` | 禁用值 / 禁用链接 | `DISA==DISV` 时这条记录不再处理（**联锁常用**） |
| `SDIS` | 扫描禁用链接（仅输入记录） | 比 DISA 更常用：外部信号为 1 就停止扫描 |
| `PINI` | 启动时是否处理 | 需要"上电即算一次"的记录写 `YES` |
| `TSE` | 时间戳来源 | `-2` 用设备时间戳（真设备时很重要） |
| `STAT` / `SEVR` | 报警状态 / 等级 | 只读，由 IOC 维护（见 4.7） |

## 4.4 输入记录（`ai`/`bi`）专属字段

| 字段 | 含义 | 说明 |
|---|---|---|
| `ADEL` / `MDEL` | 死区（绝对/百分比） | 值变化超过死区才通知订阅者。**是省网络的关键**，不是"精度" |
| `SMOO` | 平滑系数 | 0 关闭；0.8 表示新值的权重只有 20%（一阶低通）。用来压噪声，也会带来滞后 |
| `RVAL` | 原始值 | 设备给的原始读数（比如 ADC 码值） |
| `ROFF` | 原始偏移 | 换算：`VAL = ASLO * (RVAL + ROFF) + AOFF` |
| `ASLO` | 斜率 | 上面那个换算的斜率——**标定系数就放这里** |
| `AOFF` | 偏移 | 上面那个换算的偏移 |
| `LINR` | 线性化 | 非线性传感器（热电偶、热敏电阻）可以用断点表 |
| `HIHI`/`HIGH`/`LOW`/`LOLO` | 报警阈值 | 见 4.7 |
| `HYST` | 报警回差 | 防止在阈值附近反复报警/恢复 |

实测（第 4.11 节会复现）：`ai` 配 `DTYP "Soft Channel"` 时，如果 `INP` 指向
**另一个 PV**，那么每次处理就从那个 PV 取值（这就是软 IOC 里"记录联动"的标准做法）；
如果 `INP` 是常数，则 `VAL` 由客户端写入决定。

## 4.5 输出记录（`ao`/`bo`）专属字段

| 字段 | 含义 | 说明 |
|---|---|---|
| `DRVL` / `DRVH` | 驱动上下限 | **客户端写超限会被 IOC 截断到这个范围**（实测：写 300 到 `DRVH=150` 的记录，读回 150） |
| `OMSL` | 输出模式 | `"supervisory"`（客户端写）或 `"closed_loop"`（由 `DOL` 决定） |
| `DOL` | 闭环输入链接 | `OMSL=closed_loop` 时，处理记录会用 `DOL` 的值覆盖 `VAL` |
| `IVOA` | 输入无效时怎么办 | `"Continue normally"` / `"Don't drive outputs"` / `"Set output to IVOV"` |
| `IVOV` | 无效时的输出值 | **这是"安全默认值"的落点**：出问题时输出 0 还是保持？ |
| `OROC` / `RVAL` | 原始输出、校准 | 和输入的 `ASLO/AOFF` 类似，方向相反 |

> ⚠️ **实测坑**：菜单型字段（`OMSL`、`IVOA`、`SCAN`）的字符串**跟 EPICS 版本有关**。
> 在 7.0.10 里 `OMSL` 的合法值是 `"supervisory"` 和 `"closed_loop"`（全小写）；
> 写 `"Closed Loop"` 会报 `Illegal choice ... Did you mean "closed_loop"?`。
> 换一个版本可能是别的写法。**永远以本机 `dbd` 文件或在线文档为准，不要抄老教程。**

---

## 4.6 什么时候处理？——五个机制搞清楚，你就超过一半同事了

一条记录不会"自动"更新。它必须被**处理（process）**。触发方式有五种：

| 方式 | 写法 | 什么时候更新 | 用在哪 |
|---|---|---|---|
| **周期扫描** | `SCAN = ".1 second"` ~ `"10 second"` | 定时 | 慢变量的常规采集、控制运算 |
| **被动** | `SCAN = "Passive"` | 被写、被 `FLNK` 推到、或被 `CP` 链接带上 | 大多数输出记录、事件驱动的输入 |
| **中断** | `SCAN = "I/O Intr"` | 设备主动上报时 | **真硬件首选**（串口/网络设备推数据） |
| **前向链接** | `FLNK` | 上一条记录处理完就轮到我 | 把一串计算串起来，省周期任务 |
| **事件** | `SCAN = "Event"` + `EVNT` | 有人 `post_event(n)` 时 | 需要"一批记录同时更新" |

### 新手必踩的坑：为什么我的 `calc` 永远不更新？

看 [lab.db](code/softioc/db/lab.db) 里这条联锁记录：

```c
record(calc, "$(P)HeaterEnable") {
    field(INPA, "$(P)RunPermit")
    field(INPB, "$(P)OverTemp")
    field(CALC, "A&&!B")
}
```

**它是 `Passive` 的**，而没有任何东西推它 → 它永远保持初始值 0，哪怕
`RunPermit=1`、`OverTemp=0`。你第一次观察到的现象会是"联锁一直是 0，加热死活开不了"。

三种修法（我们最后同时用了前两种，这也是工程上的标准做法）：

```c
/* 方法 1：给它一个周期，让联锁自己算 */
field(SCAN, ".2 second")

/* 方法 2：让上游记录处理完主动推它（更精确、更省资源） */
record(calc, "$(P)OverTemp") { ... field(FLNK, "$(P)HeaterEnable") }

/* 方法 3：给上游链接加 CP 后缀，源记录处理时顺带处理依赖它的记录 */
field(INPA, "$(P)RunPermit CP")
```

> 💡 记住这句话：**"这条记录凭什么被处理？"** 每次写下一条记录都问一遍。
> 这是 EPICS 里 80% "值不更新"问题的答案。

### 关于 `CP` / `CPP`

- `CP`：源记录处理时，**也处理**这条记录（并把新值读过来）。
- `CPP`：在 `CP` 基础上，如果这条记录自己是 Passive，处理后仍然保持 Passive。
- 用在"我已经知道源记录一定会更新"的输入链接上，可以省掉周期扫描。

### `I/O Intr` 为什么在真设备上是首选？

因为它把"轮询"换成了"设备一有数据就处理"：
- 不会漏掉快跳变（限位闪一下就恢复）；
- 不会浪费总线带宽；
- 延迟最低。

代价是：**它依赖设备支持（`asyn` + `StreamDevice` 之类）真的会推**。
如果你把一条记录设成 `I/O Intr` 而底层驱动不会上报，那它就一直不动——
比 `Passive` 更隐蔽。**软 IOC 里没有中断源**，所以我们的 `lab.db` 用 `Passive`。

---

## 4.7 报警系统：不用你写 `if`，IOC 自己管

EPICS 把报警做进了数据模型。任何一条记录都有：

- **阈值字段**：`LOLO` / `LOW` / `HIGH` / `HIHI`
- **等级字段**：`LLSV` / `LSV` / `HSV` / `HHSV`（`NO_ALARM` / `MINOR` / `MAJOR` / `INVALID`）
- **状态字段**：`STAT`（为什么报警）、`SEVR`（多严重，取所有报警里最严重的）
- **回差**：`HYST`（防止在阈值上下反复抖动）

实测（第 4.11 节可复现）：

```
$ caput LAB:TempRaw 100
$ caget LAB:TempRaw
LAB:TempRaw                    100
$ caget -t LAB:TempRaw.SEVR LAB:TempRaw.STAT LAB:TempRaw.HIHI
MAJOR
HIHI
95
```

**三个必须知道的细节**：

1. **`UDF` 也是报警**。记录从没被写过时 `STAT=UDF`、`SEVR=INVALID`，
   读出来是 `0` 或垃圾值。真设备上设备支持会在第一次成功读取后清掉它。
   如果你看到 `INVALID / UDF`，**不要相信这个数**。
2. **报警是给谁看的**：操作界面（Phoebus）会按 `SEVR` 染色；
   报警服务器会汇总；你的 Python 也可以读 `SEVR` 做判断——
   但**不要**在 Python 里重新实现一套 `if temp > 80`，那会和 IOC 里的阈值打架。
3. **软 IOC 的报警不会自己算**：只在你写入（或它处理）时才更新。

---

## 4.8 枚举记录：让 PV 自己说人话

`mbbi`/`mbbo` 让你存"状态"而不是"数字"：

```c
record(mbbo, "$(P)StateSel") {
    field(DESC, "选择实验阶段")
    field(ZRST, "Idle")      /* 0 对应的字符串 */
    field(ONST, "Heating")   /* 1 */
    field(TWST, "Soaking")   /* 2 */
    field(THST, "Done")      /* 3 */
    field(ZRVL, "0") field(ONVL, "1") field(TWVL, "2") field(THVL, "3")
}
```

客户端可以直接写字符串，也可以写数字：

```bash
$ caput LAB:StateSel Soaking     # 写字符串
$ caput LAB:StateSel 2           # 等价
$ caget LAB:StateSel
LAB:StateSel                    Soaking
```

**为什么值得用**：人工操作时不会记错数字；日志里是可读的状态名；
客户端（包括大模型）不需要额外查表就知道 `2` 是什么意思。

---

## 4.9 数组：`waveform` 与探测器数据

`waveform` 是"一维数组"记录，探测器的一帧、示波器的一条轨迹都能放：

```c
record(waveform, "$(P)Trace") {
    field(DESC, "一维数据缓冲")
    field(DTYP, "Soft Channel")
    field(NELM, "1024")      /* 数组长度 */
    field(FTVL, "DOUBLE")    /* 元素类型：DOUBLE/LONG/STRING/FLOAT... */
    field(EGU,  "counts")
}
```

```bash
$ caput -a LAB:Trace 5 1 2 3 6 4     # 写前 5 个元素
$ caget -a LAB:Trace | head -1
LAB:Trace 1024 1 2 3 6 4 0 0 0 ...
```

真设备上用 `waveform` + `areaDetector`/`compress` 传图像和谱图。
**注意**：PV 传大数组要用 pvAccess（CA 不是为大数据设计的），
这也是散裂的图像数据走 pvAccess 的原因。

---

## 4.10 访问控制：Access Security（ASG）

CA 协议本身**没有认证、没有加密**——任何能连到网络的客户端都能读写任何 PV。
生产系统的安全靠两件事：

1. **网络隔离**（控制网与办公网分开，中间只放 CA Gateway）；
2. **Access Security（ASG）**：IOC 侧的规则，能限制"谁能写哪个 PV"。

基本用法（在启动脚本里）：

```c
asSetFilename("/path/to/access.acf")
dbLoadRecords("db/lab.db", "P=BL01:,ASG=BL01_RW")
iocInit()
```

`.acf` 文件里定义规则，例如"只有操作员组的机器能写 MOSET，其它客户端只读"。

**你现在该做的**：知道它存在、知道真设备上它一定开着、
并把"我这个账号/我这台机器能不能写"当成需求去问控制组（第 9、10 章会给清单）。
**不要**在开发阶段偷偷绕过 ASG——那等于在测试环境和生产环境用两套不同的安全模型。

---

## 4.11 把 `lab.db` 跑起来，逐条验证

```bash
cd docs/epics-course/code/softioc
softIocPVA -S st.cmd          # 或者：softIoc -S st.cmd（只有 CA，没有 pvAccess）
```

> ⚠️ 两个实测过的坑：
> - 用 `softIoc -S st.cmd` 且**标准输入是 /dev/null** 时，shell 读到 EOF 会立刻退出，
>   看起来像"启不动"。加 `-S` 就不会进入交互模式了。
> - `softIoc` **只有 CA**；要让 `pvget/pvput/pvmonitor` 也能用，必须用 `softIocPVA`，
>   并且保持标准输入打开（例如 `sleep 1000 | softIocPVA st.cmd`）。

另开一个终端，逐条跟着做（这些都是我实测的输出）：

```bash
$ caget LAB:Counter LAB:Wave
LAB:Counter                    4
LAB:Wave                       3.58678

$ caput LAB:TempRaw 65           # 写"传感器"
$ caget LAB:TempRaw LAB:TempK LAB:OverTemp
LAB:TempRaw                    65
LAB:TempK                      338.15        ← FLNK 链条自动算出来的
LAB:OverTemp                   0

$ caput LAB:SetPointTemp 300     # 故意超范围（DRVH=150）
$ caget LAB:SetPointTemp
LAB:SetPointTemp               150           ← 被 IOC 截断到驱动上限

$ caput LAB:SetPointTemp -5
$ caget LAB:SetPointTemp
LAB:SetPointTemp               0             ← 被截断到驱动下限

$ caput LAB:RunPermit 1 ; caput LAB:TempRaw 20 ; sleep 0.5
$ caget LAB:RunPermit LAB:OverTemp LAB:HeaterEnable LAB:HeatCalc LAB:Heater
LAB:RunPermit                  Permitted
LAB:OverTemp                   0
LAB:HeaterEnable               1
LAB:HeatCalc                   0
LAB:Heater                     0

$ caput LAB:TempRaw 90           # 超温
$ caget LAB:OverTemp LAB:HeaterEnable LAB:Heater
LAB:OverTemp                   1
LAB:HeaterEnable               0             ← 联锁立刻生效
LAB:Heater                     0

$ caput LAB:Heater 77            # 硬写一个 closed_loop 输出
$ caget LAB:Heater
LAB:Heater                     0             ← 被 DOL 覆盖，客户端写不进去

$ caput LAB:StateSel Soaking
$ caget LAB:StateSel LAB:State
LAB:StateSel                   Soaking
LAB:State                      Soaking
```

**这六组实验浓缩了 EPICS 数据库的全部日常**：
单位换算、前向链接、驱动限值、联锁、闭环覆盖、枚举。
你在散裂看到的每一个 PV，都是这些机制的某种组合。

---

## 4.12 练习

### 练习 4-1（单位换算）★
给 `lab.db` 加一条 `calc` 记录，把温度换算成华氏度（`A*9/5+32`），
单位 `degF`，精度 1 位。用 `caget` 验证 `100 ℃ = 212 ℉`。

### 练习 4-2（报警阈值）★★
给 `LAB:TempRaw` 增加 `LOLO=5`、`LOLO` 等级 `MAJOR`、`HYST=1`，
然后用 `caput` 依次写 6、4、4.5、6，观察 `SEVR`、`STAT` 的变化。
**问**：`HYST` 在这里起什么作用？（提示：试出不带回差时会怎样抖。）

### 练习 4-3（驱动限值 vs 显示限值）★★
把 `LAB:SetPointTemp` 的 `DRVH` 改成 60、`HOPR` 改成 200，
然后 `caput` 80。**问**：读回是多少？为什么不是 80 也不是 200？
（这题理解透了，你就不会再混淆"界面范围"和"能写到哪"。）

### 练习 4-4（把联锁做进 IOC）★★★
现在 `LAB:Power`（客户端控制的加热功率）**没有** IOC 级联锁——
只靠 Python 安全层拦着。请把它改成"必须 `HeaterEnable=1` 才能写"的形式。
提示：用 `calcout` 记录，把 `DOL` 指向一个"经过门控的功率"计算，
再把 `Power` 的 `OMSL` 设为 `closed_loop`；或者给 `Power` 加 `DISA/DISV` 联锁。
**验收**：`RunPermit=0` 时 `caput LAB:Power 50` 之后，实际功率必须为 0。

### 练习 4-5（前向链接 vs 周期扫描）★★
把 `LAB:TempK` 的 `SCAN` 从周期扫描改成 `Passive` + 靠 `TempRaw` 的 `FLNK` 驱动，
再用 `camonitor LAB:TempK` 观察它是否还跟着温度变。
**问**：这两种方式在"温度没变化时"的行为有什么不同？
（提示：周期扫描照样每秒处理，只是值没变不推送。）

### 练习 4-6（真设备 PV 表）★★★
找一台你实验室里的真实设备（温控仪、真空计、电机控制器），
向控制组要它的 PV 清单，然后填这张表：

| PV | 类型 | 单位 | 量程 | 只读? | 到位/稳定判据 | 安全默认值 | 联锁前提 |
|---|---|---|---|---|---|---|---|
| | | | | | | | |

**这就是第 8 章 `c08_instrument.json` 的原型。**
填不出来的时候，说明你还没搞清楚那台设备——先别写代码。

---

## 4.13 本章的坑（都是实测踩过的）

| 现象 | 真实原因 | 修法 |
|---|---|---|
| IIS 报 `Can't set 'X.DESC' ... String is too long` | `DESC` 上限 **41 字节**，中文 3 字节/字 | 描述控制在 13 个汉字内，或改用 `EGU`+命名表达 |
| `Illegal choice`（`OMSL`/`IVOA`/`SCAN`） | 菜单字符串随版本变化 | `7.0.10` 里 `OMSL` 是 `supervisory`/`closed_loop`（小写）；查本机 `dbd` |
| 整个 `.db` 加载失败，但只报一行错 | **一条记录出错会导致整个文件加载失败**，`iocInit` 没跑 | 看 IOC 启动日志第一屏；加载后立刻 `dbl` 确认条数 |
| `calc` 死活不更新 | 它是 `Passive`，没人推它 | 加 `SCAN` 或 `FLNK` 或 `CP` |
| 写进去的值"没保存" | 记录是 `OMSL=closed_loop`，`DOL` 每次处理都会覆盖 | 改 `OMSL`，或改 `DOL` 指向；这也是一种联锁 |
| `I/O Intr` 的记录永远不动 | 底层设备支持不会上报 | 改用周期扫描 + `asyn` 轮询，或修好驱动的中断支持 |
| `caget` 读到 0 但其实"没值" | `UDF=1`（从未被写过），`STAT=UDF`/`SEVR=INVALID` | 检查 `SEVR`；真设备要配置 `PINI` 或让驱动先读一次 |
| 两个客户端互相"抢"同一个输出 | 没有仲裁机制，谁最后写谁赢 | 用 `OMSL=closed_loop` 把控制权收进 IOC |

---

## 4.14 自检

- [ ] 我能不看文档写出 `ai`/`ao`/`calc`/`mbbi` 各一条记录，单位、限值、精度齐全。
- [ ] 我知道 `DESC` 41 字节这个限制，也知道菜单字符串要看版本。
- [ ] 我能解释"这条记录凭什么被处理"，并说出三种触发方式。
- [ ] 我知道 `DRVL/DRVH` 在 IOC 侧就会截断客户端的写入。
- [ ] 我能说清 `UDF` 报警意味着什么。
- [ ] 我能把一台真实设备的 PV 表填出来。

**下一章**：用 Python 和命令行读这些 PV——包括"怎么等设备真的到位"、
"为什么订阅比轮询靠谱"，以及在同一台机器上跑多个 IOC 时那个会让你查一整天的网络坑。
