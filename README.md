# 哔哩哔哩自动投稿程序

CLI 工具：扫码登录一次，之后按配置文件把本地视频自动投到 B 站，返回 BV 号。

自研实现，直接调用 B 站创作中心 Web 接口，不依赖 biliup 等第三方二进制，也不需要申请开放平台资质。

## 安装

```bash
pip install -r requirements.txt
```

依赖很轻：`requests`、`PyYAML`、`tqdm` 三个硬依赖；`qrcode` 缺失时自动降级为只显示登录链接。

需要 `ffmpeg` 仅在你用 `cover: auto` 自动生成封面时才有必要，可参考下方「内置 ffmpeg」。

## 打包成 Windows 单文件 exe

产物 `dist\bilibili-submit.exe`，双击即用，目标机器**不需要装 Python**。

> ⚠️ 打包必须在 Windows 上进行。PyInstaller 官方明确说明它不是交叉编译器
> （"it is not a cross-compiler"），Nuitka、PyOxidizer 同样不支持。
> 在 Linux 或 macOS 上跑 `pyinstaller` **产不出** exe。

**方式一：Windows 本地打包（推荐）**

```
1. 装 Python 3.9+（务必勾选 Add Python to PATH）
2. 双击 build_windows.bat
```

脚本会自建虚拟环境、装依赖、执行打包，并自动跑一次 `--version` 验证产物能否启动。

**方式二：GitHub Actions 云端打包（手边没有 Windows 时）**

仓库已带 `.github/workflows/build-windows.yml`，推到 GitHub 后在 Actions 页面
下载 `bilibili-submit-windows` 构建产物即可。workflow 里带了冒烟测试，
打包失败会直接标红而不是给你一个坏 exe。

### 打包相关的几个说明

- **体积约 10 MB**。spec 里已排除 tkinter/numpy/pandas/PIL 等用不到的大依赖。
- **ffmpeg 采用外置方式**（`dist/ffmpeg.exe`），不塞进 exe 归档。塞进单文件意味着
  每次启动都要解压到临时目录——启动明显变慢，且临时目录里的 exe 更容易被杀毒软件
  拦截。`build_windows.bat` 会自动把 ffmpeg 复制到 `dist/`。
  - 随包分发的是 `imageio-ffmpeg` 附带的裁剪版（实测 ~30 MB，已含 libx264），
    不是完整静态版（~76 MB）。真要完整版，自行下载后覆盖 `dist/ffmpeg.exe` 即可。
  - 不想要 ffmpeg？直接删掉 `dist/ffmpeg.exe` 即可，程序除 `cover: auto` 外照常工作。
  - 想完全自包含的单文件 exe：设环境变量 `BUNDLE_FFMPEG=1` 再打包。
  - ffmpeg 定位顺序：**程序同目录 → imageio-ffmpeg → 系统 PATH**，
    用 `check` 命令可以随时看当前命中的是哪一个。
- **控制台编码已处理**：Windows 控制台默认 GBK，遇到中文和二维码用的方块字符
  会崩。程序启动时会自动切到 UTF-8（`chcp 65001`），并探测终端是否支持方块字符，
  不支持时自动降级为只显示登录链接而不是抛异常。**建议用 Windows Terminal**，
  传统 cmd 里二维码可能显示成乱码，此时复制链接到手机打开同样可以登录。
- **杀软误报**：PyInstaller 的 bootloader 是恶意软件常用包装，启发式引擎容易
  误报。spec 里已关闭 UPX 压缩以降低误报率；自己用的话建议在 defender 里加白名单。
- 图标与版本信息通过 `assets/` 注入，仅在 Windows/macOS 生效。

## 内置 ffmpeg

封面自动抽帧（`cover: auto`）需要一个 ffmpeg。程序按下面的顺序找，找到即用：

| 优先级 | 来源 | 说明 |
|---|---|---|
| 1 | 程序同目录的 `ffmpeg.exe` | 打包后的默认方式，可整个删掉 |
| 2 | `imageio-ffmpeg` 包内二进制 | `pip install imageio-ffmpeg`（Windows wheel 约 30MB，自带 exe） |
| 3 | 系统 `PATH` | 自行安装的 ffmpeg |

查看当前状态：

```
python run.py check
# ffmpeg: 就绪（bundled，版本 7.0.2-static）
# ffmpeg: 未找到（仅影响 cover: auto 自动抽帧，其他功能不受影响）
```

手动放置 ffmpeg：

```
python tools/setup_ffmpeg.py --dest dist    # 复制到指定目录并规范命名
```

源码运行时若不装 `imageio-ffmpeg`，也可以从 <https://ffmpeg.org/download.html> 或
<https://www.gyan.dev/ffmpeg/builds/> 下载后放进项目根目录，程序同样能发现。

## 快速开始

```bash
# 1. 扫码登录（手机 B 站 App 扫码后必须点「确认登录」）
python run.py login

# 2. 投一个视频
python run.py upload ./demo.mp4 --title "我的第一个自动投稿" --tid 21 --tag "测试,自动化"

# 3. 批量投稿
cp config/config.example.yaml config/my.yaml   # 改里面的路径
python run.py submit -c config/my.yaml --dry-run   # 先预览
python run.py submit -c config/my.yaml             # 真跑
```

Cookie 保存在 `~/.config/bilibili_submit/cookie.json`（权限 0600），有效期通常数月，过期后重新 `login` 即可。

## 命令

| 命令 | 说明 |
|---|---|
| `login` | 扫码登录并保存 cookie |
| `upload <file>` | 单文件投稿，命令行参数覆盖配置 |
| `submit -c <config>` | 按配置文件批量/定时投稿，支持 `--dry-run` |
| `check [-c <config>]` | 校验配置、登录态、ffmpeg 与分区 ID |
| `tid` | 列出常用分区 ID |
| `history` | 查看本机投稿历史 |

`upload` 常用参数：`--title --tid --tag --desc --cover --copyright --source --dtime --dtime-offset`

## 配置

见 [`config/config.example.yaml`](config/config.example.yaml)。支持两种任务：

- **single**：指定单个文件
- **batch**：扫描目录，支持 `include`/`exclude` 通配符、`{stem}`/`{n}` 标题模板、按 `name` 或 `mtime` 排序

`defaults` 段的字段会被所有任务继承，任务内可单独覆盖。

定时任务：`dtime`（绝对 Unix 时间戳）或 `dtime_offset_hours`（相对小时数）二选一，**必须距今 4 小时以上**，否则直接报错。

## 实现要点

投稿链路是六步：线路探测 → 取上传凭证 → 分片上传 → 合并分片 → 封面上传 → 稿件投递。

几个容易踩的坑，代码里已经处理：

- **WBI 签名**：B 站 Web 接口的风控签名（缺失返回 `-352`）。密钥每天轮换，从 `nav` 接口获取后按固定置换表派生 mixin_key，缓存并在被拒时自动刷新。
- **`filename` 的来源**：投稿接口要的 `videos[].filename` 不是本地文件名，而是上传凭证里 `upos_uri` 的主体部分。写错会导致投稿失败。
- **`chunk_size` 由服务端给出**，不能硬编码。
- **末片长度是余数**，不是 chunk_size。
- **`preupload` 不走标准 `code`/`data` 包装**，顶层直接是 `{"OK":1,...}`，用通用解析器会误判。

风控处理：

| 错误码 | 含义 | 行为 |
|---|---|---|
| `601` | 投稿过快 | 阶梯退避重试（5/10/20/30/60 分钟 + 随机抖动），重试前刷新密钥 |
| `-352` | 签名失效 | 重新抓取密钥后重放一次 |
| `-101` | Cookie 过期 | 不重试，提示重新 `login` |
| `-412` | IP 风控 | 不重试，提示换网络 |
| `200009` | 当日投稿上限 | 不重试 |

批量任务之间可用 `per_task_interval_minutes` 设置间隔，规避 601。

## 一键发布与外链下载

拿到本项目的源码包（`bilibili-submit-source.zip`）后，解压会得到一个
`bilibili-submit/` 文件夹。里面已经带好了 git 历史，**不需要**重新 `git init`。

你只有两条路可选：

| 目标 | 怎么做 | 需要什么 |
|---|---|---|
| 自己能用 | 双击 `build_windows.bat`，几分钟后拿到 `dist\bilibili-submit.exe` | 一台 Windows |
| 想要一个能发别人的链接 | 双击 `publish.bat`，输入版本号 | 一台 Windows + GitHub 账号 |

### 路线 A：本地打包（自己用）

```
双击 build_windows.bat
```

脚本会自建虚拟环境、装依赖、执行打包、跑一次 `--version` 验证产物能启动，
并自动把 ffmpeg 复制到 `dist\`。就这一步，不需要 GitHub，不需要 Python 基础。

### 路线 B：GitHub Release 公开直链（发给别人）

推一个 tag 即可——GitHub Actions 会在 Windows 上自动打包并发布 Release，
产出**公开直链**（任何人点开即可下载，无需登录）：

```
https://github.com/<你的账号>/bilibili-submit/releases/download/v0.1.0/bilibili-submit-full-windows.zip
```

**首次准备**（只需一次）：

1. 安装 [Git for Windows](https://git-scm.com/download/win)
2. 安装 GitHub CLI：`winget install --id GitHub.cli`
3. 登录一次：`gh auth login`（浏览器授权）

**之后每次发版**：双击 `publish.bat`，输入版本号回车。

脚本会自动完成：建公开仓库 → 提交推送 → 打 tag → 触发云端构建 → 打印下载链接。
没装 GitHub CLI 或没登录时，它会逐项提示你，不会闷头失败。

会发布两个包（体积为 v0.1.0 实测值）：

| 文件 | 体积 | 说明 |
|---|---|---|
| `bilibili-submit-full-windows.zip` | ~40 MB | 自带 ffmpeg，**开箱即用，推荐** |
| `bilibili-submit-mini-windows.zip` | ~10 MB | 不含 ffmpeg，需自备（仅影响 `cover: auto`） |

> 完整版比预期小很多，因为随包分发的是 `imageio-ffmpeg` 附带的裁剪版（~30 MB），
> 不是完整静态版（~76 MB）。裁剪版已含 libx264 与常用封装，够转码和抽帧用了。

也可以完全手动：建空仓库 → `git remote add origin ...` → `git push --tags`。

> 用 Actions 的 artifact 也能下载，但需登录 GitHub 账号；
> **Release 资产才是真正公开的直链**，适合直接发给别人。

## 已验证 / 待验证

**已在本项目开发中实测通过**：WBI 签名算法（对照官方示例密钥，结果一致且被服务端接受）、二维码登录全流程、线路探测、投稿接口 `add/v3` 可达性、配置解析、分片边界算法、断点续传状态管理、**ffmpeg 定位与封面抽帧端到端**（真实生成测试视频并抽出 320×240 JPEG）。单元测试 48 项全绿（`python -m pytest tests/`）。

**打包链路已实测**：用 PyInstaller 实际构建出 11 MB 单文件产物，确认 spec 语法正确、
依赖分析无遗漏、**打包后 HTTPS 与 CA 证书链可用**（实测取到与线上一致的 WBI 密钥）、
图标与版本资源正确注入、**ffmpeg 三级降级链在打包环境下仍有效**（实测命中 system 源）。
同时确认了 Windows 控制台 GBK 编码下的崩溃风险并已修复。
受限于 PyInstaller 不支持交叉编译，Windows exe 本身的最终产物需在 Windows 上生成。

**需要你在自己机器上验证**：真实账号的上传与投递这一步无法在开发环境完成（没有你的 cookie）。建议第一次用一个几十 MB 的小视频试跑，确认端到端通了再批量用。

如果 `add/v3` 哪天被 B 站下线，配置里改 `submit.backend: web/v2`（旧接口）或 `app`（需要 `access_key`，纯扫码登录拿不到）。后端是抽象过的，切换不用改代码。

## 注意事项

- 新号、小号的投稿频控明显更严（601），批量投稿务必留足间隔。
- 非正式会员单日投稿有数量上限（`200009`），可在主站答题转为正式会员解除。
- Cookie 等价于账号凭据，别提交到仓库或外传。
- 本程序仅供管理自己的账号投稿使用。请勿用于批量注册、刷量或搬运他人作品——高频率自动化会触发风控，甚至导致封号。程序内置的保守退避策略正是为此。

## 项目结构

```
bilibili_submit/
├── wbi.py          WBI 签名
├── auth.py         扫码登录与 cookie 持久化
├── client.py       HTTP 会话、CSRF、响应归一化
├── console.py      Windows 控制台 UTF-8 适配
├── ffmpeg.py       ffmpeg 定位（外置 → imageio → PATH）
├── lines.py        线路探测与择优
├── upload.py       分片上传、断点续传
├── cover.py        封面上传与抽帧
├── metadata.py     标题/标签/分区校验与 payload 组装
├── submit.py       稿件投递（web / app 后端 + 601 退避）
├── config.py       配置加载与校验
├── scheduler.py    任务执行与历史记录
└── cli.py          命令行入口

main.py             PyInstaller 打包入口
run.py              源码运行入口
bili_submit.spec    打包配置
build_windows.bat   Windows 一键打包
assets/             图标与 Windows 版本资源
tools/make_icon.py     图标生成脚本（仅开发时用）
tools/setup_ffmpeg.py  复制 ffmpeg 到指定目录
```

依赖方向是单向的：`wbi`/`auth` → `client` → `upload`/`cover`/`submit` → `config`/`scheduler`/`cli`，任一层可独立替换。
