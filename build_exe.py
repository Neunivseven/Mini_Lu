"""
打包 Mini_Lu 为可移植目录（Windows）

产物：
  dist/Mini_Lu/
    Mini_Lu.exe
    启动Mini_Lu.bat
    请读我.txt
    assets/skins/   ← 皮肤
    config/         ← 模型 / 工作区 / 文档解析等
    data/           ← 记事、记忆、工作流等空目录与种子
    _internal/      ← PySide6、pymupdf、Agent 依赖

用法：
  python build_exe.py
  # 或双击 build_exe.bat
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DIST_APP = ROOT / "dist" / "Mini_Lu"
ASSETS_DST = DIST_APP / "assets" / "skins"
CONFIG_DST = DIST_APP / "config"
DATA_DST = DIST_APP / "data"

# 随包必带（无密钥、无本机路径）
CONFIG_SHIP = (
    "llm.yaml",
    "models.yaml",
    "quotes.yaml",
    "doc_parsers.yaml",
    "metacoding.yaml",
    "mcp.yaml",
    "skills.yaml",
    "agent.yaml",
)

# 本机密钥 / 信任列表：默认不随包；仅当 MINI_LU_SHIP_LOCAL_CONFIG=1 时复制
CONFIG_LOCAL = (
    "llm.local.yaml",
    "models.local.yaml",
    "apps.local.yaml",
    "metacoding.local.yaml",
    "mcp.local.yaml",
    "skills.local.yaml",
    "command_trust.local.yaml",
)

# 用户偏好 / 布局（永不随公开包）
CONFIG_USER_PREFS = (
    "studio_prefs.yaml",
    "ui_theme.yaml",
    "workspace.yaml",
)

# data：默认不带开发机上的任何文件；目标机首次运行自行生成
DATA_PRODUCT_SEED: tuple[str, ...] = ()

# 开发机上的个人/运行时数据 —— 打包时跳过（含聊天、记忆、checkpoint）
DATA_USER_RUNTIME = (
    "chat_history.json",
    "notes.json",
    "notes.md",
    "memory.json",
    "reminders.json",
    "reminders.json.bak",
    "reminders.json.migrated",
    "goal.json",
    "apps_index.json",
    "identity.json",
    "prompts.json",
    "quotes.json",
    "lg_checkpoints.sqlite",
    "lg_store.sqlite",
    "_studio_smoke.py",
)

DATA_DIRS = (
    "docs_out",
    "doc_parse",
    "workflows",
    "file_backups",
    "codegraph",
)

# (import_name, pip_package)
REQUIRED_MODULES: list[tuple[str, str]] = [
    ("PySide6", "PySide6"),
    ("yaml", "PyYAML"),
    ("markdown", "markdown"),
    ("openai", "openai"),
    ("langchain", "langchain"),
    ("langchain_openai", "langchain-openai"),
    ("langgraph", "langgraph"),
    ("langgraph.checkpoint.sqlite", "langgraph-checkpoint-sqlite"),
    ("httpx", "httpx"),
    ("pyperclip", "pyperclip"),
    ("pypdf", "pypdf"),
    ("fitz", "pymupdf"),
    ("docx", "python-docx"),
    ("openpyxl", "openpyxl"),
    ("reportlab", "reportlab"),
]

# 可选：代码结构 / TSA（缺失时 Agent 仍可运行，相关工具会提示未安装）
OPTIONAL_MODULES: list[tuple[str, str]] = [
    ("tree_sitter_analyzer", "tree-sitter-analyzer"),
    ("langchain_mcp_adapters", "langchain-mcp-adapters"),
    ("mcp", "mcp"),
]


def _ensure_qt_works() -> None:
    """打包前自检：坏掉的 conda base PySide6 打出来的 exe 会 DLL load failed。"""
    try:
        from PySide6.QtWidgets import QApplication  # noqa: F401
        from PySide6.QtCore import Qt  # noqa: F401
    except Exception as e:
        raise SystemExit(
            "当前 Python 无法 import PySide6.QtWidgets（DLL 损坏或不完整）。\n"
            f"  解释器: {sys.executable}\n"
            f"  错误: {e}\n"
            "请改用可用环境打包，例如：\n"
            "  D:\\Anaconda\\envs\\rembg_env\\python.exe build_exe.py\n"
            "或双击 build_exe.bat（会优先用 rembg_env）。"
        ) from e


def _check_optional_modules() -> None:
    for mod, pip_name in OPTIONAL_MODULES:
        try:
            __import__(mod)
            print(f"可选依赖就绪: {mod}")
        except ImportError:
            print(
                f"提示: 未安装可选依赖 {pip_name}（{mod}）。"
                "TSA/代码结构工具在目标机将不可用；可 pip install 后重新打包。"
            )


def ensure_dependencies(*, auto_install: bool = True) -> None:
    """检查 Agent / PDF 等运行时依赖；缺失时可按 requirements.txt 补装。"""
    missing: list[tuple[str, str]] = []
    for mod, pip_name in REQUIRED_MODULES:
        try:
            __import__(mod)
        except ImportError:
            missing.append((mod, pip_name))

    if not missing:
        # 轻量确认 pymupdf 能开文档
        try:
            import fitz

            _ = fitz.version
            print(f"依赖就绪: pymupdf {fitz.version[0]}")
        except Exception as e:
            raise SystemExit(f"pymupdf 已安装但不可用: {e}") from e
        _check_optional_modules()
        return

    names = ", ".join(f"{m}({p})" for m, p in missing)
    print(f"缺少依赖: {names}")
    if not auto_install:
        raise SystemExit(f"请先安装: pip install {' '.join(p for _, p in missing)}")

    req = ROOT / "requirements.txt"
    if req.is_file():
        print(f"正在安装 requirements.txt …")
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "-r", str(req)],
        )
    else:
        pkgs = sorted({p for _, p in missing})
        subprocess.check_call([sys.executable, "-m", "pip", "install", *pkgs])

    still: list[str] = []
    for mod, pip_name in missing:
        try:
            __import__(mod)
        except ImportError:
            still.append(pip_name)
    if still:
        raise SystemExit(f"安装后仍缺少: {', '.join(still)}")
    print("依赖已补齐。")
    _check_optional_modules()


def ensure_pyinstaller() -> None:
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("未安装 PyInstaller，正在安装…")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pyinstaller"])


def copy_config_and_data(dist_app: Path) -> None:
    """把 config / data 放到可执行文件同级。

    默认不复制本机密钥与用户运行数据；公开分发前会再跑 sanitize_public_dist。
    """
    import os

    src_cfg = ROOT / "config"
    config_dst = dist_app / "config"
    data_dst = dist_app / "data"
    if config_dst.exists():
        shutil.rmtree(config_dst)
    config_dst.mkdir(parents=True, exist_ok=True)

    ship_local = os.environ.get("MINI_LU_SHIP_LOCAL_CONFIG", "").strip() in (
        "1",
        "true",
        "yes",
        "on",
    )

    if src_cfg.is_dir():
        for p in sorted(src_cfg.iterdir()):
            if not p.is_file():
                continue
            name = p.name
            if name in CONFIG_USER_PREFS:
                print(f"已跳过用户偏好: config/{name}")
                continue
            if name.endswith(".example") or name in CONFIG_SHIP:
                shutil.copy2(p, config_dst / name)
                print(f"已复制配置: config/{name}")
            elif name in CONFIG_LOCAL:
                if ship_local:
                    shutil.copy2(p, config_dst / name)
                    print(
                        f"已复制本地配置: config/{name}"
                        "（MINI_LU_SHIP_LOCAL_CONFIG=1；勿公开外传）"
                    )
                else:
                    print(f"已跳过本地密钥/覆盖: config/{name}")

    for miss in CONFIG_SHIP:
        if not (config_dst / miss).is_file() and (src_cfg / miss).is_file():
            shutil.copy2(src_cfg / miss, config_dst / miss)

    data_dst.mkdir(parents=True, exist_ok=True)
    for d in DATA_DIRS:
        (data_dst / d).mkdir(parents=True, exist_ok=True)

    for name in DATA_PRODUCT_SEED:
        src = ROOT / "data" / name
        if src.is_file():
            shutil.copy2(src, data_dst / name)
            print(f"已复制数据种子: data/{name}")

    skipped = [n for n in DATA_USER_RUNTIME if (ROOT / "data" / n).is_file()]
    # 也跳过任意 sqlite / log
    for p in (ROOT / "data").glob("*") if (ROOT / "data").is_dir() else []:
        if p.is_file() and (
            p.suffix in {".sqlite", ".log", ".bak"}
            or p.name.endswith(".sqlite-wal")
            or p.name.endswith(".sqlite-shm")
        ):
            if p.name not in skipped:
                skipped.append(p.name)
    if skipped:
        print(
            "已跳过用户运行时数据（不随包）: "
            + ", ".join(f"data/{n}" for n in skipped)
        )

    # 不复制开发机 workflows（可能含个人脚本）；仅保留空目录
    print("data/workflows 保持空目录（不复制开发机工作流）")


def sanitize_public_dist(dist_app: Path) -> list[str]:
    """公开分发前再清扫：密钥、偏好、聊天/记忆/日志/checkpoint。

    返回已删除路径列表（相对 dist_app）。
    """
    removed: list[str] = []
    cfg = dist_app / "config"
    if cfg.is_dir():
        for p in list(cfg.iterdir()):
            if not p.is_file():
                continue
            name = p.name
            drop = False
            if name in CONFIG_USER_PREFS or name in CONFIG_LOCAL:
                drop = True
            elif name.endswith(".local.yaml") or name.endswith(".local.yml"):
                drop = True
            elif name.endswith(".log"):
                drop = True
            if drop:
                p.unlink(missing_ok=True)
                removed.append(f"config/{name}")

    data = dist_app / "data"
    if data.is_dir():
        for p in list(data.rglob("*")):
            if p.is_dir():
                continue
            rel = p.relative_to(dist_app).as_posix()
            # 保留占位
            if p.name == ".gitkeep":
                continue
            p.unlink(missing_ok=True)
            removed.append(rel)
        # 清空目录后重建标准空结构
        for d in DATA_DIRS:
            (data / d).mkdir(parents=True, exist_ok=True)
        keep = data / ".gitkeep"
        if not keep.exists():
            keep.write_text("", encoding="utf-8")

    # 根目录日志
    for p in dist_app.glob("*.log"):
        p.unlink(missing_ok=True)
        removed.append(p.name)

    if removed:
        print("公开包已清除隐私/运行数据:")
        for r in removed:
            print(f"  - {r}")
    else:
        print("公开包检查：无额外隐私文件需清除")
    return removed


def copy_qt_plugins(dist_app: Path) -> None:
    """补齐换机必需的 Qt 插件与少量核心 DLL（勿复制全部 Qt6*.dll，否则体积暴涨）。"""
    try:
        import PySide6
    except ImportError as e:
        raise SystemExit(f"本机未安装 PySide6: {e}") from e

    src_root = Path(PySide6.__file__).resolve().parent
    src_plugins = src_root / "plugins"
    dst_pyside = dist_app / "_internal" / "PySide6"
    dst_plugins = dst_pyside / "plugins"
    dst_plugins.mkdir(parents=True, exist_ok=True)

    needed = ("platforms", "imageformats", "styles", "iconengines", "generic")
    for name in needed:
        s = src_plugins / name
        if not s.is_dir():
            continue
        d = dst_plugins / name
        if d.exists():
            shutil.rmtree(d)
        shutil.copytree(s, d)
        print(f"已复制 Qt 插件: plugins/{name}")

    # 仅补核心 DLL；WebEngine / 3D / Charts / Multimedia 等不随包
    essential_names = {
        "Qt6Core.dll",
        "Qt6Gui.dll",
        "Qt6Widgets.dll",
        "Qt6Network.dll",
        "Qt6Svg.dll",
        "Qt6OpenGL.dll",
        "Qt6Pdf.dll",
        "Qt6PrintSupport.dll",
        "pyside6.abi3.dll",
        "concrt140.dll",
        "msvcp140.dll",
        "msvcp140_1.dll",
        "msvcp140_2.dll",
        "msvcp140_codecvt_ids.dll",
        "vcruntime140.dll",
        "vcruntime140_1.dll",
        "vccorlib140.dll",
        "vcamp140.dll",
        "vcomp140.dll",
    }
    for src in src_root.glob("*.dll"):
        if src.name not in essential_names and not src.name.lower().startswith("api-ms-"):
            continue
        dst = dst_pyside / src.name
        if not dst.exists():
            shutil.copy2(src, dst)
            print(f"已补齐 DLL: {src.name}")

    for pkg in ("shiboken6", "Shiboken"):
        try:
            mod = __import__(pkg.lower() if pkg != "Shiboken" else "shiboken6")
        except ImportError:
            continue
        root = Path(mod.__file__).resolve().parent
        dst_sh = dist_app / "_internal" / root.name
        dst_sh.mkdir(parents=True, exist_ok=True)
        for src in root.glob("*.dll"):
            name_l = src.name.lower()
            if not (
                name_l.startswith("shiboken")
                or name_l.startswith("msvcp")
                or name_l.startswith("vcruntime")
                or name_l.startswith("concrt")
                or name_l.startswith("vc")
            ):
                continue
            dst = dst_sh / src.name
            if not dst.exists():
                shutil.copy2(src, dst)
                print(f"已补齐 {root.name}: {src.name}")


def copy_skins(dist_app: Path) -> None:
    src_skins = ROOT / "assets" / "skins"
    if not src_skins.exists():
        raise SystemExit("错误：找不到 assets/skins")

    dst = dist_app / "assets" / "skins"
    if dst.exists():
        shutil.rmtree(dst)
    dst.mkdir(parents=True, exist_ok=True)

    count = 0
    for skin_dir in src_skins.iterdir():
        if not skin_dir.is_dir() or skin_dir.name.startswith("_"):
            continue
        shutil.copytree(
            skin_dir,
            dst / skin_dir.name,
            ignore=shutil.ignore_patterns("_*", "*.bak"),
        )
        print(f"已复制皮肤: {skin_dir.name}")
        count += 1
    if count == 0:
        raise SystemExit("错误：assets/skins 下没有可用皮肤目录")

    # 应用图标（窗口 / 任务栏）
    src_icons = ROOT / "assets" / "icons"
    dst_icons = dist_app / "assets" / "icons"
    if dst_icons.exists():
        shutil.rmtree(dst_icons)
    if src_icons.is_dir():
        shutil.copytree(src_icons, dst_icons)
        print(f"已复制 icons/（{sum(1 for _ in dst_icons.iterdir())} 个文件）")


def copy_skills(dist_app: Path) -> None:
    """打包内置 skills/ 目录。"""
    src = ROOT / "skills"
    dst = dist_app / "skills"
    if dst.exists():
        shutil.rmtree(dst)
    if not src.is_dir():
        dst.mkdir(parents=True, exist_ok=True)
        return
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    print(f"已复制 skills/（{sum(1 for _ in dst.glob('*/SKILL.md'))} 个）")


def copy_docs(dist_app: Path) -> None:
    """复制简短说明文档（可选，体积很小）。

    注意：项目根目录 README.md 是开发/打包教程，故意不入包。
    """
    src = ROOT / "docs"
    dst = dist_app / "docs"
    if dst.exists():
        shutil.rmtree(dst)
    if not src.is_dir():
        return
    dst.mkdir(parents=True, exist_ok=True)
    for name in ("SKILLS.md", "UBUNTU.md"):
        p = src / name
        if p.is_file():
            shutil.copy2(p, dst / name)
            print(f"已复制文档: docs/{name}")
    # 防御：若误把根 README 拷进过 dist，打包时清掉
    stray = dist_app / "README.md"
    if stray.is_file():
        try:
            stray.unlink()
            print("已移除发行目录中的 README.md（开发文档不入包）")
        except Exception:
            pass


def ensure_markdown_in_dist(dist_app: Path) -> None:
    """若 PyInstaller 未落到 _internal/markdown，从当前环境补拷一份。"""
    internal = dist_app / "_internal"
    target = internal / "markdown"
    if (target / "__init__.py").is_file():
        return
    try:
        import markdown as md_pkg
    except Exception as e:
        print(f"无法补拷 markdown（当前环境 import 失败）: {e}")
        return
    src = Path(md_pkg.__file__).resolve().parent
    if not (src / "__init__.py").is_file():
        print(f"无法补拷 markdown：源目录异常 {src}")
        return
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)
    shutil.copytree(
        src,
        target,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "tests", "test"),
    )
    print(f"已补拷 markdown → {target}")


def verify_app_icon_ico() -> None:
    """打包前确认 ICO 含多档 PNG，避免 Windows 只显示糊的 16x16。"""
    ico = ROOT / "assets" / "icons" / "app_icon.ico"
    if not ico.is_file():
        raise SystemExit(f"缺少应用图标: {ico}（可运行 python tools/make_app_icon.py）")
    import struct

    raw = ico.read_bytes()
    if len(raw) < 6:
        raise SystemExit(f"应用图标损坏: {ico}")
    _r, typ, count = struct.unpack_from("<HHH", raw, 0)
    if typ != 1 or count < 3:
        raise SystemExit(
            f"应用图标层数过少（count={count}），请运行: python tools/make_app_icon.py"
        )
    # 至少应有一档 ≥128
    off = 6
    max_side = 0
    png_layers = 0
    for _ in range(count):
        w, h, _c, _res, _p, _b, nbytes, img_off = struct.unpack_from(
            "<BBBBHHII", raw, off
        )
        side = w or 256
        max_side = max(max_side, side)
        if raw[img_off : img_off + 8] == b"\x89PNG\r\n\x1a\n":
            png_layers += 1
        off += 16
    if max_side < 128:
        raise SystemExit(
            f"应用图标最大尺寸仅 {max_side}px，请运行: python tools/make_app_icon.py"
        )
    print(f"应用图标 OK: {count} 层, 最大 {max_side}px, PNG 层 {png_layers}")


def write_launcher_and_readme(dist_app: Path) -> None:
    launcher = dist_app / "启动Mini_Lu.bat"
    launcher.write_text(
        "@echo off\r\n"
        "cd /d \"%~dp0\"\r\n"
        "start \"\" \"Mini_Lu.exe\"\r\n",
        encoding="utf-8",
    )
    # 带高清图标的快捷方式（桌面可复制此 .lnk，比直接拖 exe 更清晰）
    try:
        ico = dist_app / "assets" / "icons" / "app_icon.ico"
        exe = dist_app / "Mini_Lu.exe"
        lnk = dist_app / "Mini_Lu.lnk"
        if ico.is_file() and exe.is_file():
            ps = (
                f"$ws=New-Object -ComObject WScript.Shell; "
                f"$s=$ws.CreateShortcut('{lnk}'); "
                f"$s.TargetPath='{exe}'; "
                f"$s.WorkingDirectory='{dist_app}'; "
                f"$s.IconLocation='{ico},0'; "
                f"$s.Description='Mini_Lu'; "
                f"$s.Save()"
            )
            subprocess.check_call(
                ["powershell", "-NoProfile", "-Command", ps],
                cwd=str(ROOT),
            )
            print(f"已生成快捷方式: {lnk.name}（图标指向高清 ICO）")
    except Exception as e:
        print(f"生成 .lnk 失败（可忽略）: {e}")

    readme = dist_app / "请读我.txt"
    readme.write_text(
        "请优先用本文件夹内的 Mini_Lu.lnk 启动（图标更清晰）；或 Mini_Lu.exe /「启动Mini_Lu.bat」。\r\n"
        "\r\n"
        "移植到其他电脑：复制整个 Mini_Lu 文件夹即可。\r\n"
        "不要运行项目里 build\\ 目录下的 exe（那是打包中间文件，会报 Failed to load Python DLL）。\r\n"
        "也不要用第三方工具改 Mini_Lu.exe 图标（会破坏 PyInstaller 结构，报 PKG archive 错误）。\r\n"
        "\r\n"
        "【模型 / API】\r\n"
        "  右键形象 →「模型设置…」可切换 DeepSeek / 通义 / 智谱 / Kimi / OpenAI /\r\n"
        "  SiliconFlow / OpenRouter / Ollama，或填写自定义 OpenAI 兼容网关。\r\n"
        "  也可编辑 config\\models.local.yaml（密钥）与 models.yaml（预设）。\r\n"
        "  含 API Key 的文件夹请勿随意发给他人。\r\n"
        "\r\n"
        "【聊天】\r\n"
        "  Ctrl+V 可粘贴剪贴板图片/文件为附件；拖入文件同样支持。\r\n"
        "  助手回复支持 Markdown 表格/标题（需打包时含 markdown 库）。\r\n"
        "  右键 →「点选横向移动…」可让形象走到指定横坐标。\r\n"
        "\r\n"
        "PDF 解析：内置 PyMuPDF（config\\doc_parsers.yaml），无需另装 Marker/MinerU。\r\n"
        "工作区：config\\workspace.yaml；也可在程序内点「文件夹」添加项目目录。\r\n"
        "Skills / MCP：skills\\ 与 config\\skills.yaml、mcp.yaml。\r\n"
        "命令信任：可将 config\\command_trust.local.yaml.example 复制为\r\n"
        "  command_trust.local.yaml 后编辑（勿提交含敏感策略的本地文件到公开仓库）。\r\n"
        "Prompt / 身份：data\\prompts.json、data\\identity.json。\r\n",
        encoding="utf-8",
    )


def verify_dist(dist_app: Path) -> list[str]:
    """打包后检查关键产物；返回警告列表，缺致命项则抛错。"""
    warnings: list[str] = []
    exe = dist_app / "Mini_Lu.exe"
    if not exe.is_file():
        raise SystemExit("打包失败：未找到 Mini_Lu.exe")

    internal = dist_app / "_internal"
    if not internal.is_dir():
        raise SystemExit("打包失败：缺少 _internal 目录")

    # PyMuPDF：包目录或 mupdf 相关二进制
    names_lower = {p.name.lower() for p in internal.rglob("*") if p.is_file()}
    has_fitz = any(
        n.startswith("fitz") or "pymupdf" in n or "mupdf" in n for n in names_lower
    )
    if not has_fitz:
        warnings.append(
            "警告: _internal 中未检测到 pymupdf/fitz 产物，PDF 解析可能在目标机失败。"
            "请确认打包环境已 pip install pymupdf，且 Mini_Lu.spec 含 collect_all(pymupdf)。"
        )
    else:
        print("已检测到 PyMuPDF 相关文件（fitz/pymupdf）")

    for name in ("models.yaml", "doc_parsers.yaml", "mcp.yaml", "skills.yaml", "agent.yaml"):
        if not (dist_app / "config" / name).is_file():
            warnings.append(f"警告: 缺少 config/{name}")

    for name in ("prompts.json",):
        if not (dist_app / "data" / name).is_file():
            warnings.append(f"警告: 缺少 data/{name}（Prompt 面板将无内置版本）")

    # 聊天 Markdown 渲染（官方 markdown 库）
    has_md_pkg = (internal / "markdown").is_dir() or any(
        p.is_file() and p.parent.name == "markdown" and p.name == "__init__.py"
        for p in internal.rglob("__init__.py")
    )
    if not has_md_pkg:
        warnings.append(
            "警告: _internal 中未检测到 markdown 包，聊天 Markdown 表格/标题可能无法渲染。"
            "请确认打包环境已 pip install markdown，且 Mini_Lu.spec 含 markdown hiddenimports。"
        )
    else:
        print("已检测到 markdown 包")

    skills_dir = dist_app / "skills"
    if not skills_dir.is_dir():
        warnings.append("警告: 缺少 skills/ 目录")
    elif not any(skills_dir.glob("*/SKILL.md")):
        warnings.append("提示: skills/ 下没有 SKILL.md（可空，扩展面板仍可用）")

    # TSA：需有 tree_sitter_analyzer 包目录（仅有 grammar 不够）
    has_tsa = (internal / "tree_sitter_analyzer").is_dir() or any(
        "tree_sitter_analyzer" in n for n in names_lower
    )
    if not has_tsa:
        warnings.append(
            "提示: 包内未检测到 tree_sitter_analyzer；"
            "read_outline / find_callers 等在目标机不可用（主聊天仍正常）。"
            "请确认 rembg_env 已 pip install -e ./tree-sitter-analyzer-main 后重新打包。"
        )
    else:
        print("已检测到 tree_sitter_analyzer")
        if not (internal / "numpy").is_dir() and not any(
            n.startswith("numpy") for n in names_lower
        ):
            warnings.append(
                "警告: 有 tree_sitter_analyzer 但未见 numpy，TSA 运行时可能失败。"
            )

    skins = dist_app / "assets" / "skins"
    if not skins.is_dir() or not any(skins.iterdir()):
        raise SystemExit("打包失败：assets/skins 为空")

    platforms = (
        dist_app / "_internal" / "PySide6" / "plugins" / "platforms"
    )
    if not platforms.is_dir() or not any(platforms.glob("qwindows*")):
        warnings.append("警告: 未找到 Qt platforms/qwindows，换机可能无法启动窗口")

    return warnings


def dir_size_mb(path: Path) -> float:
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file()) / (1024 * 1024)


def _kill_packaged_app() -> list[str]:
    """结束可能锁住 dist/Mini_Lu 的 Mini_Lu.exe。"""
    killed: list[str] = []
    if sys.platform != "win32":
        return killed
    try:
        r = subprocess.run(
            ["taskkill", "/F", "/IM", "Mini_Lu.exe"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if r.returncode == 0:
            killed.append("Mini_Lu.exe")
    except Exception:
        pass
    return killed


def _empty_dir(path: Path) -> None:
    if not path.is_dir():
        return
    for child in list(path.iterdir()):
        try:
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
            else:
                child.unlink(missing_ok=True)
        except TypeError:
            # py<3.8 missing_ok
            try:
                child.unlink()
            except OSError:
                pass
        except OSError:
            pass


def _dir_locked(path: Path) -> bool:
    """目录是否无法删除/重命名（常见：资源管理器正打开该文件夹，或 exe 仍在运行）。"""
    if not path.exists():
        return False
    probe = path.with_name(path.name + ".__lock_probe__")
    try:
        path.rename(probe)
        probe.rename(path)
        return False
    except OSError:
        return True


def prepare_collect_name() -> str:
    """
    选定 COLLECT 输出目录名。
    dist/Mini_Lu 若被占用无法删除，则改打到 Mini_Lu_new，事后再写回。
    """
    build_dir = ROOT / "build"
    if build_dir.exists():
        print(f"清理 {build_dir}")
        shutil.rmtree(build_dir, ignore_errors=True)

    killed = _kill_packaged_app()
    if killed:
        print(f"已结束仍在运行的: {', '.join(killed)}")
        import time

        time.sleep(0.8)

    if DIST_APP.exists():
        print(f"清理 {DIST_APP}")
        _empty_dir(DIST_APP)
        if not _dir_locked(DIST_APP):
            shutil.rmtree(DIST_APP, ignore_errors=True)
            if not DIST_APP.exists():
                return "Mini_Lu"
        print()
        print("=" * 60)
        print("dist/Mini_Lu 正被占用，无法删除（WinError 32）。")
        print("常见原因：")
        print("  1) Mini_Lu.exe 还在运行（含托盘）")
        print("  2) 资源管理器打开了 dist\\Mini_Lu 文件夹")
        print("  3) 上次打包进程卡住未退出")
        print("本次改为输出到 dist/Mini_Lu_new，完成后会尽量写回 dist/Mini_Lu。")
        print("=" * 60)
        print()
        staging = ROOT / "dist" / "Mini_Lu_new"
        if staging.exists():
            _empty_dir(staging)
            if not _dir_locked(staging):
                shutil.rmtree(staging, ignore_errors=True)
        return "Mini_Lu_new"
    return "Mini_Lu"


def merge_staging_into_dist(collect_name: str) -> Path:
    """若打到备用目录，把内容合并回 dist/Mini_Lu。"""
    built = ROOT / "dist" / collect_name
    if collect_name == "Mini_Lu":
        return built
    if not built.is_dir():
        return built

    DIST_APP.mkdir(parents=True, exist_ok=True)
    _empty_dir(DIST_APP)
    for child in list(built.iterdir()):
        dest = DIST_APP / child.name
        try:
            if child.is_dir():
                if dest.exists():
                    shutil.rmtree(dest, ignore_errors=True)
                shutil.copytree(child, dest)
            else:
                shutil.copy2(child, dest)
        except OSError as e:
            print(f"写回 {dest.name} 失败: {e}")
            print(f"请直接使用: {built}")
            return built
    shutil.rmtree(built, ignore_errors=True)
    if built.exists():
        print(f"备用目录未删净（可手动删）: {built}")
    print(f"已写回: {DIST_APP}")
    return DIST_APP


def main() -> int:
    _ensure_qt_works()
    print(f"打包解释器: {sys.executable}")
    ensure_dependencies(auto_install=True)
    ensure_pyinstaller()
    verify_app_icon_ico()

    collect_name = prepare_collect_name()
    os.environ["MINI_LU_DIST_NAME"] = collect_name

    spec = ROOT / "Mini_Lu.spec"
    if not spec.is_file():
        raise SystemExit(f"找不到 {spec}")

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        str(spec),
    ]
    print("执行:", " ".join(cmd))
    print(f"COLLECT 目录名: {collect_name}")
    try:
        subprocess.check_call(cmd, cwd=str(ROOT))
    except subprocess.CalledProcessError as e:
        print()
        print("PyInstaller 失败。若仍是「拒绝访问 / WinError 32」：")
        print("  - 关掉 Mini_Lu.exe（托盘也要退）")
        print("  - 关掉打开 dist\\Mini_Lu 的资源管理器窗口")
        print("  - 任务管理器结束卡住的 python/PyInstaller")
        print("然后重新运行: python build_exe.py")
        raise SystemExit(e.returncode) from e

    dist_app = merge_staging_into_dist(collect_name)

    if not (dist_app / "Mini_Lu.exe").exists():
        print("打包失败：未找到 Mini_Lu.exe")
        return 1

    copy_qt_plugins(dist_app)
    copy_config_and_data(dist_app)
    sanitize_public_dist(dist_app)
    copy_skins(dist_app)
    copy_skills(dist_app)
    copy_docs(dist_app)
    ensure_markdown_in_dist(dist_app)
    write_launcher_and_readme(dist_app)

    warnings = verify_dist(dist_app)
    for w in warnings:
        print(w)

    # 删除 build 中间产物，避免误点 build\...\Mini_Lu.exe
    build_dir = ROOT / "build"
    if build_dir.exists():
        shutil.rmtree(build_dir, ignore_errors=True)
        print("已清理中间目录 build/（请勿运行其中的 exe）")

    size_mb = dir_size_mb(dist_app)
    print("\n打包完成！")
    print(f"请运行: {dist_app / 'Mini_Lu.exe'}")
    print(f"体积约: {size_mb:.0f} MB")
    print("把整个 Mini_Lu 文件夹拷到其他 Windows 电脑即可。")
    print("（目标机一般无需再装 Python / PySide6；建议 Win10/11 64 位）")
    if warnings:
        print(f"（有 {len(warnings)} 条警告，见上方）")
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
