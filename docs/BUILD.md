# 自行打包

面向**要自己出 exe 的人**。只想用程序的话看 [README](../README.md) 的
「下载」一节即可，不用打包。

> **打包必须在目标平台上做。** PyInstaller 官方明确说明它不是交叉编译器
> （"it is not a cross-compiler"），Nuitka、PyOxidizer 同样不支持——
> 在 Linux 上跑 `pyinstaller` **产不出** exe，在 Windows 上也产不出
> AppImage。所以：出 exe 用 Windows，出 AppImage 用 Linux，
> 两者都在仓库里有一键脚本，也都能交给云端 Actions。

## 方式一：Windows 本地打包

```
1. 装 Python 3.9+（务必勾选 Add Python to PATH）
2. 双击 build_windows.bat
```

脚本会自建虚拟环境、装依赖、准备 ffmpeg、打包命令行版与图形界面版，
并对产物做结构校验（`_internal\` 缺了会直接报错——那会导致双击闪退）。
装了 [Inno Setup 6](https://jrsoftware.org/isinfo.php) 的话还会额外编译出
`dist\bilibili-submit-setup.exe`；没装就跳过，图形界面版目录照样可用。

## 方式二：Linux 本地打包

```
./build_linux.sh
```

产物在 `dist/` 下：图形界面与命令行各一个 AppImage，外加两个目录版
`tar.gz`（AppImage 跑不了时的退路，见 README 的「Linux」一节）。

脚本八步都会校验，卡住就停：缺 ffmpeg、ffmpeg 丢了可执行位、GUI 产物
里没有 tcl/tk 运行时、AppImage 文件头不是 ELF，都会当场失败而不是
给你一个坏包。

三个前提值得一提，因为它们**不会自己报错**：

| 前提 | 缺了会怎样 |
|---|---|
| `python3-tk`（`sudo apt install python3-tk`） | PyInstaller 一声不吭，产出「一启动就静默退出」的 GUI |
| `libfuse2` | 打完包在本机跑不起来（产物本身是好的） |
| 网络（下载 appimagetool） | 第 7 步失败；可用 `APPIMAGETOOL=...` 传入，或 `NO_APPIMAGE=1` 只出目录版 |

常用开关：

```bash
ALLOW_NO_FFMPEG=1 ./build_linux.sh   # 明确产不带 ffmpeg 的包
NO_GUI=1 ./build_linux.sh            # 只打命令行版（构建机没 tkinter 时）
NO_APPIMAGE=1 ./build_linux.sh       # 只出目录版 tar.gz
SKIP_VENV=1 ./build_linux.sh         # 用系统 Python，不建 .venv
```

## 方式三：GitHub Actions 云端打包

推 tag 即可，Actions 会在 Windows runner 上打包并发 Release。
仓库自带三个 workflow：

| workflow | 触发 | 干什么 |
|---|---|---|
| `build-windows.yml` | 推 main / PR | 命令行版快速构建 |
| `build-installer.yml` | 改了安装相关文件 | **验证安装器真能装上**（静默安装 → 启动 → 卸载全跑一遍） |
| `build-linux.yml` | 改了打包相关文件 | 在 Ubuntu 上打 AppImage，并冒烟：命令行 `--version`、图形界面真的出窗口 |
| `test.yml` | 每次 push | 跑全部测试（含界面用例，Xvfb） |
| `release.yml` | 推 tag | 出正式 Release，含安装器与全部资产 |

三者都带冒烟测试，**打包失败会直接标红，而不是给你一个坏 exe**。
安装器那一关尤其重要：只编译不安装的话，「装完双击闪退」这种问题
只能等用户遇到才知道。

```bash
git tag v0.2.7 && git push origin v0.2.7   # 版本号换成你要发的
```

> 带 `rc` / `alpha` / `beta` / `pre` 后缀的 tag 会自动建成 **Pre-release**，
> 不会被挂上「Latest」。正式版请用纯三段版本号（如 `v0.2.7`）。

发布新版本也可以双击 `publish.bat`，它会把下面几步一起做完。

## 发版要同步的版本号

版本号散落在六处，**改漏任何一处都会让程序对外报一个错的版本**：

| 位置 | 用途 | 有测试兜底 |
|---|---|---|
| `bilibili_submit/__init__.py` | `--version`、更新检查 | ✅ |
| `bili_submit.spec` | 打包产物名 | ✅ |
| `assets/version_info.txt` | exe 资源段（右键「属性」里那个） | ✅ |
| `installer.iss` | 安装器显示的版本 | ✅ |
| `CHANGELOG.md` | 版本段标题 | ❌ |
| `README.md` 下载直链 | 指向对应 Release 的资产 | ❌ |

前四处由 `tests/test_spec_bundle.py` 守着，改漏了会直接变红。
后面两处没有测试，发完记得点一下下载链接确认能下——**链接指向的是
旧版本的话，用户点「下载最新版」拿到的是上一版的 exe**。

## 两种打包形态

图形界面版有两种形态，由 `INSTALLER` 环境变量切换：

| | 便携版 | 安装版 |
|---|---|---|
| 环境变量 | 不设（`BUNDLE_FFMPEG=1 GUI=1`） | `INSTALLER=1 GUI=1` |
| PyInstaller 形态 | onefile 单文件 | **onedir 目录** |
| 产物 | `dist\bilibili-submit-gui-portable.exe` | `dist\bilibili-submit-gui\` + `dist\bilibili-submit-setup.exe` |
| ffmpeg | 打进 exe 归档 | **放在 exe 同目录**（构建脚本复制） |

为什么安装版坚持 onedir、以及用户数据为什么必须放在用户目录，
见 [架构说明](ARCHITECTURE.md) 的「打包形态：onefile 与 onedir」——
那里讲的是设计理由，这里只讲怎么出包。

`INSTALLER=1` 与 `BUNDLE_FFMPEG=1` 同时给会**直接报错**——那等于
「既要目录版又要每次解压」，是个自相矛盾的组合，不如当场说清。

安装器脚本是 [`installer.iss`](../installer.iss)（Inno Setup 6），
`tools/check_installer.py` 能在提交前静态检查它（段名拼错、
`#define` 未定义、缺 `recursesubdirs` 之类）。

## 打包相关的说明

- **轻量 exe 约 10 MB**，spec 已排除 tkinter/numpy/pandas/PIL 等用不到的大依赖。
- **ffmpeg 两种带法**，靠环境变量切换，同一份 spec 出两个 exe：

  | 打包命令 | 产物 | 大小 | ffmpeg |
  |---|---|---|---|
  | 默认 | `bilibili-submit.exe` | ~10 MB | 外置，`dist/ffmpeg.exe` |
  | `BUNDLE_FFMPEG=1 EXE_NAME=<名字>` | 自定名 exe | ~69 MB | 内嵌进归档 |

  内嵌的 ffmpeg 会出现在 `sys._MEIPASS` 下；每次启动都要解压到临时目录——
  启动变慢，且临时目录里的 exe 更容易被杀软拦截。要追求启动速度就用外置。
- 随包分发的是 `imageio-ffmpeg` 的静态版（~85 MB，已含 libx264），够抽帧和转码用。
  真要完整版，自行下载后覆盖 `dist/ffmpeg.exe`（外置）或 `vendor/ffmpeg.exe`（内嵌源）。
- 不想带 ffmpeg？删掉 `dist/ffmpeg.exe` 即可，除 `cover: auto` 外功能不受影响。
- **ffmpeg 定位顺序**：程序同目录 → exe 内嵌 → `imageio-ffmpeg` → 系统 `PATH`。
  放在程序旁边的优先，方便自行换版本。用 `check` 命令可随时查看当前命中哪一个。
- **控制台编码**：Windows 控制台默认 GBK，遇到中文和二维码用的方块字符会崩。
  程序启动时自动切 UTF-8，并探测终端是否支持方块字符，不支持时降级为只显示
  登录链接而不是抛异常。**建议用 Windows Terminal**；传统 cmd 里二维码显示成
  乱码时，复制链接到手机打开同样能登录。
- **杀软误报**：PyInstaller 的 bootloader 是恶意软件常用包装，启发式引擎容易误报。
  spec 已关闭 UPX 压缩降低误报率，自己用的话建议在 Windows Defender 里加白名单。
