# 附录 A　速查表

> 打印出来贴在显示器旁边。全部内容在 EPICS Base 7.0.10 上验证过。

---

## A.1 命令行（最常用 15 条）

```bash
# ---- 读 ----
caget PV                       # 读一个 PV 的值
caget PV1 PV2 PV3              # 一次读多个
caget -t PV                    # 只输出值（terse），适合脚本
caget -a PV                    # 读数组（waveform）
caget PV.EGU PV.HIHI PV.SEVR   # 字段也是 PV
capr.pl -a -c PV               # （可选）按周期持续打印

# ---- 写 ----
caput PV 3.3                   # 写
caput -c PV 3.3                # -c：等 IOC 确认处理完成（put callback）
caput -a PV 5 1 2 3 6 4        # 写数组前 5 个元素
caput -n PV 1                  # 强制按数字解释（不是枚举字符串）

# ---- 订阅 ----
camonitor PV PV2               # 值变化就打印（Ctrl-C 退出）
camonitor -t PV                # 只打印值

# ---- 诊断 ----
cainfo PV                      # 连接状态、访问权限、字段清单
pvget PV ; pvput PV 42         # pvAccess 版本（需要 softIocPVA / 支持 PVA 的 IOC）
pvmonitor PV ; pvinfo PV

# ---- 服务器端（在 iocsh 里）----
dbl                            # 列出所有记录
dbpr("PV", 5)                  # 打印一条记录的所有字段
dbpf("PV", 42)                 # 在 IOC 内部直接写（绕过网络，用于调试）
dbLoadRecords("db/x.db", "P=LAB:")
iocInit()
```

### 常用环境变量

```bash
EPICS_BASE=/path/to/base                 # EPICS 安装根目录
EPICS_HOST_ARCH=linux-x86_64             # 找 bin/$ARCH/ 用
EPICS_CA_ADDR_LIST=                      # 留空 = 用自动（广播），推荐
EPICS_CA_AUTO_ADDR_LIST=YES              # 默认就是 YES
EPICS_CA_MAX_ARRAY_BYTES=1000000         # 传大数组时要调
EPICS_PVA_ADDR_LIST= / EPICS_PVA_AUTO_ADDR_LIST=
```

> ⚠️ **只在用 CA Gateway 跨网段时才手工设 `EPICS_CA_ADDR_LIST`。**
> 设成单播地址（如 `127.0.0.1`）且同机有多个 IOC 时，会有 PV 读不到（第 5.11 节）。

---

## A.2 记录类型（按用途）

| 用途 | 记录 | 一句话 |
|---|---|---|
| 模拟输入 | `ai` | 浮点测量值 |
| 模拟输出 | `ao` | 浮点设定值/执行器 |
| 位输入 | `bi` | 开关状态 |
| 位输出 | `bo` | 开关控制 |
| 枚举输入 | `mbbi` | 状态字（带字符串） |
| 枚举输出 | `mbbo` | 模式选择 |
| 整数 | `longin` / `longout` / `int64in` / `int64out` | 计数、编号 |
| 字符串 | `stringin` / `stringout` | 名称、消息 |
| 长字符串 | `lsi` / `lso` | 长文本 |
| 数组 | `waveform` / `aai` / `aao` / `subArray` | 谱图、图像、缓冲 |
| 计算 | `calc` / `calcout` | 表达式运算 |
| 分发 | `fanout` / `dfanout` / `seq` | 一对多、序列 |
| 其他 | `compress` / `histogram` / `sub` / `aSub` / `event` / `sel` / `state` / `printf` / `permissive` | 见第 4.2 节 |

---

## A.3 字段（按"你会用到"排序）

| 字段 | 记录 | 含义 | 坑 |
|---|---|---|---|
| `DESC` | 全部 | 描述 | **≤41 字节**，中文 ≤13 字 |
| `EGU` | 全部 | 工程单位 | 不写等于没写 |
| `PREC` | 数值 | 小数位 | 只影响显示 |
| `HOPR`/`LOPR` | 数值 | 显示上下限 | 不是"能写到哪" |
| `DRVL`/`DRVH` | 输出 | 驱动上下限 | **客户端写超限会被 IOC 截断** |
| `SCAN` | 全部 | 何时处理 | `Passive`/`I/O Intr`/周期 |
| `DTYP` | 全部 | 设备支持 | `"Soft Channel"` = 软记录 |
| `INP`/`OUT` | 全部 | 输入/输出链接 | 可指向硬件或另一个 PV |
| `FLNK` | 全部 | 前向链接 | 串起处理链 |
| `OMSL` | 输出 | 输出模式 | 7.0.10 里是 `supervisory`/`closed_loop`（小写！） |
| `DOL` | 输出 | 闭环输入 | `closed_loop` 时会覆盖客户端写 |
| `IVOA`/`IVOV` | 输出 | 无效时行为/值 | 安全默认值写这里 |
| `ADEL`/`MDEL` | 输入 | 死区 | 省网络的关键，不是精度 |
| `SMOO` | 输入 | 平滑系数 | 0=关，0.8=很平滑 |
| `ASLO`/`AOFF`/`ROFF` | 输入 | 标定换算 | `VAL = ASLO*(RVAL+ROFF)+AOFF` |
| `HIGH`/`HIHI`/`LOW`/`LOLO` | 全部 | 报警阈值 | — |
| `HSV`/`HHSV`/… | 全部 | 报警等级 | `NO_ALARM`/`MINOR`/`MAJOR`/`INVALID` |
| `HYST` | 全部 | 报警回差 | 防抖 |
| `STAT`/`SEVR` | 全部（只读） | 报警状态/等级 | `UDF` = 从没被写过，别信这个值 |
| `PINI` | 全部 | 启动即处理 | — |
| `TSE` | 全部 | 时间戳来源 | 真设备建议用设备时间戳 |
| `DISA`/`DISV` | 全部 | 禁用条件 | 联锁常用 |
| `SDIS` | 输入 | 扫描禁用链接 | 外部信号禁止扫描 |
| `PHAS` | 全部 | 处理阶段 0–9 | 同一次扫描内的顺序 |

### 菜单字段的取值（7.0.10，**随手查版本**）

```
SCAN   : Passive | Event | I/O Intr | 10 second | 5 second | 2 second |
         1 second | .5 second | .2 second | .1 second
OMSL   : supervisory | closed_loop
IVOA   : Continue normally | Don't drive outputs | Set output to IVOV
SEVR   : NO_ALARM | MINOR | MAJOR | INVALID
```

---

## A.4 Python API 速查

```python
# ---- pyepics ----
import epics
epics.caget("LAB:TempRaw")               # 失败返回 None ⚠️
epics.caput("LAB:Power", 35, wait=True)  # wait=True 等处理完成
pv = epics.PV("LAB:TempRaw")
pv.wait_for_connection(timeout=2.0)      # bool
pv.get(timeout=1.0) ; pv.put(1.0, wait=True)
pv.units ; pv.precision ; pv.enum_strs ; pv.severity ; pv.status ; pv.timestamp
pv.lower_disp_limit ; pv.upper_disp_limit ; pv.lower_ctrl_limit ; pv.upper_ctrl_limit
pv.add_callback(cb) ; pv.clear_callbacks()

# ---- p4p（pvAccess）----
from p4p.client.thread import Context
ctxt = Context("pva")
v = ctxt.get("LAB:TempRaw")              # 直接当 float 用；失败抛 TimeoutError
v.raw["display"]["units"] ; v.raw["valueAlarm"]["highAlarmLimit"]
ctxt.put("LAB:SetPointTemp", 47.5)
sub = ctxt.monitor("LAB:Counter", lambda x: print(x)) ; sub.close()

# ---- caproto（客户端）----
from caproto.threading.client import Context
cc = Context() ; (ch,) = cc.get_pvs("LAB:Counter")
ch.read().data          # 是 list ⚠️
ch.write(33.0) ; sub = ch.subscribe(cb) ; sub.clear()

# ---- caproto（写一个自己的 IOC）----
from caproto.server import PVGroup, pvproperty, run
class MyIOC(PVGroup):
    temp = pvproperty(value=25.0, name="Temp", dtype=float, precision=2)
    led  = pvproperty(value=0, name="LED", dtype=int)
    @temp.startup
    async def temp(self, instance, async_lib):     # 后台任务
        while True:
            await self.temp.write(25.0)
            await async_lib.library.sleep(1.0)
    @led.putter
    async def led(self, instance, value):          # 值来自客户端
        return int(value)
run(MyIOC(prefix="LAB:").pvdb)

# ---- 本课程的封装（推荐直接用）----
from pv_utils import PV, Monitor, PVError
t = PV("LAB:TempRaw") ; t.get() ; t.info()
h = PV("LAB:Power") ; h.put_checked(35) ; h.output_limits()
rb = PV("LAB:ThetaRB") ; rb.wait_within(20.0, tol=0.05, timeout=20)
mon = Monitor("LAB:TempRaw") ; mon.wait_for(lambda v: v > 80, timeout=120)
```

---

## A.5 错误信息 → 病因 对照

| 错误信息（节选） | 病因 | 去哪一节 |
|---|---|---|
| `Channel connect timed out: 'X' not found` | PV 不存在 / IOC 没起 / 地址列表 | 5.11 |
| `Bad character '$' in Record/Alias name "$(P,undefined)X"` | `-m` 写在了 `-d` 之后 | 3.9 |
| `Can't set 'X.DESC' ... String is too long` | `DESC` > 41 字节 | 4.13 |
| `Illegal choice ... Did you mean "closed_loop"?` | 菜单字符串大小写/版本 | 4.13 |
| `Failed to load 'db/x.db'` | 该文件里**至少一条**记录有错 | 4.13 |
| 启动后立刻退出 | stdin 是 EOF | 3.9 |
| `pvget` 超时而 `caget` 正常 | 起的是 `softIoc` 而不是 `softIocPVA` | 3.9 |
| `Invalid JSON: expected ',' or ']' input_value='[14:15:45] ...'` | MCP stdio 模式下往 stdout 打印了日志 | 8.5 |
| `写入 X 后读回 Y，不一致` | 被限值截断 / 闭环覆盖 / 设备未响应 | 5.4 |
| 第一行数据是 1990 年、值 0 | monitor 连接期占位事件 | 5.5 |
| 订阅收不到任何事件 | 值没变过（monitor 不发心跳） | 5.5 |
| `No record type`（在 iocsh 里） | `dbl` 后面写了行尾 `#` 注释 | 3.9 |

---

## A.6 一小时上手清单（给"我想马上看到东西"的人）

```bash
# 1. 装
conda create -n epics -c conda-forge epics-base -y && conda activate epics
pip install pyepics caproto matplotlib numpy pyserial mcp

# 2. 起一个虚拟实验站
cd docs/epics-course/code
bash runlab.sh start sim

# 3. 看
caget LAB:TempRaw LAB:Power LAB:Counts LAB:ThetaRB
camonitor LAB:TempRaw

# 4. 动
caput LAB:RunPermit 1
caput LAB:SetPointTemp 45      # 常驻 PID 会把温度拉上去
caget LAB:TempRaw

# 5. 让智能体自主做一次实验
python c08_agent_demo.py --policy script

# 6. 复盘
cat logs/audit.jsonl | tail -5

# 7. 收工
bash runlab.sh stop
```
