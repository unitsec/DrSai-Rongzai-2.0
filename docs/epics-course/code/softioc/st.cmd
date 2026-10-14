# =============================================================================
# st.cmd —— 启动脚本（iocsh 命令，一行一行执行）
#
# 用法（在 code/softioc 目录下）：
#     softIoc st.cmd
# 或者不用脚本：
#     softIoc -d db/lab.db -m P=LAB:
#
# 说明：这是"最小可用"的启动脚本。真实 IOC 的 st.cmd 会长得多，
#       通常还会包含：dbLoadTemplate、asyn 端口配置、drvAsynIPPortConfigure、
#       StreamDevice 协议文件、autosave 恢复、Access Security 等等。
# =============================================================================

# --- 1. 加载数据库（$(P) 宏在这里被替换） ---------------------------------
dbLoadRecords("db/hello.db", "P=LAB:")
dbLoadRecords("db/lab.db",   "P=LAB:")

# --- 2. 初始化 IOC：这一刻数据库才真正开始运行 ----------------------------
iocInit()

# --- 3. 启动后自检（可选，但非常有用） ------------------------------------
# 注意：iocsh 的命令行尾不支持 # 注释，注释要单独占一行！
dbl
# dbpr("LAB:TempRaw", 5)

# 小技巧：iocsh 支持变量，可以这样批量设置初值
# dbpf("LAB:SetPointTemp", "25")
