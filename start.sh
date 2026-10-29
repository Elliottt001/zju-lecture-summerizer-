#!/bin/sh
# Linux / macOS 通用启动入口
cd "$(dirname "$0")" || exit 1
if command -v python3 >/dev/null 2>&1; then
    exec python3 app.py
else
    echo "未找到 python3，请先安装 Python 3.9+"
    exit 1
fi
