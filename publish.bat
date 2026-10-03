@echo off
REM ================================================================
REM  一键发布 —— 自动建仓库 + 推送 + 触发云端打包 + 生成公开下载链接
REM
REM  只需首次准备：
REM    1. 安装 Git for Windows        https://git-scm.com/download/win
REM    2. 安装 GitHub CLI (gh)        winget install --id GitHub.cli
REM       并执行一次 gh auth login    （浏览器授权登录 GitHub）
REM
REM  之后每次发版：双击本文件，输入版本号，回车即可。
REM ================================================================
setlocal enabledelayedexpansion
cd /d "%~dp0"

set REPO_NAME=bilibili-submit
if "%~1"=="" (set VERSION=0.1.0) else (set VERSION=%1)
set TAG=v%VERSION%

echo.
echo ================================================================
echo   bilibili-submit 一键发布   版本 %TAG%
echo ================================================================
echo.

REM ---------- 前置检查 ----------
where git >nul 2>&1
if errorlevel 1 (
    echo [x] 未找到 Git。请安装 Git for Windows: https://git-scm.com/download/win
    echo     安装后重新运行本脚本。
    pause
    exit /b 1
)

where gh >nul 2>&1
if errorlevel 1 (
    echo [x] 未找到 GitHub CLI。请执行: winget install --id GitHub.cli
    echo     安装后重新运行本脚本。
    pause
    exit /b 1
)

gh auth status >nul 2>&1
if errorlevel 1 (
    echo [!] GitHub CLI 尚未登录，正在引导登录...
    echo.
    gh auth login --hostname github.com --git-protocol https --web
    if errorlevel 1 (
        echo [x] 登录失败，请手动执行 gh auth login 后重试
        pause
        exit /b 1
    )
)

for /f "delims=" %%i in ('gh api user --jq .login') do set ACCOUNT=%%i
echo [OK] GitHub 账号: !ACCOUNT!

REM ---------- 确认仓库名 ----------
echo.
set /p REPO_NAME="仓库名（直接回车用默认 !REPO_NAME!）: "
if "!REPO_NAME!"=="" set REPO_NAME=bilibili-submit

REM ---------- 检查仓库是否已存在 ----------
gh repo view !ACCOUNT!/!REPO_NAME! >nul 2>&1
if errorlevel 1 (
    echo.
    echo [1/4] 创建公开仓库 !ACCOUNT!/!REPO_NAME! ...
    gh repo create !REPO_NAME! --public --description "哔哩哔哩自动投稿程序" --source=. --remote=origin --push
    if errorlevel 1 (
        echo [x] 创建仓库失败
        pause
        exit /b 1
    )
    echo [OK] 仓库已创建并推送
) else (
    echo.
    echo [1/4] 仓库已存在: !ACCOUNT!/!REPO_NAME!
    git remote get-url origin >nul 2>&1
    if errorlevel 1 (git remote add origin https://github.com/!ACCOUNT!/!REPO_NAME!.git)
    git add -A
    git commit -q -m "release: !TAG!" 2>nul
    git push origin HEAD --set-upstream
)

REM ---------- 打标签并推送 ----------
echo.
echo [2/4] 创建标签 !TAG! ...
git tag -d !TAG! >nul 2>&1
git tag -a !TAG! -m "release !TAG!"
if errorlevel 1 (
    echo [x] 打标签失败
    pause
    exit /b 1
)

echo [3/4] 推送标签，触发云端构建 ...
git push origin !TAG!
if errorlevel 1 (
    echo [x] 推送标签失败
    pause
    exit /b 1
)

REM ---------- 给出链接 ----------
echo.
echo [4/4] 等待云端构建（约 3-5 分钟）...
echo.
echo ================================================================
echo   构建进度:
echo     https://github.com/!ACCOUNT!/!REPO_NAME!/actions
echo.
echo   构建完成后，公开下载链接（无需登录，直接发给别人）:
echo.
echo     完整版（自带 ffmpeg，开箱即用）:
echo     https://github.com/!ACCOUNT!/!REPO_NAME!/releases/download/!TAG!/bilibili-submit-full-windows.zip
echo.
echo     轻量版（约 11MB，不含 ffmpeg）:
echo     https://github.com/!ACCOUNT!/!REPO_NAME!/releases/download/!TAG!/bilibili-submit-mini-windows.zip
echo ================================================================
echo.
echo   提示：可在仓库 Settings - Pages 开启一个下载页，
echo         把上面的链接挂上去，做成一个对外的下载页面。
echo.
pause
