# Mini_Lu（桌面宠物 + Agent 工作台）

PySide6 桌面宠物，内置对话 Agent、工作区代码编辑、Skills / MCP 扩展。

> **本文件（项目根目录 `README.md`）仅供开发与打包参考，不会打进发行包。**  
> 发行目录 `dist/Mini_Lu/` 内另有简短「请读我.txt」与 `docs/` 运行说明。

手机端为独立工程，与本仓库互不覆盖。

---

## 目录速览

```
my_item/
├── main.py                 # 运行入口
├── requirements.txt
├── build_linux.py          # Ubuntu / Linux 打包
├── build_exe.py            # Windows 打包
├── README.md               # 本说明（不入包）
├── config/                 # 模型、工作区、MCP、Skills 等
├── agent/                  # Agent / UI / 工具
├── assets/                 # 皮肤、图标
├── data/                   # 记事、记忆、对话等运行数据
├── skills/                 # 内置 Skills
├── docs/                   # SKILLS.md、UBUNTU.md（会部分入包）
└── dist/Mini_Lu/           # 打包产物（勿提交密钥）
```

---

## 源码运行

### 依赖环境

| 项 | 建议 |
|----|------|
| Python | 3.10+（推荐 3.11 / 3.12） |
| 系统 | Windows 10/11，或 Ubuntu 22.04 / 24.04 |
| 图形 | 需桌面会话（纯 SSH 无 DISPLAY 无法开 UI） |

Ubuntu 系统库示例：

```bash
sudo apt update
sudo apt install -y \
  python3 python3-venv python3-pip python3-dev \
  libgl1 libegl1 libxkbcommon0 \
  libxcb-cursor0 libxcb-xinerama0 \
  fonts-noto-cjk fonts-wqy-microhei \
  xclip wl-clipboard
```

### 安装与启动

```bash
cd /path/to/my_item

# Linux
python3 -m venv .venv && source .venv/bin/activate
# Windows（PowerShell）
# py -3 -m venv .venv ; .\.venv\Scripts\Activate.ps1

pip install -U pip
pip install -r requirements.txt

cp config/models.local.yaml.example config/models.local.yaml
# 编辑 models.local.yaml，填入 DeepSeek / 豆包等 API Key

python main.py
```

可选冒烟：

```bash
python -m agent.smoke_test_llm
python -m agent.smoke_test_agent
```

### 常用配置

| 文件 | 作用 |
|------|------|
| `config/models.yaml` | 多模型路由（chat / asr / vision） |
| `config/models.local.yaml` | **本地密钥**（勿外传、勿提交） |
| `config/agent.yaml` | Plan-and-Execute 等 Agent 策略 |
| `config/workspace.yaml` | 工作区根目录 |
| `config/skills.yaml` / `skills.local.yaml` | Skills 开关与模式 |
| `config/mcp.yaml` / `mcp.local.yaml` | MCP 服务器 |
| `config/ui_theme.yaml` | 工作台主题 |
| `config/studio_prefs.yaml` | 工作台布局偏好（用户习惯） |

右键宠物形象也可打开：模型设置、工作台、Skills / MCP 等。

---

## 打包总原则

1. **在目标系统本机打包**：Linux 用 `build_linux.py`，Windows 用 `build_exe.py`。不要把 `.exe` 拷到 Ubuntu，也不要指望在 Windows 上打出 Linux 包。
2. **先保证源码能跑**：当前环境必须能 `python main.py`，且能 `import PySide6.QtWidgets`。
3. **产物是文件夹**：`dist/Mini_Lu/`（推荐文件夹版，比单文件稳定）。移植时复制**整个** `Mini_Lu` 目录。
4. **不要运行 `build/` 下的临时产物**：只运行 `dist/Mini_Lu/` 里的可执行文件 / 启动脚本。
5. **根目录 `README.md` 不会打进包**；包内说明以「请读我.txt」和 `docs/` 为准。

需要 PyInstaller：

```bash
pip install pyinstaller
```

（若未安装，部分脚本会尝试自动安装。）

---

## Ubuntu / Linux 打包教程

### 1. 准备环境

```bash
cd /path/to/my_item
# 激活你平时跑桌宠的环境，例如：
conda activate minilu
# 或：source .venv/bin/activate

python -c "from PySide6.QtWidgets import QApplication; print('Qt OK')"
pip install pyinstaller
```

若用 **conda**，务必用该环境的 Python 打包，避免 OpenSSL 版本错配。

### 2. 执行打包

```bash
python build_linux.py
```

成功后大致结构：

```text
dist/Mini_Lu/
  Mini_Lu                 ← 主程序
  run_mini_lu.sh          ← ASCII 启动脚本（菜单推荐）
  启动Mini_Lu.sh          ← 中文启动脚本
  install_to_menu.sh      ← 注册系统应用菜单（含图标）
  mini-lu.desktop
  请读我.txt
  assets/                 ← 皮肤、图标
  config/
  data/
  skills/
  docs/                   ← SKILLS.md、UBUNTU.md 等
  _internal/              ← Qt / Agent 等依赖
```

### 3. 本机试跑

```bash
cd dist/Mini_Lu
./run_mini_lu.sh
# 或
./启动Mini_Lu.sh
```

Wayland 下窗口异常时可：

```bash
export QT_QPA_PLATFORM=xcb
./run_mini_lu.sh
```

### 4. 加入系统应用菜单（重要）

**不要只 `cp mini-lu.desktop`。** Ubuntu / GNOME 通常还需要把图标装进用户图标主题目录，并刷新桌面数据库。

在发行目录执行：

```bash
cd dist/Mini_Lu
./install_to_menu.sh
```

然后在应用菜单搜索 **Mini_Lu** 或 **桌宠**。  
若仍看不到：注销重登，或 GNOME 下 `Alt+F2` 输入 `r` 回车刷新 Shell。

卸载菜单项（脚本末尾也会提示）：

```bash
rm -f ~/.local/share/applications/mini-lu.desktop
rm -f ~/.local/share/icons/hicolor/*/apps/mini-lu.png
```

### 5. 拷到其他 Ubuntu 电脑

1. 复制整个 `dist/Mini_Lu/` 文件夹。
2. 在目标机安装常见运行库（一般无需 Python）：

```bash
sudo apt install -y libgl1 libegl1 libxcb-cursor0 libxkbcommon0 \
  fonts-noto-cjk xclip wl-clipboard
```

3. 再执行一次 `./install_to_menu.sh`（路径会变，必须重装菜单项）。
4. 在目标机配置 `config/models.local.yaml`（含 Key 的包勿随意外传）。

### 6. Linux 常见问题

| 现象 | 处理 |
|------|------|
| `OPENSSL_3.3.0 not found` / 伪装成「请先安装 openai」 | 用当前 `build_linux.py` 重打（会覆盖匹配的 `libcrypto`/`libssl`）；或手动把 conda 的 `libcrypto.so.3` / `libssl.so.3` 拷进 `dist/Mini_Lu/_internal/` |
| 菜单里没有图标 / 找不到软件 | 运行 `./install_to_menu.sh`，不要只复制 `.desktop` |
| Qt `xcb` 插件加载失败 | 补齐 `libxcb*` / `libegl1` / `libgl1`，或 `QT_DEBUG_PLUGINS=1` 排查 |
| 中文方框 | 安装 `fonts-noto-cjk` |

---

## Windows 打包教程

### 1. 准备环境

在已能运行 `python main.py` 的环境（conda / venv）中：

```bat
cd path\to\my_item
pip install pyinstaller
python -c "from PySide6.QtWidgets import QApplication; print('Qt OK')"
```

### 2. 执行打包

```bat
python build_exe.py
```

或双击 `build_exe.bat`（若存在）。

产物：

```text
dist\Mini_Lu\
  Mini_Lu.exe
  启动Mini_Lu.bat
  请读我.txt
  assets\
  config\
  data\
  skills\
  docs\
  _internal\
```

### 3. 试跑与移植

- 运行：`dist\Mini_Lu\Mini_Lu.exe` 或「启动Mini_Lu.bat」
- **整夹复制**到目标 Windows 10/11（64 位）即可，一般无需再装 Python
- 首次可能被杀毒拦截（未签名），允许运行即可
- 换皮肤：改目标机上的 `assets\skins\`

### 4. Windows 注意

- 不要运行 `build\` 目录下的临时 exe（易缺 DLL）
- 聊天 Markdown 渲染需要打包环境能 `import markdown`
- API Key 放在目标机 `config\models.local.yaml`，勿把含 Key 的整包随意发给他人

---

## 发行包会带什么 / 不会带什么

### 会进入 `dist/Mini_Lu/`

- 主程序与 `_internal` 依赖
- `assets/`（皮肤、应用图标）
- `config/`（含示例与你本机已有的非敏感配置；**若存在 `*.local.yaml` 也可能被复制，打包前请自查**）
- `data/`、`skills/`
- `docs/SKILLS.md`、`docs/UBUNTU.md`（若存在）
- 启动脚本、菜单安装脚本、「请读我.txt」

### 不会进入发行包

| 路径 | 说明 |
|------|------|
| **`README.md`（本文件）** | 开发 / 打包教程，刻意不入包 |
| `build/` | PyInstaller 临时目录 |
| `.venv` / conda 环境本身 | 只收集运行所需库到 `_internal` |
| 工具脚本为主的源码树 | 如 `video_to_walk.py`、`fix_q_appearance.py` 等管线脚本默认不作为「用户程序」分发 |
| 大型无关仓库目录 | 如部分第三方源码树，除非被依赖分析打进 `_internal` |

若需确认文档拷贝范围，见 `build_exe.py` 中的 `copy_docs()`：只复制 `docs/` 下白名单文件名，**从不复制根目录 README**。

---

## 工作台与扩展（运行后）

- **双击宠物**：打开 Agent 工作台（聊天、编辑、文件树、终端）
- **顶栏「主题」**：配色；「重置布局为默认」可恢复出厂面板比例
- **顶栏「模型」 / 聊天旁选项卡**：配置对话 API 与能力
- **Skills**：扩展面板管理；「接入教程」按需打开
- 布局习惯保存在 `config/studio_prefs.yaml`

---

## 皮肤与素材工具（开发用，一般不入用户包）

正式皮肤目录：`assets/skins/Q版卡通/`（`idle` / `happy` / `walk_left` / `walk_right`）。

| 脚本 | 用途 |
|------|------|
| `video_to_walk.py` | 视频抽帧 → 抠图 → 写入行走帧 |
| `fix_q_appearance.py` | 统一肤色、修补 idle 手部 |
| `generate_q_assets.py` | 在线作图入库（需 `.env`） |
| `batch_fix_bg.py` | 对现有皮肤重新 rembg |

抠图管线建议使用含 `rembg` 的独立环境，与日常运行环境分开亦可。

---

## 相关文档

- `docs/UBUNTU.md` — Ubuntu 源码运行与打包要点
- `docs/SKILLS.md` — Skills 约定
- 发行包内「请读我.txt」— 给最终用户的最短说明

---

## 推荐检查清单（发版前）

1. [ ] 源码环境 `python main.py` 正常
2. [ ] `config/models.local.yaml` 是否要打进包（含 Key 则谨慎）
3. [ ] Linux：`python build_linux.py` → `./run_mini_lu.sh` 能开
4. [ ] Linux：`./install_to_menu.sh` 后菜单能搜到且有图标
5. [ ] Windows：`python build_exe.py` → `Mini_Lu.exe` 能开
6. [ ] 确认根目录 `README.md` **未**出现在 `dist/Mini_Lu/` 中
