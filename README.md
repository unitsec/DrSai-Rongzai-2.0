# RONGZAI Agent Deployment Guide

This project must be run on a Linux server.
Typical install time is half an hour

## 1. Install Python environment dependencies

- 0. Install conda (skip this step if already installed)
```shell
# x86_64 (most servers)
wget https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-Linux-x86_64.sh

# aarch64 (ARM, e.g. AWS Graviton, some RPi)
# wget https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-Linux-aarch64.sh

bash Miniforge3-Linux-x86_64.sh -b -p "$HOME/miniforge3"
"$HOME/miniforge3/bin/conda" init bash
source ~/.bashrc
```

- 1. Configure the Python runtime environment for the agent
```shell
conda create -n rongzai-agent python=3.12
conda activate rongzai-agent
cd /path/to/DrSai-Rongzai-2.0
pip install -r requirements.txt
```

- 2. Configure the gsas-ii Python runtime environment
```shell
conda create -n gsas2_py311 python=3.11
conda activate gsas2_py311
cd tools/GSAS-II-master
pip install -r requirements.txt
```

- 3. Configure the diffpy environment
```shell
conda create -n diffpy.pdffit2_env python=3.12 conda-forge::diffpy.pdffit2
conda activate diffpy.pdffit2_env
pip install 'mcp>=1.15.0,<2' matplotlib
```

## 2. Build and start the agent interactive frontend

The following steps apply to the first deployment on a new server. The frontend is based on Gatsby 5 and requires Node.js 18 or newer; Node.js 22 and Yarn Classic 1.22.x are recommended. Node.js is only used to build the frontend static files; when running `run_ui.py`, the Python environment created in Section 1 must still be used.

### 2.1 Install Node.js and Yarn

It is recommended to install Node.js with nvm to avoid affecting software already installed on the server:

```shell
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.1/install.sh | bash
source "$HOME/.nvm/nvm.sh"
nvm install 22
nvm use 22
npm install -g yarn@1.22.22
```

Verify the version:

```shell
node --version   # Should be v22.x, and not lower than v18
yarn --version   # Should be 1.22.x
```

If the server cannot access GitHub or the npm/yarn package sources, configure a proxy or intranet mirror first.

### 2.2 Check runtime configuration

Create `.env` in the repository root (skip this if Section 2 has already been completed):

```shell
cd /path/to/DrSai-Rongzai-2.0
cp .env.example .env
```

Update the following paths in `.env` to the absolute paths of the repository on the current server; do not keep the example path `/home/wangh/rongzai-agent`:

```shell
WORKDIR="/path/to/rongzai-agent/agentWorkSpace"
SYSTEM_SKILLS_DIR="/path/to/rongzai-agent/agent_skills"
DRSAI_UI_USER_FILES_ROOT="/path/to/rongzai-agent/tmp/drsai_ui/files/user"
DRSAI_UI_MODE=internal
DRSAI_UI_INTERNAL_SRC=/path/to/rongzai-agent/src

```

`.env.example` and `.env` use the same variable order and grouping; each variable above it includes the purpose, value format, and default behavior. After copying from the example, at minimum check the paths, `HEPAI_API_KEY`, runtime mode, and service ports. Do not commit `.env` or any real credentials to the repository.
`DRSAI_UI_MODE=internal` means the built-in `src/drsai_ui` is used, and the frontend static files are synchronized to:

```text
src/drsai_ui/ui_backend/backend/web/ui
```

### 2.3 Install frontend dependencies and build for the first time

When deploying for the first time or when `yarn.lock` changes, install dependencies first:

```shell
cd /path/to/DrSai-Rongzai-2.0/webui/frontend
yarn install --legacy-peer-deps
```

Then build the frontend and sync it to the built-in UI static directory:

```shell
yarn build:sync
```

After a successful build, the last line should resemble:

```text
Synced frontend static files to: .../src/drsai_ui/ui_backend/backend/web/ui (mode=internal)
```

For subsequent updates, when only the frontend source changes, you can usually just rerun `yarn build:sync`; there is no need to rerun `yarn install`.

### 2.4 Start the frontend and backend for human-machine interaction

change the .env
```shell
WORKER_IP="XX.XX.XX.XX" # server IP
DEFAULT_REMOTE_AGENTS="/path/to/DrSai-Rongzai-2.0/remote_agents.json" 
```

Open a new terminal, enter the repository root, and activate the Python environment created in Section 1:
```shell
cd /path/to/DrSai-Rongzai-2.0
conda activate rongzai-agent
python run_ui.py
```

After `Application startup complete` appears, open the following in the browser:

```text
http://<server IP>:8081/
```

The service listens on `0.0.0.0:8081` by default. If it is not remotely accessible, check whether the server firewall and security group allow TCP 8081. The port can be changed in `.env` via `RONGZAI_UI_PORT`.

## 3. Run the RONGZAI Agent backend

### 3.1 Start MCP tools in the background

Use the repository root `start_tools.sh` to manage the following 2 tools:

| Tool name | Conda environment | Entry |
| --- | --- | --- |
| `gsas2-refine` | `gsas2_py311` | `tools/GSAS-II-master/GSASII/0neutron_gsas_refine.py` |
| `pdf-refine` | `diffpy.pdffit2_env` | `tools/pdf_refinement/0pdf_refine.py` |

```shell
bash start_tools.sh                 # Start all tools in the background, skipping tools already running
bash start_tools.sh status          # View PID, log location, and actual stdout destination
bash start_tools.sh restart         # Restart all tools
bash start_tools.sh restart pdf-refine gsas2-refine
bash start_tools.sh stop pdf-refine # Stop the specified tool and its current child processes
bash start_tools.sh logs pdf-refine # View the latest 50 lines of output in real time and subsequent output; Ctrl-C exits only the viewer
bash start_tools.sh logs            # View logs for all tools in real time (log files must already be generated by startup)
```

### 3.2 Start the agent backend

login the [aiapi.ihep.ac.cn](https://aiapi.ihep.ac.cn/) to apply for an apikey
If the register needs a ihep employee email, you can use wanghao96@ihep.ac.cn. I would agree your register as soon as possible. 
add the apikey to .env
```shell
HEPAI_API_KEY="XXXXXX"
```


```shell
python run_agent.py
```

## 4. Reproducing the result in paper
change the .env to make the ablation experiment
```shell
RONGZAI_DISABLE_CORE_SKILL_TOOLS=1 # Set 1 to disable built-in core skill tools.
RONGZAI_DISABLE_MEMO_WRITE_TOOL=1 # Set 1 to disable the memo-writing tool.
```

Use the prompt under the input window on the UI; you can reproduce the experiments in the paper separately.


## License

This project is licensed under the Apache License 2.0. See [LICENSE](LICENSE) for details.

This product includes software produced by UChicago Argonne, LLC under Contract No. DE-AC02-06CH11357 with the Department of Energy (GSAS-II; see [tools/GSAS-II-master/LICENSE](tools/GSAS-II-master/LICENSE)).
