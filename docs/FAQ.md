# 常见问题排查

按「症状 → 原因 → 怎么办」组织。部署流程见 [DEPLOY.md](DEPLOY.md)。

## 登录相关

### 卡在「已扫码，等待确认」

**这是最常见的问题。** 扫码之后必须在**手机 B 站 App 里点「确认登录」**，
只是在手机上显示出二维码、不点确认，程序会一直等下去（默认 180 秒超时）。

其他可能：

- 二维码已过期 → 重新跑 `login`
- App 版本太旧 → 升级到最新版
- 网络不稳 → 见下方「网络相关」

### 图形界面点「获取二维码」没反应 / 报412

**v0.1.5 及更早版本有这个问题，已在 v0.1.6 修复，请先升级。**

旧版 GUI 登录页自己造会话，没带浏览器 UA，B 站会把它识别成脚本并返回
HTTP 412 的 HTML 风控页——表现是拿不到二维码，或日志里一句
`Expecting value: line 1 column 1`（那是风控页不是 JSON 的锅）。
命令行版当时是正常的，所以容易误判成「我网络有问题」。

如果升级后仍然失败，按这个顺序排查：

1. **看状态标签的文案**。二维码正下方那行字会直接写明失败原因，
   卡片底部的日志区还有完整错误和排查建议。
2. **412 = 被风控**。程序已自动带浏览器 UA仍被拦，通常是 IP 信誉问题。
   在「设置」页填代理（境外网络或直连被风控时），或换网络重试。
3. **超时/连接失败**。检查代理是否有效，或换网络。
4. **实在拿不到二维码**：点二维码下方的链接可复制到手机浏览器打开，
   等价于扫码。

### 提示「当前终端无法渲染二维码字符」

程序检测到当前终端不支持渲染方块字符，已自动降级为只打印链接。

按以下顺序试：

```
1. pip install qrcode          # 补上渲染依赖
2. python run.py login --no-qr  # 只拿链接，用手机浏览器打开
3. 换 Windows Terminal          # 传统 cmd 对 UTF-8 支持不完整
```

### 报「未找到有效 cookie」

Cookie 不存在或已过期。重新 `login`。

正常情况下 cookie 有效期数月。如果反复过期，检查系统时间是否准确
（时间偏差过大会导致签名校验失败）。

### 登录报网络错误

境外网络需要代理。**注意：代理对登录同样生效**，写进配置即可：

```yaml
account:
  proxy: "http://127.0.0.1:7890"
```

```bash
python run.py login -c config/my.yaml
# 或临时指定
python run.py login --proxy http://127.0.0.1:7890
```

## 投稿相关

### 报 `601 投稿过于频繁`

触发了 B 站频控。**程序会自动退避重试**，按 5/10/20/30/60 分钟阶梯等待
（最长一小时），期间加随机抖动，无需干预。

想避免触发：

```yaml
tasks:
  - name: "系列"
    type: batch
    dir: /path/to/videos
    per_task_interval_minutes: 30   # 每条之间等 30 分钟
    stop_on_error: true             # 一条失败就停，别连续撞风控
```

新号、小号频控明显更严，建议间隔更长。

### 报 `200009 今日投稿次数已达上限`

非正式会员的单日投稿上限。**这是账号等级限制，程序无法绕过。**
在 B 站主站答题转正式会员即可解除。

### 报 `-352 风险控制触发`

WBI 签名失效。密钥每天轮换，程序会自动重新抓取并重放一次。

持续报这个错说明接口可能有变动，请提 issue 附上错误码。

### 报 `-412 请求被拦截`

IP 被风控。**换网络或换代理**，不要重试——反复重试只会让风控更严。

### 报「标题不能超过 80 字」或标签相关校验失败

程序在上传前会先本地校验元数据，错误信息里会指出具体哪个字段超限：

- 标题：最多 80 字，超出自动截断
- 标签：最多 10 个，合计不超过 200 字符
- `copyright: 2`（转载）时 `source` 必填

### 报「定时时间必须距今 4 小时以上」

B 站不允许临近发布。`dtime_offset_hours` 调大到 4 以上，
或换用绝对时间戳 `dtime`。

## 上传相关

### 上传很慢 / 中途失败

按影响面从大到小排查：

1. **线路不对**（境外最常见）。配置里要改 `profile`：
   ```yaml
   upload:
     profile: "ugcupos/bupfetch"   # 大陆用 "ugcupos/bup"
   ```
2. **并发太高**。`concurrency` 默认 3、上限 4，调高不会更快，反而更容易限流。
3. **没走代理**。见上方代理配置。

中断后重跑会**自动断点续传**（2 小时内有效），不用从头传。
彻底不想续传用 `--no-resume`。

### 报「目录不存在」

示例配置里两个演示任务的路径都是占位值（`/path/to/video.mp4`、
`/path/to/series`）。把 `tasks` 换成自己的路径，或删掉不用的那个。

配置文件里路径支持 `~` 展开，写 `~/videos/demo.mp4` 是可以的。

### `cover: auto` 报找不到 ffmpeg

只有自动抽帧需要 ffmpeg，其他功能不受影响。

**用图形界面版（`bilibili-submit-gui.exe`）的话不用管这个**——
从 0.1.5 起 GUI 版已内置 ffmpeg，状态栏会显示「ffmpeg 就绪（exe 内嵌）」。
嫌 GUI 体积大（72 MB）就换命令行版，按下面选：

```
1. 下载 standalone 版 exe          # 命令行 + ffmpeg 内置，约 69MB
2. 下载 full 包                     # ffmpeg 外置，约 40MB，启动最快
3. pip install imageio-ffmpeg      # 源码运行时最省事
4. 把 cover 改成图片路径或 null   # 不用自动抽帧
```

查看当前状态：`python run.py check`，或 GUI 的「设置」页 /
底部状态栏（会告诉你命中的是"exe 内嵌"、"程序同目录"、
"imageio-ffmpeg"还是"系统 PATH"）。

### 用了 standalone 版，check 还是说找不到 ffmpeg

内嵌的 ffmpeg 在 `sys._MEIPASS` 下，只有打包出来的 exe 才有。
如果你是把 zip 解开后跑里面的**轻量 exe**，那份本来就不带 ffmpeg——
得下载 `bilibili-submit-standalone.exe`，或用 `full` 包（ffmpeg 外置）。

## 历史与记录

### 历史页显示「历史文件读取失败」

历史文件（`~/.config/bilibili_submit/history.json`）坏了。程序会明确
告诉你而不是假装「暂无历史」——这很重要，否则你会以为投稿记录被弄丢了。

原因通常是：上次运行中异常退出、磁盘写满、或者手动编辑过这个文件。

处理：按提示里的路径找到文件，**直接删掉**，程序会自动重建。
删掉不会影响投稿，只是丢了历史列表——投稿记录本身在 B 站后台能看到。

```powershell
del "$env:USERPROFILE\.config\bilibili_submit\history.json"
```

Linux / macOS：

```bash
rm ~/.config/bilibili_submit/history.json
```

## Windows 特有

### 控制台中文乱码或崩溃

程序启动时会自动切 UTF-8 并探测终端能力。仍有乱码就换 **Windows Terminal**
（Microsoft Store 可装），传统 cmd 对 UTF-8 支持不完整。

### 杀毒软件报毒

PyInstaller 的 bootloader 是恶意软件常用包装，启发式引擎容易误报。
项目已关闭 UPX 压缩降低误报率，但彻底解决需自己加白名单：

```
Windows 安全中心 → 病毒和威胁防护 → 防护历史记录 → 找到该项目
→ 还原 → 菜单里选「排除项」→ 添加文件排除
```

### 打包失败：FileNotFoundError（图标）

`make_icon.py` 产出的文件名必须与 `bili_submit.spec` 里的 `APP_NAME` 一致。
现在脚本会从 spec 读取名字，不会再错位。若手工改过其中一处，记得同步另一处：

```bash
python tools/make_icon.py    # 重新生成图标
```

### 打包失败：不是交叉编译器

**PyInstaller 只能在 Windows 上打包 exe。** 官方文档明确说明它不是
交叉编译器，Nuitka、PyOxidizer 同样不支持。在 Linux/macOS 上跑
`pyinstaller` 产不出 exe。

手边没有 Windows 时用 GitHub Actions 云端打包：推 tag 即可。

## 配置相关

### 提示「配置文件顶层应为字典」或 YAML 报错

YAML 缩进对空格敏感，不允许用 Tab。检查：

```bash
python -c "import yaml,sys; yaml.safe_load(open('config/my.yaml')); print('YAML OK')"
```

### 提示「未知字段」

配置里有 `Typos`。错误信息会指出是哪个键，本程序严格校验字段名，
拼错会直接报错而不是静默忽略。

### 想改分区但不知道 ID

```bash
python run.py tid
```

列出常用分区。完整列表以服务端返回为准。

## 仍解决不了

带上以下信息提 issue：

- 完整报错信息（含错误码）
- `python run.py check` 的输出
- Python 版本（`python --version`）
- 操作系统
- 配置文件里相关的段（**删掉 cookie 内容**）

更多技术细节见 README 的「实现要点」和 `bilibili_submit/exceptions.py`
里的错误码映射表。
