# RONGZAI Agent使用指南


## 1.安装python环境依赖

- 1.配置智能体python运行环境
```shell
conda create -n rongzai python=3.12
conda activate rongzai
pip install -r requirements.txt
```

- 2.配置gsas-iipython运行环境
```shell
conda create -n gsas2_py311 python=3.11
conda activate gsas2_py311
cd /tools/GSAS-II-master
pip install -r requirements.txt
```

- 3.配置diffpy运行环境
```shell
# conda create -n diffpy.pdffit2_env diffpy.pdffit2
conda create -n diffpy.pdffit2_env python=3.12 conda-forge::diffpy.pdffit2
conda activate diffpy.pdffit2_env
pip install mcp matplotlib
```

## 2.后端ENV变量依赖

```shell
cp .env.example .env
```

配置.env文件，主要配置以下参数：

 - MP_API_KEY="..." # 用于获取mp-cif的api_key

 - HEPAI_API_KEY="..." # 用于获取hepai 大模型的api_key

 - WORKER_SPACE="..." # 用于指定工作目录，指储存RUN数据总目录

## 3.运行RONGZAI Agent 后端

```shell
bash start_tools.sh &
python run_agent.py
drsai ui # 打开前端
```
## 4.remote_agents.json配置
里面的url，如果是走DDF,是"https://aiapi.ihep.ac.cn/apiv2"
如果是走本地，没有速率限制，是"http://10.1.252.95:42812/apiv2"
前后端api切换方法：
走前端api时，将remote_agents.json中的url改为"https://aiapi.ihep.ac.cn/apiv2"，然后run_agent.py中将HepAIChatCompletionClient的api_key= api_key or os.environ.get("HEPAI_API_KEY"),然后将run_worker中的use_api_key_mode = "frontend",重启前后端
走后端api时，将remote_agents.json中的url改为"http://10.1.252.95:42812/apiv2",然后run_agent.py中将HepAIChatCompletionClient的api_key= os.environ.get("HEPAI_API_KEY"),然后将run_worker中的use_api_key_mode = "backend"，重启前后端

## 注意：
    - 1.请确保设备参数和数据文件以RT_GROUP*.instprm和RUN***_group*.gsa的格式保存在每个组的工作目录下。
    - 具体参考datasets文件夹下的数据集格式。
    -  如果找不到apikey， rm -rf ~/.drsai_ui/ 重新启动 drsai ui


pm2 start "conda run -n pyZrietveld python tools/pyZRietveld/0zrietveld_refine.py" --name "zrietveld-refine"

pm2 start "conda run -n gsas2_py311 python tools/GSAS-II-master/GSASII/0neutron_gsas_refine.py" --name "gsas2-refine" 

pm2 start "conda run -n sasmodels python tools/sasview/0sasview_refine.py" --name "sasview-refine"

pm2 start "conda run -n diffpy.pdffit2_env python tools/pdf_refinement/0pdf_refine.py" --name "pdf-refine"

## skill中涉及文件的权限问题
    为了保证代码安全，限制智能体只能在agentWorkSpace, datasets, agent_skills三个文件夹下有操作权限。
    同时，agent_skills被设为只读，防止agent修改系统级skills。
    因此，如果skill中涉及到要求智能体去阅读特定文件，该特定文件必须放入agent_skills中，推荐放在和skill.md同级目录下

pm2常用指令
# 查看当前进程
pm2 list
# 停止指定名称的进程
pm2 stop sasview-service

# 停止指定 ID 的进程
pm2 stop 0

# 停止所有进程
pm2 stop all

# 停止多个进程（空格分隔）
pm2 stop 0 1 2

# 删除进程
stop替换成delete

# 以下已经废弃

## 3.运行Openweb UI前端

### 3.1.安装OpenWebUI

```shell
conda create -n open-webui python=3.11
conda activate open-webui
pip install open-webui
```

**设置HuggingFace镜像：**
将HuggingFace镜像：'HF_ENDPOINT '= 'https://hf-mirror.com'加入到本地的环境。对于Linux系统，可以将其加入到~/.bashrc文件中：
```export HF_ENDPOINT=https://hf-mirror.com```

在命令行中运行：```open-webui serve --port 8088```启动OpenWebUI服务。

### 3.2.配置OpenWebUI Pipeline

pipeline的密钥：```0p3n-w3bu!```，将密钥和pipeline后端服务的地址：```http://localhost:42808/pipelines```加入OpenWebUI的```管理员面板-设置-外部连接-管理OpenAI API连接```中。


\