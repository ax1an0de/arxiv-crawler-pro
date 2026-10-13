@echo off
REM 用 PyInstall_RD 打包桌面版为单文件 exe（需先 install.bat 装依赖；本机需有网）
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo 未检测到 .venv，请先运行 install.bat 安装依赖。
    pause
    exit /b
)
echo 正在用 PyInstaller 打包 desktop_app.py 为单文件 exe ...
.venv\Scripts\python.exe -m PyInstaller --noconfirm --onefile --windowed --name ArxivCrawler ^
  --hidden-import tkinter --hidden-import tkinter.ttk ^
  --hidden-import paper --hidden-import paper_sources --hidden-import litman ^
  --hidden-import run --hidden-import arxiv_crawler --hidden-import arxiv_time ^
  --hidden-import async_translator --hidden-import categories ^
  desktop_app.py
echo 完成：dist\ArxivCrawler.exe
pause
