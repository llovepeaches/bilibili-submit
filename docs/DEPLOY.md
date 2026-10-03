# 源码部署指南

从零把 bilibili-submit 跑起来，覆盖 Windows / macOS / Linux，含代理、ffmpeg、
定时任务和常见故障排查。

>只想拿现成的 exe？去 [Releases](https://github.com/llovepeaches/bilibili-submit/releases)
> 下载，不需要本文档。

## 环境要求

| 项目 | 要求 | 说明 |
|---|---|---|
| Python | 3.9 或更高 | 3.9 实测可用；更高版本也支持 |
| 磁盘 | ~50 MB | 含依赖；`imageio-ffmpeg` 约 30 MB |
| 内存 | 正常即可 | 分片并发默认 3，视频越大占用越多 |
| 网络 | 能访问 B 站 | 境外需见下方「代理」一节 |

依赖只有三个硬依赖，很轻：

```
requests   HTTP 请求
PyYAML     配置解析
tqdm       上传进度条
```

另有两个软依赖，缺失会自动降级不影响核心功能：

```
qrcode           终端渲染扫码二维码。缺失时只显示链接，用手机浏览器打开
imageio-ffmpeg   自带 ffmpeg 二进制。缺失时 cover: auto 需另找 ffmpeg
```

## 安装

### Windows

```powershell
# 1. 装 Python（务必勾选 Add Python to PATH）
winget install Python.Python.3.11

# 2. 克隆或解压源码
cd bilibili-submit

# 3. 建虚拟环境（推荐，避免污染系统 Python）
python -m venv .venv
.venv\Scripts\activate

# 4. 装依赖
pip install -r requirements.txt

# 5. 验证
python run.py --version
python run.py check
```

### macOS / Linux

```bash
# 1. 装 Python（macOS 用 brew，Debian/Ubuntu 用 apt）
brew install python@3.12                # macOS
# sudo apt install python3 python3-venv python3-pip   # Debian/Ubuntu

# 2. 克隆或解压源码
cd bilibili-submit

# 3. 建虚拟环境
python3 -m venv .venv
source .venv/bin/activate

# 4. 装依赖
pip install -r requirements.txt

# 5. 验证
python3 run.py --version
python3 run.py check
```

> **国内网络装依赖慢**时用镜像：
> `pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple`

### 不建虚拟环境行不行

行。`pip install -r requirements.txt` 直接装到当前环境即可，只是依赖会和
其他项目混在一起。三个依赖都不重，冲突风险低。

## 首次使用

```bash
# 1. 扫码登录
python run.py login
```

用手机 B 站 App 扫描终端里的二维码，**扫完必须在手机上点「确认登录」**——
只是扫码不确认会一直卡在「已扫码，等待确认」。

登录成功后 cookie 存在：

| 平台 | 路径 |
|---|---|
| Windows | `C:\Users\<你的用户名>\.config\bilibili_submit\cookie.json` |
| macOS / Linux | `~/.config/bilibili_submit/cookie.json` |

注意程序在 Windows 上也用 `~/.config/` 这个 POSIX 风格路径（依赖 `~` 展开），
**不是** `%APPDATA%`。文件权限设为 `0600`，仅当前用户可读。有效期通常数月，
过期后重新 `login` 即可。

二维码显示不出来时有三条路：

```
1. 装 qrcode 库：pip install qrcode
2. 用 --no-qr 只打印链接，在手机浏览器里打开
3. Windows Terminal 替代传统 cmd（见下）
```

## 代理

服务器在境外、或直连被风控时，**登录和投稿都要走代理**。

**方式一：写进配置（推荐，一次配好处处生效）**

```yaml
# config/my.yaml
account:
  proxy: "http://127.0.0.1:7890"
```

```bash
python run.py login -c config/my.yaml   # 登录时读同一份配置
python run.py submit -c config/my.yaml  # 投稿时也读
```

**方式二：命令行临时指定**

```bash
python run.py login --proxy http://127.0.0.1:7890
```

优先级：`--proxy` > 配置文件的 `account.proxy` > 不走代理。

**上传线路**：境外除了代理还要改 `profile`，否则即使走代理也可能上传失败或极慢。

```yaml
upload:
  line: auto                        # auto 自动择优
  profile: "ugcupos/bupfetch"       # 大陆用 "ugcupos/bup"，港澳台与海外用这个
```

## ffmpeg

只有 `cover: auto`（自动抽首帧当封面）需要 ffmpeg，其他功能完全不受影响。

程序按这个顺序找，找到即用：

| 优先级 | 来源 | 怎么来的 |
|---|---|---|
| 1 | 程序同目录的 `ffmpeg.exe` | 打包时随包分发；自己放一份可覆盖内置版 |
| 2 | exe 内嵌（`sys._MEIPASS`） | 下载 `standalone` 版，或用 `BUNDLE_FFMPEG=1` 打包 |
| 3 | `imageio-ffmpeg` 包内 | `pip install imageio-ffmpeg`（自动） |
| 4 | 系统 `PATH` | 自行安装 |

查看当前命中哪一个：

```bash
python run.py check
# ffmpeg: 就绪（bundled，版本 6.0-7.0）      程序同目录
# ffmpeg: 就绪（embedded，版本 6.0-7.0）     exe 内嵌
# ffmpeg: 未找到（仅影响 cover: auto 自动抽帧，其他功能不受影响）
```

手动放置：

```bash
# 从 imageio-ffmpeg 复制到项目根目录
python tools/setup_ffmpeg.py --dest .

# 或自行下载后放项目根目录 / 放进 PATH
# https://www.gyan.dev/ffmpeg/builds/
```

要打包出**内置 ffmpeg 的单文件 exe**：

```bash
python tools/setup_ffmpeg.py --dest vendor   # 先备好源文件
BUNDLE_FFMPEG=1 EXE_NAME=bilibili-submit-standalone \
  python -m PyInstaller bili_submit.spec --noconfirm
```

⚠️ 内嵌会把 exe 从约 10 MB 撑到约 69 MB（解压后约 94 MB），且每次启动都要解压 ffmpeg 到
临时目录（启动变慢、更易被杀软误判）。在意这两点就别内嵌。

**不想用自动封面？** 把配置里 `cover: auto` 改成图片路径或 `null`，
就不需要 ffmpeg 了。

## 投稿

```bash
# 单个视频
python run.py upload demo.mp4 --title "标题" --tid 21 --tag "标签1,标签2"

# 先自检
python run.py check -c config/my.yaml

# 批量（dry-run 只预览不真投，强烈建议先跑一遍）
python run.py submit -c config/my.yaml --dry-run
python run.py submit -c config/my.yaml
```

分区 ID 用 `python run.py tid` 查，里面列了常用分区。

## 定时投稿

B 站要求定时稿件**距今至少 4 小时**，程序会强制校验，不足直接报错。

```yaml
defaults:
  dtime_offset_hours: 5      # 5 小时后发布（相对）

# 或者用绝对时间戳
tasks:
  - name: "明早更新"
    type: single
    file: /path/to/video.mp4
    title: "标题"
    dtime: 1791638400         # Unix 时间戳
```

`dtime` 和 `dtime_offset_hours` 二选一，同时写会报错。

## 批量投稿避坑

新号频控很严，**批量投稿必须调间隔**，否则大概率撞 `601` 报错然后进入退避重试：

```yaml
tasks:
  - name: "系列合集"
    type: batch
    dir: /path/to/series
    per_task_interval_minutes: 30    # 每条之间等 30 分钟
    stop_on_error: true              # 一条失败就停，避免连续触发风控
    max_items: 10                    # 单次最多投 10 条
```

退避策略是 5/10/20/30/60 分钟阶梯 + 随机抖动，最长等一小时。批量任务建议
放后台跑：

```bash
# Linux/macOS
nohup python run.py submit -c config/my.yaml > submit.log 2>&1 &

# Windows PowerShell
Start-Process python -ArgumentList "run.py submit -c config/my.yaml"
```

**非正式会员有单日投稿数量上限**（错误码 `200009`），主站答题转正式会员可解除。

## 自动化运行

想定期投稿，用系统定时器：

```cron
# crontab：每天 20:00 跑一次（Linux/macOS）
0 20 * * * cd /path/to/bilibili-submit && .venv/bin/python run.py submit -c config/my.yaml >> submit.log 2>&1
```

```xml
<!-- Windows 任务计划程序：触发器每天 20:00，操作填 .venv\Scripts\python.exe
     参数 run.py submit -c config\my.yaml，起始位置填项目根目录 -->
```

## 从源码打包成 exe

**只能在 Windows 上打包。** PyInstaller 官方明确说明它不是交叉编译器
（"it is not a cross-compiler"），Nuitka、PyOxidizer 同样不支持——
在 Linux 或 macOS 上跑 `pyinstaller` **产不出** exe。

```powershell
# 双击这个就行
build_windows.bat
```

脚本会自动建虚拟环境、装依赖、执行打包、跑 `--version` 验证产物能启动，
并把 ffmpeg 复制到 `dist\`。产物是 `dist\bilibili-submit.exe`，约 10 MB。

想发布给别人？推 tag 触发 GitHub Actions 出 Release 资产（公开直链）：

```bash
git tag v0.1.2 && git push origin v0.1.2   # 版本号换成你要发的
```

## 常见问题

### 登录时卡在「等待扫码」

- 扫码后**没在手机上点确认**——这是最常见原因
- 二维码过期了，重新跑一次 `login`
- 网络不通，见下方代理一节
- 终端显示不出二维码：换 Windows Terminal，或用 `--no-qr` 拿链接

### 报 `-101 未找到有效 cookie`

Cookie 过期或不存在。重新 `login`。

### 报 `-352 签名失效`

B 站的 WBI 签名密钥每天轮换。程序会自动重新抓取并重放一次，若持续报错
说明接口可能有变动，请提 issue。

### 报 `-412 请求被拦截`

IP 被风控。换网络或换代理。这个错误不重试——重试只会让风控更严。

### 报 `601 投稿过于频繁`

触发了频控。程序会按 5/10/20/30/60 分钟阶梯自动退避重试，你也可以主动
调大 `per_task_interval_minutes`。急用的话等一小时再投。

### 报 `200009 今日投稿次数已达上限`

非正式会员的每日上限。转正式会员解除。

### 上传很慢或中断

- 境外检查 `upload.profile` 是否设成 `ugcupos/bupfetch`
- 降低 `upload.concurrency`（默认 3，上限 4，调高反而更容易限流）
- 中断后重跑会**自动断点续传**（2 小时内有效），不用从头来
- 彻底不想续传：`--no-resume`

### Windows 控制台中文乱码或崩溃

程序启动时会自动切 UTF-8 并探测终端能力。仍有乱码就换 **Windows Terminal**
（自带，Microsoft Store 可装），传统 cmd 对 UTF-8 支持不完整。

### 杀毒软件报毒

PyInstaller 的 bootloader 是恶意软件常用包装，启发式引擎容易误报。
项目已关闭 UPX 压缩降低误报率，但彻底解决需要自己加白名单。

### 提示目录不存在

示例配置里两个演示任务的路径都是占位值（`/path/to/video.mp4`、
`/path/to/series`）。把 `tasks` 换成自己的路径，或删掉不用的那个。

## 开发

```bash
# 跑测试
python -m pytest

# 跑需要联网的测试（会真实请求 B 站，风控风险自负）
BILI_NETWORK_TESTS=1 python -m pytest -m network

# 代码检查（--max-complexity 20 放宽核心流程的复杂度告警，见 pylama.ini）
pip install pylama
pylama --max-complexity 20 bilibili_submit tests tools
```

模块职责和依赖方向见 README 的「项目结构」一节。
