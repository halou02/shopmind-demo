@echo off
chcp 936 >nul
setlocal
cd /d "%~dp0"

echo ========================================
echo   ShopMind 启动器
echo ========================================
echo.

if not exist "%~dp0app\web_main.py" (
    echo [错误] 没有找到程序文件 app\web_main.py
    echo 请先把压缩包完整解压到一个文件夹，再双击本脚本。
    echo 不要在压缩包预览窗口里直接双击运行。
    echo.
    pause
    exit /b 1
)

set "VPY=%CD%\.venv\Scripts\python.exe"
set "PYCMD="

rem ---- 1. 校验 .venv 是否真能用（含全部依赖，不是只看文件存在）----
if exist "%VPY%" (
    "%VPY%" -c "import fastapi, uvicorn, openpyxl, dotenv, httpx, PIL" >nul 2>&1
    if not errorlevel 1 set "PYCMD="%VPY%""
)

rem ---- 2. venv 不可用，找系统里已装好依赖的 Python ----
if not defined PYCMD (
    for %%C in ("py -3.12" "py -3.11" "py -3.13" "py -3.14" "py -3" "python") do (
        if not defined PYCMD (
            %%~C -c "import fastapi, uvicorn, openpyxl, dotenv, httpx, PIL" >nul 2>&1
            if not errorlevel 1 set "PYCMD=%%~C"
        )
    )
)

rem ---- 3. 还是不行，自动安装 / 修复 ----
if not defined PYCMD (
    echo 首次运行，或依赖不完整，正在自动安装，请稍候...
    echo.
    call "%~dp0install.bat"
    if exist "%VPY%" (
        "%VPY%" -c "import fastapi, uvicorn, openpyxl, dotenv, httpx, PIL" >nul 2>&1
        if not errorlevel 1 set "PYCMD="%VPY%""
    )
)

if not defined PYCMD (
    echo.
    echo [错误] 安装没有完成，请查看上方提示。
    echo 最常见原因：这台电脑没有安装 Python 3.11 或更高版本。
    echo 下载地址：https://www.python.org/downloads/
    echo 安装时务必勾选 "Add Python to PATH"。
    echo.
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
echo 首次使用：在 .env 里填 API Key，或点网页右上角「AI 设置」；未配置也能跑演示。
echo.

set "SHOPMIND_OPEN_BROWSER=1"
%PYCMD% -m app.web_main

echo.
echo 服务已停止。
pause
exit /b 0
