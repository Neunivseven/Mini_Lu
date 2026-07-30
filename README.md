# Mini_Lu

桌面宠物 + Agent 工作台：用 PySide6 做桌面形象与面板，用 LangChain / LangGraph 做对话与工具编排。

> 本 README 面向开发与源码分发。发行目录 `dist/Mini_Lu/` 内另有「请读我.txt」；Ubuntu 细节见 [`docs/UBUNTU.md`](docs/UBUNTU.md)，Skills 约定见 [`docs/SKILLS.md`](docs/SKILLS.md)。

---

## 功能概览

| 能力 | 说明 |
|------|------|
| 桌面宠物 | 置顶透明窗、多皮肤帧动画、拖拽 / 踱步 / 点选移动、系统托盘 |
| 对话 Agent | 多会话独立上下文；流式回复；气泡与工作台双入口 |
| Agent 工作台 | 聊天、会话列表、代码编辑、改动对比（保留/放弃）、文件树、内嵌终端 |
| 工作区 | 打开文件夹作为可读写根目录；路径沙箱与写前备份 |
| 工具调用 | 读改文件、终端命令（可审批）、记事 / 闹钟、打开本机应用等 |
| Plan / ReAct | 简单任务走 ReAct；明确多步任务可走 Plan-and-Execute |
| 多模型 | DeepSeek / Moonshot(Kimi) / 通义 / OpenAI 兼容端等；Chat / ASR / Vision 分流 |
| Skills | `skills/*/SKILL.md` 按需加载，扩展面板管理 |
| MCP | 通过配置接入外部 MCP 服务器（可选依赖） |
| 文档附件 | PDF / Word / 表格等解析后进对话（内置解析，不依赖 Marker/MinerU） |
| 代码结构 | 工作区内符号 / 调用关系查询（依赖 `tree-sitter-analyzer`） |

---

## 技术栈与框架

| 层级 | 选用 | 用途 |
|------|------|------|
| UI | **PySide6**（Qt for Python） | 桌宠窗、工作台、面板、托盘 |
| 终端仿真 | **termqt** | 工作台内嵌终端（pty） |
| Agent 编排 | **LangGraph** + **LangChain** | ReAct / Plan-Execute、工具节点、checkpoint |
| LLM SDK | **openai**（兼容多厂商）、**langchain-openai** | Chat Completions / 媒体能力 |
| 配置 | **PyYAML** | `config/*.yaml` |
| 渲染 | **markdown**、自研气泡 / 消息视图 | 助手回复展示 |
| 文档 | **PyMuPDF** / **pypdf**、**python-docx**、**openpyxl**、**reportlab** | 解析与简单写出 |
| 代码智能 | **tree-sitter-analyzer** | AST / 符号 / 调用图（pip 安装） |
| MCP（可选） | **mcp**、**langchain-mcp-adapters** | 消费外部 MCP 工具 |
| 打包 | **PyInstaller**（开发时安装） | `build_linux.py` / `build_exe.py` → `dist/Mini_Lu/` |
| 图像（素材管线） | **Pillow**；抠图等可用独立环境的 rembg | 皮肤帧处理脚本 |

运行要求：Python **3.10+**（推荐 3.11 / 3.12），Windows 10/11 或 Ubuntu 22.04 / 24.04，需图形桌面会话。

---

## 开源内容说明

本项目**自有代码**主要在 `main.py`、`agent/`、`config/` 示例、`assets/` 皮肤与图标、以及部分内置 `skills/`。下列为**通过依赖或本机旁路引用**的开源能力，**请勿将大型上游源码树整仓提交**（已在 `.gitignore` 中排除）。

### 运行时依赖（pip，见 `requirements.txt`）

| 项目 | 角色 | 常见协议（以官方仓库为准） |
|------|------|---------------------------|
| [PySide6](https://doc.qt.io/qtforpython/) / Qt | 桌面 UI | LGPL / 商业双授权等（以 Qt 文档为准） |
| [LangChain](https://github.com/langchain-ai/langchain) | 工具与消息抽象 | MIT |
| [LangGraph](https://github.com/langchain-ai/langgraph) | Agent 状态图、checkpoint | MIT |
| [OpenAI Python SDK](https://github.com/openai/openai-python) | 兼容 Chat API 客户端 | MIT |
| [termqt](https://pypi.org/project/termqt/) | 终端控件 | 以 PyPI / 上游为准 |
| [tree-sitter-analyzer](https://github.com/aimasteracc/tree-sitter-analyzer) | 代码结构分析 | MIT |
| [MCP](https://modelcontextprotocol.io/) 及相关适配器 | 可选外部工具协议 | 以各包为准 |
| markdown / PyYAML / Pillow / PyMuPDF 等 | 渲染、配置、图像、PDF | 多为 BSD / MIT / AGPL 等，**以各包 LICENSE 为准** |

安装：`pip install -r requirements.txt`。本地开发若需可编辑 TSA，可另：`pip install -e ./tree-sitter-analyzer-main`（该目录仅作本机克隆，不入库）。

### 可选本机旁路（不嵌入默认发行逻辑）

| 内容 | 如何引用 | 说明 |
|------|----------|------|
| **MetaCoding** | `agent/metacoding_bridge.py` 调用旁路仓库 / Bun CLI | 未安装则降级；**不打进默认 exe** |
| **Marker / MinerU** | 历史可选 PDF 引擎 | **当前默认已改用内置 PyMuPDF 等**，无需随仓携带 |
| 本地克隆的 `langchain-master` / `langgraph-main` 等 | 仅开发对照 | 已忽略；正式依赖走 PyPI |

### 内置 Skills 与风格参考

- `skills/` 下为 Mini_Lu 可加载的 Skill 说明书（`SKILL.md`）。
- 部分 Skill 的**编排写法与主题**参考了 Cursor Agent Skills / 社区 skill 包的常见结构（短说明书 + YAML 头 + 按需注入），**并非**把上游整仓 vendoring 进本仓库；本地的 `agent-skills/`、`ai-agent-skills/` 仅作参考，已 `.gitignore`。
- 使用或再分发 Skills 正文时，请自行核对各 Skill 文件内的来源与许可说明（若有）。

### 命名说明

- 工作台内的 `MonacoEditor`（`agent/monaco_editor.py`）是 **QPlainTextEdit + 语法高亮** 的自研组件，**不是**嵌入 Microsoft Monaco Editor；命名仅为编辑区习惯称呼。
- Windows 打包若需改 exe 图标，可本机准备 [rcedit](https://github.com/electron/rcedit) 等工具；**二进制不入库**。

### 第三方许可义务

二次分发（含 PyInstaller 打包）时，请遵守各依赖的许可证要求（声明、源码提供、LGPL 动态链接约定等）。本仓库未将上游完整源码树一并发布；**以你实际 `pip freeze` / 打包 `_internal` 中的版本为准核对 LICENSE**。

---

## 源码快速开始

```bash
cd /path/to/my_item
python3 -m venv .venv && source .venv/bin/activate   # Windows: py -3 -m venv .venv
pip install -U pip
pip install -r requirements.txt

cp config/models.local.yaml.example config/models.local.yaml
# 编辑 models.local.yaml，填入 API Key（该文件已被 gitignore）

python main.py
```

冒烟（可选）：

```bash
python -m agent.smoke_test_llm
python -m agent.smoke_test_agent
```

Ubuntu 系统库与 Wayland / 托盘说明见 [`docs/UBUNTU.md`](docs/UBUNTU.md)。

---

## 目录结构（源码）

```text
my_item/
├── main.py              # 入口：桌宠宿主
├── agent/               # Agent、工作台 UI、工具、providers
│   ├── desktop/         # PanelManager / AgentController
│   ├── providers/       # 多模型适配
│   └── ...
├── config/              # 默认配置 + *.example（密钥用 *.local.yaml）
├── assets/              # 皮肤、图标
├── skills/              # 内置 Skills
├── data/                # 运行数据（默认忽略入库）
├── docs/                # UBUNTU.md、SKILLS.md
├── requirements.txt
├── build_linux.py       # Linux 打包
├── build_exe.py         # Windows 打包
└── Mini_Lu.spec         # PyInstaller 规格（可选）
```

---

## 打包（摘要）

1. 在**目标操作系统**本机打包：Linux → `python build_linux.py`；Windows → `python build_exe.py` 或 `build_exe.bat`。
2. 产物为文件夹 `dist/Mini_Lu/`（勿只拷单文件；勿提交 `dist/`）。
3. 打包前确认源码可 `python main.py`，且 `models.local.yaml` 是否应进入发行包（含 Key 则勿外传）。

更完整的 Ubuntu / Windows 步骤与排错仍以本文件历史说明与 `docs/UBUNTU.md` 为准；发版检查清单：

1. [ ] `python main.py` 正常  
2. [ ] 无密钥进入公开仓库（`*.local.yaml` / `.env`）  
3. [ ] Linux / Windows 各自本机打包试跑  
4. [ ] 发行说明使用包内「请读我.txt」，而非误把开发用 README 当唯一用户文档  

---

## 相关文档

| 文档 | 内容 |
|------|------|
| [`docs/UBUNTU.md`](docs/UBUNTU.md) | Ubuntu 源码运行与打包要点 |
| [`docs/SKILLS.md`](docs/SKILLS.md) | Skills 接入约定 |
| [`config/models.local.yaml.example`](config/models.local.yaml.example) | 模型密钥示例 |
| [`.env.example`](.env.example) | 素材管线用环境变量示例 |
