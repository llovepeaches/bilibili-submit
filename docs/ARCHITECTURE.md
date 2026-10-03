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

## 复杂度豁免

`pylama.ini` 里对两处放宽了 `max-complexity`：

- `client.py:_request`（复杂度 13）—— 统一处理重放与错误归一化，
  拆开反而要跨函数传 8 个以上状态
- `upload.py:upload_video`（复杂度 17）—— 分片上传的重试、续传、合并
  交织在一起，保持单函数能一眼读完

阈值设成 20 而不是更高，是为了保留告警能力：新增更复杂的函数仍会报。
