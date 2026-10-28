---
name: bl08a-reflection
description: 处理CSNS BL08A单组或多组中子反射数据；缺少直通身份或Rz时询问用户，生成总反射率图和汇总表。
---

# BL08A 中子反射处理

1. 获取用户明确给出的分组，不能将不同直通/实验的数据混组。每组必须包含一个直通RUN和至少一个反射RUN。没有说清分组时先问清，不猜测编号、分组、身份或角度。
2. 连接MCP：type=sse，url=http://127.0.0.1:${MCP_NEUTRON_REFLECTION_PORT}/sse。阅读工具schema，优先使用process_neutron_reflection_groups（单组也可使用）。原process_neutron_reflection仅兼容元数据完整的旧单组调用。
3. groups每项使用runs编号列表；数据按课题/年份存放时，data_root填写所提供的数据根目录（其下为RUN/RUN），同组RUN必须来自同一data_root，否则分别分组。省略data_source时使用管理员default数据源。用户明确指定直通时传direct_run，明确提供反射角时传angles_deg（RUN到数值的映射，单位度）。禁止把缺失Rz当成0，禁止修改原始JSON，不猜测数据路径或自动搜索其他目录。
4. 不知道元数据是否完整时，可以不填direct_run及angles_deg，先调用工具预检。返回needs_input时还没有计算：按questions询问用户本组哪个RUN是直通、其余各反射RUN的角度分别是多少度。用户已明确回答的值不要重复询问。收到补充后保留原分组和其他已知参数，重新提交完整groups。
5. 元数据与用户输入冲突时，向用户展示冲突值，只有用户明确确认后才能设置confirm_metadata_override=true。不能为通过校验自行确认。缺字段、文件不存在、数据源未配置、计算失败是不同问题；不能用编造角度掩盖文件错误。
6. user_id、session_id必须使用框架Current user_id和Current session_id，缺失时不得猜测。mission_name为简短英文/数字/下划线/连字符。每次实际计算生成独立task_id，目录为WORKDIR/user_id/session_id/mission_name/task_id。
7. success后读取实际返回的summary_file，在对话中展示总表；使用框架文件展示工具提供combined_plot和summary_file。summary.csv严格只有m、r_envelope(%)两列，仅含反射RUN，按用户输入的分组和RUN顺序排列。需对应编号时读取summary_row_order_file，不向CSV追加编号或其他参数。总图为带原始dR误差棒的散点，不连接数据点、不拼接/平均；图例标明组别和RUN。只用实际返回的可访问地址嵌图，不把服务器路径冒充网页链接，不编造已显示。
8. failed不得报告全部成功；needs_input不代表完成。success只表示文件生成完成，不等于质量验证或厂家验收通过。当前默认使用PHITS 3.37性能经验模型，不是膜层结构拟合。用户可见性能指标仅m和包络反射率：m为拟合截止中心/Qc_Ni；r_envelope(%)为下降前包络外推到该中心的高度，不是中心处实际曲线反射率。其他拟合参数留在内部mr_estimates.json中，除用户明确要求外不主动展示。analysis_status=needs_review及warnings仍须简短说明，不得宣称已验收。
9. PHITS方法的单RUN图片为reflectivity.png和reflectivity_fit_diagnostic.png（后者包含局部拟合与残差，不是导数图）。优先使用返回groups内individual_plots和mr_parameters_file，按用户要求展示单独图，不要只提供总图。宽度以截止因子相对包络的90%-10%或99%-1%定义，不是绝对反射率90%和10%。
10. 当前MCP只用于受信框架本机调用，不认证传入user_id，目录隔离不等于访问授权。原始数据只读，结果保存在当前用户工作区。
