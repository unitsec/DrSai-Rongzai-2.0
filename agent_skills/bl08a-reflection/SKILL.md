---
name: bl08a-reflection
description: 处理 CSNS BL08A 中子反射原始数据，进行反射率规约、绘图及计算 m、r。
---

# BL08A 中子反射处理

1. 获取用户提供的 RUN 编号，必须包含且仅包含一个直通 RUN，并包含反射 RUN。工具自动分类，不编造编号。
2. 连接 MCP 服务，type 为 sse，url 为 http://127.0.0.1:${MCP_NEUTRON_REFLECTION_PORT}/sse。
3. 阅读工具描述及参数。调用 process_neutron_reflection，传入 runs、user_id、session_id、mission_name。user_id 和 session_id 使用框架提供的 Current user_id 和 Current session_id，缺失时不得猜测。mission_name 使用简短英文、数字、下划线或连字符标识，同一任务保持一致。
4. 每次调用自动创建唯一 task_id 子目录，文件存放于 WORKDIR/user_id/session_id/mission_name/task_id。根据实际返回值报告结果路径，不将服务器路径冒充下载链接。
5. 输出目录分离不等同于账号权限控制；当前服务不会认证传入的 user_id。仅用于受信框架在本机调用，不直接开放给用户。
6. 工具执行数据规约，不执行反射模型拟合。success 不代表数据质量已经验证。
