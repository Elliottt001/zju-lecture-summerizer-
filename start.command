#!/bin/bash
# 双击启动：会打开终端运行本地服务，并自动弹出浏览器界面
cd "$(dirname "$0")"
exec python3 app.py
