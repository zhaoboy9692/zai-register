@echo off
setlocal
set "PYTHONUTF8=1"
chcp 65001 >nul 2>&1
cd /d "%~dp0"
echo ========================================
echo   z.ai 注册机
echo ========================================
echo.
if not exist ".venv\Scripts\python.exe" (
    echo [错误] 未找到项目环境，请先运行 install.bat
    pause
    exit /b 1
)
".venv\Scripts\python.exe" main.py %*
set "run_exit_code=%errorlevel%"
pause
exit /b %run_exit_code%
