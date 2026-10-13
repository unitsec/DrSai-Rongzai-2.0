# BL08A 中子反射 MCP 工具

本目录包含 MCP 入口、接口处理代码、计算 m/r 的模块、算法配置和仪器参数。
核心规约算法依赖单独安装的 rongzai 4.0 包，不包含在本目录。

## 环境与依赖

使用 Python 3.12。为反射工具使用独立虚拟环境。
先安装 requirements.txt，再从单位认可的软件源或安装包安装 rongzai 4.0。
服务器已验证的安装包名为 rongzai-4.0-py3-none-any.whl。
安装包不随本提交提供，部署者需要向算法维护者获取。
不要假设公共 PyPI 上的同名包就是所需算法包。

    python3 -m venv /absolute/path/to/reflection-venv
    /absolute/path/to/reflection-venv/bin/python -m pip install -r tools/neutron_reflection/requirements.txt
    /absolute/path/to/reflection-venv/bin/python -m pip install /approved/path/rongzai-4.0-py3-none-any.whl

## 配置与启动

在仓库根目录执行：

    cp tools/neutron_reflection/interface/machine_paths.json.example tools/neutron_reflection/interface/machine_paths.json

编辑新配置，将 data_root 设置为原始数据目录，param_root 设置为本目录
interface/paramsdata 的服务器绝对路径。save_root 仅用于接口的直接调用；
MCP 调用使用每次生成的独立输出目录。

    export WORKDIR=/absolute/path/to/agentWorkSpace
    export MCP_NEUTRON_REFLECTION_PORT=42520
    /absolute/path/to/reflection-venv/bin/python tools/neutron_reflection/reflection_mcp.py

入口默认导入本目录 interface 下的处理代码。
如需兼容外部接口部署，可设置 NEUTRON_REFLECTION_INTERFACE_DIR 覆盖默认目录。
工具与 Agent 需共享同一结果工作区。
服务监听 127.0.0.1:${MCP_NEUTRON_REFLECTION_PORT}，SSE 地址为
http://127.0.0.1:${MCP_NEUTRON_REFLECTION_PORT}/sse。也可以在仓库根目录 `.env`
中设置 `MCP_NEUTRON_REFLECTION_PORT`；入口会在启动时加载该文件。
当前按 Agent 与工具在同一台机器、同一网络环境部署。

## 调用与身份信息

工具名：process_neutron_reflection。
参数：runs、user_id、session_id、mission_name，全部必填。
示例：

    {"runs": ["RUN0000963", "RUN0000964"], "user_id": "laiyy", "session_id": "session1", "mission_name": "reflection"}

框架通过 ConnectMcpServer 连接工具，新增技能位于
agent_skills/bl08a-reflection/SKILL.md。
沿用现有 PDF 工具方式，通过框架提示词及技能说明要求模型传入当前用户、会话。
尚未修改公共 Agent 代理自动注入身份，也没有在 MCP 服务中认证账号。
身份字段及任务名支持英文、数字、下划线、@、点、加号和连字符，最长 128 字符；
拒绝路径分隔符及 .、..。部署前需确认实际框架标识与此格式兼容。

## 输出

数据、图片和汇总 CSV 放在：

    WORKDIR/user_id/session_id/mission_name/task_id/

每次调用自动生成唯一 task_id。同用户、同会话、同任务重复调用也不会覆盖。
返回 status、summary_file、output_path、user_id、session_id、mission_name、task_id。
算法配置在内存中按本次调用构建，不修改共享 JSON 模板。

## 已验证及待验证

服务器使用 RUN0000963、RUN0000964 已验证：
- 原接口直接调用、MCP 调用、框架 ConnectMcpServer 注册及代理调用成功。
- 新版输出隔离：不同用户、不同会话、重复调用产生独立目录。
- 各目录均生成反射率 .dat、两张图片及 summary.csv。

框架测试跳过模型初始化。仓库内整合部署还需复测；
完整智能体启动及自然语言自动传参尚未验证。

## 当前限制

- 目录分离不等同于身份认证、文件访问权限或原始数据授权。
- 保留进程内串行执行，避免 matplotlib 并发问题。
- 失败的任务目录可能保留部分结果，失败不会返回 success。
- 日志仍使用接口目录内共享 BL08A.log，未按会话隔离。
- 返回服务器路径，尚未接入网页下载及图片展示。
- 算法出现过除零警告，数据质量仍需检查。
- 当前以前台服务运行，未配置自动重启或开机启动。
