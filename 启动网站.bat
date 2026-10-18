@echo off
chcp 65001 >nul
cd /d "%~dp0"

set "PY=%CD%\.venv\Scripts\python.exe"

if not exist "%PY%" (
    echo 未检测到虚拟环境，正在运行首次安装...
    call "%~dp0首次安装.bat"
    if not exist "%PY%" exit /b 1
)

for /f "tokens=5" %%a in ('netstat -ano ^| findstr /R /C:":8080 .*LISTENING"') do (
    echo 正在停止旧服务 PID %%a ...
    taskkill /PID %%a /F >nul 2>&1
)
timeout /t 1 /nobreak >nul

echo ========================================
echo   ShopMind Web
echo   地址: http://127.0.0.1:8080
echo   关闭本窗口即停止服务
echo ========================================
echo.
echo 首次使用请先配置 .env 中的 API Key（见 技术说明.txt）
echo.

set SHOPMIND_OPEN_BROWSER=1
"%PY%" -m app.web_main

echo.
echo 服务已停止。
pause
