"""
打包 Mini_Lu 为可移植目录（Ubuntu / Linux）

产物：
  dist/Mini_Lu/
    Mini_Lu              ← 可执行文件
    run_mini_lu.sh / 启动Mini_Lu.sh
    install_to_menu.sh   ← 注册应用菜单（推荐）
    mini-lu.desktop
    请读我.txt
    assets/skins/
    config/
    data/
    skills/
    _internal/           ← PySide6、pymupdf、Agent 依赖

用法（在项目根、已能 python main.py 的环境）：
  python build_linux.py

完整打包教程见项目根目录 README.md（不会打进发行包）。

目标机一般无需再装 Python；建议保留 libgl1 / libxcb-cursor0 / 中文字体。
"""
from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path

# 复用 Windows 打包脚本中的共享逻辑
import build_exe as be

ROOT = be.ROOT
DIST_APP = be.DIST_APP


def _ensure_linux() -> None:
    if sys.platform != "linux":
        raise SystemExit(
            f"当前系统是 {sys.platform}。请在 Linux/Ubuntu 上运行本脚本；"
            "Windows 请用: python build_exe.py"
        )


def _ensure_qt_works() -> None:
    try:
        from PySide6.QtWidgets import QApplication  # noqa: F401
        from PySide6.QtCore import Qt  # noqa: F401
    except Exception as e:
        raise SystemExit(
            "当前 Python 无法 import PySide6.QtWidgets。\n"
            f"  解释器: {sys.executable}\n"
            f"  错误: {e}\n"
            "请先激活可用环境并安装依赖，例如：\n"
            "  conda activate minilu\n"
            "  pip install -r requirements.txt\n"
            "  python -c \"from PySide6.QtWidgets import QApplication\"\n"
        ) from e


def _conda_lib_dir() -> Path | None:
    """conda/venv 的 lib 目录（含与当前 Python 匹配的 OpenSSL）。"""
    prefix = Path(sys.prefix)
    lib = prefix / "lib"
    if (lib / "libcrypto.so.3").is_file() or (lib / "libcrypto.so").is_file():
        return lib
    # 偶发：sys.prefix 指向 envs/x，lib 在上一层
    return lib if lib.is_dir() else None


def fix_openssl_libs(dist_app: Path) -> None:
    """用打包环境的 OpenSSL 覆盖 PyInstaller 误收集的系统版。

    conda Python 的 ``_ssl`` 常链接 OPENSSL_3.3+，而系统 libcrypto.so.3
    只有 3.0.x，启动会报 version ``OPENSSL_3.3.0`` not found，
    并被 openai 包装成「请先安装 openai」。
    """
    lib_dir = _conda_lib_dir()
    if lib_dir is None:
        print("提示: 未找到 sys.prefix/lib，跳过 OpenSSL 覆盖")
        return

    internal = dist_app / "_internal"
    internal.mkdir(parents=True, exist_ok=True)

    names = (
        "libcrypto.so.3",
        "libssl.so.3",
        "libcrypto.so",
        "libssl.so",
    )
    copied = 0
    for name in names:
        src = lib_dir / name
        if not src.exists():
            continue
        # 跟随符号链接，写入真实文件，避免坏链
        src_real = src.resolve()
        if not src_real.is_file():
            continue
        dst = internal / name
        if src.is_symlink():
            # 包内用实文件同名，简化 RPATH 解析
            shutil.copy2(src_real, dst)
        else:
            shutil.copy2(src, dst)
        copied += 1
        print(f"已覆盖 OpenSSL: {name} ← {src_real}")

    # 常见依赖（conda 构建的 libssl 可能需要）
    for name in ("libz.so.1", "libz.so", "libffi.so.8", "libffi.so"):
        src = lib_dir / name
        if not src.exists():
            continue
        dst = internal / name
        if dst.exists():
            continue
        try:
            shutil.copy2(src.resolve() if src.is_symlink() else src, dst)
            print(f"已补齐: {name}")
        except OSError:
            pass

    if not copied:
        print("提示: conda lib 中未找到 libcrypto/libssl，跳过 OpenSSL 覆盖")
        return

    crypto = internal / "libcrypto.so.3"
    if crypto.is_file():
        try:
            out = subprocess.check_output(
                ["strings", str(crypto)],
                text=True,
                errors="replace",
            )
            if "OPENSSL_3.3.0" in out:
                print("OpenSSL 符号检查: 含 OPENSSL_3.3.0")
            elif "OPENSSL_3.0.0" in out:
                print(
                    "警告: 覆盖后的 libcrypto 仍只有 3.0.x；"
                    "若启动仍报 OPENSSL_3.3.0 not found，请用与 Python 同环境的 openssl 重打。"
                )
        except (FileNotFoundError, subprocess.CalledProcessError):
            pass


def copy_qt_plugins(dist_app: Path) -> None:
    """补齐 Qt 插件（xcb 等）；PyInstaller 有时漏拷换机必需项。"""
    try:
        import PySide6
    except ImportError as e:
        raise SystemExit(f"本机未安装 PySide6: {e}") from e

    src_root = Path(PySide6.__file__).resolve().parent
    src_plugins = src_root / "plugins"
    dst_pyside = dist_app / "_internal" / "PySide6"
    dst_plugins = dst_pyside / "plugins"
    dst_plugins.mkdir(parents=True, exist_ok=True)

    needed = (
        "platforms",
        "imageformats",
        "styles",
        "iconengines",
        "generic",
        "platformthemes",
        "xcbglintegrations",
    )
    for name in needed:
        s = src_plugins / name
        if not s.is_dir():
            continue
        d = dst_plugins / name
        if d.exists():
            shutil.rmtree(d)
        shutil.copytree(s, d)
        print(f"已复制 Qt 插件: plugins/{name}")

    # 补常用 Qt .so（已存在则跳过，避免体积暴涨）
    for src in sorted(src_root.glob("libQt6*.so*")):
        name = src.name
        if not any(
            x in name
            for x in (
                "Qt6Core",
                "Qt6Gui",
                "Qt6Widgets",
                "Qt6Network",
                "Qt6Svg",
                "Qt6DBus",
                "Qt6OpenGL",
                "Qt6XcbQpa",
                "Qt6Wayland",
            )
        ):
            continue
        dst = dst_pyside / name
        if not dst.exists() and src.is_file():
            shutil.copy2(src, dst)
            print(f"已补齐: {name}")

    try:
        import shiboken6

        root = Path(shiboken6.__file__).resolve().parent
        dst_sh = dist_app / "_internal" / root.name
        dst_sh.mkdir(parents=True, exist_ok=True)
        for src in root.glob("lib*.so*"):
            dst = dst_sh / src.name
            if not dst.exists() and src.is_file():
                shutil.copy2(src, dst)
                print(f"已补齐 shiboken6: {src.name}")
    except ImportError:
        pass


def _chmod_exec(path: Path) -> None:
    mode = path.stat().st_mode
    path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def write_launcher_and_readme(dist_app: Path) -> None:
    exe = dist_app / "Mini_Lu"
    if exe.is_file():
        _chmod_exec(exe)

    launcher_body = (
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'HERE="$(cd "$(dirname "$(readlink -f "$0" 2>/dev/null || realpath "$0" 2>/dev/null || echo "$0")")" && pwd)"\n'
        'cd "$HERE"\n'
        "# Wayland 下若窗口异常，可取消下一行注释强制用 xcb\n"
        "# export QT_QPA_PLATFORM=xcb\n"
        'exec "$HERE/Mini_Lu" "$@"\n'
    )
    # 中文启动脚本（手工双击友好）+ ASCII 启动脚本（.desktop / 菜单更稳）
    launcher_zh = dist_app / "启动Mini_Lu.sh"
    launcher_zh.write_text(launcher_body, encoding="utf-8")
    _chmod_exec(launcher_zh)
    launcher = dist_app / "run_mini_lu.sh"
    launcher.write_text(launcher_body, encoding="utf-8")
    _chmod_exec(launcher)
    print(f"已生成: {launcher_zh.name} / {launcher.name}")

    icon_png = dist_app / "assets" / "icons" / "app_icon_256.png"
    if not icon_png.is_file():
        icon_png = dist_app / "assets" / "icons" / "app_icon.png"
    icon_abs = str(icon_png.resolve()) if icon_png.is_file() else "mini-lu"
    run_abs = str((dist_app / "run_mini_lu.sh").resolve())
    path_abs = str(dist_app.resolve())

    # 模板：安装脚本会改成用户目录下的最终路径；包内文件也可直接用（绝对路径）
    desktop_text = (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Version=1.0\n"
        "Name=Mini_Lu\n"
        "Name[zh_CN]=Mini_Lu 桌宠\n"
        "GenericName=Desktop Pet\n"
        "GenericName[zh_CN]=桌面宠物\n"
        "Comment=Desktop pet with chat agent\n"
        "Comment[zh_CN]=桌面宠物与对话助手\n"
        f'Exec="{run_abs}"\n'
        f"Path={path_abs}\n"
        # 优先主题名；未跑 install 时用绝对 PNG 兜底（部分 DE 对绝对路径图标支持差）
        "Icon=mini-lu\n"
        f"X-MiniLu-IconFallback={icon_abs}\n"
        "Terminal=false\n"
        "Categories=Utility;\n"
        "Keywords=pet;agent;chat;desktop;Mini_Lu;桌宠;助手;\n"
        "StartupNotify=true\n"
        "StartupWMClass=Mini_Lu\n"
    )
    desktop = dist_app / "mini-lu.desktop"
    desktop.write_text(desktop_text, encoding="utf-8")
    _chmod_exec(desktop)
    print(f"已生成: {desktop.name}")

    install_sh = dist_app / "install_to_menu.sh"
    install_sh.write_text(
        """#!/usr/bin/env bash
# 将 Mini_Lu 注册到当前用户的应用菜单（含图标主题）
set -euo pipefail
HERE="$(cd "$(dirname "$(readlink -f "$0" 2>/dev/null || realpath "$0" 2>/dev/null || echo "$0")")" && pwd)"
APP_DIR="$HERE"
RUN="$APP_DIR/run_mini_lu.sh"
ICON_SRC=""
for c in \\
  "$APP_DIR/assets/icons/app_icon_256.png" \\
  "$APP_DIR/assets/icons/app_icon.png" \\
  "$APP_DIR/assets/icons/app_icon_512.png"
do
  if [[ -f "$c" ]]; then ICON_SRC="$c"; break; fi
done

if [[ ! -x "$RUN" ]]; then
  echo "错误: 找不到可执行启动脚本: $RUN" >&2
  exit 1
fi
chmod +x "$RUN" "$APP_DIR/Mini_Lu" 2>/dev/null || true
[[ -f "$APP_DIR/启动Mini_Lu.sh" ]] && chmod +x "$APP_DIR/启动Mini_Lu.sh" || true

APPS="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
ICONS="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor"
mkdir -p "$APPS"
mkdir -p "$ICONS/256x256/apps" "$ICONS/128x128/apps" "$ICONS/48x48/apps" "$ICONS/scalable/apps"

if [[ -n "$ICON_SRC" ]]; then
  install -m 644 "$ICON_SRC" "$ICONS/256x256/apps/mini-lu.png"
  # 部分菜单只扫 48/128
  install -m 644 "$ICON_SRC" "$ICONS/128x128/apps/mini-lu.png"
  install -m 644 "$ICON_SRC" "$ICONS/48x48/apps/mini-lu.png"
  echo "已安装图标 → $ICONS/*/apps/mini-lu.png"
else
  echo "警告: 未找到 app_icon_*.png，菜单可能无图标" >&2
fi

DESKTOP_OUT="$APPS/mini-lu.desktop"
cat > "$DESKTOP_OUT" <<EOF
[Desktop Entry]
Type=Application
Version=1.0
Name=Mini_Lu
Name[zh_CN]=Mini_Lu 桌宠
GenericName=Desktop Pet
GenericName[zh_CN]=桌面宠物
Comment=Desktop pet with chat agent
Comment[zh_CN]=桌面宠物与对话助手
Exec="$RUN"
Path=$APP_DIR
Icon=mini-lu
Terminal=false
Categories=Utility;
Keywords=pet;agent;chat;desktop;Mini_Lu;桌宠;助手;
StartupNotify=true
StartupWMClass=Mini_Lu
EOF
chmod 644 "$DESKTOP_OUT"
# 部分环境要求 .desktop 可执行才显示「允许启动」
chmod +x "$DESKTOP_OUT" 2>/dev/null || true

if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database "$APPS" >/dev/null 2>&1 || true
fi
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
  gtk-update-icon-cache -f -t "$ICONS" >/dev/null 2>&1 || true
fi
if command -v desktop-file-validate >/dev/null 2>&1; then
  desktop-file-validate "$DESKTOP_OUT" || true
fi

echo
echo "已写入: $DESKTOP_OUT"
echo "请在应用菜单搜索「Mini_Lu」或「桌宠」。"
echo "若仍看不到：注销重登，或按 Alt+F2 输入 r 回车（GNOME）刷新 Shell。"
echo "卸载菜单项: rm -f \\"$DESKTOP_OUT\\" \\"$ICONS\\"/*/apps/mini-lu.png"
""",
        encoding="utf-8",
    )
    _chmod_exec(install_sh)
    print(f"已生成: {install_sh.name}（推荐用它注册应用菜单）")

    readme = dist_app / "请读我.txt"
    readme.write_text(
        "启动方式\n"
        "  ./run_mini_lu.sh\n"
        "  或 ./启动Mini_Lu.sh\n"
        "  或 ./Mini_Lu\n"
        "\n"
        "加入 Ubuntu 应用菜单（推荐）\n"
        "  ./install_to_menu.sh\n"
        "  然后在菜单里搜索 Mini_Lu / 桌宠\n"
        "  （不要只 cp .desktop：还需安装图标到 ~/.local/share/icons）\n"
        "\n"
        "移植到其他 Ubuntu 电脑：复制整个 Mini_Lu 文件夹后，再跑一次 ./install_to_menu.sh\n"
        "不要运行项目里 build/ 目录下的临时文件。\n"
        "\n"
        "系统依赖（目标机建议安装）\n"
        "  sudo apt install -y libgl1 libegl1 libxcb-cursor0 libxkbcommon0 \\\n"
        "    fonts-noto-cjk xclip wl-clipboard\n"
        "\n"
        "窗口异常（尤其 Wayland）时可：\n"
        "  export QT_QPA_PLATFORM=xcb\n"
        "  ./run_mini_lu.sh\n"
        "\n"
        "【模型 / API】\n"
        "  右键形象 →「模型设置…」；或编辑 config/models.local.yaml\n"
        "  含 API Key 的文件夹请勿随意发给他人。\n"
        "\n"
        "【聊天】\n"
        "  助手回复支持 Markdown 表格/标题（需打包时含 markdown 库）。\n"
        "\n"
        "PDF：内置 PyMuPDF（config/doc_parsers.yaml）。\n"
        "工作区：config/workspace.yaml。\n"
        "Skills / MCP：skills/ 与 config/skills.yaml、mcp.yaml。\n",
        encoding="utf-8",
    )


def verify_dist(dist_app: Path) -> list[str]:
    warnings: list[str] = []
    exe = dist_app / "Mini_Lu"
    if not exe.is_file():
        raise SystemExit("打包失败：未找到可执行文件 Mini_Lu")
    if not os.access(exe, os.X_OK):
        warnings.append("警告: Mini_Lu 无执行权限，已尝试 chmod +x")
        try:
            _chmod_exec(exe)
        except OSError as e:
            warnings.append(f"警告: chmod 失败: {e}")

    internal = dist_app / "_internal"
    if not internal.is_dir():
        raise SystemExit("打包失败：缺少 _internal 目录")

    names_lower = {p.name.lower() for p in internal.rglob("*") if p.is_file()}
    has_fitz = any(
        n.startswith("fitz") or "pymupdf" in n or "mupdf" in n for n in names_lower
    )
    if not has_fitz:
        warnings.append(
            "警告: _internal 中未检测到 pymupdf/fitz，PDF 解析可能失败。"
            "请确认已 pip install pymupdf。"
        )
    else:
        print("已检测到 PyMuPDF 相关文件")

    for name in ("models.yaml", "doc_parsers.yaml", "mcp.yaml", "skills.yaml"):
        if not (dist_app / "config" / name).is_file():
            warnings.append(f"警告: 缺少 config/{name}")

    if not (dist_app / "data" / "prompts.json").is_file():
        warnings.append("警告: 缺少 data/prompts.json")

    has_md_pkg = (internal / "markdown").is_dir() or any(
        p.is_file() and p.parent.name == "markdown" and p.name == "__init__.py"
        for p in internal.rglob("__init__.py")
    )
    if not has_md_pkg:
        warnings.append(
            "警告: 未检测到 markdown 包，聊天 Markdown 可能无法渲染。"
            "请 pip install markdown 后重新打包。"
        )
    else:
        print("已检测到 markdown 包")

    skills_dir = dist_app / "skills"
    if not skills_dir.is_dir():
        warnings.append("警告: 缺少 skills/ 目录")

    has_tsa = (internal / "tree_sitter_analyzer").is_dir() or any(
        "tree_sitter_analyzer" in n for n in names_lower
    )
    if not has_tsa:
        warnings.append(
            "提示: 未检测到 tree_sitter_analyzer；"
            "可选: pip install -e ./tree-sitter-analyzer-main 后重打包。"
        )
    else:
        print("已检测到 tree_sitter_analyzer")

    skins = dist_app / "assets" / "skins"
    if not skins.is_dir() or not any(skins.iterdir()):
        raise SystemExit("打包失败：assets/skins 为空")

    # PySide6 6.x：plugins 在 PySide6/Qt/plugins/；旧布局偶见 PySide6/plugins/
    platforms_candidates = (
        dist_app / "_internal" / "PySide6" / "Qt" / "plugins" / "platforms",
        dist_app / "_internal" / "PySide6" / "plugins" / "platforms",
    )
    platforms = next((p for p in platforms_candidates if p.is_dir()), None)
    if platforms is None or not any(platforms.glob("*qxcb*")):
        warnings.append(
            "警告: 未找到 Qt platforms/libqxcb，换机可能无法启动窗口。"
            "可尝试: export QT_DEBUG_PLUGINS=1 ./Mini_Lu"
        )
    else:
        print("已检测到 Qt xcb 平台插件")

    crypto = internal / "libcrypto.so.3"
    ssl_mod = next(
        internal.rglob("_ssl.cpython-*-linux-gnu.so"),
        None,
    )
    if ssl_mod is not None and crypto.is_file():
        try:
            r = subprocess.run(
                ["ldd", str(ssl_mod)],
                capture_output=True,
                text=True,
                errors="replace",
            )
            if "OPENSSL_" in r.stderr and "not found" in r.stderr:
                warnings.append(
                    "警告: _ssl 与 libcrypto 版本不匹配（OpenSSL）。"
                    "请确认用 conda/venv 内 python 运行 build_linux.py，"
                    "且 fix_openssl_libs 已覆盖 _internal/libcrypto.so.3。"
                )
            elif r.returncode == 0:
                print("已检测 _ssl ↔ libcrypto 链接正常")
        except FileNotFoundError:
            pass

    return warnings


def _kill_packaged_app() -> list[str]:
    killed: list[str] = []
    try:
        r = subprocess.run(
            ["pkill", "-f", r"/dist/Mini_Lu(/|$)"],
            capture_output=True,
            text=True,
        )
        # pkill: 0=killed, 1=no process
        if r.returncode == 0:
            killed.append("Mini_Lu (dist)")
    except FileNotFoundError:
        pass
    except Exception:
        pass
    return killed


def prepare_collect_name() -> str:
    build_dir = ROOT / "build"
    if build_dir.exists():
        print(f"清理 {build_dir}")
        shutil.rmtree(build_dir, ignore_errors=True)

    killed = _kill_packaged_app()
    if killed:
        print(f"已结束仍在运行的: {', '.join(killed)}")
        time.sleep(0.5)

    if DIST_APP.exists():
        print(f"清理 {DIST_APP}")
        be._empty_dir(DIST_APP)
        if not be._dir_locked(DIST_APP):
            shutil.rmtree(DIST_APP, ignore_errors=True)
            if not DIST_APP.exists():
                return "Mini_Lu"
        print()
        print("=" * 60)
        print("dist/Mini_Lu 正被占用，无法删除。")
        print("常见原因：Mini_Lu 仍在运行，或文件管理器打开了该目录。")
        print("本次改为输出到 dist/Mini_Lu_new，完成后会尽量写回。")
        print("=" * 60)
        print()
        staging = ROOT / "dist" / "Mini_Lu_new"
        if staging.exists():
            be._empty_dir(staging)
            if not be._dir_locked(staging):
                shutil.rmtree(staging, ignore_errors=True)
        return "Mini_Lu_new"
    return "Mini_Lu"


def verify_app_icon() -> None:
    """Linux 优先检查 PNG；ICO 有则更好（spec 仍可用）。"""
    png = ROOT / "assets" / "icons" / "app_icon_256.png"
    ico = ROOT / "assets" / "icons" / "app_icon.ico"
    if png.is_file():
        print(f"应用图标 OK: {png.name}")
        return
    if ico.is_file():
        print(f"应用图标 OK: {ico.name}")
        return
    print(
        "提示: 未找到 assets/icons/app_icon_256.png，"
        "可运行 python tools/make_app_icon.py（非致命）"
    )


def main() -> int:
    _ensure_linux()
    _ensure_qt_works()
    print(f"打包解释器: {sys.executable}")
    be.ensure_dependencies(auto_install=True)
    be.ensure_pyinstaller()
    verify_app_icon()

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
        print("PyInstaller 失败。可检查：")
        print("  - 关掉正在运行的 Mini_Lu")
        print("  - 关掉打开 dist/Mini_Lu 的文件管理器")
        print("  - pip install -U pyinstaller && python build_linux.py")
        raise SystemExit(e.returncode) from e

    dist_app = be.merge_staging_into_dist(collect_name)

    if not (dist_app / "Mini_Lu").exists():
        print("打包失败：未找到 Mini_Lu")
        return 1

    copy_qt_plugins(dist_app)
    fix_openssl_libs(dist_app)
    be.copy_config_and_data(dist_app)
    be.sanitize_public_dist(dist_app)
    be.copy_skins(dist_app)
    be.copy_skills(dist_app)
    be.copy_docs(dist_app)
    be.ensure_markdown_in_dist(dist_app)
    write_launcher_and_readme(dist_app)

    warnings = verify_dist(dist_app)
    for w in warnings:
        print(w)

    build_dir = ROOT / "build"
    if build_dir.exists():
        shutil.rmtree(build_dir, ignore_errors=True)
        print("已清理中间目录 build/")

    size_mb = be.dir_size_mb(dist_app)
    print("\n打包完成！")
    print(f"请运行: {dist_app / '启动Mini_Lu.sh'}")
    print(f"体积约: {size_mb:.0f} MB")
    print("把整个 Mini_Lu 文件夹拷到其他 Ubuntu 电脑即可。")
    print("（目标机一般无需 Python；建议安装 libgl1 / libxcb-cursor0 / fonts-noto-cjk）")
    if warnings:
        print(f"（有 {len(warnings)} 条警告，见上方）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
