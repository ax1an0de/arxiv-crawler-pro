@echo off
REM ArxivCrawler v0.14.0 portable - Windows 一键装依赖
chcp 65001 >nul
cd /d "%~dp0"
echo === ArxivCrawler v0.14.0 依赖安装 ===
where python >nul 2>&1
if errorlevel 1 (
    echo [错误] 未检测到 python.exe，请先装 Python 3.10-3.12 ^(https://www.python.org/^)
    pause
    exit /b 1
)
if not exist ".venv\Scripts\python.exe" (
    echo 创建虚拟环境 .venv ...
    python -m venv .venv
    if errorlevel 1 (
        echo [错误] 创建 venv 失败
        pause
        exit /b 1
    )
)
echo 安装依赖到 .venv ...
.venv\Scripts\python.exe -m pip install --upgrade pip --no-cache-dir
.venv\Scripts\python.exe -m pip install -r requirements.txt --no-cache-dir
if errorlevel 1 (
    echo [错误] 依赖安装失败
    pause
    exit /b 1
)
echo.
echo === 装好了。下一步：双击 launch_desktop.bat 启动主程序，launch_generator.bat 启动激活码生成器 ===
pause
