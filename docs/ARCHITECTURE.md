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
```

底层不知道上层的存在。想换投递后端不用动上传，想换配置格式不用动签名。
任一层可整体替换——`submit.py` 里的 `SubmitBackend` 抽象就是例子。

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
