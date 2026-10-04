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
| `submit.py` | 稿件投递、`601` 退避重试 | 解析配置文件 |
| `config.py` | 配置加载与严格校验、任务展开 | 网络请求 |
| `scheduler.py` | 任务执行编排、历史记录 | 具体业务步骤 |
| `cli.py` | 参数解析、输出格式化、退出码 | 业务逻辑 |
| `console.py` | Windows 控制台 UTF-8 适配 | 任何业务 |
| `ui/` | 图形界面（tkinter）。见下方「界面层」 | 业务逻辑 |
| `ui/theme.py` | 颜色/字体/间距的唯一来源 | 具体控件 |
| `ui/widgets.py` | 可复用组件，不知道 B 站的存在 | 业务概念 |
| `ui/qr.py` | 二维码矩阵 → Canvas 绘制 | 网络请求 |
| `ui/workers.py` | 后台线程与取消 | UI 操作 |
| `ui/state.py` | 批量任务页的偏好读写（目录、默认参数） | Tk 操作 |
| `ui/environment.py` | 登录态与 ffmpeg 的纯探测，**不碰 Tk** | UI 操作 |
| `ui/views/` | 各页面：把数据画出来、把操作翻译成下层调用 | 业务逻辑 |
| `ui/app.py` | 主窗口、导航、状态栏 | 业务判断 |

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

### 窗口再小也不能让按钮点不到

投稿表单内容高约 700px，最小窗口只有 600px。早期版本直接铺控件，
结果「开始投稿」被挤出可视区——用户既看不到也点不到。
现在内容包在 `ScrollArea` 里，超出时可滚动（滚轮也支持）。

`tests/test_ui.py::test_submit_button_reachable_at_min_size` 锁住了这个行为。

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

      xvfb-run -a python -m pytest          # 227 passed / 2 skipped
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

## 复杂度豁免

`pylama.ini` 里对两处放宽了 `max-complexity`：

- `client.py:_request`（复杂度 13）—— 统一处理重放与错误归一化，
  拆开反而要跨函数传 8 个以上状态
- `upload.py:upload_video`（复杂度 17）—— 分片上传的重试、续传、合并
  交织在一起，保持单函数能一眼读完

阈值设成 20 而不是更高，是为了保留告警能力：新增更复杂的函数仍会报。
