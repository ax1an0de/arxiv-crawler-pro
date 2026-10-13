#!/usr/bin/env bash
# ArxivCrawler v0.14.0 portable - Linux/macOS 装依赖
set -e
cd "$(dirname "$0")"
echo "=== ArxivCrawler v0.14.0 依赖安装 ==="
if ! command -v python3 >/dev/null 2>&1; then
    echo "[错误] 未检测到 python3，请先装 Python 3.10-3.12"
    exit 1
fi
if [ ! -x ".venv/bin/python" ]; then
    echo "创建虚拟环境 .venv ..."
    python3 -m venv .venv
fi
echo "安装依赖到 .venv ..."
.venv/bin/python -m pip install --upgrade pip --no-cache-dir
.venv/bin/python -m pip install -r requirements.txt --no-cache-dir
echo
echo "=== 装好了。下一步：./launch_desktop.sh 启动主程序，./launch_generator.sh 启动激活码生成器 ==="
