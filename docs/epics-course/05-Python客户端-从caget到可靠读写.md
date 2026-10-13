# 第 5 章　Python 客户端：从 `caget` 到可靠读写

> **本章目标**：把"读一个 PV"这件事做到工程级可靠。读完你应该能写出
> 一个**不会骗你**的客户端：连不上会报错、写完了会读回确认、
> 会等到设备真的到位、订阅不会漏事件、数据会落盘。
>
> 这些都是我在这台机器上跑出来的真实输出，你可以逐条复现。
>
> 配套代码：[`code/pv_utils.py`](code/pv_utils.py)、
> [`code/c05_pv_explore.py`](code/c05_pv_explore.py)、
> [`code/c05_monitor_log.py`](code/c05_monitor_log.py)、
> [`code/c05_scan_demo.py`](code/c05_scan_demo.py)。

---

## 5.1 四种客户端，怎么选

| 工具 | 协议 | 适合 | 不适合 |
|---|---|---|---|
| 命令行 `caget/caput/camonitor` | CA | 手动排查、写 shell 脚本 | 复杂逻辑 |
| 命令行 `pvget/pvput/pvmonitor` | pvAccess | 看结构化元数据、大数组 | 老设备（可能不支持） |
| **`pyepics`** | CA | **Python 应用的主力**（成熟、稳、示例多） | 需要 pvAccess 结构化数据时 |
| `p4p` | pvAccess | 结构化数据、图像、高吞吐 | API 稍绕 |
| `caproto` | 纯 Python | 写自己的 IOC、零 C 依赖 | 追求极致性能 |

**建议**：应用层用 `pyepics`；要 pvAccess 就用 `p4p`；想用纯 Python 造一个 IOC（第 7 章）就用 `caproto`。

```bash
pip install pyepics p4p caproto    # 三个都装上也就几 MB
```

---

## 5.2 命令行四件套（先学会用眼睛调试）

```bash
$ caget LAB:Counter LAB:Wave LAB:TempRaw
LAB:Counter                    4
LAB:Wave                       3.58678
LAB:TempRaw                    65

$ caput LAB:SetPointTemp 42        # 写
$ caput -c LAB:SetPointTemp 42      # -c = 等 IOC 确认处理完（put callback）
$ caput -a LAB:Trace 5 1 2 3 6 4    # -a = 写数组

$ camonitor LAB:Counter            # 订阅：值变了才打印
LAB:Counter                    2026-10-12 13:48:41.794395 26
LAB:Counter                    2026-10-12 13:48:42.794403 27

$ cainfo LAB:TempRaw               # 看连接状态、字段、访问权限
$ caget -a LAB:Trace               # 看整个数组
$ caget LAB:TempRaw.EGU LAB:TempRaw.HIHI   # 字段即 PV
```

| 参数 | 作用 | 什么时候用 |
|---|---|---|
| `-t` | 只输出值（terse） | 写 shell 脚本时 |
| `-a` | 数组模式 | waveform/图像 |
| `-c`（caput） | 等回调完成 | 需要确认"命令被接受"时 |
| `-n`（caget） | 不打印名字 | 取数 |

命令行不能做的事：等待到位、读回验证、处理断线。
那些正是下一节要写的东西。

---

## 5.3 pyepics 的语义（实测，别凭记忆）

```python
import epics

pv = epics.PV("LAB:Counter")
pv.wait_for_connection(timeout=2.0)      # 连不上返回 False，不抛异常
pv.get()                                 # 读到值；失败时返回 None（注意！）
pv.units, pv.precision                   # '' 和 0（没设就空着）
pv.lower_disp_limit, pv.upper_disp_limit # HOPR/LOPR
pv.lower_ctrl_limit, pv.upper_ctrl_limit # DRVL/DRVH（输入记录会退化成显示限值）
pv.enum_strs                             # ('Idle', 'Heating', 'Soaking', 'Done')
pv.severity, pv.status                   # 0/0；UDF 时是 3/17
pv.timestamp                             # Unix 秒（含小数）
pv.put(42.0, wait=True, timeout=2.0)     # 返回 1（成功），失败返回 None
```

实测结论，逐条记住：

1. **`PV.get()` 读失败返回 `None`，不抛异常。** 你可以据此写出
   "把 None 当成 0 算了一路"的经典事故。**一定要包一层。**
2. **`pv.put(..., wait=True)` 返回的是状态（实测为 `1`），不是写入的值。**
   要确认写成功，看返回值是不是 `None`。
3. **`pv.severity` / `pv.status` 是整数**，需要映射成 `NO_ALARM/MINOR/MAJOR/INVALID`
   和 `UDF/HIHI/HIGH/...` 才有意义（见 [pv_utils.py](code/pv_utils.py) 里的两张表）。
4. **`pyepics` 不会替你检查限值**：写 1000 到 `DRVH=150` 的记录，
   客户端"成功"了，但读回来是 150——**截断发生在 IOC 里**（第 4 章实测）。
5. **任何字段都是 PV**：`epics.caget("LAB:TempRaw.HIHI")` 直接可用，
   字段不存在时返回 `None`（并会在控制台打印一句 `cannot connect`）。

---

## 5.4 可靠读写：为什么需要 `pv_utils.py`

裸 API 能用，但写业务代码时你会反复遇到四件事：

1. PV 不存在 → 返回 `None` → 十层之后崩，而且不知道是哪个 PV；
2. 写完了以为到位了 → 实际设备还在爬；
3. 用 `while True: sleep(0.1)` 轮询 → 漏跳变、费网络；
4. 出错时没有上下文 → 大模型（或三个月后的你）看不懂。

所以我们在 `pv_utils.py` 里把语义补齐：

```python
from pv_utils import PV, PVError, Monitor

temp = PV("LAB:TempRaw")                  # 连不上直接抛 PVError，带排查清单
print(temp.get(), temp.info()["units"])
print(temp.info())                        # 单位/量程/精度/报警/UDF 一次性拿到

heater = PV("LAB:Power")
heater.put_checked(35)                    # 写 + 读回确认（会重试几次）
                                          # 被限值截断会返回真实值，不会骗你

readback = PV("LAB:ThetaRB")
readback.wait_within(20.0, tol=0.05, timeout=20)   # 等到位！电机控制的核心一行

mon = Monitor("LAB:TempRaw")              # 订阅
mon.wait_for(lambda v: v > 80, timeout=120, desc="等到超温")
```

### 四个方法，对应四类事故

| 方法 | 防的事故 |
|---|---|
| `PV(...)` 构造时等连接 | "PV 名写错但代码照样跑" |
| `get()` 失败抛异常 | "None 被当成 0 算进结果" |
| `put_checked()` | "我写了" ≠ "它变了" |
| `wait_within()` / `wait_until()` | "命令发完了就以为到位了" |

### `put_checked` 里那个容易被忽略的细节

```python
self.put(value, wait=True)
deadline = time.monotonic() + verify_timeout     # 默认 0.5 s
while True:
    actual = float(self.get())
    if abs(actual - target) <= max(1e-9, abs(target) * 1e-3):
        return actual                            # 一致，成功
    ...
    time.sleep(0.05)                             # 不一致就再读几次
```

**为什么要重试？** 实测：用 Python（caproto）写的 IOC，
`put` 回调返回之后，PV 内部的可见值可能还要过几毫秒才更新。
只采样一次就会得到"写入失败"的**假故障**。

> 💡 这条经验在真实设备上同样成立：老 IOC、网关后面的设备、
> 通信慢的 PLC，读回值都要"等一小会儿再看"。
> **读回确认要写成"宽容但有限度"的循环，而不是一次采样。**

---

## 5.5 订阅 vs 轮询：两个实测的坑

### 坑 1：PV 刚连上时，你会收到一个假的"1990 年 0 度"事件

实测：订阅 `LAB:TempRaw` 后，第一条回调是
`value=0.0, timestamp=631152000`（= 1990-01-01，EPICS 时间戳的零点）。
**这不是设备掉到 0 度，而是"数据还没到"的占位事件。**

如果不过滤，你的 CSV 第一行永远是 1990 年的 0 度，
而且某天做数据回顾时会以为设备真的瞬间归零。

```python
EPICS_EPOCH = 631152000.0        # 1990-01-01T00:00:00Z
def _on_change(self, value=None, timestamp=None, **kw):
    if value is None: return
    if timestamp is None or abs(float(timestamp) - EPICS_EPOCH) < 1.0:
        return                    # 占位事件，丢掉
```

（`pv_utils.Monitor` 已经处理了，并且会统计 `skipped` 次数。）

### 坑 2：值不变时，"订阅"什么都不会给你

实测：监控一个恒定的 PV 6 秒，收到 **0 个事件**。
这不是 bug：**monitor 只在值变化超过死区（`ADEL`/`MDEL`）时推送，不发心跳。**

推论：
- 想画"心跳线"、想计算"多久没更新"→ 需要**在应用层**记录"最后一次收到的时间"，
  然后自己判断是否超时（第 9 章的看门狗就是这么做的）；
- 不想被高频噪声淹没 → 给记录的 `ADEL` 设一个合理值（第 4 章）。

### 实测对比

| | 轮询 (`pv.get()`) | 订阅 (`Monitor`) |
|---|---|---|
| 恒定值 6 秒 | 600 次读数（10 Hz） | 0 个事件 |
| 1 Hz 变化的计数器 3.5 秒 | 35 次读数 | 5 个事件（只有变化） |
| 漏跳变 | 会漏（取决于周期） | 不漏 |

---

## 5.6 一次"真正的扫描"：五个步骤，一个都不能少

`c05_scan_demo.py` 是本章的样板代码，它的骨架和散裂上一台谱仪做 θ-2θ 扫描
**完全同构**：

```python
for target in frange(start, stop, step):
    axis.put(target, wait=True)                       # 1. 下命令
    reached = readback.wait_within(target, tol=0.02)  # 2. 等真的到位
    time.sleep(dwell)                                 # 3. 稳定
    counts = measure(detector)                        # 4. 取数
    points.append((reached, counts))                  # 5. 记"实际角度"，不是"目标角度"
```

实测输出（扫描 `LAB:Theta` 15°→25°，虚拟探测器峰在 20°）：

```
  目标   15.000 → 到位   15.018  计数     20.1
  目标   16.000 → 到位   15.982  计数     22.9
  目标   17.000 → 到位   16.982  计数     54.9
  目标   18.000 → 到位   17.982  计数    217.3
  目标   19.000 → 到位   18.982  计数    571.8
  目标   20.000 → 到位   19.982  计数    815.2
  目标   21.000 → 到位   20.982  计数    603.7
  目标   22.000 → 到位   21.982  计数    220.3
  目标   23.000 → 到位   22.982  计数     57.2
  目标   24.000 → 到位   23.982  计数     23.2
  目标   25.000 → 到位   24.982  计数     20.1

峰位 ≈ 20.0156 deg，峰高 ≈ 815.2，FWHM ≈ 2.920
```

三个值得注意的细节（都是真实实验里的常识）：

1. **到位值不等于目标值**：因为到位判据是 `|回读−目标| ≤ tol(0.02)`，
   所以读回停在 19.982。**记进数据文件的是回读值**——目标值只是你想要的，
   回读值才是你实际测的。
2. **`tol` 定得太松，峰位就偏**：把 `--tol` 放到 0.2，峰位会飘 ±0.2°。
   真设备上"到位容差"要和你的科学要求匹配，不是随手写 0.5。
3. **找峰要先扫到本底**：`FWHM` 的算法会参考最低点作为本底；
   如果扫描窗口太窄（比如只在峰顶 ±1° 内扫），算出来的 FWHM 会偏小。
   这也是"两段式扫描"（先粗后细）的原因：粗扫确定本底和峰位，细扫测峰形。

---

## 5.7 pvAccess（`p4p`）：多出来的那些元数据

CA 只给你"值 + 少量元数据"。pvAccess 给你**完整的结构化描述**：

```python
from p4p.client.thread import Context
ctxt = Context("pva")

v = ctxt.get("LAB:TempRaw")          # 返回的值对象直接像 float 一样用
print(float(v), v.units, v.severity) # 25.0   'degC'   3
print(v.raw["display"]["limitLow"], v.raw["display"]["limitHigh"])   # -50  200
print(v.raw["valueAlarm"]["highWarningLimit"], v.raw["valueAlarm"]["highAlarmLimit"])
# 80 95    ← 注意：报警阈值是结构的一部分，客户端直接读得到！

ctxt.put("LAB:SetPointTemp", 47.5)
got = []
sub = ctxt.monitor("LAB:Counter", lambda x: got.append(x))
sub.close()
```

实测要点：
- `ctxt.get("不存在的PV", timeout=1.0)` 抛 `TimeoutError`（比 CA 的 `None` 好）；
- 报错的 field 名（`display`/`control`/`valueAlarm`）是 **NT（Normative Types）**标准结构；
- 大数组/图像走 pvAccess 才是"正常用法"。

**对你的意义**：第 8 章要让智能体自己看懂设备，`p4p` 拿到的这套自描述信息
（单位、量程、报警限）比让模型去猜要可靠得多。

---

## 5.8 caproto 客户端（想要纯 Python 时）

```python
from caproto.threading.client import Context
cc = Context()
(ch,) = cc.get_pvs("LAB:Counter")
rd = ch.read()
print(rd.data)          # [4.]      ← 注意：是 list，不是标量
print(rd.status)        # CAStatusCode(...)
(st,) = cc.get_pvs("LAB:State")
rd2 = st.read()
print(rd2.metadata.enum_strings, rd2.data)   # ('Idle','Heating',...) [2.]
(ch2,) = cc.get_pvs("LAB:SetPointTemp")
ch2.write(33.0)
sub = ch.subscribe(lambda r: print(r.data))
sub.clear()
```

实测要点：`read()` 返回的是**响应对象**，值在 `.data`（list），
单位和报警等级在 `.metadata` 里（需要时用不同的 `data_type` 请求）。
比 pyepics 底层一些，但它是纯 Python 的——第 7 章我们就用它把开发板做成一个 IOC。

---

## 5.9 数据落盘：CSV 也是认真的事

[`c05_monitor_log.py`](code/c05_monitor_log.py) 是个 100 行的采集程序，它做对了四件事：

1. **订阅而不是轮询**；
2. **每行都带时间戳**（优先用 EPICS 时间戳，而不是本地时钟）；
3. **追加 + 定期 flush**（Ctrl-C 也不丢数据）；
4. **退出时优雅关闭订阅**，并报告每个 PV 收到多少次变化（快速判断"是不是没接上"）。

实测：

```bash
$ python c05_monitor_log.py LAB:TempRaw LAB:Power --seconds 8 --out mon.csv
✓ 已订阅 LAB:Power
完成：mon.csv
  LAB:TempRaw              收到 81 次变化，最后值 41.815
  LAB:Power                收到 1 次变化，最后值 60.0
```

```csv
iso_time,epoch,pv,value
2026-10-12T14:04:16.964035,1791785056.964035,LAB:TempRaw,25.509
2026-10-12T14:04:16.769352,1791785056.769353,LAB:Power,60.0
```

**真实束线上的数据不会存成 CSV**——会用 Bluesky + databroker 存成
带元数据的 HDF5/NeXus（第 8 章）。但**你必须能自己写这一百行**，
否则你无法判断框架在做什么、也无法在框架外面做实验。

---

## 5.10 练习

### 练习 5-1（到位容差的影响）★
用 `c05_scan_demo.py` 分别以 `--tol 0.02` 和 `--tol 0.3` 扫同一条线（峰在 20°），
比较两次得到的峰位差多少。**问**：如果这是 θ-2θ 谱仪，
0.3° 的到位误差对你的峰位标定意味着什么？

### 练习 5-2（写一个"等到稳定"函数）★★
不要用现成的 `wait_within`，自己写一个函数：
"连续 5 次读到 `|T−目标| < 0.2` 才算稳定，最多等 60 秒"。
用 `LAB:TempRaw` 验证（用 `caput` 手动改值模拟）。
**为什么必须要求"连续 N 次"**？（提示：噪声会让单次判断误报。）

### 练习 5-3（订阅抓瞬时事件）★★
写一个脚本订阅 `LAB:Shutter`，要求：
手动 `caput LAB:Shutter 1` 再立刻 `caput LAB:Shutter 0`（间隔 20 ms），
脚本必须**能捕获到那次短暂的 1**。
再用 `while True: get(); sleep(0.1)` 的轮询版本做同样的事，比较结果。

### 练习 5-4（连接失败的诊断信息）★★
把 `PV("LAB:TempRaw")` 改成一个不存在的 PV，运行并读它的报错。
**要求**：报错必须告诉你"去检查 PV 名 / IOC 是否运行 / CA 环境变量 / 网关"。
这就是"错误信息写得好"的价值。

### 练习 5-5（跨机房那个坑）★★★
把 `EPICS_CA_ADDR_LIST=127.0.0.1`、`EPICS_CA_AUTO_ADDR_LIST=NO`，
然后**同时**运行 softIOC 和一个 caproto 版 IOC（第 7 章的桥），
用 `caget` 分别读两边的 PV。
**问**：为什么其中一个读不到？把环境变量去掉之后为什么又好了？
（答案见 5.11 节的实测。）

---

## 5.11 本章的坑（含一个能让你查一整天的网络问题）

| 现象 | 真实原因 | 修法 |
|---|---|---|
| `Channel connect timed out: 'X' not found` | 真的没有这个 PV，或 IOC 没起 | 先在 IOC 那边 `dbl` 确认 PV 存在；再查网络 |
| **同一台机器上跑两个 CA server，其中一个的 PV 读不到** | 客户端设了**单播**地址列表（如 `EPICS_CA_ADDR_LIST=127.0.0.1` + `AUTO_ADDR_LIST=NO`）。多个 CA server 共用 UDP 5064，单播报文只会送到其中一个进程 | **去掉手工设置**，让 `EPICS_CA_AUTO_ADDR_LIST` 默认开启（走广播）；或改用 CA Gateway |
| `pvget` 全部超时而 `caget` 正常 | 你起的是 `softIoc`（只有 CA），不是 `softIocPVA` | 换 `softIocPVA`，并保持标准输入打开 |
| 脚本"启动了但什么也没干" | `softIoc` 的标准输入是 EOF，读到就退出 | 用 `-S`，或保持 stdin 打开 |
| 第一行数据是 1990 年、值 0 | monitor 的连接期占位事件 | 按 5.5 节过滤时间戳 |
| 订阅收不到任何事件 | 值一直没变（monitor 不发心跳） | 这是正常行为；要周期数据就用轮询或加心跳记录 |
| 读到 `None` 当成 0 用 | pyepics 读失败返回 `None` | 用 `pv_utils.PV.get()`（抛异常） |
| 写很快时读回值乱跳 | 高频写 + IOC 处理延迟（实测：caproto 版 IOC 连写 1/0 会看到中间态） | **慢下来**（5 Hz 以内）；这正是第 8 章安全层要限速的原因 |
| 时间戳差 8 小时 | 混用了 EPICS 时间戳（UTC）和本地时间 | 统一用带时区的 ISO 字符串；`time.localtime` 只用于显示 |

---

## 5.12 自检

- [ ] 我能用 `caget/caput/camonitor/pvget/pvput/pvmonitor` 各做一次基本操作。
- [ ] 我知道 `PV.get()` 失败返回 `None`，并且我的代码不会把它当 0。
- [ ] 我能写出"写 + 读回确认 + 等待到位"的三段式动作。
- [ ] 我知道 monitor 不发心跳、以及那个 1990 年的占位事件。
- [ ] 我能解释同一台机器上多个 IOC 时那个单播地址列表的坑。
- [ ] 我能把采集数据正确落盘，且第一行不是垃圾。

**下一章**：把 PID 接到虚拟恒温箱上——先在**完全安全**的仿真里，
把控制器的脾气摸清楚。
