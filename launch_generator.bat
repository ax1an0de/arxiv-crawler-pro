@echo off
REM 启动激活码生成器（仅授权方使用，需要 license_keys\private_key.pem 放在本目录）
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo 未检测到 .venv，请先运行 install.bat 安装依赖。
    pause
    exit /b
)
.venv\Scripts\python.exe license_generator.py
