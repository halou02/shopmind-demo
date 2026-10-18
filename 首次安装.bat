@echo off
chcp 936 >nul
setlocal
cd /d "%~dp0"

echo ========================================
echo   ShopMind 环境安装 / 修复
echo ========================================
echo.

rem ---- 1. 找一个可用的 Python (>= 3.11) ----
rem   优先 py 启动器（避免 Windows 自带的应用商店占位符）
set "BASEPY="
call :try "py -3.12"
call :try "py -3.11"
call :try "py -3.13"
call :try "py -3.14"
call :try "py -3"
call :try "python"
if not defined BASEPY goto :nopython
echo 使用解释器: %BASEPY%
echo.

set "VPY=%CD%\.venv\Scripts\python.exe"

rem ---- 2. 已有 .venv 就先真跑一下，判断是否可用 ----
if exist "%VPY%" (
    "%VPY%" -c "import sys" >nul 2>&1
    if errorlevel 1 (
        echo 检测到 .venv 不可用（常见于从其它电脑拷贝过来的环境），正在重建...
        rmdir /s /q ".venv" >nul 2>&1
    )
)

rem ---- 3. 创建虚拟环境 ----
if not exist "%VPY%" (
    echo 正在创建虚拟环境 .venv ...
    %BASEPY% -m venv ".venv"
    if errorlevel 1 (
        echo [错误] 创建虚拟环境失败。
        goto :fail
    )
)

rem ---- 4. 安装依赖 ----
echo 正在安装依赖（首次可能需要几分钟，请耐心等待）...
"%VPY%" -m pip install -U pip setuptools wheel
"%VPY%" -m pip install -e .
if errorlevel 1 (
    echo.
    echo [错误] 依赖安装失败。请检查网络 / 代理后重试。
    goto :fail
)

rem ---- 5. 生成 .env ----
if not exist ".env" (
    if exist ".env.example" copy /Y ".env.example" ".env" >nul
    if exist "env.example" copy /Y "env.example" ".env" >nul
)

echo.
echo [完成] 环境已就绪，请双击「启动网站.bat」运行。
echo.
pause
exit /b 0

:nopython
echo [错误] 未找到可用的 Python（需要 3.11 或更高版本）。
echo.
echo   1) 请下载安装 Python: https://www.python.org/downloads/
echo   2) 安装时务必勾选 "Add Python to PATH"
echo.
start "" "https://www.python.org/downloads/"
pause
exit /b 1

:fail
echo.
pause
exit /b 1

rem ---- 子过程：探测某个解释器是否可用且版本达标 ----
:try
if defined BASEPY exit /b 0
%~1 -c "import sys;raise SystemExit(0 if sys.version_info[:2]>=(3,11) else 3)" >nul 2>&1
if not errorlevel 1 set "BASEPY=%~1"
exit /b 0
