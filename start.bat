@echo off
rem 双击启动：运行本地服务并自动打开浏览器
cd /d "%~dp0"
where python >nul 2>nul
if %errorlevel% neq 0 (
    echo [错误] 没找到 Python，请先到 https://www.python.org/downloads/ 安装
    echo        安装时务必勾选 "Add python.exe to PATH"
    pause
    exit /b 1
)
python app.py
pause
