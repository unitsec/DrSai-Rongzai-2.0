# 第 8 章　智能体驱动实验：从 MCP 到自主闭环

> **本章目标**：把第 4~7 章的设备能力，交给一个会犯错的大模型，
> 同时保证**它在任何情况下都干不出危险的事**。
> 读完你应该能自己搭出一套"智能体 → 安全层 → EPICS → 硬件"的完整链路，
> 并且知道每一层为什么必须存在。
>
> 全部代码：[`code/c08_instrument.json`](code/c08_instrument.json)（仪器画像）、
> [`code/c08_safety.py`](code/c08_safety.py)（安全层）、
> [`code/c08_tools.py`](code/c08_tools.py)（语义工具）、
> [`code/c08_mcp_server.py`](code/c08_mcp_server.py)（MCP 服务）、
> [`code/c08_agent_demo.py`](code/c08_agent_demo.py)（闭环演示）、
> [`code/tools_test.py`](code/tools_test.py)（安全测试，**16 项全部实测通过**）、
> [`code/skills/epics-instrument-control/SKILL.md`](code/skills/epics-instrument-control/SKILL.md)（蓉仔 skill）。

---

## 8.1 先说结论

> **不要让大模型直接写 PV。**

大模型会犯错，而且错得很有创意：

- 把 600 写成 60（小数点）；
- 把 ℃ 当 K 用（差 273）；
- 把"停止"理解成"冷却"，于是继续加热；
- 在两轮对话之间忘记自己刚才把加热开到 80%；
- 把"我以为它已经到了"写成"已到达 60 ℃"。

在纯软件里，这些错误的代价是一行日志。在实验站里，代价是：
烧掉的样品、撞坏的探测器、被辐射污染的真空腔、或者伤到人。

所以真实系统的结构永远是**分层**的：

```
┌────────────────────────────────────────────────┐
│  大模型（可以犯错、允许犯错）                    │
│  "把样品升到 60 度"                              │
└──────────────────┬─────────────────────────────┘
                   │  只调用语义工具
┌──────────────────▼─────────────────────────────┐
│  语义工具层（set_temperature / run_scan ...）    │
│  动作化、幂等化、带证据、带超时                   │
└──────────────────┬─────────────────────────────┘
                   │
┌──────────────────▼─────────────────────────────┐
│  安全层：白名单 / 限值 / 单步幅度 / 联锁 /        │
│  速率限制 / 审计日志 / dry-run / 急停             │
└──────────────────┬─────────────────────────────┘
                   │  只有通过检查的写操作
┌──────────────────▼─────────────────────────────┐
│  EPICS 客户端 + IOC（DRVL/DRVH、联锁、OMSL）      │
└──────────────────┬─────────────────────────────┘
                   │
        硬件（不可逆的那一层）
```

**这一层的目标不是"让智能体什么都能干"，而是"让智能体在任何情况下都干不出危险的事"。**
做不到这一点，就不要把智能体接到真设备上。

---

## 8.2 仪器画像：把设备写成数据，而不是代码

第一件事：**把"这台仪器有什么、能写到哪里、什么条件下能写"写成一份配置文件。**

[`c08_instrument.json`](code/c08_instrument.json) 节选：

```json
{
  "instrument": "教学实验站（虚拟恒温箱 + 假衍射仪 + 开发板）",
  "prefix": "LAB:",
  "safety": { "max_write_rate_hz": 5, "audit_log": "logs/audit.jsonl" },
  "safe_state": { "heater": 0, "setpoint": 25, "run_permit": 0, "shutter": 0 },
  "channels": {
    "temperature": {
      "pv": "LAB:TempRaw", "units": "degC", "role": "measurement",
      "writable": false, "limits": [0, 120],
      "desc": "样品温度（由温控仪/虚拟恒温箱提供）"
    },
    "heater": {
      "pv": "LAB:Power", "units": "percent", "role": "actuator",
      "writable": true, "limits": [0, 100], "max_step": 50,
      "requires": { "heater_enable": 1 },
      "desc": "加热功率。要求 heater_enable=1，否则拒绝写入"
    },
    "theta_rb": {
      "pv": "LAB:ThetaRB", "units": "deg", "role": "readback", "writable": false,
      "settle": { "tol": 0.05, "timeout": 20 },
      "desc": "角度实际回读值。写 theta 之后必须等它到位"
    }
  }
}
```

**为什么这么做（而不是把限制写死在 Python 代码里）**：

| 好处 | 说明 |
|---|---|
| 换设备只改配置 | 接真温控仪时，把 `LAB:TempRaw` 换成真实 PV 即可，代码一行不动 |
| 大模型能读 | 这份配置直接喂给模型，它就"知道"单位、限值、联锁 |
| 可评审 | 控制组和安全员可以审这份 JSON，而不是读你的 Python |
| 可版本化 | 进 git，谁在什么时候放开了什么限值，一查便知 |
| 可以生成文档 | 一键导出"本实验站 PV 手册" |

> 📌 **第 4 章练习 4-6 那张"真设备 PV 表"，就是这份 JSON 的前身。**
> 填不出那张表，就写不出这份配置；写不出这份配置，就不要让智能体上机。

---

## 8.3 安全层：六道检查，一道都不能少

[`c08_safety.py`](code/c08_safety.py) 把"防呆"做成一个绕不过去的入口：
**所有写操作只有 `safety.write()` 一个出口**，智能体没有别的路径。

| # | 检查 | 拒绝时返回的 rule | 防的是什么事故 |
|---|---|---|---|
| 0 | 必须给 `reason` | `ch0-理由` | 智能体"随手写一下"，事后无法复盘 |
| 1 | 通道在白名单里且可写 | `ch1-只读` | 把测量值当设定值写、写错 PV |
| 2 | 值在 `limits` 内 / 属于 `allowed` | `ch2-范围` `ch2-取值` | 单位搞错（600 vs 60）、写 7 到 0/1 的开关 |
| 3 | 单步幅度 ≤ `max_step` | `ch3-单步` | 一步从 25 ℃ 跳到 90 ℃（热冲击、撞限位） |
| 4 | `requires` 里的联锁满足 | `ch4-联锁` | 没有运行许可就加热、超温还继续加热 |
| 5 | 写入速率 ≤ `max_write_rate_hz` | `ch5-速率` | 用写 PV 的方式"刷数据"，把总线/IPC 打满 |
| 6 | 本轮操作次数 ≤ 上限 | `ch6-总量` | 智能体陷入循环，几小时里写了几万次 |

实测输出（[tools_test.py](code/tools_test.py)，**16 项全部通过**）：

```
=== 1. 只读通道不能写 ===
✓ 拒绝写只读通道  —— {"ok": false, "error": "temperature 是只读通道（measurement）…", "rule": "ch1-只读"}
=== 2. 超范围不能写 ===
✓ 拒绝超范围写入  —— {… "rule": "ch2-范围" …}
=== 3. 枚举取值只能是允许值 ===
✓ 拒绝非法取值  —— {… "rule": "ch2-取值" …}
=== 4. 单步幅度限制 ===
✓ 拒绝单步过大  —— {"error": "setpoint 想从 25 一步变到 90，超过单步上限 20。请分几步走…", "rule": "ch3-单步"}
=== 5. 联锁：没有运行许可时不许加热 ===
✓ 拒绝违反联锁的写入  —— {"error": "拒绝写 heater：要求 heater_enable=1，当前 0。…", "rule": "ch4-联锁"}
=== 6. 写操作必须给理由 ===
✓ 拒绝没有理由的写  —— {… "rule": "ch0-理由"}
=== 7. 速率限制 ===
✓ 拒绝过快写入  —— {"error": "heater 写入太快（限 5 Hz）。控制系统不是消息队列…", "rule": "ch5-速率"}
=== 8. 合法写入 + 读回确认 ===
✓ 合法写入成功且读回一致  —— {"requested": 35.0, "actual": 35.0, "checks": ["通过全部 5 项检查"]}
=== 10. 控温动作 ===
✓ hold_temperature 到达目标  —— {"target": 45, "actual": 44.075, "elapsed_s": 38.1}
=== 11. 扫描 + 找峰 ===
✓ 峰位在 20 度附近  —— 找到 20.0772
=== 12. 急停 ===
✓ 急停把执行器写回安全状态
=== 13. 审计日志 ===
✓ 30 行日志，其中 6 条拒绝记录
通过 16 项，失败 0 项
```

**这 16 项测试本身就是交付物。** 在真设备上启用智能体之前，
把这些测试对着真 IO 跑一遍（可以先 `dry_run`），
是"我能证明它是安全的"和"我觉得应该没事"之间的区别。

### 审计日志：出了事能复盘

每次写操作（包括被拒绝的）都会追加一行 JSON：

```json
{"ts":"2026-10-12T14:12:29.123","actor":"pytest","session":"s20261012-141229",
 "reason":"升温到 35%","ok":true,"action":"write","channel":"heater","pv":"LAB:Power",
 "requested":35.0,"actual":35.0,"units":"percent","elapsed_s":0.0212,
 "checks":["通过全部 5 项检查"],"after":{"heater":35.0}}
```

一行里有：**谁、什么时候、想做什么、为什么、实际发生了什么**。
这就是"能不能信任它"的唯一客观依据。

---

## 8.4 语义工具：为什么不是 `caput`

对比一下"让智能体把温度调到 60 度"的两种做法：

| | 裸 PV 操作 | 语义工具 |
|---|---|---|
| 模型要输出的东西 | `caput LAB:SetPointTemp 60` | `hold_temperature(target=60, reason="...")` |
| 模型必须知道 | PV 名、单位、量程、极性、要不要等 | 只要知道"温度、摄氏度" |
| 超限怎么办 | 模型自己判断（经常忘了） | 安全层强制检查 |
| 到位判断 | 模型自己写等待逻辑（容易写成"发完就算到"） | 工具内部等待并返回证据 |
| 出错时模型看到 | 一行英文报错 | `{"ok":false,"rule":"ch4-联锁","hint":"…"}` |
| 换设备 | 重写提示词 | 改 JSON |
| 审计 | 无 | 每次都有 |

[`c08_tools.py`](code/c08_tools.py) 提供的工具：

| 工具 | 类型 | 作用 | 关键返回字段 |
|---|---|---|---|
| `describe_instrument` | 只读 | 仪器说明书（通道/单位/限值/联锁） | `channels` |
| `self_check` | 只读 | 上机前体检 | `problems` `warnings` |
| `snapshot` | 只读 | 所有通道的当前值 | `values` |
| `read_channel` | 只读 | 读一个通道 | `value` `units` |
| `write_channel` | 写 | 写一个通道（必须给 reason） | `requested` `actual` |
| `hold_temperature` | 动作 | 调到目标并稳定 | `target` `actual` `elapsed_s` |
| `move_axis` | 动作 | 走轴并等到位 | `requested` `actual`（回读） |
| `run_scan` | 动作 | 扫描 + 自动找峰 + 落盘 | `peak` `summary` `data_file` |
| `emergency_stop` | 动作 | 回安全状态 | `safe_state` |
| `get_progress` | 只读 | 长任务进展 | `last_lines` |

### 三条设计铁律

1. **错误变成数据，不要抛异常。**
   工具永远返回 `{"ok": false, "error": ..., "rule": ..., "hint": ...}`。
   这样模型能读到 `hint` 并**自我纠正**；抛异常会让整轮对话崩掉，
   模型学不到任何东西。
2. **动作类工具必须返回"实际值"和"证据"。**
   `move_axis` 返回的 `actual` 是**回读值**，不是目标值；
   `hold_temperature` 返回的 `actual` 是稳定后的实测温度。
   这样模型没法把"我发过命令"说成"已经到达"。
3. **工具描述就是提示词。**
   在 `@mcp.tool` 的 docstring 里写清单位、限值、什么时候用、失败怎么办，
   比在 system prompt 里塞一堆规则有效得多——因为工具描述就在模型需要它的那一刻出现。

---

## 8.5 接进蓉仔：MCP 服务

[`c08_mcp_server.py`](code/c08_mcp_server.py) 用 `FastMCP` 把上面 10 个工具暴露出去，
两种传输方式（**都实测可用**）：

### 方式一：stdio（本机，最省事）

蓉仔那边用 `StdioServerParameters(command=..., args=[...])` 直接拉起这个进程。

```bash
python c08_mcp_server.py            # 默认 stdio
```

实测（用官方 `mcp` 客户端连上）：
```
服务名: EPICS Instrument Tools
暴露了 10 个工具:
  - describe_instrument / self_check / snapshot / read_channel
  - write_channel / hold_temperature / move_axis / run_scan
  - emergency_stop / get_progress
self_check ok = True | problems = []
非法写 -> rule = ch2-范围
合法写 -> {"ok": true, "channel": "led", "requested": 1.0, "actual": 1.0, …}
```

> ⚠️ **实测踩过的大坑：stdio 模式下绝对不要往 stdout 打印任何东西。**
> stdout 是 JSON-RPC 协议通道。我在工具里加了一行中文日志，
> 客户端立刻报：
> ```
> pydantic_core.ValidationError: Invalid JSON: expected `,` or `]`
> input_value='[14:15:45] 体检完成：全部正常'
> ```
> **报错信息完全指不到真正的原因**，能查很久。
> 规矩：人类可读的日志一律 `print(..., file=sys.stderr)`。

### 方式二：SSE（常驻服务，和蓉仔现有 MCP 服务一致）

```bash
python c08_mcp_server.py --sse --port 42521
# 蓉仔侧：
#   type: sse
#   url: http://127.0.0.1:42521/sse
```

这和蓉仔现有服务（GSAS-II、磁结构分析等）的接法完全一致，
可以直接加进 [`start_tools.sh`](../../start_tools.sh) 的 `NAMES/ENVS/SCRIPTS` 列表里：

```bash
NAMES+=(epics-instrument)
ENVS+=(epics)
SCRIPTS+=(docs/epics-course/code/c08_mcp_server.py --sse --port 42521)
```

### 方式三：配上 skill（推荐）

蓉仔的体系里，MCP 提供"能力"，skill 提供"怎么用"。
[`skills/epics-instrument-control/SKILL.md`](code/skills/epics-instrument-control/SKILL.md)
就是干这个的，它写清了：

- 先 `describe_instrument` + `snapshot` 再动手；
- 写操作必须给 reason；
- 用动作类工具而不是拼裸 PV；
- 被拒绝时读 `rule`/`hint`，**绝不绕过**；
- 长任务用 `get_progress` 转述进展，不要让用户面对静默；
- 出问题先 `emergency_stop` 再报告；
- 常见失败的对照表（哪个 rule 对应哪类操作失误、怎么改）。

**skill + MCP + 安全层 = 一个可信的"实验员"。**
其中 skill 是总纲，MCP 是手脚，安全层是护栏。

---

## 8.6 自主闭环：一次真实的"粗扫 + 细扫 + 变温"

[`c08_agent_demo.py`](code/c08_agent_demo.py) 实现的循环，
和人在谱仪上做实验的顺序**完全一样**：

```
感知(observe) → 决策(decide) → 动作(act) → 验证(verify) → 记录(log) → 回到感知
```

实测（`--policy script`，不需要大模型）的一次完整运行：

```
阶段 0：体检
  {"ok": true, "problems": [], "warnings": []}

阶段 1：粗扫 10°→30°，步长 2°
  扫描范围 10..30，11 个点，步长 2。
  counts 最小 22.7、最大 834.9，本底约 22.7。
  峰位 ≈ 20.08 theta（抛物线细化），质心 19.99。
  峰高 ≈ 834.9，半高宽 FWHM ≈ 2.9。
  → 决策：峰位 P ≈ 20.08，细扫范围取 P±1.5

阶段 2：在峰附近细扫（18.5°→21.5°，步长 0.25°）
  峰位 ≈ 20.0772 theta，峰高 ≈ 838.6

阶段 3：把轴停在峰位，分别在 25℃ / 45℃ 测峰强
  25℃ → counts = 854.3
  45℃ → counts = 837.1
  （观测：温度对峰强在这套玩具装置上没有显著影响——如实的结论）

阶段 4：收尾 + 报告
  急停/回安全状态：{"heater": 0.0, "setpoint": 25.0, "run_permit": 0.0, …}
  报告已保存：data/report_20261012-141858.json
```

**这份报告里的每一条都有出处**：峰位来自 `run_scan` 的返回
（数据落盘在 `data/scan_theta_counts_*.csv`），温度来自 `hold_temperature`
的 `actual`，每一步操作都在 `logs/audit.jsonl` 里。

### 换成真大模型（`--policy llm`）

同一个循环，把"写死的策略"换成"模型的推理"：

```python
messages = [{"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": task}]
while True:
    resp = client.chat.completions.create(model=model, messages=messages,
                                          tools=TOOLS, temperature=0.2)
    msg = resp.choices[0].message
    if not msg.tool_calls:
        break                                  # 模型给结论了
    for call in msg.tool_calls:
        result = call_tool(inst, call.function.name, json.loads(call.function.arguments))
        messages.append({"role": "tool", "tool_call_id": call.id,
                         "content": json.dumps(trim(result), ensure_ascii=False)})
```

**大模型在这里的价值，不是"会按按钮"，而是"会决定下一步做什么"：**
- 粗扫看到峰在 20.08、本底 22.7、FWHM 大约 2.9 →
  它自己决定细扫范围取 ±1.5°、步长取 0.25°（覆盖 3 个 FWHM、每个 FWHM 采样约 12 点）；
- 如果细扫发现峰是双峰 → 它会再扫一次更细的、或者报告"样品可能有第二相"；
- 如果第一次扫描没找到峰（全本底）→ 它会扩大范围或降低步长再试，
  而不是把"没找到"当成"没有峰"。

这些**决策**用 if/else 也能写，但会越写越长、越写越脆；
而模型可以在提示词里用一两句话描述实验意图。

### 提示词要点（`SYSTEM_PROMPT` 节选）

```
1. 只能用提供的工具操作设备，绝不能凭空猜测通道名或数值。
2. 每个写操作都要给出 reason，写清楚"为什么"。
3. 写完之后必须验证：读回实际值，或用会自己等待到位并返回证据的动作类工具。
4. 安全层拒绝你时（ok=false），读 rule 和 hint，改变你的做法；
   绝不要绕开它，也不要把被拒绝描述成"已完成"。
5. 任何时候觉得情况不对，立刻 emergency_stop，然后向用户报告。
6. 报告结果要给出：做了什么、实际数值、数据文件路径、有什么异常。
   不要编造没有测到的数据。
```

> 💡 **第 4 条最重要。** 大模型在被拒绝后最常见的反应是"换一种说法再试一次"
> （比如把 500 改成 499）。这条提示词明确告诉它：拒绝是设计的一部分，
> 你的任务是**理解为什么被拒绝**，而不是绕过。

---

## 8.7 长任务：不要让用户面对静默

控温 3 分钟、扫描 200 点，用户不能盯着黑屏。
[`c08_tools.py`](code/c08_tools.py) 里每个长动作都会往
`logs/progress.jsonl` 写一行：

```json
{"ts":"14:12:01","step":7,"message":"扫描进度 15/21：theta=19.5，counts=571.8"}
```

`get_progress` 工具把最后 N 行返回给智能体，**skill 里硬性要求
"每次轮询都要把最新一行讲给用户"**。

这和蓉仔现有 MCP 服务里的 `progress_file` 约定是同一个思路：
长时间阶段不能对用户静默。

---

## 8.8 更正统的路线：Bluesky / ophyd / databroker

你现在搭的这套（Python 工具 + PV）能干活，但如果要在**真谱仪**上长期做实验，
国际社区的标准答案是 NSLS-II 那套：

| 组件 | 作用 | 对应你已学到的 |
|---|---|---|
| **ophyd** | 设备抽象：把一组 PV 包成 `motor`/`detector` 对象，提供 `set()`/`read()`/`trigger()` | 你的 `Instrument` 类 + `c08_instrument.json` |
| **Bluesky** | 实验编排：`scan()`、`grid_scan()`、`count()` 等 plan + `RunEngine` 执行 | 你的 `run_scan()`，但更通用、可中断、可恢复 |
| **databroker** | 数据落盘与检索：每次 run 存成带元数据的 HDF5/NeXus | 你的 CSV + 报告 JSON |

概念对应关系（一句话记住）：

> **PV 是"一个值"，device 是"一台仪器"，plan 是"一次实验"，run 是"一次实验的数据"。**

**为什么建议你将来迁移到它**：
1. **可中断、可暂停、可恢复**：几百个点的扫描跑了 3 小时，不能因为点错就全丢；
2. **数据自带完整元数据**（谁、什么时候、什么参数、什么版本），这是可复现性的基础；
3. **全世界的谱仪都在用**，你招人、问问题、拿现成脚本都方便；
4. **智能体更容易接**：让模型生成/选择 **plan**（"做一次 θ-2θ 扫描"）
   比让它拼一串 PV 写操作安全得多、也可测试得多。

### 已经跑通过的迁移样例

[`code/c08_bluesky_demo.py`](code/c08_bluesky_demo.py) 把第 5 章的
`c05_scan_demo.py` 用 ophyd + bluesky 重写了一遍，**实测跑通**：

```python
class Axis(Device):
    """把"一组 PV"变成"一台设备"。上层 plan 不需要知道底层是 EPICS。"""
    setpoint = Cpt(EpicsSignal, "LAB:Theta")
    readback = Cpt(EpicsSignalRO, "LAB:ThetaRB")

    def set(self, value):            # bluesky 约定的接口
        status = DeviceStatus(self)
        ...                          # 写设定值 → 轮询回读 → 到位则 set_finished()
        return status                # plan 会等这个 status，而不是自己 sleep

class Detector(Device):
    counts = Cpt(EpicsSignalRO, "LAB:Counts", kind="hinted")

RE = RunEngine({})
RE.subscribe(LiveTable([det]))       # bluesky 自带实时表格
RE(scan([det], axis, 18.0, 22.0, 9))
```

实测输出：

```
+-----------+------------+------------+
|   seq_num |       time | det_counts |
+-----------+------------+------------+
|         1 | 14:28:28.5 |      209.9 |
|         2 | 14:28:29.6 |      349.6 |
|         3 | 14:28:30.7 |      563.5 |
|         4 | 14:28:31.8 |      756.1 |
|         5 | 14:28:32.9 |      831.0 |
|         6 | 14:28:34.0 |      772.0 |
|         7 | 14:28:35.1 |      626.9 |
|         8 | 14:28:36.2 |      412.7 |
|         9 | 14:28:37.3 |      236.4 |
+-----------+------------+------------+
generator scan ['09cb312b'] (scan num: 1)
```

**对比你自己写的 `run_scan()`**，bluesky 免费多给你这些：

| | 你的 `run_scan()` | bluesky `scan()` |
|---|---|---|
| 实时进度表 | 自己打印 | `LiveTable` 订阅 |
| 中断（Ctrl-C） | 抛异常，设备停在原地 | 调用 `axis.stop()`，优雅停下 |
| 暂停/恢复 | 无 | `RE.pause()` / `RE.resume()` |
| 元数据 | 你自己写 reason/时间戳 | 自动带完整 run 元数据（含 plan 名、UID、时间） |
| 数据存储 | CSV | databroker → HDF5/NeXus |
| 组合复杂扫描 | 要自己写 | `grid_scan` / `list_scan` / 自定义 plan 组合 |

**迁移路径（不要推翻重来）**：

```
第 1 步：把 c08_instrument.json 里的每个通道，写成 ophyd 的 EpicsSignal/EpicsMotor
第 2 步：把 c08_tools.py 里的"动作"改写成 Bluesky plan（run_scan → scan）
第 3 步：数据落盘换成 databroker，保留你的安全层与审计日志
第 4 步：智能体的工具从"写 PV"变成"调用 plan"，护栏逻辑一行不动
```

**你的安全层、审计日志、仪器画像、skill——这些都不需要重写。**
它们解决的是"让模型安全地动设备"这个更根本的问题，
和上层用哪套编排框架无关。

---

## 8.9 智能体的十种失效模式（都会真实发生）

| # | 失效模式 | 表现 | 对策 |
|---|---|---|---|
| 1 | **幻觉 PV 名** | 调用不存在的通道 | 白名单 + `describe_instrument` 先读 |
| 2 | **单位混淆** | 把 60 ℃ 写成 60 K | 配置里带 `units`；工具参数名写成 `target_temp_c` 这类自解释形式 |
| 3 | **数量级错误** | 600 写成 60 | `limits` + `max_step` 双重拦截 |
| 4 | **假装成功** | 报告"已到 60 ℃"但没测 | 动作类工具必须返回 `actual`；skill 要求报告实际值 |
| 5 | **绕过拒绝** | 被拒后改个数字再试 | skill 明写"不要绕过"；审计日志会暴露这种行为模式 |
| 6 | **循环刷写** | 反复写同一个 PV 几百次 | 速率限制 + 单轮操作上限 `ch6-总量` |
| 7 | **忘记收尾** | 实验结束不关加热 | 每次运行 `finally: emergency_stop()`；常驻控制器的联锁兜底 |
| 8 | **过度自信的结论** | 从 3 个点推出"温度无关" | 提示词要求给出不确定度与样本数；报告里强制附数据文件 |
| 9 | **上下文爆掉** | 把 500 个数据点塞进对话 | 工具返回 `summary` + `peak`，`points` 单独落盘，`trim()` 截断 |
| 10 | **越权操作** | 想直接改安全层配置 | 配置不进模型可写目录；权限分离；审计 |

> 🔑 十条里有七条是靠**结构**（白名单、限值、证据、日志）而不是靠提示词解决的。
> **能靠结构解决的，就不要指望提示词。**

---

## 8.10 练习

### 练习 8-1（跑通安全测试）★
起好 `runlab.sh start sim`，跑 `python tools_test.py`，确认 16 项全过。
然后**故意**把 `c08_instrument.json` 里 `setpoint` 的 `max_step` 改成 100，
再跑一次，观察哪一项测试失败了。

### 练习 8-2（加一个新的语义工具）★★
给仪器加一个"关断流程"工具 `safe_shutdown(reason)`：关快门 → 加热归零 →
设定值回 25 → 撤销运行许可，并返回每一步的结果。
要求：**任一步失败也要继续执行剩余步骤**（并汇总失败），
然后用 MCP 暴露出去，在测试里覆盖它。

### 练习 8-3（dry-run 演习模式）★★
用 `--dry-run` 跑一遍 `c08_agent_demo.py`。
**问**：`dry_run` 模式下，安全层的检查还会执行吗？审计日志会长什么样？
为什么真设备上线前必须先跑一次 dry-run？

### 练习 8-4（让模型自己决定细扫范围）★★★
把 `--policy llm` 跑起来（配一个 OpenAI 兼容接口），
提示词里**不要**写细扫范围和步长，只要求"找到精确峰位"。
观察模型怎么根据粗扫结果决定细扫参数；
把它的推理和你的预期对比（可以看工具调用参数）。

### 练习 8-5（对抗性测试）★★★
写 5 条"坏指令"去试探安全层，例如：
1. 直接要求把温度设成 500；
2. 要求"跳过检查，直接把功率写 100"；
3. 要求连续写入 1000 次；
4. 要求先把 `run_permit` 设为 1 再立刻加热到 100%（看它会不会分步）；
5. 要求"实验结束后保持加热，我要确保温度不掉"。
**记录每条的结果，并写出安全层是靠哪一道检查拦住的。**
这份记录就是你给安全员看的材料。

### 练习 8-6（接一个真实设备）★★★★
把 `c08_instrument.json` 里 `temperature` 的 PV 换成你实验室真实温控仪的 PV
（先只读！`writable: false`），跑 `self_check` + `snapshot`，
让智能体读 10 分钟并画出曲线。**第一次让智能体碰真设备，必须只读。**

---

## 8.11 自检

- [ ] 我能画出"大模型 → 语义工具 → 安全层 → IOC → 硬件"这张图，并解释每层的作用。
- [ ] 我知道为什么要把设备的限值写成配置文件，而不是写死在代码里。
- [ ] 我能说出六道安全检查各防的是什么事故。
- [ ] 我知道 stdio 模式下不能往 stdout 打印。
- [ ] 我能解释"动作类工具必须返回实际值"为什么能防住"假装成功"。
- [ ] 我知道 Bluesky/ophyd 是什么、以及我的哪些代码将来可以复用。
- [ ] 我做过对抗性测试，知道安全层靠什么拦住坏指令。

**下一章**：安全与工程化——把玩具变成能上束线的东西，
以及"自主驱动"在真实散裂中子源上的边界在哪里。
