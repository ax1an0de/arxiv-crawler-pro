@echo off
REM 启动桌面版（tkinter GUI，不依赖浏览器，联网抓取真实论文）
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo 未检测到 .venv，请先运行 install.bat 安装依赖。
    pause
    exit /b
)
.venv\Scripts\python.exe desktop_app.py
