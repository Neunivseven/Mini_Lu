@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Building Mini_Lu...

REM 优先用 rembg_env（base 的 PySide6 常 DLL 损坏，打出的 exe 会 QtWidgets 加载失败）
set "PY=D:\Anaconda\envs\rembg_env\python.exe"
if not exist "%PY%" set "PY=python"

echo Using: %PY%
"%PY%" -c "from PySide6.QtWidgets import QApplication" 2>nul
if errorlevel 1 (
  echo.
  echo [错误] 该 Python 无法加载 PySide6.QtWidgets。
  echo 请先激活 rembg_env，或安装完好的 PySide6 后再打包：
  echo   conda activate rembg_env
  echo   python build_exe.py
  pause
  exit /b 1
)

REM PDF / Markdown 等依赖（可随包，打包环境需已安装；缺失时 build_exe.py 会尝试补装）
"%PY%" -c "import fitz" 2>nul
if errorlevel 1 (
  echo [提示] 未检测到 pymupdf，build_exe.py 将尝试自动安装…
)
"%PY%" -c "import markdown" 2>nul
if errorlevel 1 (
  echo [提示] 未检测到 markdown，build_exe.py 将尝试自动安装…
)

"%PY%" build_exe.py
if errorlevel 1 (
  echo Build failed.
  pause
  exit /b 1
)
echo.
echo ========================================
echo  请运行: dist\Mini_Lu\Mini_Lu.exe
echo  不要运行 build\ 目录下的文件
echo ========================================
explorer "dist\Mini_Lu"
pause
