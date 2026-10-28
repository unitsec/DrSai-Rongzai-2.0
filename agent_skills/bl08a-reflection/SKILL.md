---
name: bl08a-reflection
description: 处理CSNS BL08A单组或多组中子反射数据；缺少直通身份或Rz时询问用户，生成总反射率图和汇总表。
---

# BL08A 中子反射处理

1. 用户无需手动分组。默认将提供的全部RUN置于同一groups项的runs中，调用process_neutron_reflection_groups（type=sse，url=http://127.0.0.1:${MCP_NEUTRON_REFLECTION_PORT}/sse）。数据源仅用管理员配置别名，默认default；不同数据源分项，不猜路径、不搜索其他RUN。若验证工具已提供课题/年份目录，可传data_root绝对路径；必须位于该data_source的管理员根目录内，不同目录分项；自动匹配仅限本项提供的RUN。
2. 工具先按Rz识别直通/反射，再比较lamda_min、lamda_max（波长范围）及S1H/S1W/S3H/S3W（狭缝开口）。六项均为有效正数且逐项max/min<=1.01才匹配，不按平均差异、不链式分组。唯一候选自动使用。
3. 缺Rz时，按questions只追问未知RUN的身份和反射角。roles填写用户确认的direct/reflection；angles_deg只填用户明确提供的反射角（度）。直通明确身份即可，不伪造Rz=0、不改原始JSON。已有信息不重复询问。
4. 多个候选直通时列出候选，询问逐条指定还是统一使用其中一个。用户选择后填direct_assignments（反射RUN到直通RUN）或direct_run（本项统一直通）。统一使用前必须对每条反射都匹配；不自行任选、不平均直通。
5. 用户明确指定对应关系时保留其分项及选择，但仍需六项校验。不匹配展示具体字段、双方值、超限百分比，要求重新提供匹配直通；缺失/无效设置说明无法校验。无匹配则请求补充直通RUN。Rz身份/角度冲突只在用户确认后设置confirm_metadata_override=true，不能豁免六项校验。needs_input时未计算，补齐后重交完整请求。
6. user_id、session_id必须使用框架Current user_id和Current session_id，缺失时不得猜测。mission_name为简短英文/数字/下划线/连字符。每次实际计算生成独立task_id，目录为WORKDIR/user_id/session_id/mission_name/task_id。
7. success返回的public_files恰好四项：representative_plot（单张m/R标注图）、combined_plot（总图）、summary_file（CSV）、curve_data_zip（所有反射率数据ZIP）。image_delivery=files_event表示四项都统一通过serve_files_for_user发送，每项提供清晰描述，先示意图、再总图、再CSV和ZIP。PNG使用文件事件内嵌内容预览和下载，不引用/files/user/等旧静态链接，不再单独用Markdown嵌入同一图片。然后回复presentation.table_markdown、presentation.conclusion、presentation.method_markdown。单RUN图只提供representative_run这一条，不扫描其他图。只有工具真实成功返回后才能说明已提供，不编造链接或已显示。
8. summary.csv依次包含Run、m、r_envelope(%)三列，第一列必须保留反射RUN编号，不包含直通行。RUN编号标识测量，不自动等同于样品编号。比较以每条反射RUN为单位，不自动合并为物理样品。程序按m由小到大分组，每组max(m)/min(m)<=1.10，组内按包络反射率排序，相对提升为(R最高-R最低)/R最低*100%。直接使用程序conclusion，不自行重新分组、计算或捏造排名；只能称反射率表现较好，不能扩大为综合性能最佳。待复核或无效结果不参与确定性排名；没有可比较组时只使用程序给出的暂不排名结论。
9. 方法说明直接使用presentation.method_markdown，仅给模型公式来源及当前m、包络R定义，不加验收/认证声明、质量警告清单或额外拟合参数。其他细节留内部文件。结论必须使用程序生成的界面质量及厚度通用优化建议。只有用户明确提供共同设计目标时才填target_m；不同目标分项；没有目标不推断厚薄，不把10%比较分组当设计容差。计算期间不输出思考或进度旁白，不重复确认任务结束或等待下一步。
10. failed时仅简短报告未完成及可操作原因，绝不显示成功表或硬排名。needs_input时只询问缺少的参数，收到后继续；不能为追求简洁跳过必要询问。常规质量标记留内部记录，不主动读取并复述diagnostics_file。没有可靠可比结果时保留程序的暂不排名结论。
11. 当前MCP只用于受信框架本机调用，不认证传入user_id，目录隔离不等于访问授权。原始数据只读，MCP结果先保存在当前用户工作区；需要数据库归档时，遵循框架权限工具返回的目录与写回要求，工作区生成成功不代表数据库归档成功。
