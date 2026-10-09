# 哔哩哔哩自动投稿程序

把本地视频投到**你自己的** B 站账号上——不用打开网页，也不用手动填投稿表单。

扫码登录一次，之后就两件事：单个视频填个标题点「开始投稿」；
一批视频选中装着视频的文件夹，勾几条就能跑。**不用写配置文件。**
跑完给你一串 BV 号，失败了会告诉你**是哪一条、因为什么**。

```bash
bilibili-submit upload 视频.mp4 --title "标题" --tid 21 --tag "标签,日常"
```

[![Release](https://img.shields.io/github/v/release/llovepeaches/bilibili-submit?style=flat-square)](https://github.com/llovepeaches/bilibili-submit/releases)
[![Build](https://github.com/llovepeaches/bilibili-submit/actions/workflows/build-windows.yml/badge.svg)](https://github.com/llovepeaches/bilibili-submit/actions/workflows/build-windows.yml)
[![Platform](https://img.shields.io/badge/platform-Windows-blue?style=flat-square)](https://github.com/llovepeaches/bilibili-submit/releases)
[![Python](https://img.shields.io/badge/python-3.9%2B-blue?style=flat-square)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green?style=flat-square)](LICENSE)

<p align="center">
  <img src="docs/images/light-07-upload-ready.png" alt="投稿页" width="47%" />
  <img src="docs/images/dark-03-tasks.png" alt="批量任务页（深色）" width="47%" />
</p>

## 特性

- **批量投稿不用配置文件**——点「选择文件夹…」自动扫描目录里的视频，
  顶部填一次分区和标签，这一批共用。勾几条跑几条，执行前先检查文件在不在。
- **多分 P**——一套视频合成**一个稿件的 P1/P2/P3**（一个 BV 号），
  而不是每个视频各占一个号。三种分法，见[多分 P 投稿](#多分-p-投稿)。
- **实时看得见**——每行随执行变色（成功绿 / 失败红 / 进行中粉 / 缺失橙），
  失败的可以**一键只重试失败项**。
- **失败有具体原因**——不笼统报「投稿失败」，而是哪一条、什么错。
  撞上频控会阶梯退避重试（5/10/20/30/60 分钟），不是立刻放弃。
- **断点续传**——大文件传一半断了，下次从断点接着传。
- **图形界面与命令行共用同一套逻辑**，可以混着用：界面里填的表单和
  `upload` 的参数一一对应，cookie 也是同一份。
- **定时发布、封面自动抽帧**（ffmpeg 抽首帧）、**转载 / 自制**都能设。

## 下载

目标机器**不需要装 Python**。

| 资产 | 体积 | 说明 |
|---|---|---|
| [`bilibili-submit-setup.exe`](https://github.com/llovepeaches/bilibili-submit/releases/download/v0.2.7-rc.6/bilibili-submit-setup.exe) | ~33 MB | **图形界面 · 安装版（推荐）**。开始菜单有入口，能干净卸载 |
| [`bilibili-submit-gui-portable.exe`](https://github.com/llovepeaches/bilibili-submit/releases/download/v0.2.7-rc.6/bilibili-submit-gui-portable.exe) | ~72 MB | 图形界面 · 便携版，免安装，拷走即用 |
| [`bilibili-submit-standalone.exe`](https://github.com/llovepeaches/bilibili-submit/releases/download/v0.2.7-rc.6/bilibili-submit-standalone.exe) | ~70 MB | 命令行 · 单文件，ffmpeg 已内置 |
| [`bilibili-submit.exe`](https://github.com/llovepeaches/bilibili-submit/releases/download/v0.2.7-rc.6/bilibili-submit.exe) | ~10 MB | 命令行 · 轻量 exe |
| [`bilibili-submit-full-windows.zip`](https://github.com/llovepeaches/bilibili-submit/releases/download/v0.2.7-rc.6/bilibili-submit-full-windows.zip) | ~41 MB | 命令行 · 轻量 exe + 外置 ffmpeg，启动最快 |
| [`bilibili-submit-mini-windows.zip`](https://github.com/llovepeaches/bilibili-submit/releases/download/v0.2.7-rc.6/bilibili-submit-mini-windows.zip) | ~10 MB | 命令行 · 轻量 exe + 配置与文档 |

**怎么选**：用图形界面 → 自己的电脑长期用装 `setup`，临时用 / U 盘 /
别人的机器用 `portable`。用命令行 → 图省事就 `standalone`，在意体积和
启动速度就 `full`。

两种界面**功能完全一致**，区别只在打包方式。安装版快是因为它把程序打成
**目录**（ffmpeg 放在程序旁边），而单文件版每次启动都要把 60 MB ffmpeg
解压到临时目录。

> ffmpeg 只影响 `cover: auto` 自动抽帧，**不影响投稿本身**。
> 不需要自动封面的话，命令行轻量版（10 MB）完全够用。

### 用户数据存在哪

登录状态、界面偏好、投稿历史都在**用户目录下**，不在程序目录：

```
%USERPROFILE%\.config\bilibili_submit\
├── cookie.json       登录状态
├── ui-state.json     界面偏好（上次用的目录等）
└── history.json      投稿历史
```

所以**卸载不会删掉这些**——重装后不用重新扫码。想彻底清干净，连这个
`.config` 目录一起删。

## 快速开始

### 图形界面版

双击 `bilibili-submit-gui.exe`，五个页签走完流程：

| 页签 | 做什么 |
|---|---|
| **登录** | 点「获取二维码」→ 手机 B 站 App 扫 → **在手机上点「确认登录」** |
| **投稿** | 选文件 → 填标题/分区/标签/简介 → 「开始投稿」。有「预览」可以先空跑一遍 |
| **批量任务** | 选文件夹自动扫描，顶部填一次共用参数，勾选后开跑 |
| **历史** | 看本机投过的稿（BV 号、时间） |
| **设置** | 配代理、换 cookie 路径，做一次环境自检 |

窗口底部常驻状态栏，随时能看到**是否已登录**和 **ffmpeg 是否就绪**。
所有耗时操作都在后台线程跑，窗口不会卡住；登录和投稿都能随时取消。

### 命令行版

```powershell
# 1. 扫码登录（扫完必须在手机上点「确认登录」）
bilibili-submit.exe login

# 2. 投一个视频
bilibili-submit.exe upload demo.mp4 --title "我的第一个视频" --tid 21 --tag "测试,日常"

# 3. 批量投稿：先复制配置再改
copy config\config.example.yaml config\my.yaml
bilibili-submit.exe submit -c config\my.yaml --dry-run   # 先预览，确认无误再去掉
bilibili-submit.exe submit -c config\my.yaml
```

源码运行把 `bilibili-submit.exe` 换成 `python run.py` 即可。

> **建议第一次拿一个几十 MB 的小视频试跑**，确认「登录 → 上传 → 拿到 BV 号」
> 整条通了再批量用。

<details>
<summary><b>更多界面截图</b>（点击展开）</summary>

条件没齐时——按钮就地灰掉，旁边写清楚差什么，不用点了才知道：

![投稿页 · 条件没齐](docs/images/light-06-upload-blocked.png)

运行中主按钮就地变成「取消」，而不是一个点不动的「开始投稿」：

![投稿页 · 运行中](docs/images/light-08-upload-busy.png)

批量任务（勾选 + 文件预检 + 状态着色）：

![批量任务页](docs/images/light-03-tasks.png)

登录、设置、历史：

![登录页](docs/images/light-01-login.png)

![设置页](docs/images/light-05-settings.png)

![历史页](docs/images/light-04-history.png)

深色主题**不是浅色的反转**——卡片比底色亮，操作条比卡片更亮，
用「更亮的表面」表达深度，从不用纯黑：

![深色 · 投稿页](docs/images/dark-07-upload-ready.png)

窗口压到最小（1000x660）时内容可滚动，**底部操作条始终可见**：

![最小尺寸](docs/images/light-09-min-size.png)

</details>

## 多分 P 投稿

一套视频可以合成**一个稿件的 P1/P2/P3**（一个 BV 号，播放器里连续切换）。
三种分法，在批量任务页的「分P合并」里选：

**按文件名前缀分组**（看文件名猜）

```
旅行_01.mp4  ┐
旅行_02.mp4  ├→ 一个稿件「旅行」，3 个分P
旅行_03.mp4  ┘
教程.mp4     ──→ 单独一个稿件（没有同伴，不合并）
```

只认**文件名尾部**的序号：`旅行_01`/`旅行-2`/`第3集`/`P4` 都能认出来。
文件名里没有别的线索时（比如 `01.mp4`）要求序号**从 1 开始且连续**才敢
合并——否则 `2023.mp4` 和 `2024.mp4` 会被误当成一套。

**按文件夹分组**（看目录结构，更省心）

```
视频/
├─ 旅行/
│  ├─ a.mp4  ┐
│  ├─ b.mp4  ├→ 一个稿件「旅行」，3 个分P
│  └─ c.mp4  ┘
├─ 教程/
│  └─ x.mp4  ──→ 一个稿件「教程」（只有一个视频，就是单 P）
└─ solo.mp4  ──→ 单独一个稿件（根目录散落的文件不合并）
```

文件夹名就是稿件标题——那通常是你自己起的名字，比 `output.mp4` 像样。
只往下钻**一层**子目录，不会因为你指到大目录就一次冒出几百个稿件。

**整个目录合并**（最简单，直接按当前文件夹合并）

```
视频/
├─ a.mp4  ┐
├─ b.mp4  ├→ 一个稿件「视频」，3 个分P
└─ c.mp4  ┘
```

### 改标题

标题默认就是文件名 / 文件夹名，**双击列表行可以改**：弹出的对话框上面是
稿件标题，下面是各分 P 标题（多 P 时才有）。

列表里「任务」和「标题」是分开两列：任务名是标识，标题是投出去的名字。
两者默认一致，**改过之后就不一样了**，「我改过哪些」一眼可见。

要给一批任务统一起名，在「更多设置」里填**投稿标题模板**再点「套用到选中行」：

```text
{name} 第{n}集   →   旅行 第1集、旅行 第2集…
合集 - {name}    →   合集 - a、合集 - b…
```

`{name}` 是文件名或文件夹名，`{n}` 是勾选顺序。只套用到**勾选的行**，
没勾的一条都不动——列表里可能有一半已经手动改过标题。

猜错了也没关系，把开关切回「不合并」就恢复成原来一个视频一个稿件。

### 命令行与配置文件

命令行**给多个文件**或**给一个目录**都是多 P：

```bash
bili-submit upload 旅行_01.mp4 旅行_02.mp4 --title "旅行" \
  --part-title "出发" --part-title "路上" --tid 21

# 整个目录就是一套视频，不用把十几个文件名敲一遍
bili-submit upload D:/视频/旅行日记 --tid 21
```

配置文件里用 `type: multip` + `files:`（数组顺序即 P1/P2/P3），
`part_titles` 可给每个分 P 单独命名；也可以直接给 `dir:`。
batch 任务加 `group_by: folder` 则是**每个子文件夹出一个稿件**。
完整示例见 [`config/config.example.yaml`](config/config.example.yaml)。

## 投稿类型：自制 / 转载

三个入口都能设（客户端 / 命令行 / 配置文件）。客户端在「投稿设置」区选：

| 类型 | `copyright` | 还要填 |
|---|---|---|
| 自制 | `1`（默认） | — |
| 转载 | `2` | **`source`**：原视频链接或出处 |

**转载不填来源会被拒稿**（服务端返回 21004），所以客户端在点「开始投稿」
时就拦住，不会让你等一批任务逐个失败完才发现是同一个原因。

```bash
bili-submit upload a.mp4 --copyright 2 --source "https://www.bilibili.com/video/BV1xx"
```

> 转载来源记的是**出处**，不是你的稿件地址。填错不会报错，但审核会问。

## 投稿选项

客户端里收在「更多设置」折叠区，默认收起——收起时标题栏会列出已开启的项。

| 选项 | 配置项 | 命令行 | 说明 |
|---|---|---|---|
| 关闭弹幕 | `up_close_danmu` | `--close-danmu` | 关闭后不再有新的弹幕 |
| 关闭评论区 | `up_close_reply` | `--close-reply` | 关闭后不再有新的评论 |
| 精选评论 | `up_selection_reply` | `--selection-reply` | 只有精选的评论公开显示 |
| 杜比音效 | `dolby` | `--dolby` | **源文件本身得是杜比音轨**，否则开了也没效果 |
| Hi-Res 无损 | `hires` | `--hires` | 同上，需要无损音轨 |

命令行上这些开关**只能开**：不传表示「听配置文件的」，不会因为没传参就把
配置里开着的项悄悄关掉。想关请改配置文件。

> **水印不在这个列表里。** B 站的水印是**账号级**设置（创作设置 →
> 原创视频添加水印），投稿接口不带这个参数。要开得去网页端自己开。

## 命令

| 命令 | 说明 |
|---|---|
| `login` | 扫码登录并保存 cookie。`--no-qr` 只显示链接、`--timeout N` 等待秒数 |
| `upload <文件或目录>…` | 投稿。**给多个文件或一个目录就合并成多分P**（`--part-title` 逐个命名） |
| `submit -c <配置>` | 按配置文件批量 / 定时投稿，`--dry-run` 只预览不真跑 |
| `check [-c <配置>]` | 自检：配置、登录态、ffmpeg、分区 ID |
| `tid` | 列出常用分区 ID |
| `gui` | 启动图形界面 |
| `history` | 查看本机投稿历史 |

`upload` 常用参数：

```
--title --tid --tag --desc --cover --copyright --source
--dtime --dtime-offset --part-title --no-resume -c
--dolby --hires --close-danmu --close-reply --selection-reply
```

投稿前建议先跑 `check`，它会一次性告诉你配置有没有问题、登录态是否有效、
ffmpeg 是否就绪。

## 配置

完整示例见 [`config/config.example.yaml`](config/config.example.yaml)，
几个容易踩的点：

- **定时发布**：`dtime`（绝对时间戳）或 `dtime_offset_hours`（相对小时数）
  二选一，**必须距今 4 小时以上**，否则直接报错。
- **转载**：`copyright: 2` 时 `source` 必填。
- **封面**：`cover: auto` 用 ffmpeg 抽首帧；给图片路径则直接上传；`null` 不设封面。
- **批量任务**：`include`/`exclude` 通配符过滤，`title_template` 支持
  `{stem}`/`{name}`/`{n}`。`per_task_interval_minutes` 控制间隔，
  **批量投稿必调**，否则大概率撞 601 频控。
- **多分P**：`type: multip` + `files:`；`type: batch` + `group_by: folder`
  则每个子文件夹出一个稿件。

### 海外投稿 / 代理

服务器在境外时，除了走代理还要改上传线路，否则即使有代理也可能失败或极慢：

```yaml
account:
  proxy: "http://127.0.0.1:7890"   # 登录和投稿都用这个

upload:
  line: auto                            # auto 自动择优，也可指定 bda2 / tx / estx
  profile: "ugcupos/bupfetch"           # 大陆用 ugcupos/bup，港澳台与海外用这个
```

**`proxy` 属于 `account` 段，不是 `upload` 段。** 登录时也要走代理，所以
登录同样用 `-c` 指定这份文件：`bilibili-submit.exe login -c config\my.yaml`。
优先级：`--proxy` > 配置的 `account.proxy` > 不走代理。

## 注意事项

- 新号、小号投稿频控明显更严，批量投稿务必设置 `per_task_interval_minutes`。
- 非正式会员单日投稿有数量上限（`200009`），可在主站答题转正式会员解除。
- **Cookie 等价于账号凭据**，别提交到仓库或外传。
- 仅供管理自己的账号投稿使用。请勿用于批量注册、刷量或搬运他人作品——
  高频自动化会触发风控甚至封号，程序内置的保守退避策略正是为此。

## 已知边界

**投稿投递这一步需要你自己的账号才能验证**——开发时没有真实 cookie，
所以「上传成功 → 拿到 BV 号」这一段没法自动跑通。其余环节都实测过：
WBI 签名算法、二维码登录全流程、线路探测、`add/v3` 接口可达性、
分片边界算法、断点续传、ffmpeg 定位与封面抽帧，以及打包链路本身。

## 开发

```bash
pip install -r requirements.txt
xvfb-run -a python -m pytest      # 612 项
```

> **必须挂虚拟屏幕**。没有 X server 时 tkinter 建不出窗口，138 条界面测试
> 会被**静默跳过**——测试照样报绿，但你改的界面代码一行都没被验到。
> CI 里设 `BILLI_REQUIRE_DISPLAY=1` 可让缺屏直接判失败。

| 文档 | 内容 |
|---|---|
| [更新日志](CHANGELOG.md) | 每个版本的增删改，升级前先看一眼 |
| [架构说明](docs/ARCHITECTURE.md) | 分层与依赖方向、关键设计决策、如何加新命令 |
| [自行打包](docs/BUILD.md) | 本地 / Actions 打包、发版要同步的版本号 |
| [部署指南](docs/DEPLOY.md) | 源码部署：三平台安装、代理、ffmpeg、定时任务 |
| [常见问题](docs/FAQ.md) | 按症状排查：错误码、二维码、上传慢、杀软误报 |
| [使用说明](docs/user-guide.html) | 面向装好的用户：五页签怎么用、数据在哪、报错含义。随安装包附带，开始菜单有入口 |

安装器脚本是 [`installer.iss`](installer.iss)（Inno Setup 6），
`tools/check_installer.py` 能在提交前静态检查它。

代码分层是单向的：`wbi`/`auth` → `client` → `upload`/`cover`/`submit` →
`config`/`scheduler`/`cli`，任一层可独立替换。`ui/` 只依赖业务层、
业务层不反向依赖它，所以想换掉界面只需重写 `ui/` 目录。
界面层的依赖方向由 `tests/test_layering.py` 守着。

## 许可证

[MIT](LICENSE)。本项目与哔哩哔哩官方无关，仅供管理自己的账号投稿使用。
