# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller：桌宠 UI + Agent。在 conda base 下务必大量 excludes，避免拖进科学计算栈。"""
import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules

block_cipher = None
ROOT = Path(SPECPATH)

hidden = [
    "yaml",
    "markdown",
    "markdown.extensions.tables",
    "markdown.extensions.fenced_code",
    "markdown.extensions.nl2br",
    "markdown.extensions.sane_lists",
    "openai",
    "httpx",
    "httpcore",
    "anyio",
    "sniffio",
    "h11",
    "certifi",
    "jiter",
    "distro",
    "tqdm",
    "pyperclip",
    "pypdf",
    "fitz",
    "pymupdf",
    "docx",
    "openpyxl",
    "reportlab",
    "langchain",
    "langchain.agents",
    "langchain.tools",
    "langchain_core",
    "langchain_openai",
    "langgraph",
    "langgraph.prebuilt",
    "langgraph.checkpoint.sqlite",
    "langgraph.store.sqlite",
    "langsmith",
    "pydantic",
    "pydantic_core",
    "typing_extensions",
    "annotated_types",
]
# markdown 扩展子模块（动态加载，需显式收集）
try:
    hidden += [
        m
        for m in collect_submodules("markdown")
        if "test" not in m.lower()
    ]
except Exception as e:
    print(f"[spec] collect_submodules(markdown) skipped: {e}")

# 只收集本项目 agent 包，不 collect_all 第三方
hidden += [
    m
    for m in collect_submodules("agent")
    if not m.endswith(".smoke_test_llm") and not m.endswith(".smoke_test_agent")
]

datas = []
binaries = []
# Linux + conda：显式带上与当前 Python 匹配的 OpenSSL，避免打进系统 3.0.x
# （否则 _ssl 需要 OPENSSL_3.3.0 时会启动失败）
if sys.platform.startswith("linux"):
    _lib = Path(sys.prefix) / "lib"
    for _name in ("libcrypto.so.3", "libssl.so.3"):
        _p = _lib / _name
        if _p.is_file():
            binaries.append((str(_p.resolve()), "."))
            print(f"[spec] bundle OpenSSL: {_p.resolve()}")
try:
    datas += collect_data_files("certifi")
except Exception:
    pass

# 强制把 markdown 包落到 _internal/markdown/（仅 hiddenimports 时校验可能看不到目录）
try:
    d, b, h = collect_all("markdown")
    datas += d
    binaries += b
    hidden += list(h)
    print(f"[spec] collect_all(markdown): datas={len(d)} binaries={len(b)} hidden+={len(h)}")
except Exception as e:
    print(f"[spec] collect_all(markdown) skipped: {e}")

# PyMuPDF：随包带上原生库（mupdf），否则目标机 PDF 解析失败
for pkg in ("pymupdf", "fitz"):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hidden += list(h)
        print(f"[spec] collect_all({pkg}): datas={len(d)} binaries={len(b)} hidden+={len(h)}")
    except Exception as e:
        print(f"[spec] collect_all({pkg}) skipped: {e}")

# 可选：代码结构分析（需 numpy；未安装则跳过）
_INCLUDE_TSA = False
try:
    import tree_sitter_analyzer  # noqa: F401

    _INCLUDE_TSA = True
    d, b, h = collect_all("tree_sitter_analyzer")
    datas += d
    binaries += b
    hidden += list(h)
    print(
        f"[spec] collect_all(tree_sitter_analyzer): "
        f"datas={len(d)} binaries={len(b)} hidden+={len(h)}"
    )
    # 各语言 grammar（已装的才收集）
    for lang_pkg in (
        "tree_sitter",
        "tree_sitter_python",
        "tree_sitter_c",
        "tree_sitter_cpp",
        "tree_sitter_javascript",
        "tree_sitter_typescript",
        "tree_sitter_java",
        "tree_sitter_go",
        "tree_sitter_rust",
        "tree_sitter_json",
        "tree_sitter_yaml",
        "tree_sitter_html",
        "tree_sitter_css",
        "tree_sitter_bash",
        "tree_sitter_markdown",
    ):
        try:
            __import__(lang_pkg)
            d, b, h = collect_all(lang_pkg)
            datas += d
            binaries += b
            hidden += list(h)
        except Exception:
            pass
except Exception as e:
    print(f"[spec] tree_sitter_analyzer skipped: {e}")

# 可选：MCP 客户端（未安装则跳过；运行时 mcp_client 会提示）
try:
    import langchain_mcp_adapters  # noqa: F401

    d, b, h = collect_all("langchain_mcp_adapters")
    datas += d
    binaries += b
    hidden += list(h)
    print(
        f"[spec] collect_all(langchain_mcp_adapters): "
        f"datas={len(d)} binaries={len(b)} hidden+={len(h)}"
    )
except Exception as e:
    print(f"[spec] langchain_mcp_adapters skipped: {e}")
try:
    import mcp  # noqa: F401

    d, b, h = collect_all("mcp")
    datas += d
    binaries += b
    hidden += list(h)
    print(f"[spec] collect_all(mcp): datas={len(d)} binaries={len(b)} hidden+={len(h)}")
except Exception as e:
    print(f"[spec] mcp skipped: {e}")

# conda base 里一堆可选依赖，必须排除，否则分析阶段会扫遍 pandas/scipy/…
# 注意：不要排除 pymupdf/fitz；torch/rembg 等大栈保持排除
# TSA 需要 numpy：启用 TSA 时不再排除 numpy
_EXCLUDE = [
    "tkinter",
    "matplotlib",
    "cv2",
    "rembg",
    "pandas",
    "scipy",
    "sklearn",
    "skimage",
    "statsmodels",
    "pyarrow",
    "xarray",
    "numba",
    "llvmlite",
    "h5py",
    "tables",
    "torch",
    "tensorflow",
    "keras",
    "transformers",
    "marker",
    "mineru",
    "sympy",
    "PIL",
    "Pillow",
    "PyQt5",
    "PyQt6",
    "IPython",
    "ipykernel",
    "ipywidgets",
    "jupyter",
    "jupyter_client",
    "jupyter_core",
    "notebook",
    "nbformat",
    "nbconvert",
    "sphinx",
    "docutils",
    "black",
    "yapf",
    "jedi",
    "parso",
    "zmq",
    "altair",
    "bokeh",
    "plotly",
    "dash",
    "streamlit",
    "seaborn",
    "lxml",
    "astor",
    "imageio",
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebEngineQuick",
    "PySide6.Qt3DCore",
    "PySide6.Qt3DRender",
    "PySide6.Qt3DInput",
    "PySide6.Qt3DLogic",
    "PySide6.Qt3DAnimation",
    "PySide6.Qt3DExtras",
    "PySide6.QtCharts",
    "PySide6.QtDataVisualization",
    "PySide6.QtGraphs",
    "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets",
    "PySide6.QtPdf",
    "PySide6.QtPdfWidgets",
    "PySide6.QtQuick",
    "PySide6.QtQuick3D",
    "PySide6.QtQml",
    "PySide6.QtBluetooth",
    "PySide6.QtNfc",
    "PySide6.QtPositioning",
    "PySide6.QtLocation",
    "PySide6.QtSensors",
    "PySide6.QtSerialPort",
    "PySide6.QtSerialBus",
    "PySide6.QtSql",
    "PySide6.QtTest",
    "PySide6.QtDesigner",
    "PySide6.QtUiTools",
]

if not _INCLUDE_TSA:
    _EXCLUDE.append("numpy")
else:
    print("[spec] numpy allowed (required by tree-sitter-analyzer)")

a = Analysis(
    [str(ROOT / "main.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=_EXCLUDE,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)


pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Mini_Lu",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ROOT / "assets" / "icons" / "app_icon.ico"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    # 允许 build_exe.py 在 dist/Mini_Lu 被占用时改打到备用目录名
    name=os.environ.get("MINI_LU_DIST_NAME", "Mini_Lu"),
)
