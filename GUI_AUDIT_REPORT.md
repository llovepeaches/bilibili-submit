# bilibili-submit 客户端 GUI 审计报告

> 审计对象：`bilibili_submit/ui/` 层（对应 `bilibili-submit-setup.exe` 桌面客户端）
> 审计方式：通读核心层（`theme / layout / state / app / widgets / upload`）+ 探索代理逐字审计其余 13 个 GUI 文件 + 对每条结论按 `file:line` 人工复核（含代码片段）
> 审计范围：**仅做梳理与报告**，未改动任何代码。文末给出改动范围与验证方式，待确认后进入实施阶段。

---

## 0. 架构与交互总览

客户端是一个分层清晰的 Tkinter 桌面应用，UI 层承担了「界面编排 + 后台线程调度 + 偏好持久化」三块职责。

| 层级 | 文件 | 职责 |
|---|---|---|
| 主题唯一来源 | `theme.py` (964 行) | 色板/字体/尺寸常量 + 完整 WCAG 推理；浅/深各一套，语义色成对 |
| 响应式断点 | `layout.py` (167 行) | `tier_for / layout_for / column_widths` 纯函数（900 / 1280 两道闸） |
| 偏好持久化 | `state.py` (318 行) | `BatchUIState / AppUIState` + 容错读写（原子写、损坏/版本区分） |
| 主框架 | `app.py` (641 行) | 状态条 / 侧栏 / 内容区 / 响应式；`launch()` DPI 感知 + 居中 |
| 组件库 | `widgets.py` (2515 行) | `Card / ScrollArea / FormRow / MaterialButton / ActionBar` 等 |
| 页面 | `views/*.py` (10 个) | 登录 / 单文件投稿 / 批量投稿 / 历史 / 设置 |
| 后台线程 | `workers.py` (195 行) | `BackgroundWorker` + `safe_after` 主线程回调调度 |

**交互骨架（设计纪律，已被代码遵守）：**
1. 组件不写死颜色/尺寸字面量，全从 `theme` 取；
2. `FormRow` 控件必须以 `body` 为父容器；
3. 视图 `apply_layout` 只做几何、**不刷数据**（防拖窗口重扫目录）；
4. 同一时刻只一页留在 grid（`grid_remove` 而非 `grid_forget`）；
5. `safe_after` 必须包住 `after` 注册本身；
6. 后台 Worker 回调做**世代校验**防过期结果覆盖。

**主交互流程：**
- 登录：扫码 → 轮询 → 成功补全设备指纹 → 写 cookie。
- 单文件投稿：选文件 → 填元信息 → `ActionBar` 实时显示校验 → 投稿/试投。
- 批量投稿：选「文件夹」或「YAML」源 → 扫描 → 表格 → 勾选 → 统一参数 → 执行（跨线程）。
- 历史 / 设置：只读展示 / 偏好修改。

---

## 1. 视觉设计与用户体验问题（A 类）

### A1 · 加载态无取消入口，且注释自相矛盾
- **位置**：`tasks.py:1257-1280` `_set_loading`
- **现象**：加载期间 `_set_loading(True)` 把「重试 / 选择 / 加载 / 配置」全部禁用，并用 `ActionBar.block("正在扫描文件夹…")` 锁死主按钮。但真正的加载 worker 是支持 `is_cancelled()` 取消的，**界面却没有任何入口触发取消**。
- **矛盾点**：注释写「加载时点主按钮没有意义（任务列表还是上一批）」——这恰恰说明用户面对一个慢扫或卡死的文件夹（深层子目录、网络盘）时，只能干等或关窗。
- **建议**：加载态镜像运行态，把主按钮变成「取消」并接 `worker.cancel()`；或在状态条旁加一个轻量取消按钮。至少要让「卡住」可恢复。

### A2 · 空文件夹：占位图与操作条文案矛盾（且占位图根本不出现）
- **位置**：`tasks.py:839-849` `_on_loaded` 空分支
```python
if not tasks:
    self._source_pill.set("没有可投稿的任务", "warn")
    self._log.append("没有找到可投稿的任务")
    return                      # ← 直接 return，从未调用 _on_table_filled(False)
```
- **现象**：当 `_on_loaded` 收到**空列表**（加载成功但无任务）时，代码只设了 pill 和日志就 `return`，**从不调用 `_on_table_filled(False)`**。而「选择视频文件夹」占位图正是由 `_on_table_filled(False)` 负责显示的（`tasks.py:964-973`）。
- **结果**：占位图永远不出现，列表区仍停留在上一次的隐藏态——用户既看不到列表，也看不到「去选文件夹」的引导，只剩一句前后矛盾的提示。
- **关键边界（不要踩）**：`test_empty_dir_keeps_previous_tasks` 测的是**另一种**空目录——加载**失败**（`tasks_editor.py:1457` 抛 `ConfigError("目录里没有找到视频文件：{directory}")`）走 `_on_load_error` 保留上一批。A2 针对的是「加载成功但返回空」的场景，**修复时绝不能破坏「加载失败保留上一批」的有意行为**（见 B4 / Part 4 守门）。
- **建议**：空分支改为 `self._on_table_filled(False)` 并给出一致文案（如「这个文件夹里没有可被投稿的视频文件」）。

### A3 · 登录超时 / 二维码失效，却一律报「获取二维码失败」
- **位置**：`login.py:275-286` `_on_error`
```python
self._status.configure(text="获取二维码失败", foreground=theme.DANGER)
self._log.append(f"错误：{exc}")
```
- **现象**：`_on_error` 对所有非取消异常（包括 `login.py:231` 登录超时、`login.py:227` 二维码已失效）**统一**显示「获取二维码失败」。但超时/失效时二维码早就生成了，文案与事实不符，用户会去排查「网络怎么了」而非「重新点一下获取」。
- **建议**：把 `exc` 携带的可读原因（含 hint）直接上状态标签，至少区分「二维码已失效 / 已超时 / 获取失败」三态。

### A4 · 登录页主体未包 `ScrollArea`
- **位置**：`login.py:84-115` `_build_body`（全文件 grep 无 `ScrollArea`）
- **现象**：其余页面都遵循「`Card` + `ScrollArea`」模式，登录页却把 `Card` 直接 `grid` 到视图、`_build_body` 内也不包裹滚动区。窗口压矮时二维码卡片被裁切且无滚动。
- **建议**：登录主体同样套一层 `ScrollArea`，与其它页面保持一致。

### A5 · 历史 Treeview 高度写死 `height=12`
- **位置**：`history.py:122` `ttk.Treeview(..., height=12)`
- **现象**：行数固定 12，既不会随高窗放大（浪费空间），在矮窗里也可能溢出。其它视图普遍跟随容器尺寸。
- **建议**：按容器可用高度换算行数（绑定 `<Configure>` 或 `pack_propagate(False)` + 相对高度），让表格随窗口伸缩。

---

## 2. 功能缺陷、异常处理与边界（B / C 类）

### B1 · 批量页缺失标题 / 简介 / 标签长度校验（与单文件页不一致）
- **位置**：`tasks.py:140-159` `SharedSubmitValues.apply`（无任何长度校验）；`tasks.py:1056-1067` `_collect_shared`（仅解析不校验）；`tasks_run.py:188-194` `snapshot_run`（只校验「转载缺来源」）
- **对比**：单文件页 `upload.py:731,734` **做了**完整校验：
```python
if len(title) > MAX_TITLE:   # 80
    raise BiliError(f"标题超过 {MAX_TITLE} 字…")
if len(desc) > MAX_DESC:     # 2000
    raise BiliError(f"简介超过 {MAX_DESC} 字…")
```
- **后果**：批量页统一参数里标题可超 80、简介可超 2000、标签可超 10，提交后服务端逐条打回（21015 / 21018 等），整批一片红——正是当初在批量页开头拦「转载缺来源」想避免的体验。
- **修复方向**：把 `upload.py` 的校验抽成共享函数（`ui/validation.py`），两页共用，消除不一致（与 C4 合并）。

### B2 · `_save_state` 忙时静默丢弃保存，并吞掉异常
- **位置**：`tasks.py:715-727` `_save_state`
```python
def _save_state(self) -> None:
    state = self._current_state()
    try:
        worker = self._state_worker
        if worker.running:
            return                 # ← 直接丢弃本次保存
        worker.run(lambda _r, _c: save_ui_state(state, self._state_file),
                   on_error=lambda exc: self._log.append(f"偏好保存失败：{exc}"))
    except RuntimeError:
        pass                      # ← 吞掉任意 RuntimeError
```
- **后果**：(a) 若前一次偏好保存还没跑完，本次 `return` 把最新状态丢了——用户快速改动（勾选、换源）后看到的「已保存」可能是旧值；(b) `except RuntimeError: pass` 把一切运行期异常静默吞掉，真正的保存故障无迹可寻。
- **修复方向**：做「合并保存」——用一个待保存队列，worker 空闲时取**最新**一份写；失败要记日志但不能无限重试；移除无差别 `except RuntimeError`。

### B3 · 定时发布（延时）只校验「是否为数字」，无上限校验
- **位置**：`tasks.py:1482-1493` `_parse_offset`；`upload.py:700-703` 同样只做 `float()` 解析
```python
try:
    return float(raw)
except ValueError as exc:
    raise BiliError(f"延时发布小时数不是数字: {raw}") from exc
```
- **后果**：两页都只拦「非数字」，不拦「超范围」。平台仅允许未来一个有限窗口内定时（常见上限 4 小时），超范围会服务端拒绝，用户只能在整批失败后回来看日志。
- **修复方向**：在解析后加 `0 < offset <= 平台上限` 校验（上限以接口文档为准，先按 4 小时实现并标注待核），与 B1 共享校验函数（C4）。

### B4 · `_on_table_filled(False)` 是死代码（A2 的根因）
- **位置**：`tasks.py:964-973` 方法定义；调用点仅在 `tasks.py:497`（`TaskTable` 构造时的 `on_filled` 回调）
- **现象**：`_on_table_filled` 的 `has_rows=False` 分支负责切到「没有任务」占位图。但表格填充只在 `_fill_tree`（`tasks.py:855`，要求 `tasks` 非空）内触发；而 `_on_loaded` 在 `tasks` 为空时已 `return`，**永远走不到**空结果填充。于是「空结果显示占位图」这条路径从未被激活——正是 A2 看不到占位图的根本原因。
- **修复方向**：与 A2 一并修复，在空分支显式调用 `_on_table_filled(False)`。

### B5 · 登录 worker 线程跨线程写 `self._qr_url`
- **位置**：`login.py:188-202` `_login_flow`（工作线程执行）`self._qr_url = qr.url`，主线程 `login.py:184` `_on_pending_qr` 读 `self._qr_url`
- **现象**：worker 线程直接写视图实例属性、主线程再读。虽是普通 Python 属性（非 Tk 变量）在 GIL 下读一般不出错，但**违反「后台线程只经 `report`/`safe_after` 回传、不碰视图状态」的设计纪律**，是潜在竞态隐患，且让 `qr.url` 的传递路径不透明。
- **修复方向**：把 `qr.url` 作为 `report(self._PENDING_QR, url)` 的一个值参数回传，主线程在回调里取，而非藏在 `self` 上。

### C1 · `safe_after` 只捕获 `TclError / RuntimeError`，回调内其它异常逃逸无堆栈
- **位置**：`workers.py:170-195` `safe_after`
```python
def wrapped() -> None:
    try:
        fn(*args)
    except (tk.TclError, RuntimeError):   # ← 只拦这两类
        pass
```
- **后果**：若回调 `fn` 内部抛 `ValueError / KeyError / AttributeError` 等，会直接逃逸到 stderr 且**无 traceback**——典型表现就是「界面永远停在执行中、既不能重试也不能取消」，且事后查日志一无所获。这正是代码注释自己警告过的「整条线程挂掉」之外的另一种静默死。
- **修复方向**：在 `wrapped` 内对 `fn(*args)` 捕获 `Exception` 并打印 `traceback`，仅保留对**注册阶段**异常的静默（窗口已关/循环已停），执行异常必须可见。

### C2 · 加载失败引导不完整
- **位置**：`tasks.py:876-888` `_on_load_error`
- **现象**：加载失败保留上一批（有意），但只写 `加载失败：{exc}`，没告诉用户下一步（重选目录 / 检查路径权限 / 看 YAML 语法）。
- **建议**：在 pill 旁补一句可操作指引（保留上一批的前提下）。

### C3 · 加载取消入口缺失 —— 与 A1 同源
已并入 A1，不重复列项。

### C4 · 输入校验缺口汇总 —— 与 B1 / B3 同源
建议落地为「共享校验函数」，详见 B1 / B3 修复方向。

### C5 · 设置页主题回填 —— 已确认安全（非缺陷）
- **位置**：`settings.py:315` 附近主题回填
- **结论**：`parse_theme_mode` 有防护，回填安全，**无需改动**。列出仅为避免后续审查重复踩点。

---

## 3. 问题清单速览（按严重度）

| 编号 | 类别 | 严重度 | 一句话 | 位置 |
|---|---|---|---|---|
| B1 | 功能 | 🔴 高 | 批量页漏标题/简介/标签长度校验 | `tasks.py:140` `apply` / `1056` `_collect_shared` / `tasks_run.py:188` |
| B2 | 功能/数据 | 🔴 高 | `_save_state` 忙时丢保存 + 吞异常 | `tasks.py:715` |
| C1 | 异常 | 🔴 高 | `safe_after` 回调异常静默逃逸 | `workers.py:170` |
| B5 | 线程安全 | 🟠 中 | worker 跨线程写视图属性 | `login.py:202` |
| A1/C3 | UX | 🟠 中 | 加载态不可取消 | `tasks.py:1257` |
| A2/B4 | UX/逻辑 | 🟠 中 | 空结果占位图不出现 | `tasks.py:839` / `964` |
| A3 | UX | 🟠 中 | 登录超时/失效误报「获取失败」 | `login.py:275` |
| B3 | 功能 | 🟡 低 | 定时发布无上限校验 | `tasks.py:1482` / `upload.py:700` |
| A4 | UX | 🟡 低 | 登录页未包 ScrollArea | `login.py:84` |
| A5 | UX | 🟡 低 | 历史 Treeview 高度写死 | `history.py:122` |
| C2 | UX | 🟡 低 | 加载失败无操作指引 | `tasks.py:876` |
| C5 | — | ✅ 无 | 主题回填安全 | `settings.py:315` |

---

## 4. 改动范围与验证方式

### 4.1 改动范围（若进入实施）

| 文件 | 涉及项 | 改动性质 |
|---|---|---|
| `ui/views/tasks.py` | B1（加长度校验）、B2（合并保存）、B3（`_parse_offset` 上限）、A1/A2/B4（`_set_loading` 取消 + 空分支 `_on_table_filled(False)` 接线） | 核心修复 |
| `ui/workers.py` | C1（`safe_after` 捕获 `Exception` + 打印 traceback） | 容错增强 |
| `ui/views/login.py` | A3（错误文案分态）、A4（包 ScrollArea）、B5（qr.url 经 `report` 回传） | UX + 线程安全 |
| `ui/views/history.py` | A5（Treeview 高度随容器） | 响应式 |
| `ui/views/upload.py` | B3（共用上限校验） | 共享化 |
| `ui/validation.py`（**新增**） | 抽出 `_validate_submit_values`（标题/简介/标签长度、定时范围） | 两页共用，消除 B1/B3 不一致 |

### 4.2 必须守住的纪律与守门测试
- **不得破坏 `test_empty_dir_keeps_previous_tasks`**：加载**失败**（抛 `ConfigError`）必须继续「保留上一批」。A2 的修复只对「加载成功但返回空」生效，二者分支严格区分。
- 继续遵守 6 条设计纪律（主题单源、FormRow 父容器、`apply_layout` 只做几何、单页在 grid、`safe_after` 包住注册、worker 世代校验）。
- 组件不写死字面量；新增校验函数仍从 `theme` 取展示常量。

### 4.3 验证方式
**单元测试（新增 / 补强）**
- `_parse_offset`：非数字→抛 `BiliError`；空串→`None`；`>上限`→抛 `BiliError`（B3）。
- `SharedSubmitValues` / 新 `validation`：标题 >80、简介 >2000、标签 >10 → 拒绝；合法 → 通过（B1）。
- 空结果加载：触发 `_on_loaded([])` 后断言占位图出现且文案一致（A2/B4）。
- `_save_state` 合并：连续快速改状态，断言「最终态」被落盘、无静默丢失（B2）。
- `safe_after`：回调内抛非 `TclError` 异常时，断言被记录、界面不被卡死（C1）。
- 登录错误分态：超时 / 失效 / 获取失败 三态文案断言（A3）。
- 登录主体短窗几何：断言 `ScrollArea` 存在并可滚动（A4）。
- 历史树高度：放大/缩小窗断言行数随容器变化（A5）。

**回归 & 全量**
- `pytest` 现有套件（此前基线 700 passed / 2 skipped）必须全绿，重点盯 `test_empty_dir_keeps_previous_tasks`。
- 手动验收：在 COMPACT(≤900) / MEDIUM / WIDE(≥1280) 三档宽度逐一走通登录、单文件、批量（文件夹 + YAML）、历史、设置五条流程，验证响应式与取消/占位/错误指引。

---

### 下一步
以上为**纯审计报告**，未修改任何代码。确认后我可按 4.1 的范围分文件落地修复，并补齐 4.3 的测试与回归。是否需要我进入实施阶段？
