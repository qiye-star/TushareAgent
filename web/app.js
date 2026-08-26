/* ============================================================
   Tushare · demo-mcp — AI 金融数据对话助手 前端逻辑
   纯原生 JS，对接 /chat SSE（POST + ReadableStream）。
   SSE 事件：thinking / text / tool_call / tool_result / done / error

   本版新增：
   - 自动滚动「仅在接近底部时跟随」，用户上翻不打断
   - 工具调用卡默认展开，返回数据自动表格化/键值化
   - 「思考过程」面板：reasoning_content 逐字流式 + 自动展开
   - 「深度思考」开关：请求切 deepseek-reasoner，从而真正流出 thinking
   ============================================================ */
(function () {
  'use strict';

  // ---------- DOM ----------
  const msgEl     = document.getElementById('messages');
  const input     = document.getElementById('input');
  const sendBtn   = document.getElementById('send');
  const listEl    = document.getElementById('sessionList');
  const newChat   = document.getElementById('newChat');
  const titleEl   = document.getElementById('sessionTitle');
  const statusEl  = document.getElementById('status');
  const apiKeyEl  = document.getElementById('apiKey');
  const toggleKey = document.getElementById('toggleKey');
  const deepThink = document.getElementById('deepThink');
  const sidebar   = document.getElementById('sidebar');
  const menuBtn   = document.getElementById('menuBtn');
  const closeBtn  = document.getElementById('closeBtn');
  const mask      = document.getElementById('mask');
  const modal     = document.getElementById('modal');
  const modalTitle  = document.getElementById('modalTitle');
  const modalBody   = document.getElementById('modalBody');
  const modalClose  = document.getElementById('modalClose');
  const STATUS_NAMES = { idle: '空闲', thinking: '思考中', tooling: '调用工具中', done: '完成', error: '错误' };

  // ---------- 全局状态 ----------
  let currentSession = null;
  let ctrl = null;
  let pendingTools = [];      // 等待回填的工具卡（FIFO，按 name 匹配）
  let nearBottom = true;      // 仅接近底部时自动跟随
  let toastTimer = null;

  // ---------- 工具函数 ----------
  const escapeHtml = (s) => String(s ?? '').replace(/[&<>"']/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const prettyJson = (v) => { try { return JSON.stringify(v, null, 2); } catch (e) { return String(v); } };
  const truncate = (s, n = 26) => (s && s.length > n ? s.slice(0, n) + '…' : (s || ''));
  const fmtTime = (iso) => iso ? new Date(iso).toLocaleString('zh-CN', {
    month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
  }) : '';

  const md = (t) => {
    t = t || '';
    if (window.DOMPurify) {
      let html = window.marked ? window.marked.parse(t) : escapeHtml(t);
      // 在 sanitize 之前做两处安全增强：
      // 1) 宽 markdown 表格包进横向滚动容器，避免把页面内容顶出右侧；
      // 2) GFM 任务项 <input> 换成纯视觉 span（不放开 input 标签，保持可安全 sanitize）。
      html = html
        .replace(/<table>/g, '<div class="md-table"><table>')
        .replace(/<\/table>/g, '</table></div>')
        .replace(/<li class="task-list-item"><input([^>]*)checked([^>]*)>/g,
          '<li class="task-list-item"><span class="task-box">✓</span>')
        .replace(/<li class="task-list-item"><input([^>]*)>/g,
          '<li class="task-list-item"><span class="task-box"></span>');
      return window.DOMPurify.sanitize(html);
    }
    return escapeHtml(t).replace(/\n/g, '<br>');
  };

  // marked 配置：GFM（表格/任务项）+ 宽松换行
  function setupMarkdown() {
    if (window.marked && window.marked.setOptions) {
      window.marked.setOptions({ gfm: true, breaks: true });
    }
  }

  // 本地 vendor 缺失时注入 CDN 兜底（最终仍缺则走 md 的转义分支）
  function ensureLibs() {
    if (window.marked && window.DOMPurify) { setupMarkdown(); return Promise.resolve(); }
    const load = (src) => new Promise((resolve) => {
      const s = document.createElement('script');
      s.src = src; s.onload = () => resolve(); s.onerror = () => resolve();
      document.head.appendChild(s);
    });
    const jobs = [];
    if (!window.marked) jobs.push(load('https://cdn.jsdelivr.net/npm/marked@12.0.2/marked.min.js'));
    if (!window.DOMPurify) jobs.push(load('https://cdn.jsdelivr.net/npm/dompurify@3.1.6/dist/purify.min.js'));
    if (!jobs.length) { setupMarkdown(); return Promise.resolve(); }
    return Promise.all(jobs).then(() => { setupMarkdown(); });
  }

  // ---------- 自动滚动（仅接近底部时跟随） ----------
  function updateNearBottom() {
    nearBottom = (msgEl.scrollHeight - msgEl.scrollTop - msgEl.clientHeight) < 90;
  }
  function maybeScroll() { if (nearBottom) msgEl.scrollTop = msgEl.scrollHeight; }
  function forceScroll() { nearBottom = true; msgEl.scrollTop = msgEl.scrollHeight; }
  msgEl.addEventListener('scroll', updateNearBottom, { passive: true });

  const setStatus = (state) => {
    statusEl.textContent = STATUS_NAMES[state] || STATUS_NAMES.idle;
    statusEl.dataset.state = state;
  };
  const setTitle = (t) => { titleEl.textContent = t || '新会话'; };

  // ============================================================
  //  单轮「助手堆栈」= 思考面板 + 工具卡 + 答案气泡，按事件顺序排布
  //  关键：常驻「答案槽」哨兵(answerSlot)是堆栈最后一个子节点，永不删除；
  //  思考面板/工具卡一律 insertBefore(answerSlot)，答案原地填充该槽 ——
  //  从而 DOM 顺序恒为「思考 → 工具 → 答案」，与 text/tool_call 谁先到无关。
  // ============================================================
  function makeStack() {
    const el = document.createElement('div');
    el.className = 'assistant-stack';
    const answerSlot = document.createElement('div');
    answerSlot.className = 'answer-slot';
    const skeleton = document.createElement('div');
    skeleton.className = 'skeleton';
    skeleton.innerHTML = '<i></i><i></i><i></i>';
    answerSlot.appendChild(skeleton);   // 骨架先占位在答案槽内
    el.appendChild(answerSlot);
    msgEl.appendChild(el);

    const s = {
      el, answerSlot, skeleton, think: null, answer: null,

      ensureThink() {
        if (this.think) return this.think;
        const det = document.createElement('details');
        det.className = 'think-panel live';
        det.open = false; // 默认折叠：不占屏，可展开查看推理过程
        det.innerHTML = '<summary><span class="tp-dot"></span><span class="tp-label">思考过程</span>' +
          '<span class="tp-state">推理中…</span><span class="tp-chev">▸</span></summary>';
        const body = document.createElement('div');
        body.className = 'tp-body';
        const pre = document.createElement('pre');
        pre.textContent = '';
        body.appendChild(pre);
        det.appendChild(body);
        el.insertBefore(det, answerSlot);
        this.think = { det, pre, state: det.querySelector('.tp-state'), start: performance.now() };
        return this.think;
      },
      addThinking(t) {
        this.ensureThink().pre.textContent += (t || '');
        maybeScroll();
      },
      finishThinking() {
        if (!this.think) return;
        this.think.det.classList.remove('live');
        this.think.det.classList.add('done');
        const sec = ((performance.now() - this.think.start) / 1000).toFixed(1);
        this.think.state.textContent = '已完成 · ' + sec + 's';
      },

      ensureAnswer() {
        if (this.answer) return this.answer;
        const msg = document.createElement('div');
        msg.className = 'msg assistant';
        const content = document.createElement('div');
        content.className = 'content';
        msg.appendChild(content);
        answerSlot.textContent = '';      // 清掉骨架，原地放入答案气泡（槽保持在末尾）
        answerSlot.appendChild(msg);
        const a = {
          msg, content, mdText: '', _raf: false,
          schedule() {
            if (this._raf) return;
            this._raf = true;
            requestAnimationFrame(() => { this._raf = false; this.render(); });
          },
          render() { content.innerHTML = md(this.mdText); maybeScroll(); },
          addText(t) { this.mdText += (t || ''); this.schedule(); },
          renderMd(t) { this.mdText = (t || ''); this.render(); },
        };
        this.answer = a;
        return a;
      },
      addText(t) { this.ensureAnswer().addText(t); },

      addToolCard(data) {
        const card = makeToolCard(data);
        el.insertBefore(card.el, answerSlot);   // 永远插到答案槽之前
        maybeScroll();
        return card;
      },
    };
    return s;
  }

  // ============================================================
  //  工具卡（折叠，默认收起；点开看 入参 / 返回数据）
  //  只构建不挂载，由调用方插入：addToolCard → 答案槽前；openSession → msgEl
  // ============================================================
  function makeToolCard(data) {
    const det = document.createElement('details');
    det.className = 'tool-card';
    det.open = false; // 默认折叠（用户要求取消自动展开）
    const summary = document.createElement('summary');
    summary.innerHTML =
      '<span class="tc-name">' + escapeHtml(data.name) + '</span>' +
      '<span class="tc-status loading">调用中…</span><span class="chev">▸</span>';
    det.appendChild(summary);

    const body = document.createElement('div');
    body.className = 'tc-body';
    // 入参
    body.innerHTML = '<div class="tc-label">入参</div><pre class="mono"></pre>';
    if (data.input && typeof data.input === 'object' && Object.keys(data.input).length) {
      body.querySelector('pre').textContent = prettyJson(data.input);
    } else {
      body.querySelector('pre').textContent = '（无入参信息）';
    }
    // 返回数据（tool_result 回填）
    const res = document.createElement('div');
    res.className = 'tc-res hidden';
    body.appendChild(res);
    det.appendChild(body);

    const card = {
      el: det,
      name: data.name,
      chip: summary.querySelector('.tc-status'),
      res,
      backfill(result) {
        this.chip.className = 'tc-status ' + (result.ok ? 'ok' : 'err');
        this.chip.textContent = result.ok ? '✔ 成功' : '✖ 失败';
        this.res.textContent = '';
        const label = document.createElement('div');
        label.className = 'tc-label';
        label.textContent = '返回数据';
        this.res.appendChild(label);
        this.res.appendChild(buildResultNode(result.content || ''));
        this.res.classList.remove('hidden');
        maybeScroll();
      },
    };
    return card;
  }

  // 把工具返回文本渲染成表格 / 键值表 / 原始文本
  function buildResultNode(content) {
    let data = null;
    try { data = JSON.parse(content); } catch (e) { data = null; }
    if (data && typeof data === 'object' && !Array.isArray(data) && 'data' in data) data = data.data;
    // {periods:[...]} 这类单键数组 → 直接展开为数组
    if (data && typeof data === 'object' && !Array.isArray(data)) {
      const keys = Object.keys(data);
      if (keys.length === 1 && Array.isArray(data[keys[0]])) data = data[keys[0]];
    }
    if (Array.isArray(data) && data.length && typeof data[0] === 'object') {
      return buildTable(data);
    }
    if (data && typeof data === 'object' && Object.keys(data).length) {
      return buildKvTable(data);
    }
    const pre = document.createElement('pre');
    pre.className = 'result';
    pre.textContent = content;
    return pre;
  }

  function buildTable(rows) {
    const wrap = document.createElement('div');
    wrap.className = 'tc-table';
    const cols = Object.keys(rows[0]);
    const table = document.createElement('table');
    const thead = document.createElement('thead');
    const thr = document.createElement('tr');
    cols.forEach((c) => { const th = document.createElement('th'); th.textContent = c; thr.appendChild(th); });
    thead.appendChild(thr);
    const tbody = document.createElement('tbody');
    rows.forEach((row) => {
      const tr = document.createElement('tr');
      cols.forEach((c) => {
        const td = document.createElement('td');
        const v = row[c];
        td.textContent = (v === null || v === undefined) ? '' : (typeof v === 'object' ? JSON.stringify(v) : String(v));
        tr.appendChild(td);
      });
      tbody.appendChild(tr);
    });
    table.appendChild(thead);
    table.appendChild(tbody);
    wrap.appendChild(table);
    return wrap;
  }

  function buildKvTable(obj) {
    const wrap = document.createElement('div');
    wrap.className = 'tc-table';
    const table = document.createElement('table');
    const tbody = document.createElement('tbody');
    Object.keys(obj).forEach((k) => {
      const tr = document.createElement('tr');
      const th = document.createElement('th'); th.textContent = k;
      const td = document.createElement('td');
      const v = obj[k];
      td.textContent = (v === null || v === undefined) ? '' : (typeof v === 'object' ? JSON.stringify(v) : String(v));
      tr.appendChild(th); tr.appendChild(td);
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    wrap.appendChild(table);
    return wrap;
  }

  // ---------- 错误提示：弹窗（网络/鉴权/服务）+ 轻提示（工具执行） ----------
  function showModal(title, body) {
    modalTitle.textContent = title;
    modalBody.textContent = body;
    modalBody.classList.add('err');
    modal.hidden = false;
  }
  function hideModal() { modal.hidden = true; }

  function toast(text) {
    let t = document.getElementById('toast');
    if (!t) { t = document.createElement('div'); t.id = 'toast'; document.body.appendChild(t); }
    t.textContent = text;
    t.classList.add('show');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => t.classList.remove('show'), 2600);
  }

  function notifyError(kind, message) {
    if (kind === 'network') {
      showModal('网络错误', '无法连接到后端服务。请确认 uvicorn 已启动，或检查网络。\n' + (message || ''));
    } else if (kind === 'auth') {
      showModal('鉴权错误 (401)', '后端拒绝了身份验证。请检查顶栏的 API Key 是否正确，或登录授权已过期。');
    } else if (kind === 'tool') {
      toast('工具执行失败：' + message);
    } else {
      showModal('服务错误', message || '发生未知错误');
    }
  }

  // ============================================================
  //  SSE 帧解析与分发
  // ============================================================
  function handleFrame(frame, stack) {
    let ev = 'message', dataStr = '';
    for (const line of frame.split('\n')) {
      if (line.startsWith('event:')) ev = line.slice(6).trim();
      else if (line.startsWith('data:')) dataStr += line.slice(5).trim();
    }
    if (!dataStr) return;
    let data; try { data = JSON.parse(dataStr); } catch (e) { return; }

    switch (ev) {
      case 'thinking':   stack.addThinking(data.text); setStatus('thinking'); break;
      case 'text':       stack.addText(data.text); setStatus('thinking'); break;
      case 'tool_call': {
        const card = stack.addToolCard(data);
        pendingTools.push(card);
        setStatus('tooling');
        break;
      }
      case 'tool_result': {
        const i = pendingTools.findIndex((c) => c.name === data.name);
        const idx = i >= 0 ? i : 0;
        const card = pendingTools.splice(idx, 1)[0];
        if (card) {
          card.backfill(data);
          if (!data.ok) notifyError('tool', data.name + (data.content ? '：' + data.content : ''));
        }
        setStatus('tooling');
        break;
      }
      case 'done':
        currentSession = data.session_id;
        stack.finishThinking();
        if (stack.skeleton.isConnected) stack.skeleton.remove();
        setStatus('done');
        forceScroll();
        setTitleFromFirstUser();
        refresh();
        break;
      case 'error':
        setStatus('error');
        notifyError('server', data.message);
        break;
    }
  }

  async function setTitleFromFirstUser() {
    if (!currentSession) return;
    try {
      const res = await fetch('/api/sessions/' + currentSession);
      if (!res.ok) return;
      const msgs = await res.json();
      const first = msgs.find((m) => m.role === 'user');
      setTitle(first ? truncate(first.content, 30) : '会话 ' + currentSession.slice(0, 6));
    } catch (e) { /* 静默 */ }
  }

  // ---------- 流式对话 ----------
  async function streamChat(message, stack) {
    ctrl = new AbortController();
    sendBtn.disabled = true; sendBtn.classList.add('loading');
    const apiKey = apiKeyEl.value || '';
    const model = deepThink.checked ? 'deepseek-reasoner' : undefined; // 深度思考 → 思考模型
    try {
      const res = await fetch('/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + apiKey },
        body: JSON.stringify(model ? { message, session_id: currentSession, model } : { message, session_id: currentSession }),
        signal: ctrl.signal,
      });

      if (res.status === 401) { setStatus('error'); notifyError('auth', ''); return; }
      if (!res.ok || !res.body) throw new Error('HTTP ' + res.status);

      const reader = res.body.getReader();
      const dec = new TextDecoder();
      let buf = '';
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        let i;
        while ((i = buf.indexOf('\n\n')) >= 0) {
          const f = buf.slice(0, i);
          buf = buf.slice(i + 2);
          handleFrame(f, stack);
        }
      }
      if (buf) handleFrame(buf, stack);
    } catch (e) {
      if (e.name !== 'AbortError') { setStatus('error'); notifyError('network', String(e && e.message || e)); }
    } finally {
      ctrl = null;
      sendBtn.disabled = false; sendBtn.classList.remove('loading');
      setTimeout(() => input.focus(), 0);
    }
  }

  // ---------- 会话列表 ----------
  async function refresh() {
    let list = [];
    try { const res = await fetch('/api/sessions'); list = await res.json(); }
    catch (e) { return; }
    listEl.innerHTML = '';
    if (!list.length) { listEl.innerHTML = '<li class="sb-empty">暂无会话</li>'; return; }
    list.forEach((s) => {
      const li = document.createElement('li');
      if (s.session_id === currentSession) li.classList.add('active');
      const date = document.createElement('span'); date.className = 's-date'; date.textContent = fmtTime(s.created_at);
      const cnt = document.createElement('span'); cnt.className = 's-count'; cnt.textContent = s.count + ' 条';
      const del = document.createElement('span'); del.className = 's-del'; del.textContent = '×'; del.title = '删除会话';
      del.onclick = async (e) => {
        e.stopPropagation();
        try { await fetch('/api/sessions/' + s.session_id, { method: 'DELETE' }); } catch (err) {}
        if (currentSession === s.session_id) { currentSession = null; renderEmpty(); setTitle('新会话'); }
        refresh();
      };
      li.append(date, cnt, del);
      li.onclick = () => openSession(s.session_id);
      listEl.appendChild(li);
    });
  }

  async function openSession(id) {
    let msgs = [];
    try {
      const res = await fetch('/api/sessions/' + id);
      if (!res.ok) return;
      msgs = await res.json();
    } catch (e) { return; }
    currentSession = id;
    msgEl.innerHTML = '';
    pendingTools = [];
    if (!msgs.length) { renderEmpty(); setStatus('idle'); refresh(); return; }
    msgs.forEach((m) => {
      if (m.role === 'user') {
        const b = makeUserBubble(); b.renderMd(m.content);
      } else if (m.role === 'assistant') {
        const stack = makeStack(); // 无 think/tool 时直接出答案
        stack.ensureAnswer().renderMd(m.content);
      } else if (m.role === 'tool') {
        const c = makeToolCard({ name: '工具调用', input: null });
        msgEl.appendChild(c.el);
        c.backfill({ content: m.content, ok: !m.is_error });
      } else if (m.role === 'error') {
        const b = makeErrorBubble('⚠ ' + m.content);
      }
    });
    forceScroll();
    await setTitleFromFirstUser();
    setStatus('idle');
    refresh();
  }

  // 用户气泡 / 错误气泡（独立，无堆栈）
  function makeUserBubble() {
    const msg = document.createElement('div');
    msg.className = 'msg user';
    const content = document.createElement('div');
    content.className = 'content';
    msg.appendChild(content);
    msgEl.appendChild(msg);
    return { el: msg, renderMd(t) { content.innerHTML = md(t || ''); maybeScroll(); } };
  }
  function makeErrorBubble(text) {
    const msg = document.createElement('div');
    msg.className = 'msg';
    const content = document.createElement('div');
    content.className = 'error-bubble';
    content.textContent = text;
    msg.appendChild(content);
    msgEl.appendChild(msg);
    return { el: msg };
  }

  // ---------- 空态 ----------
  // 首发消息时移除占位空态，避免全高空态把用户气泡顶到下方
  function clearEmpty() {
    const e = msgEl.querySelector('.empty-state');
    if (e) e.remove();
  }
  function renderEmpty() {
    msgEl.innerHTML = '';
    const d = document.createElement('div');
    d.className = 'empty-state';
    d.innerHTML = '<div class="es-title">Tushare 金融问答助手</div>' +
      '<p>用自然语言查询 <b>比亚迪</b> / <b>宁德时代</b> 的行情与财务数据「深度思考」可展开查看模型推理过程</p>' +
      '<div class="es-tags"><span>实时报价</span><span>区间行情</span><span>财务指标</span></div>';
    msgEl.appendChild(d);
  }

  // ---------- 发送 ----------
  function submit() {
    const m = input.value.trim();
    if (!m || sendBtn.disabled) return;
    input.value = '';
    autosize();
    clearEmpty();                       // 移除占位空态，让气泡顶到正确位置
    makeUserBubble().renderMd(m);       // 用户气泡
    const stack = makeStack();          // 助手堆栈（含骨架占位）
    forceScroll();                      // 新回合跳到这条消息
    setStatus('thinking');
    streamChat(m, stack);
  }

  // ---------- textarea 自动增高 + 快捷键 ----------
  function autosize() {
    input.style.height = 'auto';
    input.style.height = Math.min(input.scrollHeight, 168) + 'px';
  }
  input.addEventListener('input', autosize);
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); submit(); }
  });

  // ---------- 快捷示例 ----------
  document.querySelectorAll('.chip-btn').forEach((btn) => {
    btn.addEventListener('click', () => {
      input.value = btn.dataset.prompt;
      autosize();
      input.focus();
    });
  });

  // ---------- apiKey / 深度思考 localStorage ----------
  apiKeyEl.value = localStorage.getItem('demo_mcp_api_key') || '';
  apiKeyEl.addEventListener('input', () => localStorage.setItem('demo_mcp_api_key', apiKeyEl.value));
  toggleKey.addEventListener('click', () => { apiKeyEl.type = apiKeyEl.type === 'password' ? 'text' : 'password'; });
  deepThink.checked = localStorage.getItem('demo_mcp_deep_think') === '1';
  deepThink.addEventListener('change', () => localStorage.setItem('demo_mcp_deep_think', deepThink.checked ? '1' : '0'));

  // ---------- 行为绑定 ----------
  sendBtn.addEventListener('click', submit);
  newChat.addEventListener('click', () => {
    if (ctrl) ctrl.abort();
    currentSession = null;
    pendingTools = [];
    renderEmpty();
    setTitle('新会话');
    setStatus('idle');
    input.focus();
  });

  // ---------- 移动端抽屉 ----------
  function openDrawer() { sidebar.classList.add('open'); mask.hidden = false; }
  function closeDrawer() { sidebar.classList.remove('open'); mask.hidden = true; }
  menuBtn.addEventListener('click', openDrawer);
  closeBtn.addEventListener('click', closeDrawer);
  mask.addEventListener('click', closeDrawer);

  modalClose.addEventListener('click', hideModal);
  modal.addEventListener('click', (e) => { if (e.target === modal) hideModal(); });

  // ---------- 启动 ----------
  renderEmpty();
  setTitle('新会话');
  setStatus('idle');
  ensureLibs();
  refresh();
})();
