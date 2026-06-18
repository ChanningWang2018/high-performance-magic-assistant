@echo off
chcp 65001 >nul
title Hawarma - 烹饪游戏自动化

:: 检查虚拟环境
if not exist ".venv" (
    echo [错误] 未找到虚拟环境，请先运行 setup.bat
    pause
    exit /b 1
)

:: 启动 TUI — 直接使用虚拟环境的 Python，避免 activate 不生效的问题
.venv\Scripts\python.exe -m hawarma.tui
pause