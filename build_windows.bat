@echo off
REM ============================================================
REM  哔哩哔哩自动投稿程序 —— Windows 一键打包
REM
REM  双击本文件即可。产物: dist\bilibili-submit.exe
REM  要求: 已安装 Python 3.9+ 并勾选 "Add Python to PATH"
REM ============================================================
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo.
echo [1/6] 检查 Python...
python --version >nul 2>&1
if errorlevel 1 (
    echo [x] 未找到 Python。请先安装 Python 3.9 或更高版本，
    echo     安装时务必勾选 "Add Python to PATH"。
    pause
    exit /b 1
)

echo [2/6] 创建虚拟环境 .venv ...
if not exist ".venv" (
    python -m venv .venv
    if errorlevel 1 (
        echo [x] 创建虚拟环境失败
        pause
        exit /b 1
    )
)
call .venv\Scripts\activate.bat

echo [3/6] 安装依赖 ...
python -m pip install --upgrade pip -q
python -m pip install -r requirements.txt pyinstaller -q
if errorlevel 1 (
    echo [x] 依赖安装失败，请检查网络
    pause
    exit /b 1
)

echo [4/6] 打包主程序 ...
if exist build rd /s /q build
if exist dist rd /s /q dist
python -m PyInstaller bili_submit.spec --noconfirm --clean
if errorlevel 1 (
    echo [x] 打包失败
    pause
    exit /b 1
)

echo [5/6] 准备 ffmpeg（封面自动抽帧用，可选）...
python tools\setup_ffmpeg.py --dest dist
if errorlevel 1 (
    echo.
    echo [!] 未附带 ffmpeg。程序除 "cover: auto" 自动抽帧外不依赖它，
    echo     正常投稿不受影响。需要的话可手动把 ffmpeg.exe 放到 dist\ 目录下。
)

echo [6/6] 验证产物 ...
if not exist "dist\bilibili-submit.exe" (
    echo [x] 未生成 dist\bilibili-submit.exe
    pause
    exit /b 1
)

"dist\bilibili-submit.exe" --version
if errorlevel 1 (
    echo [!] exe 启动异常，可能是缺少 VC++ 运行库或被杀毒软件拦截
) else (
    echo [OK] exe 校验通过
)

echo.
echo ============================================
echo  打包完成，dist 目录内容:
dir /b dist
echo.
echo  使用:
echo    dist\bilibili-submit.exe login          扫码登录
echo    dist\bilibili-submit.exe --help        查看全部命令
echo    dist\bilibili-submit.exe check        自检(登录态/ffmpeg)
echo ============================================
echo.
pause
