/* 实例详情：8 个 Tab（控制台 / 文件 / 配置 / 玩家 / 建筑 / 备份 / 计划任务 / 设置） */
window.Pages = window.Pages || {};

Pages.instanceDetail = function (app, ctx) {
  var id = ctx.params.id;
  var tab = ctx.params.tab || 'console';
  var _tabCleanup = null;      // 切换 Tab 时清理定时器/长连接（建筑 Tab 用）
  var TABS = [
    { k: 'console', label: '控制台', icon: 'terminal' },
    { k: 'files', label: '文件', icon: 'folder' },
    { k: 'config', label: '配置', icon: 'sliders' },
    { k: 'players', label: '玩家', icon: 'users' },
    { k: 'build', label: '建筑', icon: 'building' },
    { k: 'backups', label: '备份', icon: 'backup' },
    { k: 'cron', label: '计划任务', icon: 'clock' },
    { k: 'settings', label: '设置', icon: 'settings' }
  ];
  var inst = null;

  app.innerHTML = C.shell({
    active: 'instances',
    crumbs: [{ text: '服务器', hash: '#/instances' }, { text: '实例详情', hash: '#/instances' }],
    actions: '<span id="hd-status"></span>'
  });
  C.bindShell(app);
  var body = document.getElementById('page-body');
  body.innerHTML = '<div class="loading"><span class="spinner"></span>加载实例信息…</div>';

  function draw() {
    var name = inst ? inst.name : ('#' + id);
    document.querySelector('.crumbs').innerHTML =
      '<a href="#/instances">实例</a><span class="sep">/</span><span class="cur">' + U.esc(name) + '</span>';
    document.getElementById('hd-status').innerHTML = inst ? C.statusBadge(inst.status) : '';
    body.innerHTML = '' +
      head() +
      '<div class="tabs" id="det-tabs">' + TABS.map(function (t) {
        return '<div class="tab' + (t.k === tab ? ' active' : '') + '" data-tab="' + t.k + '">' +
          Icon.svg(t.icon, 15) + U.esc(t.label) + '</div>';
      }).join('') + '</div>' +
      '<div id="tab-body"></div>';
    body.querySelectorAll('[data-tab]').forEach(function (el) {
      el.addEventListener('click', function () {
        var next = el.getAttribute('data-tab');
        if (next === tab) return;
        tab = next;
        /* 只换 tab 内容，不碰 hash 的导航语义 —— 以前是 location.hash = ...，
           会触发路由整页重渲染：切一次 tab 就要重新拉一遍实例、整页重画、
           滚动位置也没了，控制台还会闪一下。地址栏仍然同步（replaceState），
           刷新页面 / 复制链接进具体 tab 都照常。 */
        try { history.replaceState(null, '', '#/instances/' + id + '/' + tab); } catch (e) {}
        document.querySelectorAll('#det-tabs .tab').forEach(function (x) {
          x.classList.toggle('active', x.getAttribute('data-tab') === tab);
        });
        renderTab();
      });
    });
    body.querySelectorAll('[data-act]').forEach(function (b) {
      b.addEventListener('click', function () { lifecycle(b.getAttribute('data-act'), b); });
    });
    renderTab();
  }

  /* 渲染当前 tab 的内容区（不动外框）。切换前必须清干净上一个 tab 的
     定时器与长连接 —— 控制台每进一次会注册两个 interval，不清的话
     来回切几次就叠成十几个，请求和内存都涨。 */
  function renderTab() {
    Store.clearPollers();
    if (_tabCleanup) { try { _tabCleanup(); } catch (e) {} _tabCleanup = null; }
    if (!document.getElementById('tab-body')) return;
    var t = TABS.filter(function (x) { return x.k === tab; })[0] || TABS[0];
    (RENDER[t.k] || function () {})();
  }

  function head() {
    if (!inst) return '';
    return '<div class="page-head">' +
      '<div class="titles"><div class="kicker">' + U.esc(inst.core_type) + ' · instance ' + inst.id + '</div>' +
      '<h1>' + U.esc(inst.name) + '</h1>' +
      '<div class="sub">' + U.esc(inst.core_label || inst.core_type) +
        (inst.mc_version ? ' · ' + U.esc(inst.mc_version) : '') + ' · 端口 ' + inst.port +
        ' · ' + inst.memory_mb + 'MB · ' +
        (inst.running ? '运行中 ' + U.esc(inst.uptime_text) + ' · PID ' + inst.pid : '未运行') + '</div></div>' +
      '<div class="acts">' +
        (inst.running
          ? '<button class="btn" data-act="restart">' + Icon.svg('restart', 15) + '重启</button>' +
            '<button class="btn danger" data-act="stop">' + Icon.svg('stop', 15) + '停止</button>' +
            '<button class="btn" data-act="kill">' + Icon.svg('kill', 15) + '强制结束</button>'
          : '<button class="btn success" data-act="start">' + Icon.svg('play', 15) + '启动</button>') +
        '<button class="btn ghost btn-icon" id="hd-refresh" title="刷新">' + Icon.svg('refresh', 16) + '</button>' +
      '</div></div>';
  }

  function lifecycle(act, btn) {
    var map = { start: '启动', stop: '停止', restart: '重启', kill: '强制结束' };
    var run = function () {
      C.withLoading(btn, API.post('/api/instances/' + id + '/' + act, {}).then(function (r) {
        Toast.ok('已请求' + map[act] + (r.note ? '：' + r.note : ''));
        setTimeout(refresh, 800);
      }).catch(function (e) { Toast.err(map[act] + '失败：' + e.message); }));
    };
    if (act === 'stop') C.Modal.confirm('停止实例', '先发送 stop 优雅停止，30 秒未退出则强制结束进程树。确定继续？', run);
    else if (act === 'kill') C.Modal.confirm('强制结束', '会立即 kill 整个进程树，未保存的世界数据可能损坏。确定继续？', run, true);
    else run();
  }

  function refresh() {
    return API.get('/api/instances/' + id).then(function (d) {
      inst = d.instance;
      draw();
    }).catch(function (e) {
      /* 实例详情拿不到：403 给「权限不足」页，404/其它按类型给人话 + 返回列表 */
      if (Perm.denyIfForbidden(e, app, {
            perm: 'log.view', active: 'instances',
            crumbs: [{ text: '服务器', hash: '#/instances' }, { text: '实例详情' }] })) { throw e; }
      View.showError(body, e, { title: '实例不可用', showStack: true },
                     { onRetry: function () { refresh(); } });
      throw e;
    });
  }

  var RENDER = {};

  /* =============================================================== 控制台 */
  RENDER.console = function () {
    var el = document.getElementById('tab-body');
    var autoScroll = true, filterText = '', history = [], hIdx = -1, searchText = '';
    var MAX_LINES = 3000;

    el.innerHTML = '' +
      '<div class="console-layout">' +
        '<div class="console-box" style="position:relative">' +
          '<div class="console-spine"></div>' +
          '<div class="console-bar" id="c-bar"></div>' +
          '<div class="console-tools">' +
            '<input type="text" id="c-filter" placeholder="只显示包含…（例如 WARN / player）" style="flex:1 1 180px" />' +
            '<input type="text" id="c-search" placeholder="高亮搜索" style="flex:1 1 140px" />' +
            '<span class="chip on" id="c-autoscroll" title="粘性自动滚动">' + Icon.svg('chevronDown', 12) + '自动滚动</span>' +
            '<span class="chip" id="c-wrap" title="仅显示错误与警告">' + Icon.svg('warn', 12) + '只看异常</span>' +
            '<button class="btn xs ghost" id="c-clear">' + Icon.svg('clear', 13) + '清屏</button>' +
            '<button class="btn xs ghost" id="c-download">' + Icon.svg('download', 13) + '下载日志</button>' +
          '</div>' +
          '<div class="console-out" id="c-out"></div>' +
          '<button class="btn sm primary console-jump" id="c-jump" style="display:none">' +
            Icon.svg('chevronDown', 14) + '回到底部</button>' +
          '<div class="console-in">' +
            '<span class="prompt">&gt;</span>' +
            '<input type="text" id="c-cmd" class="mono" placeholder="输入命令后回车下发到服务端 stdin（↑/↓ 翻历史）" />' +
            '<button class="btn sm" id="c-send">发送</button>' +
          '</div>' +
        '</div>' +
        '<div id="c-side"></div>' +
      '</div>';

    var out = document.getElementById('c-out');
    var barEl = document.getElementById('c-bar');
    var cmdEl = document.getElementById('c-cmd');

    function lineHtml(rec) {
      var t = U.fmtTime(rec.ts);
      var text = U.esc(rec.line);
      if (searchText) {
        var rx = new RegExp('(' + searchText.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + ')', 'gi');
        text = text.replace(rx, '<mark>$1</mark>');
      }
      return '<span class="ln ' + U.esc(rec.level || 'info') + '"><span class="t">' + t + '</span>' + text + '</span>';
    }

    function visible(rec) {
      if (onlyErrors && rec.level !== 'error' && rec.level !== 'warn') return false;
      if (filterText && rec.line.toLowerCase().indexOf(filterText) < 0) return false;
      return true;
    }

    var onlyErrors = false;

    function append(rec) {
      if (!visible(rec)) return false;
      out.insertAdjacentHTML('beforeend', lineHtml(rec));
      while (out.childNodes.length > MAX_LINES) out.removeChild(out.firstChild);
      return true;
    }

    function stick() {
      if (autoScroll) out.scrollTop = out.scrollHeight;
    }

    function recomputeFilter() {
      var rows = window.__mcLines || [];
      out.innerHTML = rows.filter(visible).map(lineHtml).join('');
      stick();
    }

    window.__mcLines = [];

    function addLines(list) {
      list.forEach(function (r) { window.__mcLines.push(r); });
      if (window.__mcLines.length > MAX_LINES) window.__mcLines = window.__mcLines.slice(-MAX_LINES);
      var html = list.filter(visible).map(lineHtml).join('');
      if (html) {
        out.insertAdjacentHTML('beforeend', html);
        while (out.childNodes.length > MAX_LINES) out.removeChild(out.firstChild);
        stick();
      }
    }

    function renderBar(st) {
      st = st || {};
      var tps = st.tps_available ? Number(st.tps).toFixed(1) : '不可用';
      barEl.innerHTML = '' +
        sb('状态', U.statusText(st.status || (inst && inst.status)), (st.running ? 'ok' : '')) +
        sb('PID', st.pid || '—') +
        sb('CPU', (st.cpu || 0).toFixed(1) + '%') +
        sb('内存', Math.round(st.mem_mb || 0) + 'MB') +
        sb('玩家', String(st.players || 0)) +
        sb('TPS', tps, st.tps_available ? 'ok' : 'warn') +
        sb('运行', st.uptime_text || U.fmtDur(st.uptime || 0));
    }
    function sb(k, v, cls) {
      return '<span class="sb-item"><span class="k">' + U.esc(k) + '</span>' +
        '<span class="v ' + (cls || '') + '">' + U.esc(v) + '</span></span>';
    }

    var retry = 0;
    var ws = null;
    function connect() {
      try { ws = new WebSocket(API.wsUrl('/api/instances/' + id + '/console/ws')); } catch (e) { return; }
      ws.onopen = function () { addLines([{ ts: Date.now() / 1000, line: '— 控制台已连接 —', level: 'panel' }]); retry = 0; };
      ws.onmessage = function (ev) {
        var m;
        try { m = JSON.parse(ev.data); } catch (e) { return; }
        if (m.type === 'hello') {
          if (m.history) addLines(m.history);
          renderBar(m.status);
        } else if (m.type === 'log') {
          addLines([m]);
          if (m.level === 'panel') renderBar();
        } else if (m.type === 'status') {
          renderBar(m.status);
        } else if (m.type === 'ack') {
          if (!m.ok) Toast.err('命令下发失败：' + (m.error || '未知错误'));
        }
      };
      ws.onclose = function () {
        addLines([{ ts: Date.now() / 1000, line: '— 控制台连接已断开 —', level: 'warn' }]);
        retry++;
        if (retry <= 6) setTimeout(connect, Math.min(8000, 800 * retry));
      };
      ws.onerror = function () {};
    }
    connect();

    out.addEventListener('scroll', function () {
      var near = out.scrollHeight - out.scrollTop - out.clientHeight < 30;
      document.getElementById('c-jump').style.display = near ? 'none' : 'inline-flex';
      if (!near && autoScroll) {
        autoScroll = false;
        document.getElementById('c-autoscroll').classList.remove('on');
      }
    });
    document.getElementById('c-jump').addEventListener('click', function () {
      autoScroll = true;
      document.getElementById('c-autoscroll').classList.add('on');
      stick();
    });
    document.getElementById('c-autoscroll').addEventListener('click', function () {
      autoScroll = !autoScroll;
      this.classList.toggle('on', autoScroll);
      stick();
    });
    document.getElementById('c-filter').addEventListener('input', U.debounce(function (e) {
      filterText = e.target.value.trim().toLowerCase();
      recomputeFilter();
    }, 180));
    document.getElementById('c-search').addEventListener('input', U.debounce(function (e) {
      searchText = e.target.value.trim();
      recomputeFilter();
    }, 180));
    document.getElementById('c-wrap').addEventListener('click', function () {
      onlyErrors = !onlyErrors;
      this.classList.toggle('on', onlyErrors);
      recomputeFilter();
    });
    document.getElementById('c-clear').addEventListener('click', function () {
      window.__mcLines = []; out.innerHTML = '';
    });
    document.getElementById('c-download').addEventListener('click', function () {
      U.download(API.fileUrl('/api/instances/' + id + '/log/download'), inst.name + '-latest.log');
    });

    function send() {
      var cmd = cmdEl.value.trim();
      if (!cmd) return;
      if (!ws || ws.readyState !== 1) { Toast.err('控制台未连接，无法下发命令'); return; }
      ws.send(JSON.stringify({ type: 'cmd', data: cmd }));
      history.push(cmd);
      if (history.length > 100) history.shift();
      hIdx = history.length;
      cmdEl.value = '';
    }
    document.getElementById('c-send').addEventListener('click', send);
    cmdEl.addEventListener('keydown', function (e) {
      if (e.key === 'Enter') { e.preventDefault(); send(); return; }
      if (e.key === 'ArrowUp') {
        e.preventDefault();
        if (!history.length) return;
        hIdx = Math.max(0, hIdx - 1);
        cmdEl.value = history[hIdx] || '';
      } else if (e.key === 'ArrowDown') {
        e.preventDefault();
        if (!history.length) return;
        hIdx = Math.min(history.length, hIdx + 1);
        cmdEl.value = history[hIdx] || '';
      }
    });

    // 侧栏：状态卡 + 实时曲线
    function side(st) {
      var tpsNote = st.tps_available ? '' :
        '<div class="tip warn small mt">TPS 不可用：该服务端没有在日志里输出 TPS，且未启用 RCON。面板不会编造数值。</div>';
      document.getElementById('c-side').innerHTML = '' +
        '<div class="card"><div class="card-head"><h3>运行状态</h3></div>' +
          '<div class="metrics" style="grid-template-columns:repeat(2,1fr)">' +
            C.metric('CPU', (st.cpu || 0).toFixed(1) + '%') +
            C.metric('内存', Math.round(st.mem_mb || 0) + 'MB') +
            C.metric('玩家', String(st.players || 0) + (st.max_players ? '/' + st.max_players : '')) +
            C.metric('TPS', st.tps_available ? Number(st.tps).toFixed(1) : '不可用') +
          '</div>' +
          '<div class="kv mt">' +
            '<div class="k">运行时长</div><div class="v">' + U.esc(st.uptime_text || U.fmtDur(st.uptime || 0)) + '</div>' +
            '<div class="k">PID</div><div class="v">' + (st.pid || '—') + '</div>' +
            '<div class="k">端口</div><div class="v">' + inst.port + '</div>' +
            '<div class="k">MSPT</div><div class="v">' + (st.mspt === null || st.mspt === undefined ? '不可用' : Number(st.mspt).toFixed(1)) + '</div>' +
          '</div>' + tpsNote +
          '<div class="mt"><button class="btn sm block" id="c-rcon">' + Icon.svg('spark', 14) + 'RCON 测试（list）</button></div>' +
        '</div>' +
        '<div class="card mt"><div class="card-head"><h3>最近 1 小时</h3>' +
          '<div class="right"><span class="badge">CPU / 内存</span></div></div>' +
          '<div id="c-chart"></div></div>';
      var rb = document.getElementById('c-rcon');
      if (rb) rb.addEventListener('click', function () {
        C.withLoading(rb, API.get('/api/instances/' + id + '/rcon/test').then(function (r) {
          if (r.ok) Toast.ok('RCON OK：' + (r.result || '').slice(0, 120));
          else Toast.err('RCON 不可用：' + (r.error || '').slice(0, 160));
        }).catch(function (e) { Toast.err(e.message); }));
      });
      loadChart();
    }

    function loadChart() {
      API.get('/api/instances/' + id + '/metrics?hours=1').then(function (d) {
        var box = document.getElementById('c-chart');
        if (!box) return;
        var pts = d.points || [];
        Chart.area(box, [
          { name: 'CPU%', color: 'var(--accent-bright)', unit: '%', values: pts.map(function (p) { return p.cpu; }),
            labels: pts.map(function (p) { return U.fmtTime(p.ts); }),
            labelsFull: pts.map(function (p) { return U.fmtTime(p.ts, true); }) },
          { name: '内存 MB', color: 'var(--info)', unit: 'MB', values: pts.map(function (p) { return p.mem_mb; }),
            labels: pts.map(function (p) { return U.fmtTime(p.ts); }),
            labelsFull: pts.map(function (p) { return U.fmtTime(p.ts, true); }) }
        ], { height: 150, emptyText: '还没有采样数据（运行后每 ' + 5 + ' 秒采样一次）' });
      }).catch(function () {});
    }

    renderBar(inst);
    side(inst);
    Store.every(2000, function () {
      if (location.hash.indexOf('/console') < 0 && location.hash.split('/').length > 3) return;
      API.get('/api/instances/' + id + '/status').then(renderBar).catch(function () {});
    }, false);
    Store.every(15000, function () { loadChart(); }, false);
    window.addEventListener('beforeunload', function () { if (ws) ws.close(); });
    // 离开页面（切换 Tab）时关闭 ws
    body.addEventListener('click', function h(e) {
      if (e.target.closest && e.target.closest('[data-tab]')) {
        if (ws) { ws.onclose = null; ws.close(); }
        body.removeEventListener('click', h);
      }
    });
  };

  /* =============================================================== 文件 */
  RENDER.files = function () {
    var el = document.getElementById('tab-body');
    var cur = '';
    el.innerHTML = '' +
      '<div class="card">' +
        '<div class="card-head"><h3>文件管理</h3>' +
          '<div class="right">' +
            '<button class="btn sm" id="f-up">' + Icon.svg('upload', 14) + '上传</button>' +
            '<button class="btn sm" id="f-mkdir">' + Icon.svg('folder', 14) + '新建文件夹</button>' +
            '<button class="btn sm ghost" id="f-refresh">' + Icon.svg('refresh', 14) + '刷新</button>' +
          '</div></div>' +
        '<div class="crumbs-file" id="f-crumbs"></div>' +
        '<div id="f-list"><div class="loading"><span class="spinner"></span>读取目录…</div></div>' +
        '<div class="drop-zone mt" id="f-drop">' + Icon.svg('upload', 20) +
          '<div class="small mt">把文件拖到这里上传到当前目录</div></div>' +
        '<input type="file" id="f-input" style="display:none" multiple />' +
      '</div>';

    function load(rel) {
      cur = rel || '';
      API.get('/api/instances/' + id + '/files?path=' + encodeURIComponent(cur)).then(function (d) {
        var crumbs = '<a href="#" data-go="">根目录</a>';
        if (d.path) {
          var parts = d.path.split('/');
          var acc = '';
          parts.forEach(function (p, i) {
            acc += (i ? '/' : '') + p;
            crumbs += ' / <a href="#" data-go="' + U.esc(acc) + '">' + U.esc(p) + '</a>';
          });
        }
        document.getElementById('f-crumbs').innerHTML = crumbs;
        document.getElementById('f-crumbs').querySelectorAll('[data-go]').forEach(function (a) {
          a.addEventListener('click', function (e) { e.preventDefault(); load(a.getAttribute('data-go')); });
        });
        var items = d.items || [];
        if (!items.length) {
          document.getElementById('f-list').innerHTML = C.empty('folder', '这个目录是空的',
            '可以上传服务端 jar、插件或配置文件，也可以在这里新建文件夹。',
            '<button class="btn primary" id="f-empty-up">' + Icon.svg('upload', 15) + '上传文件</button>');
          var eu = document.getElementById('f-empty-up');
          if (eu) eu.addEventListener('click', function () { document.getElementById('f-input').click(); });
          return;
        }
        document.getElementById('f-list').innerHTML = items.map(function (it) {
          var p = (d.path ? d.path + '/' : '') + it.name;
          return '<div class="file-row" data-p="' + U.esc(p) + '">' +
            Icon.svg(it.dir ? 'folder' : 'file', 16) +
            '<div class="fname' + (it.dir ? ' dir' : '') + '" data-open="' + U.esc(p) + '">' + U.esc(it.name) +
              (it.symlink ? ' <span class="tag">symlink</span>' : '') + '</div>' +
            '<div class="ftime">' + U.fmtTime(it.mtime, true) + '</div>' +
            '<div class="fsize">' + (it.dir ? '—' : U.fmtSize(it.size)) + '</div>' +
            '<div class="fops">' +
              (it.dir ? '' : '<button class="btn xs ghost" data-dl="' + U.esc(p) + '" title="下载">' + Icon.svg('download', 13) + '</button>') +
              (it.editable ? '<button class="btn xs ghost" data-edit="' + U.esc(p) + '" title="编辑">' + Icon.svg('edit', 13) + '</button>' : '') +
              (it.name.toLowerCase().endsWith('.zip') ? '<button class="btn xs ghost" data-unzip="' + U.esc(p) + '" title="解压">' + Icon.svg('box', 13) + '</button>' : '') +
              '<button class="btn xs ghost" data-ren="' + U.esc(p) + '" title="重命名">' + Icon.svg('sliders', 13) + '</button>' +
              '<button class="btn xs ghost" data-del="' + U.esc(p) + '" title="删除">' + Icon.svg('trash', 13) + '</button>' +
            '</div></div>';
        }).join('');
        bindItems();
      }).catch(function (e) {
        document.getElementById('f-list').innerHTML = '<div class="tip err">读取失败：' + U.esc(e.message) + '</div>';
      });
    }

    function bindItems() {
      document.querySelectorAll('[data-open]').forEach(function (el) {
        el.addEventListener('click', function () {
          var p = el.getAttribute('data-open');
          var row = el.closest('.file-row');
          if (el.classList.contains('dir')) load(p); else editFile(p);
        });
      });
      document.querySelectorAll('[data-dl]').forEach(function (b) {
        b.addEventListener('click', function () {
          U.download(API.fileUrl('/api/instances/' + id + '/files/download?path=' + encodeURIComponent(b.getAttribute('data-dl'))));
        });
      });
      document.querySelectorAll('[data-edit]').forEach(function (b) {
        b.addEventListener('click', function () { editFile(b.getAttribute('data-edit')); });
      });
      document.querySelectorAll('[data-unzip]').forEach(function (b) {
        b.addEventListener('click', function () {
          C.withLoading(b, API.post('/api/instances/' + id + '/files/unzip', { path: b.getAttribute('data-unzip') })
            .then(function (r) { Toast.ok('已解压 ' + r.extracted + ' 个文件'); load(cur); })
            .catch(function (e) { Toast.err('解压失败：' + e.message); }));
        });
      });
      document.querySelectorAll('[data-ren]').forEach(function (b) {
        b.addEventListener('click', function () {
          var p = b.getAttribute('data-ren');
          var old = p.split('/').pop();
          C.Modal.open({
            title: '重命名', okText: '重命名',
            body: '<div class="field"><label>新名称</label><input type="text" id="rn" value="' + U.esc(old) + '" /></div>',
            onOk: function (m) {
              var v = m.querySelector('#rn').value.trim();
              if (!v) return false;
              return API.post('/api/instances/' + id + '/files/rename', { path: p, new_name: v })
                .then(function () { Toast.ok('已重命名'); load(cur); })
                .catch(function (e) { Toast.err(e.message); return true; });
            }
          });
        });
      });
      document.querySelectorAll('[data-del]').forEach(function (b) {
        b.addEventListener('click', function () {
          var p = b.getAttribute('data-del');
          C.Modal.confirm('删除确认', '将永久删除「' + p + '」，此操作不可撤销。', function () {
            return API.post('/api/instances/' + id + '/files/delete', { path: p })
              .then(function () { Toast.ok('已删除'); load(cur); })
              .catch(function (e) { Toast.err(e.message); });
          }, true);
        });
      });
    }

    function editFile(p) {
      API.get('/api/instances/' + id + '/files/read?path=' + encodeURIComponent(p)).then(function (d) {
        C.Modal.open({
          title: '编辑 ' + p, wide: true, okText: '保存',
          body: '<div class="field"><textarea class="mono" id="ed" style="min-height:44vh">' + U.esc(d.content) + '</textarea>' +
            '<div class="desc">UTF-8 读写；' + U.fmtSize(d.size) + '</div></div>',
          onOk: function (m) {
            var v = m.querySelector('#ed').value;
            return API.put('/api/instances/' + id + '/files/write', { path: p, content: v })
              .then(function () { Toast.ok('已保存'); load(cur); })
              .catch(function (e) { Toast.err('保存失败：' + e.message); return true; });
          }
        });
      }).catch(function (e) { Toast.err(e.message); });
    }

    document.getElementById('f-refresh').addEventListener('click', function () { load(cur); });
    document.getElementById('f-up').addEventListener('click', function () { document.getElementById('f-input').click(); });
    document.getElementById('f-input').addEventListener('change', function (e) {
      var files = Array.prototype.slice.call(e.target.files);
      if (!files.length) return;
      uploadSeq(files, 0);
      e.target.value = '';
    });
    function uploadSeq(files, i) {
      if (i >= files.length) { Toast.ok('上传完成（' + files.length + ' 个文件）'); load(cur); return; }
      API.upload('/api/instances/' + id + '/files/upload', files[i], { path: cur }, function (p) {
        Toast.info('上传 ' + files[i].name + ' ' + p + '%');
      }).then(function () { uploadSeq(files, i + 1); })
        .catch(function (e) { Toast.err('上传失败：' + e.message); uploadSeq(files, i + 1); });
    }
    document.getElementById('f-mkdir').addEventListener('click', function () {
      C.Modal.open({
        title: '新建文件夹', okText: '创建',
        body: '<div class="field"><label>文件夹名称</label><input type="text" id="dn" placeholder="例如 plugins" /></div>',
        onOk: function (m) {
          var v = m.querySelector('#dn').value.trim();
          if (!v) return false;
          var p = (cur ? cur + '/' : '') + v;
          return API.post('/api/instances/' + id + '/files/mkdir', { path: p })
            .then(function () { Toast.ok('已创建'); load(cur); })
            .catch(function (e) { Toast.err(e.message); return true; });
        }
      });
    });
    var dz = document.getElementById('f-drop');
    dz.addEventListener('dragover', function (e) { e.preventDefault(); dz.classList.add('over'); });
    dz.addEventListener('dragleave', function () { dz.classList.remove('over'); });
    dz.addEventListener('drop', function (e) {
      e.preventDefault(); dz.classList.remove('over');
      var files = Array.prototype.slice.call(e.dataTransfer.files);
      if (files.length) uploadSeq(files, 0);
    });

    load('');
  };

  /* =============================================================== 配置 */
  var GROUPS = {
    '基础': ['motd', 'server-port', 'server-ip', 'max-players', 'online-mode', 'enable-status', 'hide-online-players'],
    '规则': ['white-list', 'enforce-whitelist', 'gamemode', 'force-gamemode', 'difficulty', 'hardcore', 'pvp',
             'allow-flight', 'spawn-protection', 'op-permission-level', 'function-permission-level', 'player-idle-timeout'],
    '世界': ['level-name', 'level-seed', 'level-type', 'view-distance', 'simulation-distance', 'generate-structures',
             'allow-nether', 'spawn-monsters', 'spawn-animals', 'spawn-npcs', 'max-world-size'],
    '性能与网络': ['max-tick-time', 'network-compression-threshold', 'sync-chunk-writes', 'rate-limit',
                   'prevent-proxy-connections', 'enable-command-block', 'allow-command-block'],
    'RCON 与查询': ['enable-rcon', 'rcon.port', 'rcon.password', 'enable-query', 'query.port', 'text-filtering-config']
  };

  RENDER.config = function () {
    var el = document.getElementById('tab-body');
    el.innerHTML = '<div class="loading"><span class="spinner"></span>读取 server.properties…</div>';
    Promise.all([
      API.get('/api/instances/' + id + '/config/properties').catch(function (e) { return { ok: false, error: e.message }; }),
      API.get('/api/instances/' + id + '/config/eula').catch(function () { return {}; }),
      API.get('/api/instances/' + id + '/config/startup').catch(function () { return {}; }),
      API.get('/api/instances/' + id + '/jars').catch(function () { return { jars: [] }; })
    ]).then(function (r) {
      var props = r[0], eula = r[1], startup = r[2], jars = r[3];
      var rows = props.rows || [];
      var pairs = rows.filter(function (x) { return x.kind === 'pair'; });
      var used = {};
      var groupsHtml = Object.keys(GROUPS).map(function (g) {
        var items = pairs.filter(function (p) { return GROUPS[g].indexOf(p.key) >= 0; });
        if (!items.length) return '';
        items.forEach(function (i) { used[i.key] = 1; });
        return '<div class="mb"><div class="kicker mb">' + U.esc(g) + '</div>' +
          items.map(propInput).join('') + '</div>';
      }).join('');
      var unknown = pairs.filter(function (p) { return !used[p.key]; });

      el.innerHTML = '' +
        '<div class="grid cols-2">' +
          '<div class="card"><div class="card-head"><h3>server.properties</h3>' +
            '<div class="right">' +
              '<span class="badge' + (props.exists ? ' ok' : '') + '">' + (props.exists ? '文件存在' : '尚未生成') + '</span>' +
              '<button class="btn sm primary" id="p-save">保存修改</button>' +
            '</div></div>' +
            (props.exists ? '' : '<div class="tip mb">该文件还没有生成：服务端首次启动时才会写出。下面的键会以「追加」方式写入。</div>') +
            '<div id="p-groups">' + (groupsHtml || '<div class="empty small">没有可编辑的键</div>') + '</div>' +
            (unknown.length ? '<hr class="hair" /><div class="kicker mb">其它键（原样保留）</div>' +
              unknown.map(function (p) {
                return '<div class="prop-line"><div class="key">' + U.esc(p.key) + '</div>' +
                  '<input type="text" data-pk="' + U.esc(p.key) + '" value="' + U.esc(p.value) + '" /></div>';
              }).join('') : '') +
            '<div class="desc mt">注释、空行、未知键与键顺序在保存时都会保留。</div>' +
          '</div>' +
          '<div>' +
            '<div class="card"><div class="card-head"><h3>EULA</h3>' +
              '<div class="right">' + (eula.accepted ? C.statusBadge('running', '已接受') : C.statusBadge('stopped', '未接受')) + '</div></div>' +
              '<div class="tip mb">Minecraft 服务端要求显式接受 EULA（https://aka.ms/MinecraftEULA）。</div>' +
              '<button class="btn ' + (eula.accepted ? '' : 'primary') + '" id="p-eula">' + Icon.svg('check', 15) +
                (eula.accepted ? '重新写入 eula=true' : '一键接受 EULA') + '</button>' +
            '</div>' +
            '<div class="card mt"><div class="card-head"><h3>启动参数</h3>' +
              '<div class="right"><button class="btn sm primary" id="s-save">保存</button></div></div>' +
              '<div class="field"><label>最大内存（MB，-Xmx）</label>' +
                '<input type="number" class="num" id="s-mem" value="' + (startup.memory_mb || 2048) + '" min="512" max="65536" step="512" /></div>' +
              '<div class="field"><label>JVM 参数</label>' +
                '<input type="text" class="mono" id="s-jvm" value="' + U.esc(startup.extra_jvm_args || '') + '" /></div>' +
              '<div class="field"><label>服务端附加参数</label>' +
                '<input type="text" class="mono" id="s-srv" value="' + U.esc(startup.extra_server_args || '') + '" /></div>' +
              '<div class="field"><label class="check"><input type="checkbox" id="s-nogui"' + (startup.nogui ? ' checked' : '') + ' />附加 nogui（无图形界面，必须开）</label></div>' +
              '<div class="field"><label class="check"><input type="checkbox" id="s-auto"' + (startup.auto_restart ? ' checked' : '') + ' />崩溃自动重启</label></div>' +
              '<div class="field"><label>Java 路径</label>' +
                '<input type="text" class="mono" id="s-java" value="' + U.esc(startup.java_path || '') + '" placeholder="留空=自动检测" /></div>' +
              '<div class="field"><label class="check"><input type="checkbox" id="s-rcon"' + (startup.rcon_enabled ? ' checked' : '') + ' />启用 RCON</label></div>' +
              '<div class="grid cols-2 tight">' +
                '<div class="field"><label>RCON 端口</label><input type="number" class="num" id="s-rport" value="' + (startup.rcon_port || 25575) + '" /></div>' +
                '<div class="field"><label>RCON 密码</label><input type="password" class="mono" id="s-rpwd" placeholder="' + (startup.has_rcon_password ? '已设置（留空不修改）' : '未设置') + '" /></div>' +
              '</div>' +
              '<div class="desc">实际启动命令预览：</div>' +
              '<div class="crumbs-file mt" id="s-preview">' + U.esc(startup.command_preview || '—') + '</div>' +
            '</div>' +
            '<div class="card mt"><div class="card-head"><h3>服务端 jar</h3>' +
              '<div class="right"><button class="btn sm" id="j-refresh">' + Icon.svg('refresh', 14) + '重新扫描</button></div></div>' +
              '<div class="field"><label>选择实例目录中的 jar 作为启动目标</label>' +
                '<select id="j-select">' + (jars.jars || []).map(function (j) {
                  return '<option value="' + U.esc(j.path) + '"' + (j.path === jars.current ? ' selected' : '') + '>' +
                    U.esc(j.rel) + ' · ' + U.fmtSize(j.size) + '</option>';
                }).join('') + '</select></div>' +
              '<div class="row"><button class="btn" id="j-apply">设为启动 jar</button>' +
                '<button class="btn" id="j-installer">运行 Forge/NeoForge 安装器</button></div>' +
              '<div class="desc mt">安装器会在实例目录执行 --installServer（可能需要数分钟）。</div>' +
            '</div>' +
          '</div>' +
        '</div>';

      document.getElementById('p-save').addEventListener('click', function (b) {
        var updates = {};
        el.querySelectorAll('[data-pk]').forEach(function (inp) { updates[inp.getAttribute('data-pk')] = inp.value; });
        C.withLoading(this, API.put('/api/instances/' + id + '/config/properties', { updates: updates })
          .then(function () { Toast.ok('已保存 ' + Object.keys(updates).length + ' 个键（注释与未知键已保留）'); })
          .catch(function (e) { Toast.err('保存失败：' + e.message); }));
      });
      document.getElementById('p-eula').addEventListener('click', function () {
        C.withLoading(this, API.post('/api/instances/' + id + '/config/eula', {}).then(function () {
          Toast.ok('已写入 eula=true'); RENDER.config();
        }).catch(function (e) { Toast.err(e.message); }));
      });
      document.getElementById('s-save').addEventListener('click', function () {
        var body = {
          memory_mb: parseInt(document.getElementById('s-mem').value, 10),
          extra_jvm_args: document.getElementById('s-jvm').value,
          extra_server_args: document.getElementById('s-srv').value,
          nogui: document.getElementById('s-nogui').checked,
          auto_restart: document.getElementById('s-auto').checked,
          java_path: document.getElementById('s-java').value.trim(),
          rcon_enabled: document.getElementById('s-rcon').checked,
          rcon_port: parseInt(document.getElementById('s-rport').value, 10)
        };
        var rp = document.getElementById('s-rpwd').value;
        if (rp) body.rcon_password = rp;
        C.withLoading(this, API.put('/api/instances/' + id + '/config/startup', body).then(function (r) {
          Toast.ok('启动参数已保存');
          if (r.command_preview) document.getElementById('s-preview').textContent = r.command_preview;
        }).catch(function (e) { Toast.err(e.message); }));
      });
      document.getElementById('j-apply').addEventListener('click', function () {
        var v = document.getElementById('j-select').value;
        if (!v) { Toast.warn('没有可选的 jar'); return; }
        C.withLoading(this, API.post('/api/instances/' + id + '/jar', { jar_path: v }).then(function () {
          Toast.ok('已设为启动 jar'); refresh();
        }).catch(function (e) { Toast.err(e.message); }));
      });
      document.getElementById('j-refresh').addEventListener('click', function () { RENDER.config(); });
      document.getElementById('j-installer').addEventListener('click', function () {
        C.Modal.confirm('运行安装器', '将在实例目录执行 java -jar <installer> --installServer，输出会实时打到控制台。继续？', function () {
          return API.post('/api/instances/' + id + '/run-installer', {}).then(function (r) {
            if (r.ok) Toast.ok('安装器完成：' + (r.scripts || []).join(', '));
            else Toast.err('安装器失败：' + (r.error || '未知'));
          }).catch(function (e) { Toast.err(e.message); });
        });
      });
    });
  };

  function propInput(p) {
    var meta = { label: p.label, type: p.type, desc: p.desc };
    var type = p.type || 'text';
    var input;
    if (type === 'bool') {
      input = '<select data-pk="' + U.esc(p.key) + '">' +
        '<option value="true"' + (p.value === 'true' ? ' selected' : '') + '>true</option>' +
        '<option value="false"' + (p.value !== 'true' ? ' selected' : '') + '>false</option></select>';
    } else if (type.indexOf('enum:') === 0) {
      var opts = type.slice(5).split(',');
      input = '<select data-pk="' + U.esc(p.key) + '">' + opts.map(function (o) {
        return '<option value="' + U.esc(o) + '"' + (p.value === o ? ' selected' : '') + '>' + U.esc(o) + '</option>';
      }).join('') + '</select>';
    } else if (type === 'int') {
      input = '<input type="number" class="num" data-pk="' + U.esc(p.key) + '" value="' + U.esc(p.value) + '" />';
    } else {
      input = '<input type="text" data-pk="' + U.esc(p.key) + '" value="' + U.esc(p.value) + '" />';
    }
    return '<div class="prop-line"><div class="key">' + U.esc(p.key) +
      (meta.label ? '<span class="lbl">' + U.esc(meta.label) + '</span>' : '') + '</div>' +
      '<div>' + input + (meta.desc ? '<div class="desc">' + U.esc(meta.desc) + '</div>' : '') + '</div></div>';
  }

  /* =============================================================== 玩家 */
  RENDER.players = function () {
    var el = document.getElementById('tab-body');
    el.innerHTML = '<div class="loading"><span class="spinner"></span>读取玩家名单…</div>';

    function draw(d) {
      var online = d.online_source === 'unavailable'
        ? '<span class="badge">不可用</span>'
        : '<span class="badge ' + (d.online_count ? 'running' : '') + '"><span class="dot"></span>' +
          U.esc(String(d.online_count || 0)) + ' 在线</span>';
      el.innerHTML = '' +
        '<div class="grid cols-2">' +
          '<div class="card"><div class="card-head"><h3>在线玩家</h3>' +
            '<div class="right">' + online +
            '<span class="tag">' + U.esc(d.online_source === 'rcon' ? 'RCON' : d.online_source === 'log' ? '日志解析' : '不可用') + '</span></div></div>' +
            (d.online_source === 'unavailable'
              ? '<div class="tip warn">实例未运行，或既没有 RCON 也没有可解析的日志。面板不会编造在线列表。</div>'
              : '<div class="crumbs-file">' + U.esc(d.online_text || '（空）') + '</div>') +
            (d.online_names && d.online_names.length
              ? '<div class="row tight mt">' + d.online_names.map(function (n) {
                  return '<span class="badge">' + U.esc(n) + '</span>';
                }).join('') + '</div>' : '') +
            '<div class="row mt tight">' +
              '<button class="btn sm" id="pl-list">' + Icon.svg('refresh', 13) + '刷新 list</button>' +
              '<button class="btn sm" id="pl-whitelist-on">' + Icon.svg('check', 13) + '开启白名单</button>' +
              '<button class="btn sm" id="pl-save-all">' + Icon.svg('backup', 13) + 'save-all</button>' +
            '</div>' +
          '</div>' +
          '<div class="card"><div class="card-head"><h3>添加</h3></div>' +
            '<div class="inline-form">' +
              '<div class="field"><label>类型</label><select id="pa-which">' +
                '<option value="whitelist">白名单 whitelist.json</option>' +
                '<option value="ops">OP ops.json</option>' +
                '<option value="banned-players">封禁 banned-players.json</option>' +
                '<option value="banned-ips">IP 封禁 banned-ips.json</option>' +
              '</select></div>' +
              '<div class="field"><label>玩家名 / IP</label><input type="text" id="pa-name" placeholder="Steve" /></div>' +
              '<div class="field"><label>UUID（可选）</label><input type="text" class="mono" id="pa-uuid" placeholder="离线模式可留空" /></div>' +
              '<button class="btn primary" id="pa-add">' + Icon.svg('plus', 14) + '添加</button>' +
            '</div>' +
            '<div class="tip mt small">离线模式（online-mode=false）下 UUID 留空也能写入；正版服建议填 UUID。</div>' +
            '<hr class="hair" />' +
            '<div class="kicker mb">在线操作（需要实例运行）</div>' +
            '<div class="inline-form">' +
              '<div class="field"><label>玩家名</label><input type="text" id="po-name" placeholder="Steve" /></div>' +
              '<button class="btn sm" data-op="op">给 OP</button>' +
              '<button class="btn sm" data-op="deop">收回 OP</button>' +
              '<button class="btn sm" data-op="kick">踢出</button>' +
              '<button class="btn sm danger" data-op="ban">封禁</button>' +
              '<button class="btn sm" data-op="pardon">解封</button>' +
            '</div>' +
          '</div>' +
        '</div>' +
        '<div class="grid cols-2 mt">' +
          listCard('白名单', 'whitelist', d.whitelist, 'Steve') +
          listCard('OP 列表', 'ops', d.ops, 'Steve') +
          listCard('封禁玩家', 'banned-players', d.banned_players, 'Steve') +
          listCard('封禁 IP', 'banned-ips', d.banned_ips, '1.2.3.4') +
        '</div>';

      el.querySelectorAll('[data-rm]').forEach(function (b) {
        b.addEventListener('click', function () {
          var parts = b.getAttribute('data-rm').split('|');
          C.withLoading(b, API.post('/api/instances/' + id + '/players/remove', { which: parts[0], name: parts[1] })
            .then(function () { Toast.ok('已移除'); load(); })
            .catch(function (e) { Toast.err(e.message); }));
        });
      });
      document.getElementById('pa-add').addEventListener('click', function () {
        var which = document.getElementById('pa-which').value;
        var name = document.getElementById('pa-name').value.trim();
        var uuid = document.getElementById('pa-uuid').value.trim();
        if (!name) { Toast.warn('请填写玩家名或 IP'); return; }
        C.withLoading(this, API.post('/api/instances/' + id + '/players/add', { which: which, name: name, uuid: uuid })
          .then(function () { Toast.ok('已写入 ' + which); load(); })
          .catch(function (e) { Toast.err(e.message); }));
      });
      el.querySelectorAll('[data-op]').forEach(function (b) {
        b.addEventListener('click', function () {
          var name = document.getElementById('po-name').value.trim();
          var act = b.getAttribute('data-op');
          if (!name) { Toast.warn('请填写玩家名'); return; }
          var ep = { op: 'op', deop: 'deop', kick: 'kick', ban: 'ban', pardon: 'pardon' }[act];
          C.withLoading(b, API.post('/api/instances/' + id + '/players/' + ep, { name: name })
            .then(function (r) { Toast.ok(r.note || '已执行'); load(); })
            .catch(function (e) { Toast.err(e.message); }));
        });
      });
      document.getElementById('pl-list').addEventListener('click', function () { load(); });
      document.getElementById('pl-whitelist-on').addEventListener('click', function () {
        C.withLoading(this, API.post('/api/instances/' + id + '/command', { command: 'whitelist on' })
          .then(function () { Toast.ok('已下发 whitelist on'); })
          .catch(function (e) { Toast.err(e.message); }));
      });
      document.getElementById('pl-save-all').addEventListener('click', function () {
        C.withLoading(this, API.post('/api/instances/' + id + '/command', { command: 'save-all' })
          .then(function () { Toast.ok('已下发 save-all'); })
          .catch(function (e) { Toast.err(e.message); }));
      });
    }

    function listCard(title, which, rows, placeholder) {
      rows = rows || [];
      var key = which === 'banned-ips' ? 'ip' : 'name';
      return '<div class="card"><div class="card-head"><h3>' + U.esc(title) + '</h3>' +
        '<div class="right"><span class="badge">' + rows.length + ' 条</span></div></div>' +
        (rows.length
          ? '<div class="table-wrap"><table><tbody>' + rows.map(function (r) {
              return '<tr><td class="mono">' + U.esc(r[key] || r.name || '') + '</td>' +
                '<td class="mono faint small">' + U.esc((r.uuid || r.reason || '').slice(0, 24)) + '</td>' +
                '<td style="text-align:right"><button class="btn xs danger" data-rm="' + which + '|' + U.esc(r[key] || r.name) + '">移除</button></td></tr>';
            }).join('') + '</tbody></table></div>'
          : '<div class="empty small">' + U.esc(title) + '为空</div>') +
        '</div>';
    }

    function load() {
      API.get('/api/instances/' + id + '/players').then(draw).catch(function (e) {
        el.innerHTML = '<div class="tip err">读取失败：' + U.esc(e.message) + '</div>';
      });
    }
    load();
    Store.every(5000, function () {
      if (location.hash.indexOf('/players') < 0) return;
      API.get('/api/instances/' + id + '/players').then(draw).catch(function () {});
    }, false);
  };

  /* =============================================================== 建筑导入 */
  RENDER.build = function () {
    // 具体 UI 在 pages/build.js（PagesBuild）；这里只把上下文递过去
    var h = PagesBuild.render({
      iid: id,
      instance: inst,
      // 真实实现应从插件列表读取"是否已装 WorldEdit"；mock 阶段按未装处理，
      // 这样预检只会给出 engine_available:['offline']（实例停止时）
      hasWorldEdit: false
    });
    _tabCleanup = (h && h.destroy) ? h.destroy : null;
  };

  /* =============================================================== 备份 */
  RENDER.backups = function () {
    var el = document.getElementById('tab-body');
    el.innerHTML = '<div class="loading"><span class="spinner"></span>读取备份列表…</div>';
    function load() {
      API.get('/api/instances/' + id + '/backups').then(function (d) {
        var rows = d.backups || [];
        el.innerHTML = '<div class="card"><div class="card-head"><h3>备份与还原</h3>' +
          '<div class="right"><span class="badge">' + rows.length + ' 份</span>' +
          '<button class="btn sm primary" id="b-new">' + Icon.svg('backup', 14) + '立即备份</button></div></div>' +
          '<div class="tip mb">备份为 tar.gz，默认排除 logs / cache / crash-reports；超过保留份数会自动轮转删除最旧的。还原前会自动创建一份 pre-restore 备份。</div>' +
          (rows.length
            ? '<div class="table-wrap"><table><thead><tr><th>文件</th><th>类型</th><th class="mono">大小</th>' +
              '<th class="mono">时间</th><th>状态</th><th style="text-align:right">操作</th></tr></thead><tbody>' +
              rows.map(function (b) {
                return '<tr><td class="mono">' + U.esc(b.name) + (b.note ? '<div class="small faint">' + U.esc(b.note) + '</div>' : '') + '</td>' +
                  '<td><span class="badge ' + (b.kind === 'cron' ? 'info' : b.kind === 'pre-restore' ? 'gold' : '') + '">' + U.esc(b.kind) + '</span></td>' +
                  '<td class="mono num">' + U.fmtSize(b.size) + '</td>' +
                  '<td class="mono num">' + U.fmtTime(b.created_at, true) + '</td>' +
                  '<td>' + (b.exists ? '<span class="badge ok">在位</span>' : '<span class="badge err">文件缺失</span>') + '</td>' +
                  '<td style="text-align:right" class="nowrap">' +
                    '<button class="btn xs" data-bdl="' + b.id + '">下载</button> ' +
                    '<button class="btn xs" data-brs="' + b.id + '">还原</button> ' +
                    '<button class="btn xs danger" data-bdel="' + b.id + '">删除</button>' +
                  '</td></tr>';
              }).join('') + '</tbody></table></div>'
            : C.empty('backup', '还没有备份', '点击「立即备份」把实例目录打包成 tar.gz；也可以建一个计划任务定时备份。',
                '<button class="btn primary" id="b-new2">' + Icon.svg('backup', 15) + '立即备份</button>')) +
          '</div>';

        var mk = function (btn) {
          C.withLoading(btn, API.post('/api/instances/' + id + '/backups', { note: '' }).then(function (r) {
            Toast.ok('备份完成：' + U.fmtSize(r.size) + (r.removed && r.removed.length ? '（已轮转删除 ' + r.removed.length + ' 份）' : ''));
            load();
          }).catch(function (e) { Toast.err('备份失败：' + e.message); }));
        };
        var bn = document.getElementById('b-new'); if (bn) bn.addEventListener('click', function () { mk(this); });
        var bn2 = document.getElementById('b-new2'); if (bn2) bn2.addEventListener('click', function () { mk(this); });
        el.querySelectorAll('[data-bdl]').forEach(function (b) {
          b.addEventListener('click', function () {
            U.download(API.fileUrl('/api/instances/' + id + '/backups/' + b.getAttribute('data-bdl') + '/download'));
          });
        });
        el.querySelectorAll('[data-brs]').forEach(function (b) {
          b.addEventListener('click', function () {
            var bid = b.getAttribute('data-brs');
            C.Modal.confirm('还原备份', '会把备份解包覆盖到实例目录（先自动备份当前状态）。实例必须处于停止状态。确定继续？', function () {
              return API.post('/api/instances/' + id + '/backups/' + bid + '/restore', {}).then(function (r) {
                Toast.ok('已还原；还原前备份：' + (r.pre_backup ? r.pre_backup.split(/[\\/]/).pop() : '无'));
                load();
              }).catch(function (e) { Toast.err('还原失败：' + e.message); });
            }, true);
          });
        });
        el.querySelectorAll('[data-bdel]').forEach(function (b) {
          b.addEventListener('click', function () {
            C.Modal.confirm('删除备份', '将永久删除该备份文件。确定？', function () {
              return API.del('/api/instances/' + id + '/backups/' + b.getAttribute('data-bdel')).then(function () {
                Toast.ok('已删除'); load();
              }).catch(function (e) { Toast.err(e.message); });
            }, true);
          });
        });
      }).catch(function (e) {
        el.innerHTML = '<div class="tip err">读取失败：' + U.esc(e.message) + '</div>';
      });
    }
    load();
  };

  /* =============================================================== 计划任务 */
  RENDER.cron = function () {
    var el = document.getElementById('tab-body');
    el.innerHTML = '<div class="loading"><span class="spinner"></span>读取计划任务…</div>';
    function load() {
      API.get('/api/cron').then(function (d) {
        var jobs = (d.jobs || []).filter(function (j) { return String(j.instance_id) === String(id); });
        el.innerHTML = '<div class="card"><div class="card-head"><h3>本实例的计划任务</h3>' +
          '<div class="right"><button class="btn sm primary" id="cr-new">' + Icon.svg('plus', 14) + '新建任务</button></div></div>' +
          '<div class="tip mb">cron 为 5 段标准表达式（分 时 日 月 周），例如 <span class="mono">0 4 * * *</span> 表示每天 4:00。' +
          '动作支持重启 / 停止 / 启动 / 备份 / 执行命令 / 广播。</div>' +
          (jobs.length
            ? '<div class="table-wrap"><table><thead><tr><th>名称</th><th class="mono">cron</th><th>动作</th>' +
              '<th>启用</th><th class="mono">上次执行</th><th>结果</th><th style="text-align:right">操作</th></tr></thead><tbody>' +
              jobs.map(function (j) {
                return '<tr><td>' + U.esc(j.name) + '</td>' +
                  '<td class="mono">' + U.esc(j.schedule) + '</td>' +
                  '<td><span class="badge">' + U.esc(j.action) + '</span>' + (j.payload ? '<div class="small faint mono">' + U.esc(j.payload.slice(0, 40)) + '</div>' : '') + '</td>' +
                  '<td>' + (j.enabled ? '<span class="badge running"><span class="dot"></span>开</span>' : '<span class="badge">关</span>') + '</td>' +
                  '<td class="mono num">' + (j.last_run ? U.fmtTime(j.last_run, true) : '—') + '</td>' +
                  '<td>' + (j.last_status === 'ok' ? '<span class="badge ok">成功</span>' : j.last_status ? '<span class="badge err">失败</span>' : '—') + '</td>' +
                  '<td style="text-align:right" class="nowrap">' +
                    '<button class="btn xs" data-run="' + j.id + '">立即执行</button> ' +
                    '<button class="btn xs" data-runs="' + j.id + '">历史</button> ' +
                    '<button class="btn xs" data-en="' + j.id + '|' + (j.enabled ? 0 : 1) + '">' + (j.enabled ? '停用' : '启用') + '</button> ' +
                    '<button class="btn xs danger" data-deljob="' + j.id + '">删除</button>' +
                  '</td></tr>';
              }).join('') + '</tbody></table></div>'
            : C.empty('clock', '该实例还没有计划任务', '可以定时重启（例如每天凌晨 4 点）、定时备份，或定时执行一条命令。',
                '<button class="btn primary" id="cr-new2">' + Icon.svg('plus', 15) + '新建计划任务</button>')) +
          '</div>';

        function newJob() {
          C.Modal.open({
            title: '新建计划任务', okText: '创建',
            body: '' +
              '<div class="field"><label>任务名称</label><input type="text" id="cj-name" value="每日重启" /></div>' +
              '<div class="field"><label>cron 表达式（分 时 日 月 周）</label>' +
                '<input type="text" class="mono" id="cj-cron" value="0 4 * * *" />' +
                '<div class="desc" id="cj-hint">例如 0 4 * * * = 每天 04:00；*/30 * * * * = 每 30 分钟</div></div>' +
              '<div class="field"><label>动作</label><select id="cj-action">' +
                '<option value="restart">重启实例</option><option value="backup">备份实例</option>' +
                '<option value="command">执行控制台命令</option><option value="broadcast">广播消息（say）</option>' +
                '<option value="stop">停止实例</option><option value="start">启动实例</option></select></div>' +
              '<div class="field"><label>参数（命令内容 / 广播文本）</label><input type="text" id="cj-payload" placeholder="仅 command 与 broadcast 需要" /></div>',
            onOk: function (m) {
              var body = {
                name: m.querySelector('#cj-name').value.trim(),
                schedule: m.querySelector('#cj-cron').value.trim(),
                action: m.querySelector('#cj-action').value,
                payload: m.querySelector('#cj-payload').value,
                instance_id: Number(id), enabled: true
              };
              if (!body.name) { Toast.warn('请填写名称'); return false; }
              return API.post('/api/cron', body).then(function () { Toast.ok('已创建'); load(); })
                .catch(function (e) { Toast.err(e.message); return true; });
            }
          });
        }
        var a = document.getElementById('cr-new'); if (a) a.addEventListener('click', newJob);
        var b = document.getElementById('cr-new2'); if (b) b.addEventListener('click', newJob);
        el.querySelectorAll('[data-run]').forEach(function (x) {
          x.addEventListener('click', function () {
            C.withLoading(x, API.post('/api/cron/' + x.getAttribute('data-run') + '/run', {}).then(function (r) {
              if (r.ok) Toast.ok('执行成功：' + (r.output || '').slice(0, 100));
              else Toast.err('执行失败：' + (r.output || '').slice(0, 160));
              load();
            }).catch(function (e) { Toast.err(e.message); }));
          });
        });
        el.querySelectorAll('[data-en]').forEach(function (x) {
          x.addEventListener('click', function () {
            var p = x.getAttribute('data-en').split('|');
            C.withLoading(x, API.patch('/api/cron/' + p[0], { enabled: p[1] === '1' }).then(function () { load(); })
              .catch(function (e) { Toast.err(e.message); }));
          });
        });
        el.querySelectorAll('[data-deljob]').forEach(function (x) {
          x.addEventListener('click', function () {
            C.Modal.confirm('删除任务', '确定删除该计划任务？（历史记录一并删除）', function () {
              return API.del('/api/cron/' + x.getAttribute('data-deljob')).then(function () { Toast.ok('已删除'); load(); })
                .catch(function (e) { Toast.err(e.message); });
            }, true);
          });
        });
        el.querySelectorAll('[data-runs]').forEach(function (x) {
          x.addEventListener('click', function () {
            API.get('/api/cron/' + x.getAttribute('data-runs') + '/runs').then(function (r) {
              C.Modal.open({
                title: '执行历史', wide: true, footer: false,
                body: (r.runs || []).length
                  ? '<div class="table-wrap"><table><thead><tr><th class="mono">时间</th><th>结果</th><th class="mono">耗时</th><th>输出</th></tr></thead><tbody>' +
                    r.runs.map(function (t) {
                      return '<tr><td class="mono num">' + U.fmtTime(t.ts, true) + '</td>' +
                        '<td>' + (t.status === 'ok' ? '<span class="badge ok">成功</span>' : '<span class="badge err">失败</span>') + '</td>' +
                        '<td class="mono num">' + (Math.round((t.duration || 0) * 100) / 100) + 's</td>' +
                        '<td class="mono small">' + U.esc((t.output || '').slice(0, 300)) + '</td></tr>';
                    }).join('') + '</tbody></table></div>'
                  : '<div class="empty small">还没有执行记录</div>'
              });
            }).catch(function (e) { Toast.err(e.message); });
          });
        });
      }).catch(function (e) {
        el.innerHTML = '<div class="tip err">读取失败：' + U.esc(e.message) + '</div>';
      });
    }
    load();
  };

  /* =============================================================== 设置 */
  RENDER.settings = function () {
    var el = document.getElementById('tab-body');
    el.innerHTML = '<div class="loading"><span class="spinner"></span>读取实例设置…</div>';
    Promise.all([
      API.get('/api/instances/' + id).then(function (d) { return d.instance; }),
      API.get('/api/instances/' + id + '/crashes').catch(function () { return { crashes: [] }; }),
      API.get('/api/instances/' + id + '/metrics?hours=24').catch(function () { return { points: [] }; })
    ]).then(function (r) {
      var it = r[0], crashes = r[1].crashes || [], metrics = r[2];
      el.innerHTML = '' +
        '<div class="grid cols-2">' +
          '<div class="card"><div class="card-head"><h3>实例属性</h3>' +
            '<div class="right"><button class="btn sm primary" id="i-save">保存</button></div></div>' +
            '<div class="field"><label>名称</label><input type="text" id="i-name" value="' + U.esc(it.name) + '" /></div>' +
            '<div class="grid cols-2 tight">' +
              '<div class="field"><label>端口</label><input type="number" class="num" id="i-port" value="' + it.port + '" /></div>' +
              '<div class="field"><label>备注</label><input type="text" id="i-note" value="' + U.esc(it.note || '') + '" /></div>' +
            '</div>' +
            '<div class="field"><label class="check"><input type="checkbox" id="i-auto"' + (it.auto_restart ? ' checked' : '') + ' />崩溃自动重启（指数退避 5→10→20s，上限在面板设置里）</label></div>' +
            '<div class="kv mt">' +
              '<div class="k">实例 ID</div><div class="v">' + it.id + '</div>' +
              '<div class="k">核心</div><div class="v">' + U.esc(it.core_type) + ' ' + U.esc(it.mc_version || '') + '</div>' +
              '<div class="k">目录</div><div class="v">' + U.esc(it.dir) + '</div>' +
              '<div class="k">目录占用</div><div class="v">' + U.fmtSize(it.dir_size) + '</div>' +
              '<div class="k">jar</div><div class="v">' + U.esc(it.jar_path || '（未设置）') + '</div>' +
              '<div class="k">创建时间</div><div class="v">' + U.fmtTime(it.created_at, true) + '</div>' +
              '<div class="k">上次退出码</div><div class="v">' + (it.exit_code === null || it.exit_code === undefined ? '—' : it.exit_code) + '</div>' +
            '</div>' +
          '</div>' +
          '<div>' +
            '<div class="card"><div class="card-head"><h3>24 小时监控</h3>' +
              '<div class="right"><span class="badge' + (metrics.tps_available ? ' ok' : '') + '">' +
                (metrics.tps_available ? 'TPS 有数据' : 'TPS 不可用') + '</span></div></div>' +
              '<div id="s-chart"></div>' +
              '<div class="legend mt">' +
                '<span><i style="background:var(--accent-bright)"></i>CPU %</span>' +
                '<span><i style="background:var(--info)"></i>内存 MB</span>' +
                '<span><i style="background:var(--success)"></i>玩家数</span>' +
              '</div>' +
              (metrics.tps_available ? '' : '<div class="tip warn mt small">' + U.esc(metrics.tps_note || '') + '</div>') +
            '</div>' +
            '<div class="card mt"><div class="card-head"><h3>崩溃记录</h3>' +
              '<div class="right"><span class="badge' + (crashes.length ? ' err' : '') + '">' + crashes.length + ' 次</span></div></div>' +
              (crashes.length
                ? crashes.slice(0, 6).map(function (c) {
                    return '<div class="prop-line"><div class="key">' + U.fmtTime(c.ts, true) +
                      '<span class="lbl">退出码 ' + (c.exit_code === null ? '—' : c.exit_code) + ' · ' + U.esc(c.action) + '</span></div>' +
                      '<div><details><summary class="small muted" style="cursor:pointer">最后 50 行日志</summary>' +
                      '<pre class="mono small" style="max-height:220px;overflow:auto;white-space:pre-wrap">' + U.esc(c.tail || '') + '</pre></details></div></div>';
                  }).join('')
                : '<div class="empty small">没有崩溃记录 👍</div>') +
            '</div>' +
            '<div class="card mt"><div class="card-head"><h3>危险操作</h3></div>' +
              '<div class="row"><button class="btn danger" id="i-del">' + Icon.svg('trash', 14) + '删除实例记录</button>' +
              '<button class="btn danger" id="i-del-purge">' + Icon.svg('trash', 14) + '删除实例并清空目录</button></div>' +
              '<div class="desc mt">删除记录会保留实例目录（数据不丢）；清空目录会连世界与配置一起删除，不可恢复。</div>' +
            '</div>' +
          '</div>' +
        '</div>';

      var pts = metrics.points || [];
      Chart.area(document.getElementById('s-chart'), [
        { name: 'CPU%', color: 'var(--accent-bright)', unit: '%', values: pts.map(function (p) { return p.cpu; }),
          labels: pts.map(function (p) { return U.fmtTime(p.ts); }), labelsFull: pts.map(function (p) { return U.fmtTime(p.ts, true); }) },
        { name: '内存 MB', color: 'var(--info)', unit: 'MB', values: pts.map(function (p) { return p.mem_mb; }),
          labels: pts.map(function (p) { return U.fmtTime(p.ts); }), labelsFull: pts.map(function (p) { return U.fmtTime(p.ts, true); }) },
        { name: '玩家', color: 'var(--success)', unit: '', values: pts.map(function (p) { return p.players; }),
          labels: pts.map(function (p) { return U.fmtTime(p.ts); }), labelsFull: pts.map(function (p) { return U.fmtTime(p.ts, true); }) }
      ], { height: 190, emptyText: '还没有 24 小时采样数据' });

      document.getElementById('i-save').addEventListener('click', function () {
        C.withLoading(this, API.patch('/api/instances/' + id, {
          name: document.getElementById('i-name').value.trim(),
          port: parseInt(document.getElementById('i-port').value, 10),
          note: document.getElementById('i-note').value,
          auto_restart: document.getElementById('i-auto').checked
        }).then(function () { Toast.ok('已保存'); refresh(); })
          .catch(function (e) { Toast.err(e.message); }));
      });
      document.getElementById('i-del').addEventListener('click', function () {
        C.Modal.confirm('删除实例记录', '面板记录会删除，实例目录保留在磁盘上。确定？', function () {
          return API.del('/api/instances/' + id).then(function (r) {
            Toast.ok(r.note || '已删除'); location.hash = '#/instances';
          }).catch(function (e) { Toast.err(e.message); });
        }, true);
      });
      document.getElementById('i-del-purge').addEventListener('click', function () {
        C.Modal.confirm('删除实例并清空目录', '会连同世界存档、插件与配置一起永久删除。此操作不可恢复！', function () {
          return API.del('/api/instances/' + id + '?purge=true').then(function (r) {
            Toast.ok(r.note || '已删除'); location.hash = '#/instances';
          }).catch(function (e) { Toast.err(e.message); });
        }, true);
      });
    }).catch(function (e) {
      el.innerHTML = '<div class="tip err">读取失败：' + U.esc(e.message) + '</div>';
    });
  };

  refresh();
  Store.every(6000, function () {
    if (location.hash.indexOf('#/instances/' + id) !== 0) return;
    API.get('/api/instances/' + id).then(function (d) {
      var was = inst ? inst.status : null;
      inst = d.instance;
      var st = document.getElementById('hd-status');
      if (st) st.innerHTML = C.statusBadge(inst.status);
      var sub = document.querySelector('.page-head .sub');
      if (sub) {
        sub.innerHTML = U.esc(inst.core_label || inst.core_type) +
          (inst.mc_version ? ' · ' + U.esc(inst.mc_version) : '') + ' · 端口 ' + inst.port +
          ' · ' + inst.memory_mb + 'MB · ' +
          (inst.running ? '运行中 ' + U.esc(inst.uptime_text) + ' · PID ' + inst.pid : '未运行');
      }
      if (was !== inst.status) {
        Toast.info('实例状态变为：' + U.statusText(inst.status));
        draw();
      }
    }).catch(function () {});
  }, false);
};
