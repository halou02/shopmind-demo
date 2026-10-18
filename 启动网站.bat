@echo off
chcp 936 >nul
setlocal
cd /d "%~dp0"

set "VPY=%CD%\.venv\Scripts\python.exe"
set "PYCMD="

rem ---- 1. 校验 .venv 是否真的能跑（不只是文件存在）----
if exist "%VPY%" (
    "%VPY%" -c "import fastapi, uvicorn" >nul 2>&1
    if not errorlevel 1 set "PYCMD="%VPY%""
)

rem ---- 2. venv 不可用：借用系统里已装好依赖的 Python ----
if not defined PYCMD (
    for %%C in ("py -3.12" "py -3.11" "py -3.13" "py -3.14" "py -3" "python") do (
        if not defined PYCMD (
            %%~C -c "import fastapi, uvicorn" >nul 2>&1
            if not errorlevel 1 set "PYCMD=%%~C"
        )
    )
)

rem ---- 3. 仍不可用：自动调用安装 / 修复 ----
if not defined PYCMD (
    echo 未检测到可用的运行环境，正在执行安装 / 修复...
    echo.
    call "%~dp0首次安装.bat"
    if exist "%VPY%" (
        "%VPY%" -c "import fastapi, uvicorn" >nul 2>&1
        if not errorlevel 1 set "PYCMD="%VPY%""
    )
)

if not defined PYCMD (
    echo.
    echo [错误] 运行环境仍未就绪，请查看上方提示后重试。
    pause
    exit /b 1
)

rem ---- 4. 释放 8080 端口上的旧进程 ----
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
echo 首次使用请先配置 .env 中的 API Key（见 技术说明.txt），未配置也可离线运行。
echo.

set "SHOPMIND_OPEN_BROWSER=1"
%PYCMD% -m app.web_main

echo.
echo 服务已停止。
pause
exit /b 0
