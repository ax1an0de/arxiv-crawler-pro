#!/usr/bin/env bash
# 启动激活码生成器（仅授权方使用）
set -e
cd "$(dirname "$0")"
if [ ! -x ".venv/bin/python" ]; then
    echo "未检测到 .venv，请先运行 ./install.sh 安装依赖。"
    exit 1
fi
.venv/bin/python license_generator.py
