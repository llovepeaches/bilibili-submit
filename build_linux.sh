#!/usr/bin/env bash
# ============================================================
#  哔哩哔哩自动投稿程序 —— Linux 一键打包（AppImage）
#
#  用法：
#      ./build_linux.sh                     # CLI + GUI + AppImage + tar.gz
#      ALLOW_NO_FFMPEG=1 ./build_linux.sh   # 明确产不带 ffmpeg 的包
#      NO_APPIMAGE=1 ./build_linux.sh       # 只出目录版 tar.gz
#      SKIP_VENV=1 ./build_linux.sh         # 用系统 Python，不建 .venv
#      APPIMAGETOOL=/path/to/appimagetool ./build_linux.sh
#
#  产物（都在 dist/ 下）：
#      bilibili-submit-cli-<arch>.AppImage        命令行版
#      bilibili-submit-gui-<arch>.AppImage        图形界面版
#      bilibili-submit-cli-linux-<arch>.tar.gz    目录版（AppImage 跑不了时的退路）
#      bilibili-submit-gui-linux-<arch>.tar.gz
#
#  必须在 Linux 上跑：PyInstaller 不是交叉编译器。
# ============================================================
set -euo pipefail
cd "$(dirname "$0")"

fail() {
    echo
    echo "[x] $*"
    exit 1
}

ARCH="$(uname -m)"
DIST="dist"
PY="${PYTHON:-python3}"

echo
echo [1/8] 检查环境 ...
case "$(uname -s)" in
    Linux) ;;
    *)
        fail "本脚本只能在 Linux 上运行（PyInstaller 不是交叉编译器）。
     Windows 请用 build_windows.bat。"
        ;;
esac
command -v "$PY" >/dev/null 2>&1 || fail "未找到 $PYTHON。请安装 Python 3.9 或更高版本。"
echo "    架构: $ARCH"
echo "    解释器: $(command -v "$PY")"

echo
echo [2/8] 安装依赖 ...
if [ "${SKIP_VENV:-0}" = "1" ]; then
    echo "    [i] SKIP_VENV=1：直接用系统 Python"
    # Debian/Ubuntu 的 Python 是 externally-managed，往系统解释器里装包
    # 会被 pip 拒绝。给一个口子：PIP_ARGS=--break-system-packages。
    # 不默认带上——那是真的会污染系统 Python，只在明确要求时才做。
else
    if [ ! -d .venv ]; then
        if ! "$PY" -m venv .venv; then
            fail "创建虚拟环境失败。Debian/Ubuntu 上常见原因是缺 python3-venv：
       sudo apt install python3-venv        # 或者 python3.12-venv
     也可以跳过虚拟环境：SKIP_VENV=1 ./build_linux.sh"
        fi
    fi
    # shellcheck disable=SC1091
    . .venv/bin/activate
    PY="python3"
fi
# shellcheck disable=SC2086
if ! "$PY" -m pip install ${PIP_ARGS:-} --upgrade pip -q; then
    # 系统 Python 上这步几乎必失败（Debian 打包的 pip 卸不掉），
    # 而升级 pip 从来不是构建的前提——真正需要的东西在下一步，
    # 那一步失败会真的停下来。为噪音中断构建不值得。
    echo "    [!] 升级 pip 失败，沿用现有版本继续"
fi
# shellcheck disable=SC2086
"$PY" -m pip install ${PIP_ARGS:-} -r requirements.txt pyinstaller -q \
    || fail "依赖安装失败，请检查网络"

# tkinter 在 Linux 上**不是 pip 能装的**——它是 CPython 的一个可选
# 组件，由发行版单独打包。缺了它 PyInstaller 不报错，只是安静地产出
# 一个不含 tkinter 的图形界面版：界面永远起不来，而这个包文件齐全、
# 体积正常、ffmpeg 也在，下面八步校验一条都发现不了。
# 所以要在这里自己验一次，并且说清各发行版该怎么装。
if [ "${NO_GUI:-0}" != "1" ]; then
    if ! "$PY" -c "import tkinter" 2>/dev/null; then
        fail "图形界面版需要 tkinter，但当前解释器里没有。
      Linux 上它由发行版单独提供（pip 装不了）：
        sudo apt install python3-tk          # Debian/Ubuntu
        sudo dnf install python3-tkinter     # Fedora
        sudo pacman -S tk                    # Arch
      只要命令行版的话：NO_GUI=1 ./build_linux.sh"
    fi
else
    echo "    [i] NO_GUI=1：不打图形界面版"
fi

echo
echo [3/8] 生成图标 ...
# 图标是 AppImage 的门面：.desktop 里 Icon= 指向它，缺了在应用菜单里
# 就是一个空白方块。仓库里已经落了一份 PNG（tools/make_icon.py 产出），
# 所以这里只在它缺失、或 Pillow 可用需要重画时才真的画。
ICON="assets/bilibili-submit.png"
if [ -f "$ICON" ]; then
    echo "    [i] 已存在 $ICON"
else
    "$PY" tools/make_icon.py || fail "图标生成失败。手动装 Pillow 后重试：pip install Pillow"
fi
[ -f "$ICON" ] || fail "缺少 $ICON——AppImage 的 .desktop 需要它"

echo
echo [4/8] 准备 ffmpeg ...
# 与 Windows 脚本同一条规矩：**必须成功**。失败只打一行警告继续的话，
# 会静默产出一个不带 ffmpeg 的图形界面版——用户装完发现 cover:auto
# 用不了，而我们从头到尾不知道。真要不带，显式设 ALLOW_NO_FFMPEG=1。
FFMPEG_OK=1
if [ "${ALLOW_NO_FFMPEG:-0}" = "1" ]; then
    FFMPEG_OK=0
    echo "    [i] ALLOW_NO_FFMPEG=1：跳过，产出的包不带 ffmpeg"
elif [ -n "${FFMPEG_EXE:-}" ]; then
    [ -f "$FFMPEG_EXE" ] || fail "FFMPEG_EXE 指向的文件不存在：$FFMPEG_EXE"
    echo "    使用 FFMPEG_EXE=$FFMPEG_EXE"
else
    if ! "$PY" tools/setup_ffmpeg.py --dest vendor --force; then
        echo
        echo "    ffmpeg 只影响 cover: auto 自动抽帧，其他功能不依赖它，"
        echo "    但默认产出的图形界面版**必须**带它。"
        echo
        fail "未能准备 ffmpeg。三选一：
       1. $PY -m pip install imageio-ffmpeg
       2. 自己装好 ffmpeg（apt install ffmpeg）后重跑本脚本
       3. 确实要出不带 ffmpeg 的包：ALLOW_NO_FFMPEG=1 ./build_linux.sh"
    fi
fi

# 统一的 ffmpeg 落点：vendor/ffmpeg。setup_ffmpeg.py 往这放，系统装的
# 那份也拷到这里，下游几步只认这一个路径。
FFMPEG_SRC=""
if [ "$FFMPEG_OK" = "1" ]; then
    if [ -n "${FFMPEG_EXE:-}" ]; then
        FFMPEG_SRC="$FFMPEG_EXE"
    elif [ -f vendor/ffmpeg ]; then
        FFMPEG_SRC="vendor/ffmpeg"
    else
        # setup_ffmpeg.py 在 Linux 上可能给出 imageio 里的那份二进制，
        # 不一定落在 vendor/ffmpeg；退回到 PATH 里的系统 ffmpeg。
        FFMPEG_SRC="$(command -v ffmpeg || true)"
        [ -n "$FFMPEG_SRC" ] || fail "ffmpeg 备好了却找不到文件：vendor/ffmpeg 不存在，PATH 里也没有"
    fi
    echo "    ffmpeg: $FFMPEG_SRC"
fi

# 每个 onedir 产物都要带一份 ffmpeg 在 exe 同目录——代码里本来就优先
# 找这个位置（ffmpeg.py 的 _candidates_in_app_dir）。用 install -m 755
# 而不是 cp：cp 会继承源文件的权限，而 squashfs 里一个 mode 644 的
# ffmpeg 只会报 Permission denied，看不出是权限问题。
stage_ffmpeg() {
    local dir="$1"
    [ "$FFMPEG_OK" = "1" ] || return 0
    install -m 755 "$FFMPEG_SRC" "$dir/ffmpeg" || fail "复制 ffmpeg 到 $dir 失败"
}

echo
echo [5/8] 打包命令行版 ...
rm -rf "$DIST/bilibili-submit-cli"
# APPIMAGE=1 让 spec 走 onedir。AppImage 本身就是「一个文件装着整个
# 目录」（squashfs 挂载，不是解压），所以目录形态在这里不付任何代价，
# 反而省掉 onefile 每次启动把整个归档解到 /tmp 的过程。
APPIMAGE=1 EXE_NAME=bilibili-submit-cli \
    "$PY" -m PyInstaller bili_submit.spec --noconfirm --clean \
    || fail "命令行版打包失败"
[ -d "$DIST/bilibili-submit-cli" ] || fail "未生成 $DIST/bilibili-submit-cli/"
stage_ffmpeg "$DIST/bilibili-submit-cli"

echo
echo [6/8] 打包图形界面版 ...
if [ "${NO_GUI:-0}" = "1" ]; then
    echo "    [i] NO_GUI=1：跳过"
else
rm -rf "$DIST/bilibili-submit-gui"
APPIMAGE=1 GUI=1 EXE_NAME=bilibili-submit-gui \
    "$PY" -m PyInstaller bili_submit.spec --noconfirm --clean \
    || fail "图形界面版打包失败"
[ -d "$DIST/bilibili-submit-gui" ] || fail "未生成 $DIST/bilibili-submit-gui/"
stage_ffmpeg "$DIST/bilibili-submit-gui"
# 图形界面版额外带的东西：配置示例与离线使用说明（tar.gz 目录版与
# AppImage 都一样，双击就能看）。
cp -a config "$DIST/bilibili-submit-gui/" 2>/dev/null || true
cp docs/user-guide.html "$DIST/bilibili-submit-gui/" 2>/dev/null || true
fi

echo
echo [7/8] 组装 AppDir 与 AppImage ...
if [ "${NO_APPIMAGE:-0}" = "1" ]; then
    echo "    [i] NO_APPIMAGE=1：跳过 AppImage，只出目录版"
else
    command -v mksquashfs >/dev/null 2>&1 || echo "    [!] 没装 squashfs-tools；appimagetool 自带会自己解决，先继续"
fi

# AppDir 骨架：appimagetool 要求根目录有且仅有一个 .desktop，图标也放
# 根（Icon= 写不带扩展名的名字），真正的程序放 usr/bin/ 下。
make_appdir() {
    local name="$1" terminal="$2" label="$3"
    local appdir="build/AppDir-$name"
    rm -rf "$appdir"
    mkdir -p "$appdir/usr/bin"
    cp -a "$DIST/$name/." "$appdir/usr/bin/"

    # AppRun 必须 exec（不是 fork）：不 exec 的话退出码和信号都传不回
    # 调用方，`./xxx.AppImage --version` 拿不到真实退出码。
    cat > "$appdir/AppRun" <<EOF
#!/bin/sh
# AppImage 入口。\$0 是挂载点里的 AppRun，真正要跑的在 usr/bin/ 下。
SELF="\$(readlink -f "\$0" 2>/dev/null || echo "\$0")"
HERE="\$(dirname "\$SELF")"
exec "\$HERE/usr/bin/$name" "\$@"
EOF
    chmod 755 "$appdir/AppRun"

    cat > "$appdir/$name.desktop" <<EOF
# 只写一个主类别：freedesktop 规范里 AudioVideo 与 Network 都是主类别，
# 同时写会让 appimagetool 警告「可能在菜单里出现两次」。
[Desktop Entry]
Type=Application
Name=$label
Comment=批量投稿视频到哔哩哔哩
Exec=$name
Icon=bilibili-submit
Categories=AudioVideo;
Terminal=$terminal
StartupWMClass=$name
EOF
    cp "$ICON" "$appdir/bilibili-submit.png"

    # squashfs 是只读的，权限必须在打包前就钉好。cp -a 理论上保住了，
    # 但这件事失败得太隐蔽（运行时一句 Permission denied），再钉一次。
    if [ "$FFMPEG_OK" = "1" ]; then
        chmod 755 "$appdir/usr/bin/ffmpeg"
        [ -x "$appdir/usr/bin/ffmpeg" ] || fail "$appdir/usr/bin/ffmpeg 没有可执行位"
    fi
    echo "$appdir"
}

if [ "${NO_APPIMAGE:-0}" != "1" ]; then
    APPIMAGETOOL="${APPIMAGETOOL:-}"
    if [ -z "$APPIMAGETOOL" ]; then
        APPIMAGETOOL="build/appimagetool-${ARCH}.AppImage"
        if [ ! -f "$APPIMAGETOOL" ]; then
            echo "    下载 appimagetool ..."
            curl -fsSL -o "$APPIMAGETOOL" \
                "https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-${ARCH}.AppImage" \
                || fail "下载 appimagetool 失败。可手动下载后传进来：
       APPIMAGETOOL=/path/to/appimagetool ./build_linux.sh
       或只出目录版：NO_APPIMAGE=1 ./build_linux.sh"
            chmod 755 "$APPIMAGETOOL"
        fi
    fi
    [ -f "$APPIMAGETOOL" ] || fail "appimagetool 不存在：$APPIMAGETOOL"
    chmod 755 "$APPIMAGETOOL" 2>/dev/null || true

    # AppImage 的 runtime（ELF 头那段）由 appimagetool **现用现下载**。
    # 它自己的下载器在受限网络下会报 "server returned status code 0"——
    # 一句完全看不出是网络问题的话，而且失败时 squashfs 已经打好了，
    # 前功尽弃。自己下好再传 ``--runtime-file``：少一次运行时联网，
    # 失败了也知道是卡在哪一步。
    RUNTIME_FILE="${RUNTIME_FILE:-}"
    if [ -z "$RUNTIME_FILE" ]; then
        RUNTIME_FILE="build/runtime-${ARCH}"
        if [ ! -f "$RUNTIME_FILE" ]; then
            echo "    下载 AppImage runtime ..."
            curl -fsSL -o "$RUNTIME_FILE" \
                "https://github.com/AppImage/type2-runtime/releases/download/continuous/runtime-${ARCH}" \
                || fail "下载 AppImage runtime 失败。可手动下载后传进来：
       RUNTIME_FILE=/path/to/runtime-${ARCH} ./build_linux.sh"
        fi
    fi
    [ -s "$RUNTIME_FILE" ] || fail "AppImage runtime 是空的：$RUNTIME_FILE"

    variants=("bilibili-submit-cli:true:哔哩哔哩自动投稿（命令行）")
    if [ "${NO_GUI:-0}" != "1" ]; then
        variants=("bilibili-submit-gui:false:哔哩哔哩自动投稿" "${variants[@]}")
    fi
    for spec in "${variants[@]}"; do
        name="${spec%%:*}"
        rest="${spec#*:}"
        terminal="${rest%%:*}"
        label="${rest#*:}"
        appdir="$(make_appdir "$name" "$terminal" "$label")"
        out="$DIST/${name}-${ARCH}.AppImage"
        echo "    打包 $out ..."
        # --appimage-extract-and-run：让 appimagetool 自己**不需要 FUSE**
        # 也能跑。Ubuntu 23.04+ 默认没有 libfuse2，而 AppImage 的运行
        # 依赖它——缺了的表现是 "AppImages require FUSE to run"，
        # 不看文档根本不知道该怎么修。
        "$APPIMAGETOOL" --appimage-extract-and-run \
            --runtime-file "$RUNTIME_FILE" "$appdir" "$out" \
            || fail "$name 的 AppImage 打包失败"
        chmod 755 "$out"
    done
fi

# 目录版：AppImage 的退路。有些环境（容器、没有 FUSE 的老内核、某些
# 发行版的安全策略）就是跑不了 AppImage，给它一个解压即用的版本。
names=(bilibili-submit-cli)
[ "${NO_GUI:-0}" = "1" ] || names=(bilibili-submit-gui bilibili-submit-cli)
for name in "${names[@]}"; do
    tarball="$DIST/${name}-linux-${ARCH}.tar.gz"
    echo "    打包 $tarball ..."
    tar -czf "$tarball" -C "$DIST" "$name" || fail "$tarball 打包失败"
done

echo
echo [8/8] 验证产物 ...
for name in "${names[@]}"; do
    dir="$DIST/$name"
    [ -f "$dir/$name" ] || fail "缺少 $dir/$name"
    [ -x "$dir/$name" ] || fail "$dir/$name 没有可执行位"
    [ -d "$dir/_internal" ] || fail "缺少 $dir/_internal（Python 运行时）——程序会启动即退出"
    if [ "$FFMPEG_OK" = "1" ]; then
        [ -f "$dir/ffmpeg" ] || fail "缺少 $dir/ffmpeg（ffmpeg 应与可执行文件同目录）"
        # 这一条是 Linux 独有的坑：Windows 上能否执行只看扩展名，
        # Linux 看属性位。打包链里任何一步丢了 +x，用户看到的是一句
        # Permission denied，而它跟 ffmpeg 坏掉长得一模一样。
        [ -x "$dir/ffmpeg" ] || fail "$dir/ffmpeg 没有可执行位——自动抽帧会报 Permission denied"
    fi
    # 图形界面版必须真的带上了 tkinter 运行时。缺它时 PyInstaller
    # **不报错**，产物文件齐全、体积正常、上面几条全过——但一启动
    # 就静默退出（windowed 程序看不到报错）。这是唯一能拦住的地方。
    if [ "$name" = "bilibili-submit-gui" ]; then
        tk_hits="$(find "$dir/_internal" -maxdepth 1 \
            \( -name 'tcl*' -o -name 'tk*' -o -name 'libtk*' -o -name 'libtcl*' \) \
            -print -quit)"
        [ -n "$tk_hits" ] || fail "$dir/_internal 里没有 tcl/tk 运行时——
     图形界面版缺 tkinter，启动会静默退出。
     多半是构建机没装 python3-tk（pip 装不了它），见第 2 步的检查。"
    fi
done

if [ "${NO_APPIMAGE:-0}" != "1" ]; then
    for name in "${names[@]}"; do
        image="$DIST/${name}-${ARCH}.AppImage"
        [ -f "$image" ] || fail "缺少 $image"
        # AppImage 本质是个 ELF（前面挂着 squashfs），魔数必须是 \x7fELF。
        # 下载到一半的 HTML 错误页、代理塞的占位文件都过不了这一关。
        magic="$(head -c 4 "$image" | od -An -tx1 | tr -d ' \n')"
        case "$magic" in
            7f454c46*) ;;
            *) fail "$image 的文件头不是 ELF（实际 $magic）——多半是下载不完整" ;;
        esac
        [ -x "$image" ] || fail "$image 没有可执行位"
    done
    echo "[OK] AppImage 校验通过"
fi

"$DIST/bilibili-submit-cli/bilibili-submit-cli" --version \
    || fail "命令行版启动失败"
echo "[OK] 命令行版校验通过"

echo
echo ============================================
echo  打包完成
echo
if [ "${NO_GUI:-0}" = "1" ]; then
    echo "  （NO_GUI=1：本次只出命令行版）"
else
    echo  图形界面版:  dist/bilibili-submit-gui-${ARCH}.AppImage
fi
echo  命令行版:    dist/bilibili-submit-cli-${ARCH}.AppImage
echo  目录版:      dist/bilibili-submit-{gui,cli}-linux-${ARCH}.tar.gz
echo
echo  使用:
if [ "${NO_GUI:-0}" != "1" ]; then
    echo    图形界面:  ./dist/bilibili-submit-gui-${ARCH}.AppImage
fi
echo    命令行:    ./dist/bilibili-submit-cli-${ARCH}.AppImage --help
echo
echo  AppImage 跑不起来时（常见于缺少 FUSE 的新发行版）：
echo    1. 解包运行:  ./xxx.AppImage --appimage-extract-and-run
echo    2. 装 FUSE:   sudo apt install libfuse2
echo    3. 用目录版:  tar xzf dist/xxx-linux-${ARCH}.tar.gz
echo ============================================
