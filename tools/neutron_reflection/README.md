# BL08A 中子反射工具

通过本机MCP接入单组或多组BL08A反射规约。各组使用一个直通RUN独立归一化，
输出带原始dR误差棒的反射率散点总图，以及只有m和包络反射率的汇总表。

## 运行环境

Python 3.12；建议使用独立环境：

```bash
python3 -m venv /absolute/path/to/reflection-venv
/absolute/path/to/reflection-venv/bin/python -m pip install -r tools/neutron_reflection/requirements.txt
/absolute/path/to/reflection-venv/bin/python -m pip install /approved/path/rongzai-4.0-py3-none-any.whl
```

rongzai 4.0须从算法维护者或认可的软件源获取，不随代码交付，不能假定公共PyPI同名包可用。

## 管理员配置

在框架仓库根目录执行：

```bash
cp tools/neutron_reflection/interface/machine_paths.json.example tools/neutron_reflection/interface/machine_paths.json
```

machine_paths.json为本机配置，不提交版本库。正式统一目录部署只需：

```json
{
  "data_root": "/absolute/path/to/raw_data",
  "save_root": "/absolute/path/to/manual_results",
  "param_root": "/absolute/path/to/repository/tools/neutron_reflection/interface/paramsdata"
}
```

data_root下每个RUN有同名元数据文件及算法需要的探测器数据，例如RUN0000001/RUN0000001。
可选data_sources对象仅供多目录测试/部署，键为管理员别名、值为数据根目录。
省略调用参数data_source时使用default，即data_root；不会自动搜索其他目录。
reflection_config.py保留原配置合并方式，只对实际引用的字符串路径替换模板，
避免rongzai 4.0旧加载器把data_sources字典传入str.replace的问题；不修改安装的算法包。

启动：

```bash
export WORKDIR=/absolute/path/to/agentWorkSpace
export MCP_NEUTRON_REFLECTION_PORT=42520
/absolute/path/to/reflection-venv/bin/python tools/neutron_reflection/reflection_mcp.py
```

Agent和工具需共享结果工作区，当前按同机部署设计。监听127.0.0.1:${MCP_NEUTRON_REFLECTION_PORT}，
SSE地址http://127.0.0.1:${MCP_NEUTRON_REFLECTION_PORT}/sse。可用NEUTRON_REFLECTION_INTERFACE_DIR覆盖默认内置接口目录。

## 分组输入与缺参交互

新增工具process_neutron_reflection_groups，必填groups、user_id、session_id、mission_name。
groups每项字段：

|字段|说明|
|---|---|
|runs|必填，至少一个直通与一个反射RUN，格式RUN加七位数字；组内不能重复|
|group_name|可选显示名称；输出目录使用自动生成group_001等标识|
|data_source|可选管理员别名，默认default|
|direct_run|用户明确提供直通身份时填写，必须属于本组|
|angles_deg|反射RUN到角度数值的映射，单位度，不能填直通RUN|
|confirm_metadata_override|默认false，仅用户明确确认元数据冲突覆盖后为true|

用户必须明确哪些RUN属于一组，Agent不得猜测分组。元数据角度读取
summary.json.devices.Rz（按嵌套JSON键summary→json→devices→Rz）。
缺少devices/Rz时不视为零；返回needs_input/questions，由Agent询问直通编号和
每个反射角后重新提交完整分组。预检所有组通过前不执行任何规约，不创建任务输出目录。
元数据与用户指定身份或角度冲突时需明确确认；原始JSON不修改。
文件不存在或JSON损坏属于数据错误，不冒充缺少角度。

旧工具process_neutron_reflection(runs,user_id,session_id,mission_name)保留，
仅用于元数据完整、恰好一个直通的旧单组输入；新流程优先使用分组工具。
身份必须来自框架当前用户和会话，不猜测。当前通过框架提示词/skill传入，
未实现公共代理自动注入身份，也未在MCP内认证user_id。

## 输出

每次实际计算创建唯一task_id：

```text
WORKDIR/user_id/session_id/mission_name/task_id/
  combined_reflectivity.png
  summary.csv
  summary_row_order.json
  resolved_groups.json
  group_001/
    summary.csv
    summary_row_order.json
    mr_estimates.json
    BL08A/...
    plot/RUNxxxxxxx/reflectivity.png
    plot/RUNxxxxxxx/reflectivity_fit_diagnostic.png
```

summary.csv严格只有m、r_envelope(%)两列，仅列反射RUN，按输入组和组内RUN顺序排列。
行号与RUN对应关系另存summary_row_order.json，其他参数留内部JSON，不默认展示给用户。
总图是各RUN的散点和原始dR误差棒，点间不连线，不拼接、不平均。
对数纵轴不能显示的非正反射率及无效误差不用于总图，忽略数量写入warnings，不改原始数据。
反射率.dat、各RUN图与每组CSV均保留。返回individual_plots等路径供框架文件展示工具使用，
服务器路径不能冒充下载URL。单独图需由框架显式展示，MCP返回路径不等于网页已回显。

返回status=needs_input时未开始计算；failed可能留下部分输出，不得宣称批次全部成功。
success表示文件生成完成，参数质量由analysis_status及warnings另报。

## m与包络反射率的定义

默认参考PHITS 3.37手册5.15节的公开经验模型：
https://phits.jaea.go.jp/manual/PHITS-en/chapters/sections/supermirror.html

```text
Qc_Ni = 0.0217 Å^-1
Q <= Qc_Ni: R(Q)=R0
Q > Qc_Ni:
  B(Q)=R0*[1-alpha*(Q-Qc_Ni)]
  R(Q)=B(Q)*[1-tanh((Q-Qstar)/W)]/2
m=Qstar/Qc_Ni
r_envelope(%)=100*B(Qstar)
```

包络值是平台/缓慢下降包络外推到拟合截止中心的高度，不是R(Qstar)；
在此模型中R(Qstar)=B(Qstar)/2，但中心反射率不在用户CSV中输出。
自动窗口、1%包络尺度误差下限、soft_l1稳健损失、多初值及质量阈值是本项目分析约定，
不是PHITS指定的实验算法或厂家验收规程。拟合窗口和参数写入mr_estimates.json。
该方法不做膜层结构拟合、不卷积仪器分辨率，也不提供统计置信区间。
R0>1、连续同向边沿大残差等情况标needs_review，不裁成看似正常的值。
NEUTRON_REFLECTION_MR_METHOD=local_edge可切回保留的局部导数选段算法作对照。

## 验证与限制

```bash
python -m unittest discover -s tools/neutron_reflection -p 'test_*.py' -v
```

34项测试；设置REFLECTION_REGRESSION_DATA为包含4条指定导出曲线的目录时运行全部测试，
未设置时2个实际曲线回归项跳过。测试覆盖缺角度、冲突确认、独立归一化路由、
PHITS公式与参数恢复、异常点、CSV两列以及总图不连线和原始误差棒。

已在Linux服务器网页端完成两组实际数据端到端试跑，包含缺失角度用户补录。
RUN0000963使用元数据，RUN0000216～218使用用户提供的1.22度与RUN0000219直通。
216～218的对称截止模型残差仍需复核；216、217的R0略高于1。
这些记录证明流程可运行，不证明科学参数或厂家指标已通过验收。

MCP保持进程内串行计算；目录隔离不等于账号认证或原始数据授权。
日志仍为接口共享BL08A.log；失败可能留下部分文件；未提供自动重启/开机启动。
服务器路径配置、key、数据、结果和部署备份不应进入MR。

## MCP 端口配置

MCP_NEUTRON_REFLECTION_PORT 默认42520，也可在仓库根目录 .env 中配置；入口启动时加载该文件。Agent会解析skill地址中的端口占位符。工具端口与Agent解析后的连接端口必须一致。
