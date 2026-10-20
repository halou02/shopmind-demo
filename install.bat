@echo off
chcp 936 >nul
setlocal
cd /d "%~dp0"

echo ========================================
echo   ShopMind 首次安装 / 修复
echo ========================================
echo.

if not exist "%~dp0app\web_main.py" (
    echo [错误] 没有找到程序文件 app\web_main.py
    echo 请先把压缩包完整解压到一个文件夹，再双击本脚本。
    echo.
    pause
    exit /b 1
)

rem ---- 1. 找一个可用的 Python (>= 3.11) ----
rem   注意 py 启动器是 Windows 自带应用商店占位符时不可用
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

rem ---- 2. 检查 .venv 是否还能用，坏了就重建 ----
if exist "%VPY%" (
    "%VPY%" -c "import sys" >nul 2>&1
    if errorlevel 1 (
        echo 检测到 .venv 已损坏（可能是在别的电脑上生成的），正在重建...
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

rem ---- 4. 安装依赖：优先国内镜像，失败回退官方源 ----
set "MIRROR=-i https://pypi.tuna.tsinghua.edu.cn/simple --timeout 30"
echo 正在安装依赖（首次可能需要几分钟），请耐心等待...
"%VPY%" -m pip install -U pip setuptools wheel %MIRROR%
"%VPY%" -m pip install -e . %MIRROR%
if errorlevel 1 (
    echo.
    echo 国内镜像不可用，改用官方源重试...
    "%VPY%" -m pip install -U pip setuptools wheel
    "%VPY%" -m pip install -e .
)
if errorlevel 1 (
    echo.
    echo [错误] 依赖安装失败，通常是网络问题。
    echo 建议：换个网络或手机热点后，重新双击本脚本。
    echo 也可以手动执行：
    echo   "%VPY%" -m pip install -e . -i https://pypi.tuna.tsinghua.edu.cn/simple
    goto :fail
)

rem ---- 5. 生成 .env ----
if not exist ".env" (
    if exist ".env.example" copy /Y ".env.example" ".env" >nul
    if exist "env.example" copy /Y "env.example" ".env" >nul
)

echo.
echo [完成] 安装完成，请双击 启动网站.bat（或 start.bat）运行。
echo.
pause
exit /b 0

:nopython
echo [错误] 未找到可用的 Python，需要 3.11 或更高版本。
echo.
echo   1) 打开下载页安装 Python: https://www.python.org/downloads/
echo   2) 安装时务必勾选 "Add Python to PATH"
echo   3) 装完关掉所有黑窗口，重新双击 首次安装.bat
echo.
start "" "https://www.python.org/downloads/"
pause
exit /b 1

:fail
echo.
pause
exit /b 1

rem ---- 子过程：探测某个命令是否可用且版本够 ----
:try
if defined BASEPY exit /b 0
%~1 -c "import sys;raise SystemExit(0 if sys.version_info[:2]>=(3,11) else 3)" >nul 2>&1
if not errorlevel 1 set "BASEPY=%~1"
exit /b 0
