# BL08A 中子反射工具接入

将外部反射程序的 agent_interface.process_reflection 封装为 MCP SSE 工具。

## 前置条件

- 单独部署反射处理程序及其依赖文件。
- 使用已验证能运行反射接口的 Python 环境。
- 此环境安装 mcp>=1.15.0,<2。
- machine_paths.json 中的数据、输出和参数路径适用于服务器。
- 待处理 RUN 的原始数据和元数据已存在。

## 启动

在仓库根目录运行以下命令，替换其中的绝对路径：

    export NEUTRON_REFLECTION_INTERFACE_DIR=/absolute/path/to/reflection
    /absolute/path/to/reflection/venv/bin/python tools/neutron_reflection/reflection_mcp.py

服务仅监听本机，SSE 地址：
http://127.0.0.1:42520/sse

工具名称：process_neutron_reflection
参数示例：{"runs": ["RUN0000963", "RUN0000964"]}

框架通过 ConnectMcpServer 连接服务，type 为 sse，url 为上述地址。
agent_skills/bl08a-reflection/SKILL.md 提供使用场景和调用流程。

## 已完成验证

服务器测试数据：RUN0000963、RUN0000964。

- 反射接口直接调用成功。
- MCP 工具发现及实际调用成功。
- 仓库内 MCP 入口调用成功。
- 框架 ConnectMcpServer 注册代理成功。
- 通过框架代理处理数据成功，返回 summary_file 和 output_path。

框架测试跳过模型初始化，只验证 MCP 连接和代理调用；
完整智能体启动及自然语言自动调度尚未验证。

## 当前限制

- 外部反射处理程序不随本目录提供，需要单独部署。
- 使用公共输出目录，重复处理可能覆盖同名结果。
- 单个服务进程内串行执行，尚未实现用户和会话输出隔离。
- 返回服务器路径，尚未接入网页下载和图片展示。
- 计算出
## 当前限制

- 外部反射处理程序不随本目录提供，需要单独部署。
- 使用公共输出目录，重复处理可能覆盖同名结果。
- 单个服务进程内串行执行，尚未实现用户和会话输出隔离。
- 返回服务器路径，尚未接入网页下载和图片展示。
- 计算出现过除零警告，数据质量需要进一步检查。
- 当前为前台服务，尚未配置自动重启或开机启动。
