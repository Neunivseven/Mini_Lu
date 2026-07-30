# Mini_Lu · Ubuntu 源码运行说明

在 Ubuntu 上用源码跑通 `python main.py`（当前推荐路径；Linux 二进制包需在本机构建，勿拷贝 Windows 的 `.exe`）。

## 环境要求

| 项 | 建议 |
|----|------|
| 系统 | Ubuntu 22.04 / 24.04（其它发行版类似） |
| Python | **3.10–3.12**（推荐 3.12；3.13+ 暂不保证） |
| 桌面 | X11 或 Wayland（见下文托盘说明） |
| 显示 | 需图形会话；纯 SSH 无 DISPLAY 无法开 UI |

## 1. 系统依赖

```bash
sudo apt update
sudo apt install -y \
  python3 python3-venv python3-pip python3-dev \
  libgl1 libxkbcommon0 libegl1 \
  libxcb-cursor0 libxcb-xinerama0 \
  fonts-noto-cjk fonts-wqy-microhei \
  xclip wl-clipboard
```

说明：

- `libgl*` / `libxcb*`：PySide6 / Qt 运行所需
- `fonts-noto-cjk` / `fonts-wqy-microhei`：中文界面字体
- `xclip` / `wl-clipboard`：剪贴板工具兜底（X11 / Wayland）

可选（开终端、打开应用更顺）：

```bash
sudo apt install -y gnome-terminal xdg-utils
```

## 2. 创建虚拟环境并安装 Python 依赖

在项目根目录（含 `main.py` 的目录）：

```bash
cd /path/to/my_item
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -r requirements.txt
```

可选：本地可编辑安装代码结构解析（与 Windows 开发一致）：

```bash
pip install -e ./tree-sitter-analyzer-main
```

## 3. 配置 API Key

```bash
cp config/models.local.yaml.example config/models.local.yaml
# 编辑 models.local.yaml，填入 deepseek / openai 等密钥
```

也可用旧版 `config/llm.local.yaml`（仍兼容）。

## 4. 启动

```bash
source .venv/bin/activate
python main.py
```

首次启动会在后台预热本机应用索引（扫描 `*.desktop` 与常见 PATH 程序），略慢属正常。

## 5. 功能对照（Linux）

| 能力 | 行为 |
|------|------|
| 形象 / 聊天 / Agent | 与 Windows 相同 |
| 打开软件 `open_app` | 匹配 `.desktop` / PATH 命令（如 `firefox`、`code`） |
| 打开终端 | `gnome-terminal` / `konsole` / `x-terminal-emulator` 等 |
| 剪贴板 | `pyperclip`；失败则 `wl-copy`/`wl-paste` 或 `xclip` |
| 系统托盘 | 依赖桌面扩展；GNOME 常需 AppIndicator 扩展 |

### Wayland / 托盘

- 若窗口异常，可尝试：`export QT_QPA_PLATFORM=xcb` 后再 `python main.py`
- GNOME 上托盘图标可能不显示：安装 **AppIndicator and KStatusNotifierItem Support** 扩展，或从右键菜单操作即可

### 无头 / 远程

无图形界面时不要指望 UI；可先测 API：

```bash
python -m agent.smoke_test_llm
python -m agent.smoke_test_agent
```

## 6. MCP / Skills（可选）

```bash
cp config/mcp.local.yaml.example config/mcp.local.yaml   # 若存在示例
# 在「扩展」面板或 mcp.local.yaml 中配置外部 MCP
```

依赖已含在 `requirements.txt`（`mcp`、`langchain-mcp-adapters`）。

## 7. 常见问题

**`Could not load the Qt platform plugin "xcb"`**  
补齐上文 `libxcb*` / `libegl1` / `libgl1`，或设置 `QT_DEBUG_PLUGINS=1` 看缺哪个库。

**中文显示方框**  
安装 `fonts-noto-cjk`，重启应用。

**`open_app` 找不到软件**  
确认有对应 `.desktop`（`~/.local/share/applications/` 或 `/usr/share/applications/`），或命令在 `PATH` 中。

**权限 / 沙箱**  
Snap 版 Python 可能缺权限；建议用 `apt` 的 `python3` + venv。

## 8. 打包成可移植目录（Ubuntu）

在本机（已能 `python main.py` 的 conda/venv）执行：

```bash
pip install pyinstaller   # 若尚未安装
python build_linux.py
```

产物：`dist/Mini_Lu/`（`Mini_Lu` 可执行文件 + `启动Mini_Lu.sh` + assets/config/data/skills）。

```bash
cd dist/Mini_Lu
./run_mini_lu.sh
# 推荐：注册到系统应用菜单（含图标）
./install_to_menu.sh
# 然后在菜单搜索 Mini_Lu / 桌宠
```

仅 `cp mini-lu.desktop` **不够**：Ubuntu/GNOME 通常还要把图标装进 `~/.local/share/icons/hicolor/`，并刷新桌面数据库；请用上面的 `install_to_menu.sh`。

目标机建议安装：`libgl1` `libegl1` `libxcb-cursor0` `fonts-noto-cjk` `xclip` `wl-clipboard`。  
Wayland 窗口异常时可：`export QT_QPA_PLATFORM=xcb`。

若用 **conda** 打包后启动报 `OPENSSL_3.3.0 not found` / 「请先安装 openai」：请用当前 `build_linux.py` 重打（会自动覆盖匹配的 `libcrypto`/`libssl`）；或对已有包手动：

```bash
cp -a "$CONDA_PREFIX/lib/libcrypto.so.3" "$CONDA_PREFIX/lib/libssl.so.3" dist/Mini_Lu/_internal/
```

## 9. 与 Windows 打包的关系

- Windows：`python build_exe.py` → `dist/Mini_Lu/Mini_Lu.exe`
- Linux：`python build_linux.py` → `dist/Mini_Lu/Mini_Lu`（须在 **Ubuntu 本机** 打包；不要把 `.exe` 拷到 Linux）
