# RONGZAI Agent部署指南


## 1.安装python环境依赖

- 1.配置智能体python运行环境
```shell
conda create -n rongzai-agent python=3.12
conda activate rongzai-agent
pip install -r requirements.txt
```

- 2.配置gsas-ii python运行环境
```shell
conda create -n gsas2_py311 python=3.11
conda activate gsas2_py311
cd tools/GSAS-II-master
pip install -r requirements.txt
```

- 3.配置diffpy运行环境
```shell
conda create -n diffpy.pdffit2_env python=3.12 conda-forge::diffpy.pdffit2
conda activate diffpy.pdffit2_env
pip install mcp matplotlib
```

- 4.配置sasview/Irena运行环境
```shell
conda create -n sasmodels python=3.11
conda activate sasmodels
cd tools/sasview
pip install -r requirements.txt
```

## 2. 构建和启动智能体人机交互端

以下步骤适用于第一次在新服务器部署。前端基于 Gatsby 5，需要 Node.js 18
及以上版本，推荐使用 Node.js 22 和 Yarn Classic 1.22.x。Node.js 只用于构建
前端静态文件，运行 `run_ui.py` 时仍需使用第 1 节创建的 Python 环境。

### 2.1 安装 Node.js 和 Yarn

推荐使用 nvm 安装 Node.js，避免影响服务器中已有的软件：

```shell
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.1/install.sh | bash
source "$HOME/.nvm/nvm.sh"
nvm install 22
nvm use 22
npm install -g yarn@1.22.22
```

确认版本：

```shell
node --version   # 应为 v22.x，最低不能低于 v18
yarn --version   # 应为 1.22.x
```

如果服务器无法访问 GitHub 或 npm/yarn 软件源，需要先配置代理或内网镜像。

### 2.2 检查运行环境配置

在仓库根目录创建 `.env`（第 2 节已经执行过则无需重复）：

```shell
cd /path/to/rongzai-agent
cp .env.example .env
```

将 `.env` 中以下路径改为当前服务器上仓库的绝对路径，不要保留示例中的
`/home/wangh/rongzai-agent`：

```shell
WORKDIR="/path/to/rongzai-agent/agentWorkSpace"
SYSTEM_SKILLS_DIR="/path/to/rongzai-agent/agent_skills"
DRSAI_UI_USER_FILES_ROOT="/path/to/rongzai-agent/tmp/drsai_ui/files/user"
DRSAI_UI_MODE=internal
DRSAI_UI_INTERNAL_SRC=/path/to/rongzai-agent/src
```

`.env.example` 与 `.env` 使用相同的变量顺序和分组；每个变量上方都有用途、取值格式和默认行为说明。
从示例复制后，至少检查路径、`HEPAI_API_KEY`、运行模式以及服务端口。不要把 `.env` 或任何真实凭据提交到版本库。
`DRSAI_UI_MODE=internal` 表示使用仓库自带的 `src/drsai_ui`，前端静态文件会同步到：

```text
src/drsai_ui/ui_backend/backend/web/ui
```

### 2.3 首次安装前端依赖并构建

首次部署或 `yarn.lock` 发生变化时，先安装依赖：

```shell
cd /path/to/rongzai-agent/webui/frontend
yarn install --legacy-peer-deps
```

然后构建前端并同步到内置 UI 静态目录：

```shell
yarn build:sync
```

构建成功后，最后一行应类似：

```text
Synced frontend static files to: .../src/drsai_ui/ui_backend/backend/web/ui (mode=internal)
```

后续仅修改前端源码时，通常直接重新执行 `yarn build:sync` 即可，不需要重复
执行 `yarn install`。

### 2.4 启动人机交互前后端

打开一个新终端，进入仓库根目录并激活第 1 节创建的 Python 环境：

```shell
cd /path/to/rongzai-agent
conda activate rongzai-agent
python run_ui.py
```

看到 `Application startup complete` 后，在浏览器访问：

```text
http://服务器IP:8081/
```

服务默认监听 `0.0.0.0:8081`。如果远程无法访问，请检查服务器防火墙和
安全组是否放行 TCP 8081。端口可在 `.env` 中通过 `RONGZAI_UI_PORT` 修改。
MCP 工具端口同样集中在 `.env` 的 “Service ports” 分组：Worker 使用 `42501`，
GSAS-II、PDF、SasView、Irena 和 Z-Rietveld 分别默认使用 `42504`、`42505`、
`42503`、`42506` 和 `42511`。修改端口后需重启对应服务。

`run_ui.py` 只启动人机交互页面及其 UI 后端。要让页面中的智能体正常响应，
还需要按第 4 节启动 RONGZAI Agent Worker。

远程智能体暂停时，页面先显示“正在暂停”，只有 Worker 确认成功后才显示
“已暂停”。如果远程暂停失败或 60 秒内未确认，页面会显示错误原因，
恢复到 UI 后端记录的运行状态并继续接收输出，不会仅关闭消息流来伪装暂停。
暂停请求超时意味着 Worker 状态尚未确认，可检查 Worker 日志后重试。
这不包含取消已提交的 MCP 精修计算任务。

## 3.运行RONGZAI Agent 后端

### 3.1 在后台启动mcp工具

使用仓库根目录的 `start_tools.sh` 管理以下 5 个工具：

| 工具名称 | Conda 环境 | 入口 |
| --- | --- | --- |
| `zrietveld-refine` | `pyZrietveld` | `tools/pyZRietveld/0zrietveld_refine.py` |
| `gsas2-refine` | `gsas2_py311` | `tools/GSAS-II-master/GSASII/0neutron_gsas_refine.py` |
| `sasview-refine` | `sasmodels` | `tools/sasview/0sasview_refine.py` |
| `pdf-refine` | `diffpy.pdffit2_env` | `tools/pdf_refinement/0pdf_refine.py` |
| `Irena-refine` | `sasmodels` | `tools/Irena/0Irena_fit.py` |

```shell
bash start_tools.sh                 # 后台启动全部工具，跳过已运行的工具
bash start_tools.sh status          # 查看 PID、日志位置及实际 stdout 去向
bash start_tools.sh restart         # 重启全部工具
bash start_tools.sh restart pdf-refine gsas2-refine
bash start_tools.sh stop pdf-refine # 停止指定工具及当前子进程
bash start_tools.sh logs pdf-refine # 实时查看最近 50 行及后续输出，Ctrl-C 仅退出查看
bash start_tools.sh logs            # 实时查看全部工具日志（需先启动生成日志文件）
```

脚本以自身位置定位仓库，不依赖调用时的工作目录。通过 `CONDA_EXE` 或
PATH 查找 conda，不再硬编码服务器上的 conda 安装路径：

```shell
CONDA_EXE=/path/to/anaconda3/bin/conda bash start_tools.sh start
```

日志保存在 `tmp/tools/logs/<工具名称>.log`，stdout/stderr 合并记录；
Conda 不缓存输出，Python 使用 `-u`，因此新输出会实时写入文件。
重启追加日志，不清空历史。后台服务在退出终端后仍运行，但脚本不提供
崩溃自动拉起或开机自启；可用 `status` 检查，再用 `restart` 恢复。

脚本会识别当前用户在本仓库启动的同入口 Python 进程，即使它不是由脚本
启动的，`start` 也不会重复启动。旧进程不会自动切换日志文件：
需要执行 `restart <工具名称>` 后才会写入上述统一日志。重启/停止可能中断
正在进行的精修任务；发送 TERM 后最多等待 15 秒，若仍未退出则报错，
不会强制杀死进程或启动替代进程。不要同时使用 PM2/其他自动拉起服务管理
这些入口，应先停止对应管理器再使用本脚本。


### 3.2 启动智能体后端
```shell
python run_agent_test.py
```
## 4 其他须知内容
### 4.1 后台运行非中子会话监控
监控脚本默认单次扫描最近 2 天的 UI 数据库消息。使用 `--loop` 可在后台常驻运行，默认每 2 天扫描一次；首次启动会立即执行一轮扫描。

```shell
cd /home/dur/rongzai-agent
nohup env PYTHONUNBUFFERED=1 \
    /home/dur/miniconda3/envs/rongzai_agent/bin/python \
    monitor_non_neutron_chats.py --loop \
    >> tmp/monitor/runner.log 2>&1 &
echo $! > tmp/monitor/runner.pid
```

运行报告会追加至 `tmp/monitor/non_neutron_sessions.jsonl`，运行日志写入 `tmp/monitor/runner.log`。检测到非中子会话时，脚本会使用 `monitor_non_neutron_chats.py` 中的 SMTP 配置发送邮件。

查看日志、确认进程和停止服务：

```shell
cd /home/dur/rongzai-agent
tail -f tmp/monitor/runner.log
ps -fp "$(cat tmp/monitor/runner.pid)"
kill "$(cat tmp/monitor/runner.pid)"
```

首次验证时不发送邮件，可在启动命令的 `--loop` 后增加 `--email-dry-run`。


### 4.2 更新新会话页通知内容

当前内置 UI 的新会话页包含两类提示：顶部走马灯通知、示例问题下方的引用提示。

#### 顶部走马灯通知

走马灯优先读取运行时静态文件：

```text
src/drsai_ui/ui_backend/backend/web/ui/announcements.json
```

可以直接修改该文件更新线上通知内容，不需要重启 `run_ui.py`，也不需要重启智能体 Worker。前端会每 30 秒重新拉取一次，用户刷新页面也会立即读取最新内容。

文件格式示例：

```json
{
    "announcements": [
        {
            "zh": "这里填写中文通知。",
            "en": "Write the English notice here."
        }
    ]
}
```

如果希望修改在下次前端构建后仍保留，需要同步修改源码文件：

```text
webui/frontend/static/announcements.json
```

否则执行 `yarn build:sync` 时，运行时静态目录中的 `announcements.json` 会被源码版本覆盖。

#### 引用提示

引用提示组件源码位于：

```text
webui/frontend/src/components/common/CitationNotice.tsx
```

该组件会跟随界面语言在中文/英文说明之间切换。修改引用提示文案后，需要重新构建并同步前端静态资源：

```shell
cd webui/frontend
yarn build:sync
```

该操作只更新 UI 静态文件，不会重启或中断智能体 Worker。用户刷新页面后会看到新的引用提示。

### 4.3 统一认证开关（测试时不需要统一认证，默认关闭）

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


## 4.4 remote_agents.json配置
里面的url，如果是走DDF,是"https://aiapi.ihep.ac.cn/apiv2"
如果是走本地，没有速率限制，是"http://10.1.252.95:42812/apiv2"(注意这里的ip地址应该是本地服务器IP地址)
前后端api切换方法：
走前端api时，将remote_agents.json中的url改为"https://aiapi.ihep.ac.cn/apiv2"，然后run_agent.py中将HepAIChatCompletionClient的api_key= api_key or os.environ.get("HEPAI_API_KEY"),然后将run_worker中的use_api_key_mode = "frontend",重启前后端
走后端api时，将remote_agents.json中的url改为"http://10.1.252.95:42812/apiv2",然后run_agent.py中将HepAIChatCompletionClient的api_key= os.environ.get("HEPAI_API_KEY"),然后将run_worker中的use_api_key_mode = "backend"，重启前后端


## 4.5 skill中涉及文件的权限问题
    为了保证代码安全，限制智能体只能在agentWorkSpace, datasets, agent_skills三个文件夹下有操作权限。
    同时，agent_skills被设为只读，防止agent修改系统级skills。
    因此，如果skill中涉及到要求智能体去阅读特定文件，该特定文件必须放入agent_skills中，推荐放在和skill.md同级目录下

## 4.6 pm2常用指令
### 查看当前进程
pm2 list
### 停止指定名称的进程
pm2 stop sasview-service

### 停止指定 ID 的进程
pm2 stop 0

### 停止所有进程
pm2 stop all

### 停止多个进程（空格分隔）
pm2 stop 0 1 2

### 删除进程
stop替换成delete