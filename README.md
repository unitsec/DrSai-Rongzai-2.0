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

### 分析命令的安全隔离（必需）

Worker 的 Shell、后台 Shell、文件读写、搜索和下载工具均通过 Bubblewrap
在独立 Linux 命名空间中执行，不再直接使用宿主机权限。先安装系统依赖：

```shell
# Debian / Ubuntu
sudo apt-get update
sudo apt-get install bubblewrap
# RHEL 系发行版使用：sudo dnf install bubblewrap
```

部署环境必须允许创建用户、挂载、PID 和网络命名空间。容器平台若禁止这些操作，
需要管理员配置适当的运行策略；不要直接启用 privileged 或挂载宿主机根目录。
使用启动 Worker 的同一账户、同一 Python 环境预检：

```shell
python -c "from src.core.agent.analysis_sandbox import check_sandbox_support; check_sandbox_support()"
python -m pytest tests/test_workspace_permissions.py -q
```

`run_agent_web.py` 启动时也会预检。缺少依赖或隔离失败会报错，**不会回退到宿主机执行**；
`only_in_workspace=False` 和危险命令开关也不能关闭沙箱。

保留的分析能力与边界：

- 当前 Python 环境、系统运行库只读挂载，支持 NumPy/Pandas/SciPy 拟合、Matplotlib
  非交互式绘图、脚本执行、管道和工作区文件读写；后台任务仍可运行，并可通过
  `get_bash_task` 查询状态及工作区日志。后台任务受 timeout 限制，Worker 退出时终止。
- Web 入口的共享 `datasets`、`agent_skills` 只读挂载。工作区可写；
  `CheckUserPermission` 成功后追加的数据目录仍按已有授权流程动态开放。
  额外的只读数据目录应同时加入 `extra_work_dirs` 和 `read_only_work_dirs`。
- 只向子进程传入固定的分析环境变量，不继承 Worker 的 API 密钥、Token、
  `BASH_ENV`、`PYTHONPATH`、代理或其他服务环境。宿主机 home、SSH 文件和服务
  `.env` 不挂载；`~` 是沙箱内临时 home，`/tmp` 也是临时目录。需持久化的产物必须写到工作区。
- 沙箱无网络，不能直接访问外网或宿主机 localhost。SSE MCP 仍由主服务连接，
  原有 GSAS-II/PDF/SasView 等 MCP 服务路径不变。需要联网的脚本应改用受控业务工具，
  不要给分析沙箱共享宿主机网络或注入主服务凭据。
- stdio MCP 启动的本地程序也进入同一沙箱，不接受自定义 `env`；
  图片理解工具读取本地图片时同样通过沙箱，避免其他工具绕开文件隔离。
- `/proc` 为空，避免暴露宿主机进程环境，也兼容禁止新挂载 procfs 的容器。
  文件描述符硬上限为 65536（若原硬上限更低则保留），避免无 procfs 时 Python
  子进程关闭过高的继承 FD 范围而长时间阻塞。依赖 procfs、GPU/特殊设备、
  新装软件或自定义环境变量的任务需另行适配和验证。

### 数据目录配置与迁移

在 `.env` 配置共享数据目录（Web、95 和测试 Worker 入口共用）：

```shell
RONGZAI_DATASETS_DIR=/path/to/shared/datasets
```

不配置或留空时默认使用当前项目下的 `datasets/`，不依赖启动目录或宿主机 home。
显式配置必须是已存在的绝对目录；无效路径会报错，不会静默回退。
迁移时先复制数据并保持 `BLXXX/instrumentFiles`、`BLXXX/RUNdataFiles` 等结构，
再修改此变量并重启 Worker。SSE MCP 服务必须也能读取传入的绝对文件路径；
若 MCP 位于不同容器，需将同一数据卷挂载到相同路径。

该路径会自动加入只读授权目录、系统提示和 GSAS-II/PDF/Z-Rietveld skill
内容；分析沙箱仅在挂载该数据目录时显式传入 `RONGZAI_DATASETS_DIR`，不会因此
继承其他服务环境变量。Shell 可使用 `"$RONGZAI_DATASETS_DIR"`，Python 可使用
`os.environ["RONGZAI_DATASETS_DIR"]`，文件工具使用 skill 返回的绝对路径。
数据目录只读，修改输入或生成结果时使用工作区；不要再使用
`~/rongzai-agent/datasets/`。无需额外设置 `RONGZAI_SANDBOX_READONLY_PATHS`。

### 额外分析环境

如脚本确实需要其他 Conda 环境或分析程序，只配置必要的可信只读路径，例如：

```shell
RONGZAI_SANDBOX_READONLY_PATHS=["/opt/conda/envs/gsas2_py311","/opt/analysis-tools"]
```

不要配置整棵服务目录、宿主机 home 或含凭据的目录；只读挂载仍允许读取其中的内容。
此配置仅由管理员维护，不能开放给模型或普通用户修改。额外环境中的 Python 可用绝对
路径执行；不会读取宿主机的 Conda/Shell 启动文件。

上述回归测试使用合成 CSV，验证拟合系数、PNG/CSV 导出、授权目录、文件工具、
后台执行、超时，以及环境变量、宿主机文件和网络隔离。上线前仍须在测试部署中用真实
业务样例完成分析回归，并核查独立 MCP/远程 Agent 自身的执行权限；本沙箱不能隔离
另一个服务内部执行的命令。沙箱并非 CPU、内存和磁盘配额，生产部署还应配置相应资源限制。
疑似已泄露的凭据须轮换，SSH 授权和历史登录须另行排查，代码修复不会清除已有入侵。

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
GSAS-II、PDF、SasView、Irena 和 Z-Rietveld 分别默认使用 `42506`、`42507`、
`42508`、`42509` 和 `42510`。修改端口后需重启对应服务。
SasView 和 Irena 启动时会加载项目根目录的 `.env`，其中的配置优先于同名进程环境变量；
Irena 使用 `MCP_IRENA_REFINE_PORT`，未配置时默认监听 `42509`。

### 2.5 rongzai_web Pod 的 Dr.Sai 前端构建与重启

在 `rongzai_web + drsai` 的 Pod 部署中，实际运行的前端源码位于
`drsai/apps/webui/frontend`，服务由 `rongzai_web` 的独立 PM2 实例管理。
不要使用 `drsai/apps/webui/build_and_restart_30003.sh`：该脚本管理旧的
30003 端口服务，且不会加载 Pod 的环境变量。

使用 Pod 专用脚本重新构建、同步静态文件并重启当前环境的
`drsai-frontend`：

```shell
cd /path/to/rongzai/rongzai_web
RZ_ENV=dev ./build_and_restart_drsai_frontend.sh
```

生产环境使用：

```shell
cd /path/to/rongzai/rongzai_web
RZ_ENV=prod ./build_and_restart_drsai_frontend.sh
```

该脚本会加载对应的 `.env.dev` 或 `.env.prod`，校验 `GATSBY_API_URL` 和
`PM2_HOME`，再以 `gatsby build --prefix-paths` 构建并同步静态文件。最后它
只重启 `drsai-frontend`，不会重启后端、数据库、Redis 或智能体 Worker。

`GATSBY_API_URL` 是 Gatsby 的构建时变量。开发环境必须使用 `.env.dev` 中的
`/drsai/api`；若未加载该变量，构建产物会回退到 `/api`，导致
`/api/sessions/` 等请求返回 404 并显示 “Error loading sessions”。

构建完成后，可检查前端进程和后端请求：

```shell
PM2_HOME=/path/to/rongzai/.pm2-dev pm2 list
PM2_HOME=/path/to/rongzai/.pm2-dev pm2 logs run_backend --lines 50 --nostream
```

开发环境中 `drsai-frontend` 应为 `online`，并使用 `.env.dev` 配置的端口
（当前为 30013）。浏览器需要执行一次强制刷新后再验证页面。

`run_ui.py` 只启动人机交互页面及其 UI 后端。要让页面中的智能体正常响应，
还需要按第 4 节启动 RONGZAI Agent Worker。

远程智能体暂停时，页面先显示“正在暂停”，只有 Worker 确认成功后才显示
“已暂停”。如果远程暂停失败或 60 秒内未确认，页面会显示错误原因，
恢复到 UI 后端记录的运行状态并继续接收输出，不会仅关闭消息流来伪装暂停。
暂停请求超时意味着 Worker 状态尚未确认，可检查 Worker 日志后重试。
这不包含取消已提交的 MCP 精修计算任务。

## 3.运行RONGZAI Agent 后端

### 3.1 在后台启动mcp工具

使用仓库根目录的 `start_tools.sh` 管理以下 6 个工具：

| 工具名称 | Conda 环境 | 入口 |
| --- | --- | --- |
| `zrietveld-refine` | `gsas2_py311` | `tools/pyZRietveld/0zrietveld_refine.py` |
| `gsas2-refine` | `gsas2_py311` | `tools/GSAS-II-master/GSASII/0neutron_gsas_refine.py` |
| `sasview-refine` | `sasmodels` | `tools/sasview/0sasview_refine.py` |
| `pdf-refine` | `diffpy.pdffit2_env` | `tools/pdf_refinement/0pdf_refine.py` |
| `Irena-refine` | `sasmodels` | `tools/Irena/0Irena_fit.py` |
| `neutron-reflection` | Python 3.12，`NEUTRON_REFLECTION_PYTHON` 指定 | `tools/neutron_reflection/reflection_mcp.py` |

```shell
bash start_tools.sh                 # 后台启动全部工具，跳过已运行的工具
bash start_tools.sh status          # 查看 PID、日志位置及实际 stdout 去向
bash start_tools.sh restart         # 重启全部工具
bash start_tools.sh restart pdf-refine gsas2-refine
bash start_tools.sh start neutron-reflection
bash start_tools.sh stop pdf-refine # 停止指定工具及当前子进程
bash start_tools.sh logs pdf-refine # 实时查看最近 50 行及后续输出，Ctrl-C 仅退出查看
bash start_tools.sh logs            # 实时查看全部工具日志（需先启动生成日志文件）
```

脚本以自身位置定位仓库，不依赖调用时的工作目录。通过 `CONDA_EXE` 或
PATH 查找 conda，不再硬编码服务器上的 conda 安装路径：

```shell
CONDA_EXE=/path/to/anaconda3/bin/conda bash start_tools.sh start
```

生产反射使用现有 `/dg_workfs/domassy/rongzai/miniconda3/envs/rongzai-agent/bin/python`
（已安装 rongzai 4.1）；可用 `NEUTRON_REFLECTION_PYTHON` 覆盖。仅启动反射不要求 conda 命令。
磁结构服务已从此启动脚本移除，磁结构代码保留；已经运行的旧进程不会被自动停止。
BL08A 原始数据位于 `/csnsdata/userData/BL08A/<课题>/<年份>/<RUN>`。
权限验证沿用 `check_user_permission_for_file` 的 DoMAS/ICAT 分支，
仅 ICAT 分支为 BL08A 返回原始数据目录 `data_root`，供反射工具使用，
同时创建并授权 `/csns_data/BL08A/<课题>/reduction`、`analysis` 和
`/csns_data/BL08A/BL08A_info`（非生产分别追加 `/dev`）。
沿用现有智能体归档机制：权限工具返回路径和强制写回要求，由智能体在处理完成后
归档实际文件和 info；权限检查本身不生成分析结果，也不代表已完成归档。
规约和分析分别归档到对应目录下的 `RUNxxxxxxx_YYYYMMDD_HHMMSS/`，
并在 info 目录写入 `reduction_RUNxxxxxxx_YYYYMMDD_HHMMSS.json` 或
`analysis_RUNxxxxxxx_YYYYMMDD_HHMMSS.json`，结构为
`{课题: {reduction或analysis_RUN号_时间戳: {归档文件绝对路径: 文件描述}}}`。
同时做了两类处理必须分别归档；写回 `/csns_data`，不写回原始数据挂载 `/csnsdata`。
MCP 不做账号认证，只服务受信本机框架。
端口由 `.env` 中 `MCP_NEUTRON_REFLECTION_PORT` 配置；当前生产值为 42521。

日志保存在 `tmp/tools/logs/<工具名称>.log`，stdout/stderr 合并记录；
Conda 不缓存输出，Python 使用 `-u`，因此新输出会实时写入文件。
重启追加日志，不清空历史。后台服务在退出终端后仍运行，但脚本不提供
崩溃自动拉起或开机自启；可用 `status` 检查，再用 `restart` 恢复。

脚本会识别当前用户在本仓库启动的同入口 Python 进程，即使它不是由脚本
启动的，`start` 也不会重复启动。旧进程不会自动切换日志文件：
需要执行 `restart <工具名称>` 后才会写入上述统一日志。重启/停止可能中断
正在进行的精修任务；发送 TERM 后最多等待 15 秒，若仍未退出则输出警告，
仅对 PID 和启动时间仍一致的残留进程发送 KILL，再等待最多 5 秒。
只有确认旧进程退出后才启动替代进程；强制终止可能中断未完成的请求。
不要同时使用 PM2/其他自动拉起服务管理
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

### 重启前端服务
pkill -f 'run_ui\.py' && nohup python run_ui.py > run_ui.log 2>&1 < /dev/null & 

### 重启智能体服务
pkill -f 'run_agent_web\.py' && nohup python run_agent_web.py > run_agent.log 2>&1 < /dev/null &


DDF很可能无法直接访问东莞的服务器，需要有一个能直接访问的服务器做转发。

### 4.7 测试端和生产端端口的分离
测试：42500-42510 生产：42510-42520  生产：30003 - 30010 测试：30012-30020

---

## 可用能力

### 磁结构分析（magnetic-structure-analysis）

新增的磁结构分析能力，遵循"一个 skill + 一个独立 MCP 服务"的既有架构：

| 项 | 详见 |
|---|---|
| Skill | [agent_skills/magnetic-structure-analysis/SKILL.md](agent_skills/magnetic-structure-analysis/SKILL.md) |
| MCP 服务（6 个工具） | [tools/magnetic_structure_mcp.py](tools/magnetic_structure_mcp.py)，端口 42511 |
| 引擎与配图 | [tools/GSAS-II-master/magnetic_analysis/](tools/GSAS-II-master/magnetic_analysis/) |
| Benchmark 与验收记录 | [BenchMark/MagneticStructureAnalysis/](BenchMark/MagneticStructureAnalysis/) |
| gsas2-refine 磁能力扩展 | [tools/GSAS-II-master/GSASII/0neutron_gsas_refine.py](tools/GSAS-II-master/GSASII/0neutron_gsas_refine.py)（同环境 `gsas2_py311`，端口 42506） |

磁结构暂不纳入生产 `start_tools.sh` 的工具列表；相关源码保留，恢复上线时另行配置启动入口。
轮询类新增可选的 `progress_file` 约定：声明它的工具，每次 poll 都会带上实时步骤行（参见
[src/core/tools/mcp_task_runner.py](src/core/tools/mcp_task_runner.py) 的 `progress_file` 参数）。

流程与设计详见上述 skill 与引擎 README；近期 benchmark 实测（2026-10-12）v2 口径 PASS。
