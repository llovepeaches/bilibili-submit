@echo off
REM ============================================================
REM  哔哩哔哩自动投稿程序 —— Windows 一键打包
REM
REM  双击本文件即可。会打出三种产物：
REM    dist\bilibili-submit.exe            命令行轻量版
REM    dist\bilibili-submit-gui\...\      图形界面版（安装用，onedir）
REM    dist\bilibili-submit-setup.exe     安装器（需要 Inno Setup）
REM
REM  要求: 已安装 Python 3.9+ 并勾选 "Add Python to PATH"
REM  打包安装器还需 Inno Setup 6（https://jrsoftware.org/isinfo.php）；
REM  没装的话前两步照常出 exe，只是跳过安装器。
REM ============================================================
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo.
echo [1/8] 检查 Python...
python --version >nul 2>&1
if errorlevel 1 (
    echo [x] 未找到 Python。请先安装 Python 3.9 或更高版本，
    echo     安装时务必勾选 "Add Python to PATH"。
    pause
    exit /b 1
)

echo [2/8] 创建虚拟环境 .venv ...
if not exist ".venv" (
    python -m venv .venv
    if errorlevel 1 (
        echo [x] 创建虚拟环境失败
        pause
        exit /b 1
    )
)
call .venv\Scripts\activate.bat

echo [3/8] 安装依赖 ...
python -m pip install --upgrade pip -q
python -m pip install -r requirements.txt pyinstaller -q
if errorlevel 1 (
    echo [x] 依赖安装失败，请检查网络
    pause
    exit /b 1
)

REM ------------------------------------------------------------
REM  准备 ffmpeg。两种打包方式对它的要求不同，所以放vendor\
REM  （不直接放 dist\）：
REM    * 命令行轻量版：要放在 dist\ffmpeg.exe（外置）
REM    * 图形界面安装版：要放在 exe 同目录，ffmpeg.py 本来就优先
REM      找这个位置，用户也能自己换版本
REM ------------------------------------------------------------
echo [4/8] 准备 ffmpeg ...
python tools\setup_ffmpeg.py --dest vendor --force
if errorlevel 1 (
    echo [!] 未能准备 ffmpeg。程序除 "cover: auto" 自动抽帧外
    echo     不依赖它，缺了仍可打包，只是该功能不可用。
)

echo.
echo [5/8] 打包命令行版 ...
if exist build rd /s /q build
python -m PyInstaller bili_submit.spec --noconfirm --clean
if errorlevel 1 (
    echo [x] 打包失败
    pause
    exit /b 1
)
if exist "dist\ffmpeg.exe" del "dist\ffmpeg.exe"
if exist vendor\ffmpeg.exe copy vendor\ffmpeg.exe dist\ffmpeg.exe >nul

echo.
echo [6/8] 打包图形界面版（onedir，安装用）...
REM  INSTALLER=1 让 spec 走 onedir：ffmpeg 不进归档，改放exe 同目录。
REM  每次启动省掉把60MB 解压到 %%TEMP%%，启动明显更快。
REM  --clean 会清build\ 缓存（**不会**删 dist\ 里已有的产物，实测过），
REM  用上它避免两次构建共用缓存。dist\bilibili-submit.exe 因此得以保留。
set GUI=1
set INSTALLER=1
set EXE_NAME=bilibili-submit-gui
python -m PyInstaller bili_submit.spec --noconfirm --clean
set GUI=
set INSTALLER=
set EXE_NAME=
if errorlevel 1 (
    echo [x] 图形界面版打包失败
    pause
    exit /b 1
)

REM  onedir 产物只有 EXE 和 _internal\，其余文件要手工补
if not exist "dist\bilibili-submit-gui" (
    echo [x] 未生成 dist\bilibili-submit-gui\
    pause
    exit /b 1
)
if exist vendor\ffmpeg.exe (
    copy vendor\ffmpeg.exe "dist\bilibili-submit-gui\ffmpeg.exe" >nul
) else (
    echo [!] vendor 里没有 ffmpeg.exe，图形界面版将不带ffmpeg
)
if not exist "dist\bilibili-submit-gui\config" (
    xcopy config "dist\bilibili-submit-gui\config\" /e /i /q >nul
)
copy README.md "dist\bilibili-submit-gui\" >nul

REM ------------------------------------------------------------
REM  编译安装器。ISCC 只在装了 Inno Setup 时才有，找不到就跳过
REM  （不当成失败——本地没装 Inno Setup 很常见）。
REM ------------------------------------------------------------
echo.
echo [7/8] 编译安装器 ...
set ISCC=
for %%P in (
    "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
    "%ProgramFiles%\Inno Setup 6\ISCC.exe"
    "%ProgramFiles(x86)%\Inno Setup 7\ISCC.exe"
    "%ProgramFiles%\Inno Setup 7\ISCC.exe"
) do (
    if exist %%P if not defined ISCC set ISCC=%%~P
)
if not defined ISCC (
    echo [!] 未找到 Inno Setup，跳过安装器编译。
    echo     图形界面版目录已就绪：dist\bilibili-submit-gui\
    echo     装 Inno Setup 6 后重跑本脚本即可生成 setup.exe。
) else (
    echo     使用 %ISCC%
    python tools\check_installer.py
    if errorlevel 1 (
        echo [x] installer.iss 静态检查未通过
        pause
        exit /b 1
    )
    !ISCC! installer.iss
    if errorlevel 1 (
        echo [x] 安装器编译失败
        pause
        exit /b 1
    )
)

echo.
echo [8/8] 验证产物 ...
if not exist "dist\bilibili-submit.exe" (
    echo [x] 未生成 dist\bilibili-submit.exe
    pause
    exit /b 1
)
if not exist "dist\bilibili-submit-gui\bilibili-submit-gui.exe" (
    echo [x] 未生成 dist\bilibili-submit-gui\bilibili-submit-gui.exe
    pause
    exit /b 1
)
if not exist "dist\bilibili-submit-gui\_internal" (
    echo [x] 缺少 _internal\（Python 运行时），图形界面版会双击闪退
    pause
    exit /b 1
)

"dist\bilibili-submit.exe" --version
if errorlevel 1 (
    echo [!] exe 启动异常，可能是缺少 VC++ 运行库或被杀毒软件拦截
) else (
    echo [OK] 命令行版校验通过
)

echo.
echo ============================================
echo  打包完成
echo.
echo  命令行版:
echo    dist\bilibili-submit.exe
echo.
echo  图形界面版（可整个目录拷走直接用）:
echo    dist\bilibili-submit-gui\
echo.
if exist "dist\bilibili-submit-setup.exe" (
    echo  安装器:
    echo    dist\bilibili-submit-setup.exe
    echo.
)
echo  使用:
echo    图形界面版: 双击 dist\bilibili-submit-gui\bilibili-submit-gui.exe
echo              先在「登录」页扫码，再去「投稿」页
echo    命令行:    dist\bilibili-submit.exe login
echo              dist\bilibili-submit.exe --help
echo ============================================
echo.
pause
