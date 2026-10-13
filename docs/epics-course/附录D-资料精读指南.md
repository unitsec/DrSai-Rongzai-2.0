# 附录 D　资料精读指南：你手上那批 EPICS 官方文档怎么读

控制组给你的那批文档都是好东西，但**直接从头读会劝退**：
它们写给的读者是"已经在做控制系统的人"，不是"想接设备的应用开发者"。

下面逐份给你"读什么、跳过什么、什么时候读"。

---

## D.1 一份对照表（先看这个）

| 你手上的资料 | 性质 | **什么时候读** | 读哪部分 |
|---|---|---|---|
| [EPICS 文档中心](https://docs.epics-controls.org/) | 导航页 | 随时 | 先只看目录，建立"有哪些东西"的印象 |
| [Getting Started / 系统概述](https://docs.epics-controls.org/en/latest/getting-started/EPICS_Intro.html) | 概念 | **读完本书第 2 章后立刻读** | 全篇（就几页），用来校准你的心智模型 |
| [Application Developer's Guide (R3.16.2)](https://epics.anl.gov/base/R3-16/2-docs/AppDevGuide/AppDevGuide.html) | 圣经 | **第 4 章学完后当工具书查** | ①记录类型与字段（第 6 章）②`calc` 表达式语言（第 8 章）③Access Security（第 16 章） |
| [Build Facility](https://docs.epics-controls.org/en/latest/build-system/specifications.html) | 编译系统 | 需要编译模块时 | `RELEASE`/`CONFIG_SITE`/交叉编译那几节；**初学跳过** |
| [Channel Access 参考](https://epics.anl.gov/docs/ca.php) | 协议规范 | 排网络问题时 | `caget/caput/camonitor` 的 man 页最有用；协议细节初学跳过 |
| [pvAccess 协议规范](https://docs.epics-controls.org/en/latest/pv-access/protocol.html) | 协议规范 | 用 `p4p` 之后 | 只需要"Normative Types"一节；其余跳到你要用的部分 |
| [pvAccess 概述 / pvData](https://docs.epics-controls.org/en/latest/pv-access/OverviewOfpvData.html) | 概念 | 读完本书第 5 章后 | 全篇（理解"结构化数据"这一件事就够了） |
| [CA 协议规范（内部）](https://docs.epics-controls.org/en/latest/internal/ca_protocol.html) | 协议细节 | **基本不需要** | 除非你要自己实现一个 CA 客户端/服务端 |

> 💡 **反直觉但重要的建议**：这八份资料里，**对你（应用开发者）价值最高的
> 是 "Getting Started" 和 "App Developer's Guide 的记录/字段部分"**，
> 价值最低的是两份协议规范——协议由库作者负责，你只需要知道"CA 没有认证加密"。

---

## D.2 按"你遇到问题"来读（更实用）

| 你想知道 | 去读 |
|---|---|
| EPICS 整体是什么、为什么这么设计 | Getting Started；本书第 2 章 |
| 某个字段是什么意思 | App Dev Guide 的"Fields Common to Many Record Types" + 在线 [Record Reference](https://docs.epics-controls.org/en/latest/）；或直接 `dbpr("PV",5)` |
| `calc` 表达式怎么写 | App Dev Guide 的 "Calculation (CALC) Expression" 一节；本书第 4 章 |
| 记录为什么不更新 | App Dev Guide 的 "Processing" + 本书 4.6 节 |
| 怎么写自己的设备支持 | App Dev Guide 的 "Device Support"；**先别急**，先用 asyn/StreamDevice |
| 怎么跨网段访问 | CA Gateway 文档（不是你手上这几份）；本书 9.3 节 |
| 怎么写界面 | Phoebus 文档；本书不涉及（但要能看懂操作员在做什么） |
| 怎么做扫描/自动化 | 本方资料里没有——去读 [Bluesky 文档](https://blueskyproject.io/)，本书第 8.8 节给了迁移路径 |

---

## D.3 三条阅读技巧

1. **先查字段名，再读章节。**
   你几乎不会"通读"App Dev Guide，而是"要写一个 `ao` 了，去查 `ao` 的字段表"。
   这很正常——控制组的工程师也是这么用的。

2. **版本文档只用来看"概念"，不用来抄"取值"。**
   你手上的 App Dev Guide 是 **R3.16.2**，而你现场可能是 7.0.x。
   记录类型和字段名基本稳定，但**菜单字符串会变**
   （我在 7.0.10 上实测 `OMSL` 是小写的 `closed_loop`）。
   凡是"能取哪些值"，一律以本机 `dbd` 或用 `dbpr` 现场确认。

3. **卡住时去 tech-talk 邮件列表搜索。**
   [EPICS 邮件列表](https://epics-controls.org/resources-and-support/mailing-lists/)
   从 1990 年代到现在都在用，你遇到的 90% 报错都有人问过。
   搜之前先把报错原文复制进去（去掉路径信息）。

---

## D.4 建议的阅读顺序（配合本书）

```
本书第 1~2 章        →  建立心智模型
Getting Started      →  校准模型（1 小时，必读）
本书第 3~4 章        →  动手：跑 IOC、写 .db
App Dev Guide 的     →  当工具书：记录类型 / 字段 / calc 表达式
  记录与字段章节
本书第 5 章          →  客户端
CA 参考里的 man 页    →  caget/caput/camonitor 参数（5 分钟）
本书第 6~7 章        →  闭环与硬件
（可选）pvData 概述   →  理解结构化数据
本书第 8~10 章       →  智能体驱动 + 安全 + 实战
Build Facility        →  只在你要编译 asyn/motor/StreamDevice 时读
协议规范两份          →  需要时查，不需要通读
```

---

## D.5 你在读这些文档时会看到的"黑话"对照

| 文档里的说法 | 实际意思 |
|---|---|
| "device support" | 一段 C 代码，负责把记录的值和硬件对上 |
| "soft device support" | 不用硬件，值由客户端写（学习用） |
| "link" / "INP field" | 记录上的一个"来源"描述：`@asyn(...)` 是硬件，`PV` 名是另一个记录 |
| "process the record" | 让这条记录跑一遍它的处理逻辑（读设备/算表达式/往外写） |
| "passive record" | 不会自己跑，得有人推它 |
| "record instance" | 记录名 + 类型的一个具体记录（`LAB:TempRaw` 就是一条 `ai` 的实例） |
| "channel" | 客户端视角的一个 PV 连接 |
| "beacon" | IOC 广播"我在线"的 UDP 包（排查连接问题时有用） |
| "the record is UDF" | 这条记录从来没被写过，"未定义状态" |
| "ASG" | Access Security Group，访问控制组 |
| "cross-compile for RTEMS" | 给嵌入式实时系统编译 IOC —— 运维层面的事，多半不需要你做 |

---

## D.6 一句话总结

> 这批文档用来**查**，不用来**读**。
> 真正的学习路径是"跑一遍、坏一遍、修一遍"，
> 然后再回文档里找"为什么"——那时你会发现文档写得其实很清楚。
