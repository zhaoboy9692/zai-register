@echo off
chcp 65001 >nul 2>&1
echo ========================================
echo   z.ai 注册机
echo ========================================
echo.
python main.py %*
pause
