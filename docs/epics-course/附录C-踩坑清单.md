# 附录 C　踩坑清单（40 条）

> 打 ★ 的是我在写这本书的过程中**亲手踩过并实测复现**的。
> 每一条都给你"现象 → 病因 → 修法"，可以当速查手册用。

---

## C.1 安装与启动（1–9）

| # | 现象 | 病因 | 修法 |
|---|---|---|---|
| 1 ★ | 一条命令装完最省事 | — | `conda create -n epics -c conda-forge epics-base -y`，Windows/macOS/Linux 都有包 |
| 2 ★ | `softIoc: command not found` | `PATH` 里没有 `bin/$EPICS_HOST_ARCH` | `conda activate epics`；或手工设 `EPICS_BASE` + `PATH` |
| 3 ★ | `make` 报各种找不到头文件 | 缺少 `gcc/g++/make/perl` | `sudo apt install build-essential perl libreadline-dev` |
| 4 ★ | 编译报 `EPICS_HOST_ARCH` 为空 | 环境没设好 | 显式 `export EPICS_HOST_ARCH=$(uname -m 对应值)`，Linux x86_64 是 `linux-x86_64` |
| 5 ★ | `Bad character '$' in Record/Alias name "$(P,undefined)X"` | `-m` 写在了 `-d` **后面** | `-m` 必须在 `-d` 前（帮助里写了 "*subsequent* -d"） |
| 6 ★ | IOC"启动了但立刻退出" | 标准输入是 `/dev/null`，`iocInit()` 后读到 EOF | 用 `-S`；要常驻就 `sleep infinity \| softIocPVA -S st.cmd &` |
| 7 ★ | `pvget` 全部超时，`caget` 正常 | 起的是 `softIoc`（**只有 CA**） | 换 `softIocPVA` |
| 8 ★ | 脚本传路径给 softIoc 后报 `Can't open /tmp/x.iocsh` | 文件真不存在（例如不同 shell 的临时目录不同） | 用绝对路径 + 确认文件存在；别用 `/tmp` 存跨会话文件 |
| 9 | `iocInit()` 报错但看不出哪条记录 | EPICS 的错误信息不够友好 | 在 `st.cmd` 里逐段注释调试；先 `dbLoadRecords` 一个文件再 `dbl` 核对 |

## C.2 数据库与记录（10–20）

| # | 现象 | 病因 | 修法 |
|---|---|---|---|
| 10 ★ | `Can't set 'X.DESC' ... String is too long` | `DESC` **上限 41 字节**，中文 1 字 = 3 字节 | 描述 ≤13 个汉字，或换用 `EGU` + 命名表达 |
| 11 ★ | `Illegal choice ... Did you mean "closed_loop"?` | 菜单字段的字符串**随版本变化**；7.0.10 里 `OMSL` 是小写 | 查本机 `dbd/*.dbd` 或在线文档，别抄老教程 |
| 12 ★ | 整个 `.db` 文件加载失败 | **一条记录出错 = 整个文件失败** | 看启动日志第一屏；加载成功后用 `dbl` 核对条数 |
| 13 ★ | `calc`/联锁记录永远不更新 | 它是 `Passive`，没有任何东西推它 | 加 `SCAN`、或加 `FLNK`、或给上游链接加 `CP` |
| 14 ★ | 客户端写进去的值"没保存" | 该记录 `OMSL=closed_loop`，`DOL` 每次处理都会覆盖 | 改 `OMSL`；或接受它——这本身就是一种联锁 |
| 15 ★ | 写 300 却读回 150 | `DRVL/DRVH` 在 **IOC 侧**截断（不是客户端） | 这是正确行为；要更大范围得改记录 |
| 16 ★ | `caput` 显示了 300，`caget` 却是 150 | `caput` 打印的是你给的值，不是生效值 | 永远以 `caget`/读回为准 |
| 17 ★ | `caget` 读到 0，但其实"没值" | `UDF=1`（从未被写过），`SEVR=INVALID` | 检查 `SEVR`；给安全初值 `field(VAL,"25")` 或用 `PINI` |
| 18 ★ | iocsh 里 `dbl` 报 `No record type` | 行尾写了 `# 注释`，被当成参数 | 注释单独占一行（**iocsh 不支持行尾注释**） |
| 19 | 单位/精度在界面上不对 | 只改了 `PREC` 没改 `EGU`，或反之 | 两者一起改；`PREC` 只影响显示 |
| 20 | 报警一直不触发 | 只设了 `HIGH` 没设 `HSV`，或记录没被处理 | 阈值 + 等级 + 处理触发器，三样都要 |

## C.3 客户端与网络（21–31）

| # | 现象 | 病因 | 修法 |
|---|---|---|---|
| 21 ★★ | **同机多个 IOC，其中一个的 PV 永远读不到** | 客户端设了**单播** `EPICS_CA_ADDR_LIST`（如 `127.0.0.1`）+ `AUTO_ADDR_LIST=NO`；多个 CA server 共用 UDP 5064，单播只送到一个进程 | **别手工设**，用默认自动地址列表（广播）；跨网段就用 CA Gateway |
| 22 ★ | `Channel connect timed out: 'X' not found` | PV 名错 / IOC 没起 / 网络不通 | 先在 IOC 侧 `dbl`；再看环境变量；再 ping |
| 23 ★ | 订阅的第一条是"1990 年、0.0" | monitor 连接期的**占位事件**（EPICS 纪元 631152000） | 过滤 `value is None` 或时间戳等于纪元的事件 |
| 24 ★ | 订阅收不到任何事件 | 值一直没变（monitor **不发心跳**） | 正常现象；要"心跳"就自己记录最后更新时间 |
| 25 ★ | `PV.get()` 返回 `None` 被当成 0 | pyepics 读失败返回 `None` | 用会抛异常的封装（`pv_utils.PV.get`） |
| 26 ★ | 写完之后读回不一致（假故障） | caproto/Python IOC 的值要过几毫秒才更新 | **读回要重试**（`put_checked` 默认重试 0.5 s） |
| 27 ★ | 快速连写同一个 PV，读回乱跳 | 高频写 + IOC 处理延迟 | 限速（本课程安全层默认 5 Hz）；控制系统不是消息队列 |
| 28 | `camonitor` 刷屏 | 该 PV 噪声大 / `ADEL` 太小 | 设 `ADEL`（死区）；或订阅时自己节流 |
| 29 | 时间戳比本地时间差 8 小时 | 混用 EPICS 时间戳（UTC）与本地时间 | 统一转成带时区的 ISO 字符串 |
| 30 | 传大数组失败 | CA 有数组大小限制 | 设 `EPICS_CA_MAX_ARRAY_BYTES`，或改用 pvAccess |
| 31 ★ | 容器里连不上宿主机的 IOC | 容器网络隔离，CA 广播出不去 | `--network host`，或显式设地址列表（注意第 21 条） |

## C.4 智能体与 MCP（32–37）

| # | 现象 | 病因 | 修法 |
|---|---|---|---|
| 32 ★★ | `Invalid JSON: expected ',' or ']' input_value='[14:15:45] 体检完成：全部正常'` | **stdio 模式下往 stdout 打印了人类日志**，破坏了 JSON-RPC | 日志一律 `print(..., file=sys.stderr)` |
| 33 ★ | MCP 服务 `Connection closed` | 服务进程启动就崩了（语法错、import 错、配置错） | 先手工跑 `python c08_mcp_server.py`，看 stderr |
| 34 ★ | `name 'X' is used prior to global declaration` | Python 语法：`global` 必须在该函数内首次使用**之前** | 把 `global` 移到函数第一行 |
| 35 | 模型输出"已完成"，但设备没动 | 工具没返回可验证的证据；提示词没要求核验 | 动作类工具返回 `actual` + `elapsed_s`；skill 里写"报告实际值" |
| 36 | 模型被拒绝后换个数字继续试 | 提示词没讲清"拒绝是设计的一部分" | 提示词 + skill 明写"不要绕过"；审计日志监控这种模式 |
| 37 | 长任务用户以为卡死 | 中间过程没有输出 | 写 `progress.jsonl`，让 `get_progress` 能读到，并强制模型转述 |

## C.5 硬件（38–40）

| # | 现象 | 病因 | 修法 |
|---|---|---|---|
| 38 | 开发板随机重启 | 舵机/电机抢电流 | 电机独立电源 + **共地** + 加电容 |
| 39 | ADC 读数一直 0 或 4095 / 抖动大 | 接了只输出引脚、分压接错、高阻源直接进 ADC | 检查引脚类型；高阻源加跟随器或用 ADS1115 |
| 40 | 灯不亮 / 舵机不动 / DS18B20 读 85.0 | 极性反、没串限流电阻、上拉缺失、线只充电不传数据 | 按 [`hardware/wiring.md`](code/hardware/wiring.md) 的分步验证表一步步查；**每次只加一个器件** |

---

## C.6 三条"元规律"

踩了 40 个坑之后，我发现它们其实都能归到三条：

1. **"我以为它是那样的"** → 对策：**先读**（`dbl`、`dbpr`、`snapshot`、`self_check`）。
   不要假设 PV 名、单位、极性、上电状态。
2. **"我发过命令了"** → 对策：**读回确认 + 等到位**。
   `put` 成功 ≠ 设备动了；`monitor` 没事件 ≠ 设备停了。
3. **"它应该没问题"** → 对策：**让它坏一次看看**。
   拔线、断电、kill 掉进程、把值写超范围——异常路径才是你真正要交付的东西。

**把这三条贴在心里，比记住上面 40 条更有用。**
