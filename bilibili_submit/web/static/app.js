/* bilibili-submit Web 界面。无框架、无构建链。
   事件流用原生 EventSource：断线浏览器会自动重连，
   比自己写轮询省事，也不会漏掉「已完成」那条。 */

const $ = (id) => document.getElementById(id);

let state = {
  tasks: [],        // 预览出来的稿件列表
  tids: [],
  source: null,     // 当前的 EventSource
  jobId: null,
};

/* ---------- 通用 ---------- */

function toast(message, bad = false) {
  const el = $('toast');
  el.textContent = message;
  el.classList.toggle('is-bad', bad);
  el.hidden = false;
  clearTimeout(el._timer);
  el._timer = setTimeout(() => { el.hidden = true; }, bad ? 6000 : 3500);
}

async function api(url, options) {
  const resp = await fetch(url, options);
  const data = await resp.json().catch(() => ({ ok: false, error: '返回不是 JSON' }));
  if (!resp.ok || data.ok === false) {
    throw new Error(data.error || `请求失败（HTTP ${resp.status}）`);
  }
  return data;
}

function post(url, body) {
  return api(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body || {}),
  });
}

/* ---------- 导航 ---------- */

document.querySelectorAll('.nav-item').forEach((btn) => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.nav-item').forEach((b) => b.classList.remove('is-active'));
    document.querySelectorAll('.page').forEach((p) => p.classList.remove('is-active'));
    btn.classList.add('is-active');
    $(`page-${btn.dataset.page}`).classList.add('is-active');
    if (btn.dataset.page === 'history') loadHistory();
  });
});

/* ---------- 状态 ---------- */

async function loadStatus() {
  try {
    const s = await api('/api/status');
    state.tids = s.tids || [];
    const sel = $('c-tid');
    sel.innerHTML = '';
    for (const t of s.tids) {
      const opt = document.createElement('option');
      opt.value = t.id;
      opt.textContent = `${t.id} · ${t.name}`;
      if (t.id === 21) opt.selected = true;
      sel.appendChild(opt);
    }
    const login = $('pill-login');
    login.textContent = s.logged_in ? '登录状态：已登录' : '登录状态：未登录';
    login.className = 'pill ' + (s.logged_in ? 'is-ok' : 'is-bad');
    const ff = $('pill-ffmpeg');
    ff.textContent = s.ffmpeg_ready ? `ffmpeg：就绪（${s.ffmpeg}）` : 'ffmpeg：未找到（仅影响自动抽帧）';
    ff.className = 'pill ' + (s.ffmpeg_ready ? '' : 'is-bad');
  } catch (err) {
    toast(`读取状态失败：${err.message}`, true);
  }
}

/* ---------- 目录浏览 ---------- */

async function openDir(path) {
  try {
    const data = await api('/api/browse?path=' + encodeURIComponent(path));
    $('dir-path').value = data.path;
    $('browser').hidden = false;
    $('browser-path').textContent = data.path;
    $('browser-count').textContent = `${data.dirs.length} 个子目录 · ${data.files.length} 个视频`;
    $('browser-truncated').hidden = !data.truncated;

    const list = $('browser-list');
    list.innerHTML = '';
    for (const d of data.dirs) {
      const li = document.createElement('li');
      li.className = 'dir';
      li.textContent = d.name;
      li.addEventListener('click', () => openDir(d.path));
      list.appendChild(li);
    }
    for (const f of data.files) {
      const li = document.createElement('li');
      li.className = 'file';
      const mb = (f.size / 1048576).toFixed(1);
      li.textContent = `${f.name}（${mb} MB）`;
      list.appendChild(li);
    }
  } catch (err) {
    toast(err.message, true);
  }
}

$('btn-browse').addEventListener('click', () => openDir($('dir-path').value || ''));
$('btn-up').addEventListener('click', async () => {
  try {
    const data = await api('/api/browse?path=' + encodeURIComponent($('dir-path').value || ''));
    if (data.parent) openDir(data.parent);
  } catch (err) {
    toast(err.message, true);
  }
});

/* ---------- 扫描 ---------- */

async function scan() {
  const dir = $('dir-path').value.trim();
  if (!dir) { toast('先选一个目录', true); return; }
  try {
    const data = await post('/api/preview', { dir, group_by: $('group-by').value });
    state.tasks = data.tasks;
    renderTasks();
    $('tasks').hidden = false;
    toast(`扫到 ${data.tasks.length} 个稿件`);
  } catch (err) {
    toast(err.message, true);
  }
}
$('btn-scan').addEventListener('click', scan);

function renderTasks() {
  const box = $('tasks-list');
  box.innerHTML = '';
  state.tasks.forEach((task, i) => {
    const row = document.createElement('div');
    row.className = 'task';
    row.dataset.index = String(i);

    const check = document.createElement('input');
    check.type = 'checkbox';
    check.checked = true;
    check.addEventListener('change', () => row.classList.toggle('is-off', !check.checked));

    const title = document.createElement('input');
    title.type = 'text';
    title.value = task.title;
    title.addEventListener('input', () => { task.title = title.value; });

    const parts = document.createElement('span');
    parts.className = 'parts';
    parts.textContent = task.parts > 1 ? `${task.parts} 个分P` : '';
    parts.title = (task.files || []).join('\n');

    row.append(check, title, parts);
    box.appendChild(row);
  });
  $('tasks-count').textContent = String(state.tasks.length);
}

$('btn-select-all').addEventListener('click', () => {
  document.querySelectorAll('#tasks-list input[type=checkbox]').forEach((c) => { c.checked = true; });
});
$('btn-select-none').addEventListener('click', () => {
  document.querySelectorAll('#tasks-list input[type=checkbox]').forEach((c) => { c.checked = false; });
});

/* ---------- 投稿参数 ---------- */

function collectCommon() {
  const num = (id, dflt) => {
    const raw = $(id).value.trim();
    if (!raw) return dflt;
    const v = Number(raw);
    return Number.isFinite(v) ? v : dflt;
  };
  const dtimeRaw = $('c-dtime').value.trim();
  return {
    tid: Number($('c-tid').value) || 21,
    desc: $('c-desc').value,
    tag: $('c-tag').value,
    cover: $('c-cover').value.trim(),
    dtime_offset_hours: dtimeRaw === '' ? null : Number(dtimeRaw),
    interval_minutes: num('c-interval', 0),
    up_close_reply: $('c-close-reply').checked,
    up_close_danmu: $('c-close-danmu').checked,
    up_selection_reply: $('c-selection').checked,
    dolby: $('c-dolby').checked ? 1 : 0,
    hires: $('c-hires').checked ? 1 : 0,
    stop_on_error: $('c-stop').checked,
    copyright: 1,
    source: '',
  };
}

/* 整份列表回传（含未勾选的）：服务端要按它在完整列表里的下标回报
   进度，只传勾选的话行号会对不上。 */
function collectTasks() {
  const rows = document.querySelectorAll('#tasks-list .task');
  rows.forEach((row) => {
    const check = row.querySelector('input[type=checkbox]');
    state.tasks[Number(row.dataset.index)].selected = !!(check && check.checked);
  });
  return state.tasks.map((t) => ({
    name: t.name, files: t.files, title: t.title, selected: t.selected !== false,
  }));
}

/* ---------- 执行 ---------- */

async function startRun(dryRun) {
  const tasks = collectTasks();
  if (!tasks.length) { toast('先扫描一个目录', true); return; }
  if (!tasks.some((t) => t.selected)) { toast('至少勾选一个稿件', true); return; }
  try {
    const data = await post('/api/jobs', {
      tasks, common: collectCommon(), dry_run: dryRun,
    });
    state.jobId = data.job.id;
    $('run-card').hidden = false;
    $('log').textContent = '';
    $('progress-bar').style.width = '0%';
    $('btn-cancel').disabled = false;
    $('btn-run').disabled = true;
    listenEvents(data.job.id);
  } catch (err) {
    toast(err.message, true);
  }
}

$('btn-run').addEventListener('click', () => startRun(false));
$('btn-dry').addEventListener('click', () => startRun(true));
$('btn-cancel').addEventListener('click', async () => {
  if (!state.jobId) return;
  try {
    await post(`/api/jobs/${state.jobId}/cancel`, {});
    toast('已请求取消（当前任务会跑完）');
  } catch (err) {
    toast(err.message, true);
  }
});

function listenEvents(jobId) {
  if (state.source) state.source.close();
  const source = new EventSource(`/api/jobs/${jobId}/events`);
  state.source = source;

  source.addEventListener('open', () => toast('已连接'));

  source.onmessage = (evt) => {
    const data = JSON.parse(evt.data);
    if (data.type === 'log') {
      appendLog(data.message);
      return;
    }
    if (data.type === 'event') {
      applyTaskEvent(data);
      $('progress-bar').style.width = `${data.percent}%`;
      $('run-state').textContent = `${data.position}/${data.total} · ${data.status}`;
      return;
    }
    if (data.type === 'end') {
      source.close();
      state.source = null;
      $('btn-cancel').disabled = true;
      $('btn-run').disabled = false;
      const text = { done: '完成', cancelled: '已取消', error: '出错' }[data.state] || data.state;
      $('run-state').textContent = data.error ? `${text}：${data.error}` : text;
      if (data.state === 'error') toast(data.error, true);
      loadStatus();
      loadHistoryIfVisible();
    }
  };

  // EventSource 的默认行为是断线自动重连。已经结束的任务重连只会
  // 拿到空流然后再次超时，所以出错时直接关掉。
  source.onerror = () => { source.close(); state.source = null; };
}

function appendLog(message) {
  const log = $('log');
  log.textContent += message + '\n';
  log.scrollTop = log.scrollHeight;
}

function applyTaskEvent(data) {
  const row = document.querySelector(`#tasks-list .task[data-index="${data.index}"]`);
  if (!row) return;
  if (data.kind === 'done') {
    row.classList.remove('is-done', 'is-failed');
    row.classList.add(data.succeeded ? 'is-done' : 'is-failed');
  }
}

/* ---------- 登录 ---------- */

let pollTimer = null;

async function startLogin() {
  stopPolling();
  try {
    const data = await post('/api/login/start', {});
    $('qr').innerHTML = data.svg || '（未安装 qrcode，无法绘制二维码，请手动复制链接）';
    $('login-url').textContent = data.url || '';
    $('login-state').textContent = '等待扫码（扫完要在手机上点确认）';
    pollTimer = setInterval(pollLogin, 2000);
  } catch (err) {
    $('login-state').textContent = err.message;
    toast(err.message, true);
  }
}

async function pollLogin() {
  try {
    const data = await api('/api/login/status');
    $('login-state').textContent = data.message || data.state;
    if (data.state === 'scanned') {
      $('login-state').textContent = '已扫码，请在手机上点「确认登录」';
    }
    if (data.state === 'done') {
      stopPolling();
      $('login-state').textContent = '登录成功';
      loadStatus();
      toast('登录成功');
      return;
    }
    if (data.state === 'expired') {
      stopPolling();
      $('login-state').textContent = '二维码已失效，正在换新…';
      startLogin();   // 自动换一个，用户不用再点一次
      return;
    }
    if (data.state === 'failed') {
      stopPolling();
      toast(data.error || '登录失败', true);
    }
  } catch (err) {
    stopPolling();
    toast(err.message, true);
  }
}

function stopPolling() {
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
}

$('btn-login-start').addEventListener('click', startLogin);

/* ---------- 历史 ---------- */

function loadHistoryIfVisible() {
  if ($('page-history').classList.contains('is-active')) loadHistory();
}

async function loadHistory() {
  const box = $('history');
  try {
    const data = await api('/api/history');
    box.innerHTML = '';
    if (data.error) {
      box.innerHTML = `<p class="warn">${escapeHtml(data.error)}</p>`;
      return;
    }
    if (!data.entries.length) {
      box.innerHTML = '<p class="muted">还没有投稿记录。</p>';
      return;
    }
    for (const e of data.entries.slice().reverse()) {
      const item = document.createElement('div');
      item.className = 'item';
      const when = e.time ? new Date(e.time * 1000).toLocaleString('zh-CN') : '?';
      const parts = e.parts > 1 ? `（${e.parts} 个分P）` : '';
      item.innerHTML =
        `<span class="when">${when}</span>` +
        `<span>${escapeHtml(e.name || '')}${parts}</span>` +
        (e.url ? `<a href="${escapeHtml(e.url)}" target="_blank" rel="noreferrer">${escapeHtml(e.bvid || '打开')}</a>` : '');
      box.appendChild(item);
    }
  } catch (err) {
    box.innerHTML = `<p class="warn">${escapeHtml(err.message)}</p>`;
  }
}

function escapeHtml(text) {
  const div = document.createElement('div');
  div.textContent = String(text);
  return div.innerHTML;
}

/* ---------- 启动 ---------- */

loadStatus();
openDir('');
