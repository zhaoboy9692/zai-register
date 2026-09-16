@echo off
setlocal
set "PYTHONUTF8=1"
chcp 65001 >nul 2>&1
cd /d "%~dp0"
echo ========================================
echo   z.ai 注册机 — 环境安装
echo ========================================
echo.

echo [1/3] 安装 Python 依赖...
if not exist ".venv\Scripts\python.exe" (
    python -m venv .venv
    if errorlevel 1 (
        echo [错误] 创建虚拟环境失败，请检查 Python 安装和 PATH
        pause
        exit /b 1
    )
)
".venv\Scripts\python.exe" -m pip install -r requirements.txt -q
if errorlevel 1 (
    echo [错误] pip install 失败，请检查 Python 环境
    pause
    exit /b 1
)
echo [OK] Python 依赖安装完成
echo.

echo [2/3] 安装 Playwright Chromium 浏览器...
".venv\Scripts\python.exe" -m playwright install chromium
if errorlevel 1 (
    echo [错误] Playwright 浏览器安装失败
    pause
    exit /b 1
)
echo [OK] Chromium 安装完成
echo.

echo [3/3] 验证安装...
".venv\Scripts\python.exe" -c "import requests; import playwright.sync_api; print('  requests:', requests.__version__); print('  playwright: OK')"
if errorlevel 1 (
    echo [错误] 验证失败
    pause
    exit /b 1
)
echo.
echo ========================================
echo   安装完成！用法:
echo   run.bat --email user@example.com --password YourPass123
echo   run.bat --batch emails.txt --password YourPass123
echo ========================================
pause
