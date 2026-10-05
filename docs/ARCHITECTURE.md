# 架构说明

给想改代码的人看。想知道「为什么这么设计」时读这里。

## 分层与依赖方向

依赖是**单向**的，没有反向引用也没有循环：

```
wbi / auth / exceptions      底层：签名、登录、错误类型
        ↓
      client                 会话、CSRF、响应归一化
        ↓
upload / cover / submit      业务：上传、封面、投递
        ↓
config / scheduler / cli     编排：配置、任务执行、命令行
        ↓
        ui                   界面（可选的一层，只在 GUI 版存在）
```

底层不知道上层的存在。想换投递后端不用动上传，想换配置格式不用动签名。
任一层可整体替换——`submit.py` 里的 `SubmitBackend` 抽象就是例子。

`ui` 在最上面，只被 `cli.py` 的 `gui` 子命令引用。删掉整个 `ui/`
目录，命令行功能不受任何影响。

## 模块职责

| 模块 | 职责 | 不该出现在这里的东西 |
|---|---|---|
| `wbi.py` | WBI 签名：密钥获取、置换派生、参数签名 | 任何业务语义 |
| `auth.py` | 二维码登录全流程、cookie 持久化、buvid 补全 | 知道投稿接口存在 |
| `exceptions.py` | 异常体系与错误码 → 异常类型映射 | 任何 `try` 块 |
| `client.py` | HTTP 会话、CSRF 注入、响应归一化、`-352` 自动重放 | 业务判断 |
| `lines.py` | 上传线路探测与择优 | 投稿逻辑 |
| `upload.py` | 分片上传、断点续传、合并 | 知道封面/投稿的存在 |
| `cover.py` | 封面上传、ffmpeg 抽帧 | 上传分片逻辑 |
| `metadata.py` | 标题/标签/简介/分区校验、payload 组装 | 发 HTTP 请求 |
| `multipart.py` | 判断**哪些文件算同一组分 P**（按文件名前缀或按文件夹） | 上传、投递、任何 Tk |
| `submit.py` | 稿件投递、`601` 退避重试 | 解析配置文件 |
| `config.py` | 配置加载与严格校验、任务展开 | 网络请求 |
| `scheduler.py` | 任务执行编排、历史记录 | 具体业务步骤 |
| `cli.py` | 参数解析、输出格式化、退出码 | 业务逻辑 |
| `console.py` | Windows 控制台 UTF-8 适配 | 任何业务 |
| `ui/` | 图形界面（tkinter）。见下方「界面层」 | 业务逻辑 |
| `ui/theme.py` | 颜色/字体/间距的唯一来源（Fluent 规范，浅/深两套色板） | 具体控件 |
| `ui/widgets.py` | 可复用组件（`Collapsible` 折叠区、`OptionSwitches` 投稿开关、自绘圆角的 `FluentButton`），不知道 B 站的存在 | 业务概念 |
| `ui/qr.py` | 二维码矩阵 → Canvas 绘制 | 网络请求 |
| `ui/workers.py` | 后台线程与取消 | UI 操作 |
| `ui/state.py` | 界面偏好读写（批量页参数 + 主题模式） | Tk 操作 |
| `ui/environment.py` | 登录态与 ffmpeg 的纯探测，**不碰 Tk** | UI 操作 |
| `ui/views/` | 各页面：把数据画出来、把操作翻译成下层调用 | 业务逻辑 |
| `ui/app.py` | 主窗口、导航、状态栏 | 业务判断 |
| `bili_submit.spec` | 打包配置。`INSTALLER=1` 走 onedir（安装版），否则 onefile | 业务代码 |
| `installer.iss` | Inno Setup 安装器脚本（只装文件，不含代码逻辑） | 运行时行为 |

## 关键设计决策

### 响应归一化在 client 层

B 站的接口包装不统一：绝大多数是 `{code, message, data}`，但 `preupload`
的顶层直接是 `{"OK":1,"lines":[...]}`，没有 `code`/`data` 外壳。

用通用解析器处理所有接口会误判，所以分了两条路径：

```python
client._parse(resp)          # 标准包装，返回 data；非标准包装回退 HTML 解析
client.request_raw(...)      # 跳过归一化，原样返回 JSON（给 preupload 用）
```

如果 B 站改了包装导致 `_parse` 抓不到 `code`，会抛 `ApiChangedError` 而不是
静默返回 `None` —— 后者会让下游在奇怪的地方炸，排查成本高得多。

### `601` 退避在 submit 层，不在 scheduler

`601`（投稿过快）是 B 站的频率限制，重试时必须**先刷新签名密钥**——用旧密钥
重试必然再次被拒。所以退避循环住在 `submit_archive()` 而不是通用调度层：

```python
for attempt, wait in enumerate(cooldown):
    if attempt:
        client.signer.invalidate()   # 重试前必须换密钥
        time.sleep(wait + jitter)
    resp = backend.add(...)
```

放在 scheduler 会让退避逻辑散到每个调用点，且容易漏掉密钥刷新。

### 配置严格校验，拒绝未知字段

`_build()` 会把 YAML 里不在 dataclass 字段中的键全部报错：

```python
unknown = set(data) - known
if unknown:
    raise ConfigError(f"{cls.__name__} 含未知字段: {sorted(unknown)}")
```

宽松解析会让 `tyop: 21` 这种拼写错误静默失效——用户以为设了分区，
实际走的是默认值，投稿出来才发现。

### 续传状态是本地文件，服务端 TTL 未公开

`upload.py` 的 `ResumeState` 存在本地，2 小时后主动作废。原因：

- upos 的 `upload_id` 服务端 TTL 没有公开文档
- 客户端猜一个保守值（`resume_ttl=7200`）比猜一个长的安全
- 状态文件过期时会自动清理，不会越攒越多

### ffmpeg 三级降级

```
程序同目录 → imageio-ffmpeg 包内 → 系统 PATH
```

为什么不塞进 exe 归档：PyInstaller 单文件模式每次启动都要把归档解压到
临时目录，多 30 MB 意味着每次启动都多一次解压，而且**临时目录里的 exe
更容易被杀毒软件拦截**。外置 `ffmpeg.exe` 是启动最快、误报最低的方案。

### 后端抽象

`submit.py` 的 `SubmitBackend` 有两个实现：

- `WebBackend`：默认。`add/v3`（失败可退 `add` v2），扫码登录即可用
- `AppBackend`：需要 `access_key`。纯扫码登录拿不到，除非你另有渠道

接口下线时改配置 `submit.backend` 即可切换，不用改代码。

## 界面层

GUI 用 **tkinter**（标准库）而不是 Qt/Web，理由是零新增依赖：
不用引入 40MB+ 的第三方库，打包体积只增加约 2MB。

几条硬性约束：

### 任务的两种来源，默认不需要配置文件

| 来源 | 入口 | 特点 |
| --- | --- | --- |
| `folder` | 「选择文件夹…」 | 扫目录**第一层**的视频，每片一条任务，标题取文件名 stem，投稿参数由页面顶部统一提供 |
| `yaml` | 「从 YAML 加载…」（次要位置） | 逐任务元数据、`title_template`、`upload.*`、`submit.backend` 以配置为准 |

文件夹模式**完全绕开 `load_config` / `expand_tasks`**，直接造
`TaskConfig` 和 `AppConfig()`——`run_task` 只读 `cfg.upload.*` 和
`cfg.submit.cooldown_seconds`，现场造一个默认值的 `AppConfig` 就够用。
这正是投稿页一直以来的做法（`upload.py` 的 `_collect`）。

扫描只做一层、不递归：文件对话框里很容易指到「视频」这种大目录，
递归下去可能一次生成上千条任务。

两种模式不能互相覆盖。yaml 模式下顶部统一参数被置灰，否则一份
`title_template: "第{{n}}期"` 或逐任务不同的 tid 会被一刀盖成同一个值，
这些能力等于废了。

由此还删掉了一个概念：客户端不再有「配置文件」这回事，
所以 `AppContext.config_path` 没了，设置页环境自检里的「配置文件」一行
也没了。留着只会让人以为「不加载配置就不算装好」。

### 分 P 分组：猜用户意图要保守，且必须能反悔

「哪些文件算同一套视频」本质是**猜**。猜错的代价不对称：误合并会把两个
不相干的视频投成同一个稿件的两 P（要删稿重投，BV 号也废了），
漏合并只是多占一个 BV 号。所以 `multipart.py` 的每条规则都朝"宁可不合并"
的方向倾斜。

分组有**两种依据**，由 `group_files(files, mode, root)` 分派：

| 模式 | 依据 | 什么时候更合适 |
| --- | --- | --- |
| `prefix` | 文件名前缀 | 视频都堆在一个目录里，靠 `旅行_01` 这种命名区分 |
| `folder` | 所在文件夹 | 用户自己建了目录结构——比文件名可靠得多 |

`prefix` 模式的规则：

| 规则 | 例子 | 理由 |
| --- | --- | --- |
| 只认**文件名尾部**的序号 | `旅行_01` / `旅行-2` / `第3集` / `P4` / `电影 (1)` | `第3集 正片.mp4` 里的 3 描述的是内容，不是分 P 序号 |
| 先剥分辨率后缀，再剥序号 | `旅行_01_1080p` → `旅行` + 1 | 不剥的话尾部是 `1080p`，整批都分不成组 |
| 名字里只剩数字时，要求序号**从 1 开始且连续** | `01/02/03` 合并；`2023/2024`、缺 P1 的 `P2/P3` 不合并 | 名字里没有别的线索时无从判断关联，只能靠这条硬指标兜底 |
| 组内按**自然序**排 | `P2` 在 `P10` 前面 | 字典序会把 `P10` 排到 `P2` 前面，分 P 顺序就反了 |
| 单文件组照样返回 | — | 分组只回答"哪些看起来是一套"，要不要按多 P 投由调用方决定 |

`folder` 模式的规则（目录结构是用户自己建的，比文件名可靠）：

| 规则 | 理由 |
| --- | --- |
| 组名取**文件夹名**，哪怕文件夹里只有一个文件 | 文件名常是 `output.mp4` 这种没信息量的，文件夹名才是用户起的名字 |
| **根目录第一层的散落文件不合并** | 它们没被放进任何子文件夹，说明用户没把它们当成一套；硬按目录名合成一个稿件就是误合并 |
| 组顺序按**文件夹名**排，不按组内文件名 | 按文件名排会让不同文件夹里的同名文件（`旅行/01.mp4`、`教程/01.mp4`）互相干扰，排出来跟目录结构对不上 |
| 扫描深度只放开到子文件夹**一层** | 指到「视频」这种大目录时递归到底会一次冒出上千个任务（这条也是 `scan_video_files` 默认不递归的原因） |

猜测一定会出错，所以结果**必须可见、可反悔**：列表有「分P」列显示每个
稿件的分 P 数，双击行能改各分 P 标题，开关切回「不合并」就完全恢复成
一个视频一个稿件。

多 P 的执行语义是「**上传 N 次，投递一次**」：`run_task` 逐个上传拿到各自
的 `filename`，按顺序塞进 `ArchiveMeta.videos`，只调一次 `submit_archive`。
B 站按数组下标排分 P，投错了顺序没法在投稿后调整，所以 `files` 的原序
在任何环节都不做重排。

### 跨线程传的是快照，不是页面

`_do_run` 收到的是主线程准备好的 `(AppConfig, [(index, TaskConfig)])`，
**不读 `self._tasks` / `self._cfg` / 任何 Tk 变量**。

这不是洁癖。执行期间虽然锁住了勾选，但页面属性随时可能被换掉——
「重新扫描」会把 `_tasks` 整个换掉，而工作线程正逐项遍历它，
换掉一半时投出去的是新任务配旧参数，或者直接越界。主线程建快照、
工作线程只认传进来的东西，「谁在什么时候读什么」就固定下来了。

`SharedSubmitValues` 是不可变 dataclass，`apply()` 用 `dataclasses.replace`
产出新对象而不是就地改：就地改的话执行线程可能读到改了一半的字段
（tag 已经换了、tid 还是旧的）。

统一参数填的是「距今几小时」，逐任务的绝对时间 `dtime` 要一并清掉，
否则两套定时来源打架，`resolve_dtime` 拿到的是上次的旧时间点。

### 界面偏好文件

批量任务页上次选的目录和顶部参数记在
`~/.config/bilibili_submit/ui-state.json`（`ui/state.py`），跟随 cookie
同目录——**不放 exe 同目录**：打包后那可能是 Program Files，只读；
源码运行时还会把仓库根目录弄脏。

三条约定：

- **只存用户填过的选择**，不存 cookie、代理、执行结果、勾选状态。
  凭据不该落到这种文件里。
- **缺失与损坏要分开**。文件不存在是第一次用，返回默认值且不报错；
  损坏或版本不认识必须如实报告——静默当成首次使用的话，用户会以为
  程序把设置弄丢了，而界面上什么都看不出来。
- **原子写入**（临时文件 + `os.replace`）。这个文件正是「下次打开还在」
  的唯一凭据，写到一半崩了留下半个 json 就再也读不出来了。

恢复只做一次。`refresh()` 里靠 `_restored` 挡住重复恢复——否则用户执行到
一半切去看了眼历史，回来发现目录被重扫、勾选被清空。

### 环境自检必须离开主线程

`ui/environment.py` 的 `probe_environment()` 是纯函数：进 cookie 路径，
出 `EnvironmentSnapshot`，一个 Tk 符号都不碰。**必须在工作线程调**。

它要读 cookie 文件、探 ffmpeg 路径、必要时 `subprocess.run` 跑
`ffmpeg -version`（最坏 10 秒超时）。放在 Tk 主线程，用户点开设置页
看到的就是程序死了而不是在忙。实测切换设置页从数百毫秒降到约 3 毫秒。

状态栏的 `refresh_status()` 有同样的问题，且有 4 个调用方（启动、换 cookie
路径、登录成功后、批量页发现登录失效）——**只修设置页不够**。

两个容易漏的点：

- **一次检测里 `load_cookies` 只调一次**。原先写成
  `theme.tone(...) if ctx.logged_in else ...` 加 `text=... if ctx.logged_in else ...`，
  那个 property 在同一行里被求值了两遍，文件也就读了两遍。
- **多次刷新要用代号丢弃过期结果**。旧 cookie 路径的探测可能更慢、后到，
  把新结果显示覆盖掉——用户明明刚换好文件却看到「未登录」。
  每次刷新推进一个 generation，回包时先比对，不一致就丢弃。

**不要用「已有探测在跑就 return」来防重入**：用户刚换完 cookie 路径就
又点了刷新，第二次探测必须能发出去，否则界面永远停在**上一个路径**的状态，
而用户恰恰是在确认新路径有没有登录成功。

刷新期间界面显示「检测中…」，探测抛异常也要显示出来——不能永远停在
「检测中…」，那比显示错误更让人困惑。

### ttk 的 `state()` 和 `configure(state=)` 不是一回事

分区下拉框是 `state="readonly"` 的组合框。要恢复它必须用
`configure(state="readonly")`，走 `state(["!readonly"])` **没有效果**——
readonly 是 ttk 的**选项**，不是状态标志位。踩过这个坑：yaml 模式置灰后
切回文件夹模式，下拉框永远灰着。

### 界面不实现业务逻辑

页面只做两件事：把下层数据画出来，把用户操作翻译成下层调用。
投稿走的是和 CLI 完全相同的 `run_task`，登录用的是同一套
`request_qrcode` / `poll_qrcode`。否则两套实现迟早走偏。

`login_interactive` 是阻塞的且内部直接 `print`，**GUI 不用它**——
改为自己驱动 `request_qrcode` + `poll_qrcode` 的轮询循环，
这样才能一边轮询一边刷新界面、还能取消。

### 投稿选项有两套名字，翻译点只有两处

界面上叫「关闭弹幕 / Hi-Res」，B 站接口叫 `up_close_danmu` /
`lossless_music`。名字不统一是故意的：界面说人话，投递说接口的话。
错位的代价是**静默失效**——字段名写错 B 站不会报错，只是忽略，
用户看到的是「开关点了没反应」。

翻译只发生在两处，都有测试盯着：

| 位置 | 干什么 |
| --- | --- |
| `views/upload.py` 的 `_collect`、`views/tasks.py` 的 `apply()` | 界面 flags → `TaskConfig`（此时已换成接口名 `up_close_reply` 等） |
| `scheduler.build_archive_meta` | `task.hires` → `ArchiveMeta.lossless_music` |

第二处单独抽成函数就是为了能被**直接**测到。埋在长函数里就只能
打桩整条上传链路才摸得着，而那种测试一旦挂了看不出是哪一步错。

另外 `dolby` / `lossless_music` 官方参数表标了「必要」，payload 里
**关着也要写 0**，只在开启时写的话服务端收不到字段。

### tkinter 不是线程安全的

工作线程里直接改控件会让进程崩溃（Tcl 解释器不可重入）。
所有耗时操作都走 `ui/workers.py`，结果用 `after()` 排回主线程。

闭包捕获 `except ... as exc` 的变量要小心：except 块结束时 Python
会 `del` 掉它，而 `after` 是延迟执行的，必须用默认参数绑定
（`lambda e=exc: on_error(e)`）。

### 二维码不依赖 Pillow

`ui/qr.py` 从 `qrcode` 拿布尔矩阵，再用 Canvas 逐格画方块。
打包配置里 PIL 是被排除的，为一张二维码把它拖进来不划算。

### 颜色不写死在组件里

所有颜色/字体/间距来自 `ui/theme.py`。改主题改一处即可。

### 主题遵循 Fluent Design，且只在启动时确定

`ui/theme.py` 按 Windows 11 Fluent 规范给出度量（8px 栅格、6px 控件圆角、
32/40px 控件高度）；`set_mode()` 就地更新模块级常量，几百处 `theme.XXX`
引用不用跟着改。主题在**启动时**由「设置页选的模式 + 系统深浅色」解析出来
（`ui/state.py: resolve_theme_mode`），运行期不热切换——tk/ttk 把颜色写进
控件，改色板不影响已建好的控件，要真切换得重建整棵控件树。设置页明确
提示「重启生效」。

色板用**语义名**而不是「颜色用途之外的名字」：`PAPER` / `INK_MUTED` /
`LINE_STRONG` / `FOCUS_RING`，而不是 `SURFACE` / `TEXT_MUTED`。改名会波及
几百处引用，所以 `palette_for()` 同时返回新旧两套键，`_ALIASES` 做映射——
旧名保留只为过渡，新代码一律用语义名。

三条颜色上的硬约定，都有测试盯着（`tests/test_theme_contrast.py`）：

1. **纯灰不存在**。所有中性色都带品牌粉紫相，判据是 **OKLCH 色相**落在
   −32° ~ 4°，不是「红通道 > 蓝通道」也不是「彩度 > 0」。`#6B5B5B`（褐灰）
   的 R 确实大于 B、彩度 0.021 也不低，但色相 +18° 已经跑到橙褐，肉眼一眼
   看出脏——**这类错误只有色相抓得住**；
2. **粉底配墨字**。B 站粉 `#FB7299` 配白字只有 2.64:1（AA 不达标），配深梅墨
   `#241A1F` 是 6.41:1。焦点环另用深梅 `#A62052`（6.86:1），因为白底上的粉
   只有 2.64:1，当焦点环等于看不见；
3. **深色不是浅色的反转**。深色下卡片 `#211A1F`、底色更深 `#120E11`，而
   操作条与状态栏比卡片**更亮**（`#2E2429`）——用「更亮的表面」表达深度，
   从不用纯黑。状态色因此有两组：`tone()`（浅底）与 `tone_on_ink()`（墨底），
   同一语义在两种底上取不同值。

字体的坑：**`tkfont.Font(name=...)` 创建的 Font 对象必须被 Python 侧持有
引用**，否则 GC 之后 Tk 端的字体定义消失，之后再`Font(name=...)` 会报
`expected font name`。`theme._FONT_OBJECTS` 就是干这个的。9 个字体角色
（display / title / body / caption / mono …）注册为 `Bili.*`，样式里引用
字体名字符串；`measure_font()` 拿测量用的 Font 对象，走 `Font(font=...)`
查询路径而不是新建。

两条 tk 的硬边界，用降级方案而不是假装实现：

- **输入框/下拉框画不出圆角**（ttk 引擎绘制）→ 1px 细边框 + 聚焦色；
  按钮用 Canvas 自绘（`ui/widgets.py: FluentButton`），圆角是真的；
  - 自绘按钮的事件绑定方法**不能叫 `_bind`**——那会覆盖 `tk.Misc._bind()`，
    叫 `_bind_events`；
- **没有原生阴影** → 卡片用底色 + 1px 描边表达层级。

Windows 专属的窗口效果收在 `ui/win_effects.py`：深色标题栏、窗口圆角、
Mica 调用（控件不透明所以看不见，保留调用不假装实现），全部失败静默。

### 底部固定操作条：主操作不该需要找

`ui/widgets.py: ActionBar`，投稿页与批量任务页各一个，**放在滚动区之外**
固定在窗口底部。早期版本主按钮在表单末尾，表单高约 700px、最小窗口只有
600px，要滚到列表底部才找得到——「开始投稿」这种按钮一旦需要找，就是没有。

操作条一行四段（从左到右）：

| 段 | 内容 |
| --- | --- |
| 0（weight=1） | 标题 + 实时摘要（「投稿「第03期.mp4」· 分区 21-日常 · 自制 · 立即发布」） |
| 1 | 禁用原因，就地说明 |
| 2 | 次按钮（「仅预览」/「重试失败项」） |
| 3 | 主按钮 |

四条设计约束，都是被自己的bug 教出来的：

- **主次按钮必须各占一列**。最初两者都grid 到 column 2 靠 sticky 区分，
  结果粉底主按钮被次按钮完全压住——从截图上才能看出来；
- **原因要截断**（`_ellipsize`，24 字）。这一行是固定高度，写长了会把主按钮
  挤出可视区。**按钮比原因重要**，要解释清楚位置应该是日志区；
- **`block("")` 也必须清掉旧文案**。状态变了、提示还停在「尚未登录」比
  什么都不说更误导；
- **组件不管次按钮的可用性**。`set_busy()` 刻意不动次按钮——它多半是
  「仅预览」这类无害操作，而组件不知道那个按钮是干什么的。判断放在调用方
  （`views/upload.py: _refresh_action_bar`）。

`Ctrl+Enter` / `Esc` 绑在整个窗口上，空闲时是开始/取消，运行中是取消——
不给用户两个含义不同的入口。

加载态与运行态**必须拆开**（`_set_loading` / `_set_busy`）：扫描文件夹时
主按钮保持原样并显示「正在扫描文件夹…」，而不是变成「取消」——那会让人
以为要放弃整批投稿。

`tests/test_action_bar.py` 锁住以上行为。

### `grid()` 跟着 `widget.master` 走，不跟着调用者走

这条踩过一次真实 bug，值得单独写。

Tk 的 `widget.grid()` 永远作用于 `widget.master`——**不是**调用它的那段代码
所在的容器。所以下面这种写法看着对、实际会把所有控件排到同一格：

```python
form = ttk.Frame(card)
entry = ttk.Entry(form)          # 父容器是 form
FormRow(form, "标题", entry)     # FormRow.grid(row=0, column=1)
#                                   ↑ 实际排到 form 的 (0, 1)，不是 FormRow 里
```

六个输入框会全部叠在第一行，互相压住。改 `.master` 属性也没用（Tcl 层的
父子关系在创建时就定了）。

`FormRow` 因此不接收成品控件，而是接收控件**类**，由内部容器 `body`
实例化：

```python
row = FormRow(form, "标题", hint="留空则自动取文件名")
row.grid(row=1, column=0, sticky="ew")
row.add(ttk.Entry, textvariable=self._title_var)
```

**加新表单控件时一律用 `row.add(...)`**，别自己 new 完再塞进去。
`tests/test_ui.py` 里有两条测试专门量控件的实际纵坐标来防回归——
布局错位静态检查抓不到，必须真跑 Tk 量。

### 实例属性不能叫 `_options`

`Misc` 内部有个 `_options` 方法，`grid()` 会调 `self._options(cnf, kw)`
拼参数。自定义组件里写 `self._options = ...` 会把它盖掉，然后**所有**
`grid()` 调用炸成 `TypeError: 'XxxSwitches' object is not callable`——
堆栈指向 `tkinter/__init__.py`，看着像 Tk 自己的 bug。

所以投稿开关组件在页面里叫 `self._option_switches`。这也是 Tk 组件
命名时的通用提醒：下划线开头的名字先确认没撞 `Misc` 的方法。

### 窗口再小也不能让按钮点不到

投稿表单内容高约 700px，最小窗口只有 660px。早期版本直接铺控件，
结果「开始投稿」被挤出可视区——用户既看不到也点不到。
现在内容包在 `ScrollArea` 里，超出时可滚动（滚轮也支持），**主操作另外
放在滚动区之外的 `ActionBar` 上**，连滚都不用滚就能看见。

`tests/test_ui.py::test_submit_button_reachable_at_min_size` 与
`tests/test_action_bar.py` 一起锁住了这个行为。

顺带一个反直觉的点：**滚动容器里不能用 `weight=1` 让日志区吃掉剩余空间**。
父容器高度就等于内容高度，权重不会带来额外空间，反而会挤压上面的按钮。
固定高度 + 内容超出时日志自己滚。

### 「读不出来」和「本来就没有」要说不同的话

`read_history` 早期把文件损坏和没有记录都返回空列表。界面上就只显示
「暂无投稿历史」——文件明明坏了，用户却以为程序把记录弄丢了，
然后开始找备份、翻 cookie、怀疑人生。

现在拆成两个函数：

- `read_history()` —— 保持宽容，返回空列表。`append_history` 复用它
  来「读旧记录再整体写回」，一旦抛异常，损坏的历史文件会让**之后所有
  投稿记录都写不进去**。为了能继续写，宁可丢弃旧数据。
- `read_history_diagnose()` —— 返回 `(记录, 错误说明)`，给需要给用户
  提示的场合用（GUI 历史页）。

### 列表行每行只打一个 tag

ttk 的 Treeview 着色只能靠 tag，于是很自然会想「斑马纹一个 tag、
状态色一个 tag」叠加。但**多个 tag 同时作用于一行时哪个 `background`
生效没有保证**，tag 背景与 `selected` 态的覆盖关系同样不确定。

所以 `theme.apply_tree_tags` 配的每种状态都自带前景和浅底，
而业务代码保证**每行只打一个 tag**：`idle / busy / ok / error / missing`。
`test_row_never_carries_two_tags` 守着这条。

放弃斑马纹不损失什么——任务列表里「扫一眼看出成败」本来也比横向对齐
更有价值。

### 工作线程上报结构化 `Event`

`Worker` 的 `report` 原本只接受 `str`（一行日志）。但「第 3 个任务跑完了、
状态是成功」这种信息需要同时更新列表行、进度条和状态栏，硬塞进字符串
再解析太脆。

`workers.Event` 是一个冻结 dataclass，承载
`kind / index / position / total / status / error`。回调侧 `isinstance` 区分::

    def _on_progress(self, message: str | Event) -> None:
        if isinstance(message, Event):
            ...   # 更新行状态 / 进度条 / 状态栏
        else:
            self._log.append(message)

这样批量任务页的 `_do_run` 里没有任何一处碰 Tk——此前它是直接调
`self.after(0, ...)` 的，全项目最后一处这类违规。

**`index` 和 `position` 不能混用。** 前者是任务在完整列表里的下标（用来
定位行），后者是它在**本次执行**里第几个（用来算进度）。用户只勾了第 4 和
第 6 个任务时，`total=2` 而 `index` 是 3 和 5——拿 `index + 1` 除以 `total`
会算出 200%，进度条第一项就顶满，状态栏还会显示 `6/2`。
`Event.percent` 只认 `position`，且 `position` 缺失时返回 0 而不是拿
`index` 顶替：宁可进度条不动，也不要给一个错的百分比。

### 跨越线程边界的东西必须能安全丢弃

`Worker` 的所有回调都经 `workers.safe_after` 送出。它挡两种异常：

- `tk.TclError`：窗口销毁后排队的 `after` 已被 Tcl 清掉。
- `RuntimeError`：主线程不在事件循环时（启动阶段、或事件循环已退出），
  `after` 注册不到 Tcl 的命令表上。

关键在于 `try` 必须包住 **`after` 注册本身**，而不只是回调——只包回调的话
异常从注册那一行就漏出去了，而它几乎总是发生在工作线程里。一抛整条
线程就死：`on_done` 和 `on_error` 都收不到，界面永远停在「执行中」，
用户既不能重试也不能取消。`test_safe_after_swallows_*` 守着这两条。

推论：**工作线程里不要碰任何 Tk 符号**，包括看起来很安全的 `after`
（`after` 自己也要往 Tcl 的命令表里注册东西，见上面的 `RuntimeError`）。

### 执行期间界面要锁定

批量任务页在执行期间必须拒绝三类操作，否则会出现「界面变了但行为没变」：

| 操作 | 后果 |
| --- | --- |
| 改勾选（含全选/只选失败项） | `_run` 启动时已快照 `indexes`，实际执行的仍是原来那几个 |
| 换目录 / 重新扫描 | 整体换掉 `_tasks`/`_cfg`，而工作线程正逐项读这两个属性 |
| 从 YAML 加载 | 同上，且会把顶部参数切成 yaml 模式 |
| 点「重试失败项」 | `_run` 会因「已有任务在运行」直接 return，成了假按钮 |

判定统一走 `TasksView._editable()`，它**只看 `self._busy`**（界面态，
由 `_set_busy` 维护），不要掺 `self._worker.running`（线程态）。

理由是两个信号并不等价，而且掺进来会引入一个真 bug：`on_done` /
`on_error` 是排进 Tk 事件循环执行的，那时工作线程**只是排完队就返回、
还没真正退出**，`running` 仍是 True。拿它当「是否解锁」的判据，
收尾时会把「重试失败项」重新锁死，而之后没有任何东西会再来刷新一次——
有失败项却点不了重试。

`_busy` 覆盖了「线程还活着」的整个窗口：`_run` / `_start_load` 开头置 True，
`_on_done` / `_on_error` / `_on_loaded` / `_on_load_error` / 启动失败五处归 False。
相比线程内部状态，界面态才是这里真正该依据的东西。

由此推出一条纪律：**`_busy` 的置位与归位必须成对，且归位不能依赖
「后续一定会有回调」**。`_run` 里 `_set_busy(True)` 之后的
`_log.clear()` / `_progress.reset()` / `_worker.run()` 任一抛异常，
就没有 `_on_done` / `_on_error` 来收尾，`_busy` 永久卡在 True——
「开始投稿」灰着、取消点不动，只能重启进程。所以那段整体包在
`try/except` 里。

按钮的启用状态则要由**唯一**的收口函数决定。`_mark` 每完成一项都会触发
`_update_summary`，如果汇总里直接写「有失败项就解锁重试」，运行途中
出现的第一个失败项会把按钮解开——所以那里必须一并检查 `_editable()`。

同样的道理适用于登录页和投稿页：`_on_done` / `_on_error` 都必须
**先 `_set_busy(False)` 再做可能失败的活**。登录页曾把 `save_cookies()`
（会 `makedirs/open/os.replace/os.chmod`）排在解锁之前，路径不可写时
回调抛出、没人接，按钮就永久锁死——而用户看到的现象是「登录成功了」，
比直接报错更难排查。

### 会话只能从 `new_session()` 来

早期 GUI 登录页在 `_login_flow` 里写了一句
`session = requests.Session()`，理由是「只想设个代理」。
问题在于 B 站会**识别 UA**：`requests` 的默认 UA 是
`python-requests/x.y.z`，一眼就被认成脚本，`passport.bilibili.com`
直接返回 **HTTP 412 的 HTML 风控页**，而不是 JSON。
于是 `.json()` 抛出 `JSONDecodeError: Expecting value: line 1 column 1`，
界面上只有一行日志写着「获取二维码失败」。

命令行的 `login` 一直正常，所以这个bug 拖到GUI 发布才暴露——
**两条路径各自造会话，配置漂移就是这么发生的。**

现在的规则：

- 唯一入口是 `auth.new_session(proxy)`，负责 UA、Referer、代理三件事。
  它是公开 API（`__all__` 里），供 UI 与 CLI 共用。
- 需要「传一个现成的 session」时，用它而不是裸 `Session()`——
  `request_qrcode()` / `poll_qrcode()` 内部只补默认 session，不改调用方给的对象。
- `test_no_bare_session_left_in_login_paths` 直接扫 `login.py` 与 `cli.py`
  的源码（剔除注释行），一旦有人写回裸 `Session()` 就失败。

配套的 `_json_or_raise()` 负责另一端：**别把非JSON 响应直接喂给 `.json()`**。
412 → `NetworkError`（提示换代理），200 但返回 HTML → `ApiChangedError`。
用户看到的是「被风控拦截，换代理试试」，而不是一句解析器报错。

写这类「读用户数据」的函数时要分清两种失败：**没有**和**读不了**。
前者是正常状态，后者是问题——两者混为一谈会让用户做无用功。

### GUI 默认带 ffmpeg，命令行默认不带

同一个 spec 产出两类 exe，ffmpeg 策略相反：

| | GUI | 命令行 |
|---|---|---|
| 默认 | 内嵌（约 72 MB） | 不带（约 10 MB） |
| 理由 | 用窗口界面的人不会自己去装 ffmpeg | 用命令行的人通常已经有了 |

`bili_submit.spec` 里用 `_FFMPEG_EXPLICIT` 区分两种「想要 ffmpeg」：

- 显式 `BUNDLE_FFMPEG=1` 却找不到 → **硬失败**。用户明确要求的，
  产出缺 ffmpeg 的包不如当场报错。
- GUI 的隐式默认却找不到 → **警告并降级**为不内嵌。
  否则一台没准备 ffmpeg 的机器（CI 首次运行、新同事 clone 下来）
  连 GUI 包都打不出来。

⚠️ 降级后有个容易漏的点：判断「要不要排 `imageio_ffmpeg`」必须用
`BUNDLE_FFMPEG` 的**最终值**，不能写成 `if/else`。降级路径把变量改回
`False` 后走不到 `else`，imageio 就会留在包里——既没内嵌也没 imageio
兜底，白白多打约 30 MB。这个 bug 是 `test_gui_without_ffmpeg_degrades_instead_of_failing`
抓出来的。

## 添加新命令

以「加一个 `sync` 子命令」为例：

1. **业务逻辑放独立模块**，比如 `sync.py`。不要写进 `cli.py`
2. 需要发请求就用 `BiliClient`，别自己 `requests.get`——否则拿不到
   `-352` 自动重放、CSRF 注入、代理设置
3. 在 `cli.py` 的 `build_parser()` 里加解析器，在 `handlers` 字典里注册
4. 退出码用 `EXIT_OK` / `EXIT_FAIL`，参数错误用 `2`
5. 写测试放 `tests/`，命名 `test_<模块>.py`

新命令如果要读配置，用 `_client_from_config(cfg)`，它已经处理了
「未登录抛错」和「`--dry-run` 允许匿名」两种情况。

## 测试约定

```bash
python -m pytest                          # 默认，跳过联网测试
BILI_NETWORK_TESTS=1 python -m pytest -m network   # 联网测试
pylama --max-complexity 20 bilibili_submit tests tools
```

- **默认不联网**。B 站接口会风控，CI 上跑联网测试容易变成随机失败
- 需要联网的测试标 `@pytest.mark.network` 且 `skipif` 环境变量
- 涉及 Windows 编码的测试要**真的构造 cp1252 环境**去验证，
  mock 掉 encoding 属性只能验证一半（`reconfigure` 之后原 buffer 就丢了）
- UI 测试在无显示环境时自动跳过。**本地验证请用 `xvfb-run`**，
  否则被跳过的几十项等于没跑::

      xvfb-run -a python -m pytest          # 403 passed / 2 skipped
      python -m pytest                     # 无显示时约 160 passed / 60+ skipped

### 异步 UI 测试必须跑真实事件循环

`root.update_idletasks()` 只处理重绘，**不处理普通 `after` 回调**。
Worker 完成只是把结果 `after(0)` 排进队列，能不能排上、什么时候排上
全由事件循环决定。所以：

    def run_until(root, predicate, timeout_ms=5000):
        """跑真实 mainloop 直到 predicate() 为真；超时直接 fail。"""
        ...

凡是要等 Worker 结果的测试都用它，不用 `update_idletasks()`。
**超时必须 fail 而不是静默通过**——宁可红一次，也不要让「异步结果没到达」
被读成「功能正常」。

同一个道理适用于「等待某个后台动作已落地」的断言：造一个计数器，
确认回调**确实执行过**，再断言界面状态。否则「没被覆盖」可能只是因为
它压根还没到——那这条用例就是白绿。

### 造竞态要造出真的前提

测「旧结果不该覆盖新结果」时，光是连续调两次刷新不够——
两次都瞬间返回的话根本没有竞态。要把第一次卡住（用 `threading.Event`
当闸门）、让第二次先返回、最后放掉第一次，这才复现了真实场景。

### 测试要能抓住它声称要抓的 bug

新加一条测试后，**把修复注回去看它是否变红**。这一步经常发现
「测试自己就有盲点」。

实际遇到过两个例子：

1. 验证「运行中不能改勾选」的测试写成

       before = view._selected_indexes()
       view._select_failed()
       view._select_none()
       view._select_all()          # ← 恰好把状态恢复原样
       assert view._selected_indexes() == before

   注回 bug 后测试照样绿——因为最后那次「全选」正好抵消了前面的改动。
   改成每一步都断言才真正抓住。

2. `test_worker_survives_dead_window` 断言「线程退出了」

       assert not worker._thread.is_alive()

   但线程抛未捕获异常后**照样退出**，新旧代码都通过，等于没测。
   真正的信号是 `threading.excepthook` 有没有被触发——接管它，
   断言「无未捕获异常」才有效。

顺带一条：**模拟并发/竞态要造出真的那个前提**。测「线程还活着时
不该锁按钮」时，只调 `_set_busy(False)` 是不够的——那一刻压根没有
线程，竞态根本没被复现。要起一个真线程卡在那里，才能测到。

还有一个反向的例子值得记：验证「批量任务不用配置文件」时，光断言
「加载后有 2 条任务」太弱——换成 yaml 加载也是 2 条。把 `load_config`
打桩成一调即抛，再走文件夹加载还能成功，才真正证明主路径没碰 yaml。

所以顺序：写测试 → 注回 bug → 确认红 →（必要时修正测试）→ 还原 → 确认绿。

## 打包形态：onefile 与 onedir

图形界面版有两种分发形态，由 `bili_submit.spec` 读 `INSTALLER` 决定：

| | 便携版 | 安装版 |
|---|---|---|
| 环境变量 | `GUI=1 BUNDLE_FFMPEG=1` | `GUI=1 INSTALLER=1` |
| PyInstaller 形态 | onefile 单文件 | **onedir 目录** |
| 产物 | 一个 exe | `dist\<EXE_NAME>\` 目录 + 安装器 |
| ffmpeg | 打进 exe 归档 | **构建脚本复制到 exe 同目录** |
| 启动 | 每次解压 60MB 到 `%TEMP%` | 即用 |

安装版选 onedir 不是省事，是因为 onefile 唯一的代价就是启动时解压：
GUI 内嵌 ffmpeg 后每次启动都要把 60MB 写到临时目录。装在
Program Files 下这会明显变慢，临时目录里的 exe 也更容易被杀软拦。
onedir 把 ffmpeg 放 exe 同目录，`ffmpeg.py` 本来就优先找那个位置
（`_candidates_in_app_dir`），于是既省了解压，用户也能自己换版本。

代价是「一个文件」变成「一个目录」，所以便携版保留：U 盘、别人的机器、
「不想装东西」都是真实需求。

`INSTALLER=1` 与 `BUNDLE_FFMPEG=1` 同时给会**直接 SystemExit**：那等于
「既要目录版又要每次解压」，自相矛盾。让它当场失败比产出一个行为与
预期不符的包好——后者要到用户那里才暴露成「为什么安装版还是这么慢」。

`EXE()` 的参数抽成 `_make_exe()` 由两个分支共用。写成两份的话，以后
调个图标 / console 很容易只改一处，于是两种形态行为悄悄分叉——
这类 bug 极难发现，因为两边都能跑，只是行为不一致。

### 安装版能成立的前提：用户数据不在程序目录

装到 `C:\Program Files\` 之后程序目录是**只读**的（普通用户没有写
权限）。所以 cookie、界面偏好、投稿历史全部落在
`~/.config/bilibili_submit/`：

```
~/.config/bilibili_submit/
├── cookie.json       登录状态
├── ui-state.json     界面偏好（上次用的目录、主题模式等）
└── history.json      投稿历史
```

这条约束早就写在 `ui/state.py` 的模块 docstring 里（当时是为了避免
「源码运行时把仓库根弄脏」），安装版只是让它变得**不可违背**。
写程序目录会有两种坏结果：直接崩，或者被 UAC 静默重定向到
`VirtualStore`——后者更糟，用户以为存在 A 处、重装后读的是 B 处，
表现为「登录状态莫名丢了」。

同理，`installer.iss` 的 `[UninstallDelete]` **只删程序目录的
`_internal` 残留，不碰用户数据**。删了等于重装后必须重新扫码登录，
这是最容易被「清理干净」这个念头害到的地方。`tests/test_packaging.py`
把这条钉死了。

### 安装器脚本的坑（都是在 CI 上真踩出来的）

`installer.iss`（Inno Setup 6）有以下坑，能静态挡的都在
`tools/check_installer.py` 或 `tests/test_packaging.py` 里；挡不住的
在注释里写明了成因。

**编译期（ISCC 才报，本地查不出）**

- **必须存成 UTF-8 with BOM**。Inno Setup 6 靠 BOM 识别编码；存成
  不带 BOM 的 UTF-8 或 GBK 都会让中文在安装向导里变乱码，**且不报错**
  ——只在用户眼前发生；
- **事件函数原型不能写错**。最容易混的是这两个：
  `InitializeSetup` 是 `function ... : Boolean`（返回 False 能拒绝
  安装），`InitializeWizard` 是 **`procedure`**（只做初始化）。名字像，
  写法不一样，写反了 ISCC 报 `Invalid prototype for 'InitializeWizard'`；
- **`[Code]` 里 `{src}` 和 `{#BuildDir}` 不能直接拼**。两者基准不同：
  `{src}` 是安装器 exe 所在目录（`dist\`），`{#BuildDir}` 是相对于
  `.iss` 所在目录（仓库根）。直接拼 = `dist\dist\...`，文件不存在。
  用 `ExtractFileName('{#BuildDir}')` 取末段。

**运行期（ISCC 不报，装出来才炸）**

- **`[Files]` 必须 `recursesubdirs`**。onedir 的绝大部分内容在
  `_internal\`（Python 运行时、tkinter 的 tcl/tk 数据）。漏了这个
  flag 时安装器只装顶层 exe，界面能出现、点一下就闪退——因为
  `import tkinter` 找不到；
- **`AppId` 必须有**。缺了它 Inno Setup 认不出是同一个程序：装新版
  时不覆盖而是并排装第二份，开始菜单出现两个图标，卸载时互删错文件。

还有一条不算坑但容易忽略：**`installer.iss` 里的 `#define` 名字要与
`EXE_NAME` 对得上**（`BuildDir` 末段 = `EXE_NAME`）。对不上时 ISCC
**不会报错**——它照抄 `[Files]` 的通配路径，装出一个缺文件的安装器，
用户双击闪退才发现。

### CI 上验证安装器的坑（PowerShell 侧）

这一组是 Windows runner 上真实踩出来的，本地（Linux）一个都测不到：

- **静默安装不能靠 `$LASTEXITCODE`**。安装器是 GUI 程序，
  `& $setup /VERYSILENT` 会立即返回，`$LASTEXITCODE` **从不被赋值**，
  下一行判空就当成失败。要用 `Start-Process -Wait -PassThru` 读
  `.ExitCode`。不过 **Inno Setup 提权时会 fork 自己**，父进程立即退出、
  子进程等 UAC —— 无人值守的 CI 上没人点那个框，就是永久挂起。所以
  最终方案是加 `/CURRENTUSER` 装到 `%LOCALAPPDATA%\Programs`，整条链
  不需要提权；
- **PowerShell 的续行符是反引号 `` ` `` 不是反斜杠**。写成 `\` 的话
  每行被当成独立命令，报错是
  `The term '-ArgumentList' is not recognized`；
- **卸载不能用 `-Wait`**。卸载器若卡住会永远不返回。改成轮询 + 超时，
  超时就杀掉并报错；
- **`Stop-Process` 要按进程名杀，不只是那个 PID**。PyInstaller 可能
  留子进程，残留会让卸载器一直等着程序关闭；
- **`tools/check_installer.py` 自己会崩在 Windows 编码上**。它打印
  中文，而 Windows 控制台默认 cp1252（PowerShell）/ cp936（cmd.exe），
  一句 `print` 就抛 `UnicodeEncodeError`。已在脚本里强制 UTF-8 输出。

这些坑的共同点是**失败方式恶劣**：不是报错，而是挂死或误判。所以
每一条都配了测试或检查器断言。

### 查段不能靠 `text.split("[Files]")`

解析 `installer.iss` 时有个反复踩的坑：**段名会出现在注释里**。

本项目 `[Code]` 段的注释写了一句「照抄 `[Files]` 的写法」，于是
`text.split("[Files]")[-1]` 取到的是**文件最后一段**（`[Code]`）的
内容——`recursesubdirs` 检查跑去 Pascal 代码里找，报出一个和真实
原因毫无关系的失败（"[Files] 缺 recursesubdirs"，而它明明在）。

正确做法是只认**独占一行**的段名：

```python
match = re.search(rf"^\[{re.escape(name)}\][ \t]*$", text, re.MULTILINE)
```

`tools/check_installer.py` 和 `tests/test_packaging.py` 里各有一份
`_section()`，都这么写。注释里**保留**段名是可以的（甚至刻意留着
——`[UninstallDelete]` 那条断言要靠注释里的「刻意不删」），只要不
拿它当定位依据。

同理，断言某条指令在不在时要看**指令行**，别整段文本搜索：
`[Files]` 的注释里把「recursesubdirs 一定要开」写了一遍，删掉真正的
指令测试照样绿——注释替它作证。

### 别写 `[Components]`，除非真给每条 `[Files]` 加了 `Types:`

定义了组件选择页却没给 `[Files]` 加 `Types:` 限定时，用户勾来勾去
对文件毫无影响——一个假装能选、其实不能选的选项比没有更糟。GUI 版
只有一个 exe 加一个 ffmpeg，没有可拆的部分，索性不设。

### 验证要装一遍，不能只编译

CI 里安装器的验证是「编译 → 静默安装 → 启动 → 卸载」全跑一遍，
不是只跑 ISCC。只编译的话，「装完双击闪退」「`_internal` 没装进去」
这类问题只能等用户遇到才知道。

顺带一个实测结论：**PyInstaller 的 `--clean` 只清 `build\` 缓存，
不会删 `dist\` 里已有的产物**。所以同一次CI 里可以先打 onefile 版
再打 onedir 版，两份产物共存——我一度以为会互相覆盖，实测才发现不会。

### 打包相关的测试

`tests/test_packaging.py` 不真跑 PyInstaller（太慢、且需要 Windows），
而是把「构建脚本 ↔ spec ↔ installer.iss ↔ 代码」之间的**约定**钉死：
`INSTALLER` 开关真的改变形态、`EXE(` 只出现一次（防止改样式只改一处）、
冲突组合被拒绝、ffmpeg 在 onedir 下命中 exe 同目录且优先于系统 PATH、
ffmpeg 不该出现在 `_internal`、`BuildDir` 与 `EXE_NAME` 同名、
缺 `recursesubdirs` 会报错、卸载不删用户数据。

`tools/check_installer.py` 补一层静态检查（段名拼错、`#define`
未定义、BOM 缺失）。注意它得跳过 `[Code]` 段——那里是 Pascal，
不是 `Key=Value`；`AppId={{GUID}}` 的 `{{` 也是转义写法，正则当不配对
花括号会误报。

## 复杂度豁免

`pylama.ini` 里对两处放宽了 `max-complexity`：

- `client.py:_request`（复杂度 13）—— 统一处理重放与错误归一化，
  拆开反而要跨函数传 8 个以上状态
- `upload.py:upload_video`（复杂度 17）—— 分片上传的重试、续传、合并
  交织在一起，保持单函数能一眼读完

阈值设成 20 而不是更高，是为了保留告警能力：新增更复杂的函数仍会报。
