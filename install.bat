@echo off
chcp 65001 >nul 2>&1
echo ========================================
echo   z.ai 注册机 — 环境安装
echo ========================================
echo.

echo [1/3] 安装 Python 依赖...
python -m pip install -r requirements.txt -q
if errorlevel 1 (
    echo [错误] pip install 失败，请检查 Python 环境
    pause
    exit /b 1
)
echo [OK] Python 依赖安装完成
echo.

echo [2/3] 安装 Playwright Chromium 浏览器...
python -m playwright install chromium
if errorlevel 1 (
    echo [错误] Playwright 浏览器安装失败
    pause
    exit /b 1
)
echo [OK] Chromium 安装完成
echo.

echo [3/3] 验证安装...
python -c "import requests; import playwright.sync_api; print('  requests:', requests.__version__); print('  playwright: OK')"
if errorlevel 1 (
    echo [错误] 验证失败
    pause
    exit /b 1
)
echo.
echo ========================================
echo   安装完成！用法:
echo   python main.py --email user@example.com --password YourPass123
echo   python main.py --batch emails.txt --password YourPass123
echo ========================================
pause
