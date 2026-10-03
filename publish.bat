@echo off
REM ============================================================
REM  一键发布：打包 -> 推 tag -> GitHub Actions 自动构建并发布 Release
REM
REM  前置条件（只需做一次）：
REM    1. 在 GitHub 新建一个空仓库（不要勾选 README/.gitignore）
REM    2. 在本目录执行一次：
REM         git remote add origin https://github.com/你的账号/仓库名.git
REM    3. 首次使用需登录：git config --global user.name / user.email
REM ============================================================
setlocal
cd /d "%~dp0"

if "%1"=="" (
    set VERSION=0.1.0
) else (
    set VERSION=%1
)
set TAG=v%VERSION%

echo.
echo ============================================
echo  准备发布版本 %TAG%
echo ============================================
echo.

where git >nul 2>&1
if errorlevel 1 (
    echo [x] 未找到 git，请先安装 Git for Windows
    pause
    exit /b 1
)

REM --- 检查远程仓库 ---
git remote get-url origin >nul 2>&1
if errorlevel 1 (
    echo [!] 尚未配置远程仓库。
    echo.
    echo     请先在 https://github.com/new 新建一个空仓库，然后执行：
    echo         git remote add origin https://github.com/你的账号/仓库名.git
    echo.
    pause
    exit /b 1
)

for /f "delims=" %%i in ('git remote get-url origin') do set REMOTE=%%i
echo 远程仓库: %REMOTE%
echo.

REM --- 提交 ---
echo [1/3] 提交代码...
git add -A
git commit -m "release: %TAG%" 2>nul
if errorlevel 1 (
    echo      (无改动可提交，跳过)
)

REM --- 打 tag ---
echo [2/3] 创建标签 %TAG% ...
git tag -d %TAG% >nul 2>&1
git tag -a %TAG% -m "release %TAG%"
if errorlevel 1 (
    echo [x] 打标签失败
    pause
    exit /b 1
)

REM --- 推送 ---
echo [3/3] 推送到 GitHub（将自动触发构建，约需 3-5 分钟）...
git push origin main
if errorlevel 1 (
    echo [!] 推送 main 失败，尝试推送当前分支...
    git push origin HEAD
)
git push origin %TAG%
if errorlevel 1 (
    echo [x] 推送标签失败
    pause
    exit /b 1
)

echo.
echo ============================================
echo  已推送，等待 GitHub Actions 构建...
echo.
echo  查看构建进度:
echo    https://github.com/%REMOTE:.git=%/actions
echo.
echo  构建完成后，公开下载链接形如:
echo    https://github.com/%REMOTE:.git=%/releases/download/%TAG%/bilibili-submit-full-windows.zip
echo.
echo  （该链接公开可访问，无需登录，别人可以直接点开下载）
echo ============================================
echo.
pause
