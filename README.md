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

## 2.1 切换drsai_ui来源（external/internal）

在不改代码的情况下，可通过环境变量切换是使用外部安装的drsai_ui，还是使用仓内源码drsai_ui。

- 默认模式（external）：使用当前Python环境里安装的drsai_ui包
- 内置模式（internal）：优先从本仓源码路径加载drsai_ui

建议在.env中增加：

```shell
# external | internal
DRSAI_UI_MODE=external

# 仅internal模式使用，默认就是 /home/wangh/rongzai-agent/src
DRSAI_UI_INTERNAL_SRC=/home/wangh/rongzai-agent/src

# 可选：手动指定前端静态资源同步目标
# DRSAI_UI_STATIC_DIR=/path/to/site-packages/drsai_ui/ui_backend/backend/web/ui
# DRSAI_UI_INTERNAL_STATIC_DIR=/home/wangh/rongzai-agent/src/drsai_ui/ui_backend/backend/web/ui
```

切换后，对应执行：

```shell
cd webui/frontend
yarn build:sync
cd ../..
python run_ui.py
```

## 2.2 统一认证开关

统一认证由 `SERVICE_MODE` 唯一控制。在仓库 `.env` 中设置：

```shell
SERVICE_MODE=PROD
```

同时配置原 DrSai 所需的 `IHEP_SSO_APP_KEY`、`IHEP_SSO_APP_SECRET` 和
`IHEP_SSO_REDIRECT_URI`。重新构建并同步前端后再启动 UI：

```shell
cd webui/frontend
yarn build:sync
cd ../..
python run_ui.py
```

将 `SERVICE_MODE` 改为 `DEV` 后重新构建并重启，即关闭统一认证并只保留本地登录。

### 2.3 95 服务器隔离 DEV 测试环境

生产前端使用 `webui/frontend/public`，不要在生产目录直接执行 DEV 构建。
仓库提供 `scripts/build_test_ui.sh`，它会在独立目录中构建 DEV 静态文件并同步到测试 UI：

```shell
cd /home/dur/rongzai-agent
./scripts/build_test_ui.sh
```

该脚本等价于在临时目录中执行：

```shell
SERVICE_MODE=DEV GATSBY_SERVICE_MODE=DEV yarn build:dev
```

隔离目录如下：

- 临时前端构建目录：`tmp_test/frontend-dev`
- 测试 UI 静态目录：`tmp_test/drsai_ui/ui`
- 测试 UI 数据库：`tmp_test/drsai_ui/drsai_ui.db`

测试环境使用 `.env_test`，其中应保持：

```shell
SERVICE_MODE="DEV"
RONGZAI_UI_PORT="8088"
DRSAI_UI_STATIC_DIR=/home/dur/rongzai-agent/tmp_test/drsai_ui/ui
```

启动测试智能体和测试 UI：

```shell
cd /home/dur/rongzai-agent
conda activate rongzai_agent

nohup python run_agent_95_test.py > run_agent_95_test.log 2>&1 &
nohup python run_ui_95_test.py > run_ui_95_test.log 2>&1 &
```

默认端口分配如下：

| 服务 | 生产端口 | 测试端口 |
| --- | ---: | ---: |
| 智能体 Worker | `42812` | `42818` |
| DrSai UI | `8081` | `8088` |

测试 UI 可通过 `http://10.1.252.95:8088/` 访问。测试静态文件和数据库都位于 `tmp_test`，不会覆盖生产前端或生产 UI 数据库。

停止测试进程：

```shell
pkill -f 'run_agent_95_test.py'
pkill -f 'run_ui_95_test.py'
```

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

pm2 start "conda run -n sasmodels python tools/Irena/0Irena_fit.py" --name "Irena-refine"

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


DDF很可能无法直接访问东莞的服务器，需要有一个能直接访问的服务器做转发。