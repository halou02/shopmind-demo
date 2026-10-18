@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ========================================
echo   ShopMind 首次安装
echo ========================================
echo.

where python >nul 2>&1
if errorlevel 1 (
    echo [错误] 未找到 Python。请先安装 Python 3.11 或更高版本：
    echo   https://www.python.org/downloads/
    echo 安装时勾选 "Add Python to PATH"。
    pause
    exit /b 1
)

for /f "delims=" %%v in ('python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"') do set PYVER=%%v
echo 检测到 Python %PYVER%

if not exist ".venv\Scripts\python.exe" (
    echo 正在创建虚拟环境 .venv ...
    python -m venv .venv
    if errorlevel 1 (
        echo [错误] 创建虚拟环境失败。
        pause
        exit /b 1
    )
)

set "PY=%CD%\.venv\Scripts\python.exe"
set "PIP=%CD%\.venv\Scripts\pip.exe"

echo 正在安装依赖（首次可能需要几分钟）...
"%PIP%" install -U pip setuptools wheel >nul
"%PIP%" install -e .
if errorlevel 1 (
    echo [错误] 依赖安装失败，请检查网络或代理。
    pause
    exit /b 1
)

if not exist ".env" (
    if exist ".env.example" (
        copy /Y ".env.example" ".env" >nul
        echo 已生成 .env，请填入 DASHSCOPE_API_KEY（可选）。
    ) else if exist "env.example" (
        copy /Y "env.example" ".env" >nul
        echo 已生成 .env，请填入 DASHSCOPE_API_KEY（可选）。
    )
)

echo.
echo [完成] 安装成功。请双击「启动网站.bat」运行。
echo.
pause
