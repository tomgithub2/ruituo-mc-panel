/* 实例列表：**四种视图** —— 卡片（大卡+sparkline）/ 紧凑（单行，一屏更多）/
   表格（完整字段）/ 分组（按运行状态归堆，一眼看出谁该管）。
   视图的"有哪些 / 默认哪个 / 怎么记住"由 ViewMode 统一管（见 js/viewmode.js）。 */
window.Pages = window.Pages || {};

Pages.instances = function (app) {
  var view = ViewMode.get('instances');
  var filter = '';
  var data = [];

  app.innerHTML = C.shell({
    active: 'instances',
    crumbs: [{ text: '服务器', hash: '#/instances' }, { text: '实例列表' }],
    actions: '<button class="btn sm" id="btn-refresh">' + Icon.svg('refresh', 15) + '刷新</button>' +
             '<button class="btn sm primary" id="btn-new">' + Icon.svg('plus', 15) + '新建实例</button>'
  });
  C.bindShell(app);
  var body = document.getElementById('page-body');

  body.innerHTML = '' +
    '<div class="page-head">' +
      '<div class="titles"><div class="kicker">Instances</div><h1>实例列表</h1>' +
        '<div class="sub" id="head-sub">加载中…</div></div>' +
      '<div class="acts">' +
        '<div class="search-box">' + Icon.svg('search', 15) +
          '<input type="text" id="inst-search" placeholder="搜索实例名 / 核心 / 端口" /></div>' +
        ViewMode.switchHtml('instances', view) +
      '</div>' +
    '</div>' +
    '<div class="grid cols-4 mb" id="stats"></div>' +
    '<div id="inst-list"><div class="loading"><span class="spinner"></span>正在读取实例…</div></div>';

  document.getElementById('btn-new').addEventListener('click', function () { location.hash = '#/create'; });
  document.getElementById('btn-refresh').addEventListener('click', function () { load(true); });
  document.getElementById('inst-search').addEventListener('input', U.debounce(function (e) {
    filter = e.target.value.trim().toLowerCase();
    render();
  }, 200));
  ViewMode.bind(body, 'instances', function (key) {
    view = key;
    render();
  });

  var sparkHist = {};

  function load(showToast) {
    return API.get('/api/instances').then(function (d) {
      data = d.instances || [];
      Store.set({ instances: data });
      C.fillServerSwitch(app);              // 顶栏"快速切换实例"
      if (showToast) Toast.ok('已刷新（' + data.length + ' 个实例）');
      render();
      /* 兜底自检：数据明明拿到了，列表里却还只有那个"正在读取…"占位 →
         说明富视图渲染链路上某处没走到（本次就出过这种事：用户看到"有标题和数字、列表空的"）。
         这时直接退到简化视图，保证实例一定看得见。 */
      var guard = document.getElementById('inst-list');
      if (guard && data.length && guard.querySelector('.loading')) renderPlain();
    }).catch(function (e) {
      if (showToast) Toast.err('刷新失败：' + e.message);
      var box = document.getElementById('inst-list');
      /* 403 且本地权限表也说没权限 → 「权限不足」页；其余按错误类型给人话 + 重试 */
      if (Perm.denyIfForbidden(e, app, { perm: 'log.view', active: 'instances',
                                         crumbs: [{ text: '服务器' }, { text: '实例列表' }] })) return;
      View.showError(box, e, { title: '读取实例列表失败', showStack: true },
                     { onRetry: function () { load(); } });
    });
  }

  function statBlock(label, value, sub) {
    return '<div class="stat"><div class="k">' + U.esc(label) + '</div>' +
           '<div class="v">' + U.esc(value) + '</div><div class="s">' + U.esc(sub || '') + '</div></div>';
  }

  function pushHist(id, cpu, mem) {
    var h = sparkHist[id] || (sparkHist[id] = { cpu: [], mem: [] });
    h.cpu.push(Number(cpu) || 0);
    h.mem.push(Number(mem) || 0);
    if (h.cpu.length > 60) h.cpu.shift();
    if (h.mem.length > 60) h.mem.shift();
    return h;
  }

  function ringPercent(it) {
    if (it.status === 'running') {
      return Math.min(100, Math.max(8, (it.cpu || 0)));
    }
    return it.status === 'crashed' ? 100 : 0;
  }

  /* 单张卡片渲染失败**不能拖垮整个列表**：捕到异常就退化成一行朴素的卡片，
     这样"某一个实例的数据怪"只会让那一张变简单，其余照常显示。 */
  function safeCard(it) {
    try {
      return cardHtml(it);
    } catch (err) {
      try { console.error('[instances] card failed for #' + it.id, err); } catch (e) {}
      return '<div class="inst-card ' + U.esc(it.status || '') + '" data-id="' + it.id + '">' +
        '<div class="inst-top"><div style="min-width:0">' +
          '<div class="inst-name" data-open="' + it.id + '">' + U.esc(it.name) + '</div>' +
          '<div class="inst-meta">卡片渲染出错：' + U.esc((err && err.message) || String(err)) + '</div>' +
        '</div></div>' +
        '<div class="inst-actions"><button class="btn sm" data-act="detail" data-id="' + it.id + '">详情</button></div>' +
        '</div>';
    }
  }

  function cardHtml(it) {
    var h = pushHist(it.id, it.cpu, it.mem_mb);
    return '' +
      '<div class="inst-card ' + U.esc(it.status) + '" data-id="' + it.id + '">' +
        C.ring(it.status, ringPercent(it), it.core_type) +
        '<div class="inst-top">' +
          '<div style="min-width:0">' +
            '<div class="inst-name" data-open="' + it.id + '">' + U.esc(it.name) + '</div>' +
            '<div class="inst-meta">' + U.esc(it.core_label || it.core_type) +
              (it.mc_version ? ' · ' + U.esc(it.mc_version) : '') +
              ' · :' + it.port + ' · ' + it.memory_mb + 'MB</div>' +
          '</div>' +
          '<div class="spacer"></div>' +
          (C.statusBadge(it.status)) +
        '</div>' +
        '<div class="metrics">' +
          C.metric('CPU', (it.running ? (it.cpu || 0).toFixed(1) + '%' : '—')) +
          C.metric('内存', (it.running ? Math.round(it.mem_mb || 0) + 'MB' : '—')) +
          C.metric('玩家', (it.running ? String(it.players || 0) : '—')) +
          C.metric('TPS', (it.tps_available ? Number(it.tps).toFixed(1) : '不可用')) +
        '</div>' +
        '<div class="spark-wrap">' + Chart.sparkline(h.cpu, { height: 38, color: it.status === 'running' ? 'var(--success)' : 'var(--neutral)' }) + '</div>' +
        '<div class="row between small muted">' +
          '<span>' + (it.running ? '运行 ' + U.esc(it.uptime_text) + ' · PID ' + it.pid : '未运行') + '</span>' +
          (it.auto_restart ? '<span class="badge gold">自动重启</span>' : '') +
        '</div>' +
        '<div class="inst-actions">' +
          (it.running
            ? '<button class="btn sm" data-act="restart" data-id="' + it.id + '">' + Icon.svg('restart', 14) + '重启</button>' +
              '<button class="btn sm danger" data-act="stop" data-id="' + it.id + '">' + Icon.svg('stop', 14) + '停止</button>'
            : '<button class="btn sm success" data-act="start" data-id="' + it.id + '">' + Icon.svg('play', 14) + '启动</button>') +
          '<button class="btn sm" data-act="detail" data-id="' + it.id + '">' + Icon.svg('terminal', 14) + '详情</button>' +
        '</div>' +
      '</div>';
  }

  function tableHtml(list) {
    return '<div class="card flush"><div class="table-wrap"><table>' +
      '<thead><tr><th>实例</th><th>核心</th><th>版本</th><th class="mono">端口</th>' +
      '<th class="mono">内存上限</th><th class="mono">CPU</th><th class="mono">内存</th>' +
      '<th class="mono">玩家</th><th class="mono">TPS</th><th>状态</th><th style="text-align:right">操作</th></tr></thead>' +
      '<tbody>' + list.map(function (it) {
        return '<tr>' +
          '<td><a href="#/instances/' + it.id + '">' + U.esc(it.name) + '</a>' +
            (it.auto_restart ? ' <span class="badge gold">自动重启</span>' : '') + '</td>' +
          '<td>' + U.esc(it.core_label || it.core_type) + '</td>' +
          '<td class="mono">' + U.esc(it.mc_version || '-') + '</td>' +
          '<td class="mono num">' + it.port + '</td>' +
          '<td class="mono num">' + it.memory_mb + 'MB</td>' +
          '<td class="mono num">' + (it.running ? (it.cpu || 0).toFixed(1) + '%' : '—') + '</td>' +
          '<td class="mono num">' + (it.running ? Math.round(it.mem_mb || 0) + 'MB' : '—') + '</td>' +
          '<td class="mono num">' + (it.running ? (it.players || 0) : '—') + '</td>' +
          '<td class="mono num">' + (it.tps_available ? Number(it.tps).toFixed(1) : '不可用') + '</td>' +
          '<td>' + C.statusBadge(it.status) + '</td>' +
          '<td style="text-align:right" class="nowrap">' +
            (it.running
              ? '<button class="btn xs" data-act="restart" data-id="' + it.id + '">重启</button> ' +
                '<button class="btn xs danger" data-act="stop" data-id="' + it.id + '">停止</button>'
              : '<button class="btn xs success" data-act="start" data-id="' + it.id + '">启动</button>') +
            ' <button class="btn xs" data-act="detail" data-id="' + it.id + '">详情</button>' +
          '</td></tr>';
      }).join('') + '</tbody></table></div></div>';
  }

  function render() {
    try {
      renderInner();
    } catch (err) {
      /* 渲染异常绝不能静默：以前这里会把异常吞掉，用户看到"有标题和数字、列表空的"。
         现在统一走 View.showError（含堆栈 + 重试 + 简化视图兜底）。 */
      var box2 = document.getElementById('inst-list');
      if (box2) {
        View.showError(box2, err, {
          title: '实例列表渲染失败', showStack: true,
          plain: data.length > 0, plainLabel: '用简化视图显示'
        }, { onRetry: function () { load(); }, onPlain: renderPlain });
      }
      try { console.error('[instances] render failed', err); } catch (e) {}
    }
  }

  /* 简化视图：只用最基础的元素把实例列出来，任何装饰性组件（指示环、sparkline）都不碰。
     作为"富视图渲染失败"时的兜底，保证**永远看得到实例**。 */
  function renderPlain() {
    var box = document.getElementById('inst-list');
    if (!box) return;
    if (!data.length) {
      box.innerHTML = C.empty('box', '还没有任何服务器实例', '先创建一个实例。',
        '<button class="btn primary" data-go="#/create">新建实例</button>');
      View.bindGo(box);
      return;
    }
    box.innerHTML = '<div class="card"><div class="card-head"><h3>实例（简化视图）</h3>' +
      '<div class="right small muted">富视图渲染出错时的兜底显示</div></div>' +
      '<div class="table-wrap"><table><thead><tr><th>实例</th><th>核心</th><th class="mono">端口</th>' +
      '<th class="mono">内存</th><th>状态</th><th style="text-align:right">操作</th></tr></thead><tbody>' +
      data.map(function (it) {
        return '<tr><td><a href="#/instances/' + it.id + '">' + U.esc(it.name) + '</a></td>' +
          '<td>' + U.esc(it.core_label || it.core_type || '') + '</td>' +
          '<td class="mono num">' + (it.port || '') + '</td>' +
          '<td class="mono num">' + (it.memory_mb || '') + 'MB</td>' +
          '<td>' + U.esc(it.status || '') + '</td>' +
          '<td style="text-align:right"><a class="btn xs" href="#/instances/' + it.id + '/console">详情</a></td></tr>';
      }).join('') + '</tbody></table></div></div>';
  }

  function renderInner() {
    var list = data.filter(function (it) {
      if (!filter) return true;
      return (it.name + ' ' + it.core_type + ' ' + (it.mc_version || '') + ' ' + it.port).toLowerCase().indexOf(filter) >= 0;
    });
    var running = data.filter(function (i) { return i.running; }).length;
    var players = data.reduce(function (a, i) { return a + (i.running ? (i.players || 0) : 0); }, 0);
    var mem = data.reduce(function (a, i) { return a + (i.running ? (i.mem_mb || 0) : 0); }, 0);
    var tpsList = data.filter(function (i) { return i.tps_available; });
    var tps = tpsList.length ? (tpsList.reduce(function (a, i) { return a + Number(i.tps); }, 0) / tpsList.length) : null;

    document.getElementById('stats').innerHTML =
      statBlock('实例总数', String(data.length), running + ' 个运行中') +
      statBlock('在线玩家', String(players), '所有实例合计') +
      statBlock('进程内存', Math.round(mem) + ' MB', '实际 RSS 合计') +
      statBlock('平均 TPS', tps === null ? '不可用' : tps.toFixed(1),
        tps === null ? '所有实例都没取到 TPS（需运行中且 RCON 可用）' : tpsList.length + ' 个实例有数据');
    document.getElementById('head-sub').textContent =
      data.length + ' 个实例 · ' + running + ' 个运行中' + (filter ? ' · 已过滤 ' + list.length + ' 条' : '');

    var box = document.getElementById('inst-list');
    if (!data.length) {
      box.innerHTML = C.empty('box', '还没有任何服务器实例',
        '先创建一个实例：选核心（原版 / Paper / Purpur / Fabric / Quilt / Forge / NeoForge）→ 选版本 → 定内存与端口 → 确认下载。也可以先本地上传一个 jar。',
        '<button class="btn primary" onclick="location.hash=\'#/create\'">' + Icon.svg('plus', 15) + '新建第一个实例</button>');
      return;
    }
    if (!list.length) {
      box.innerHTML = C.empty('search', '没有匹配的实例', '试试其它关键字，或清空搜索框。',
        '<button class="btn" id="clear-filter">清空搜索</button>');
      var cf = document.getElementById('clear-filter');
      if (cf) cf.addEventListener('click', function () {
        document.getElementById('inst-search').value = ''; filter = ''; render();
      });
      return;
    }
    box.innerHTML = view === 'compact' ? compactHtml(list)
      : view === 'table' ? tableHtml(list)
      : view === 'grouped' ? groupedHtml(list)
      : '<div class="inst-grid">' + list.map(safeCard).join('') + '</div>';

    box.querySelectorAll('[data-act]').forEach(function (b) {
      b.addEventListener('click', function (e) {
        e.stopPropagation();
        var id = b.getAttribute('data-id');
        var act = b.getAttribute('data-act');
        if (act === 'detail') { location.hash = '#/instances/' + id; return; }
        doAction(id, act, b);
      });
    });
    box.querySelectorAll('[data-open]').forEach(function (el) {
      el.addEventListener('click', function () { location.hash = '#/instances/' + el.getAttribute('data-open'); });
    });
    if (view === 'grouped') bindGroups(box);
  }

  /* ---------------------------------------------------------------- 紧凑视图
     一行一个实例：状态点 + 名称 + 核心/版本 + CPU/内存/玩家 + 启停。
     目标是在 1080p 上**一屏看清二十来个实例**，所以砍掉 sparkline 与指示环，
     但保留最关键的"能不能点、谁在跑"。 */
  function compactHtml(list) {
    return '<div class="card flush dense-list">' +
      '<div class="dense-head"><span>实例</span><span>状态</span><span class="num">CPU</span>' +
        '<span class="num">内存</span><span class="num">玩家</span><span>运行时长</span>' +
        '<span class="num">端口</span><span></span></div>' +
      list.map(function (it) {
        return '<div class="dense-row" data-id="' + it.id + '">' +
          '<span class="dense-name" data-open="' + it.id + '">' +
            '<i class="dot ' + U.esc(it.status) + '"></i>' + U.esc(it.name) +
            '<em>' + U.esc(it.core_label || it.core_type) +
              (it.mc_version ? ' ' + U.esc(it.mc_version) : '') + '</em></span>' +
          '<span>' + C.statusBadge(it.status) + '</span>' +
          '<span class="num mono">' + (it.running ? (Number(it.cpu) || 0).toFixed(2) + '%' : '—') + '</span>' +
          '<span class="num mono">' + (it.running ? Math.round(it.mem_mb || 0) + 'MB' : '—') + '</span>' +
          '<span class="num mono">' + (it.running ? (it.players || 0) : '—') + '</span>' +
          '<span class="mono small">' + (it.running ? U.esc(it.uptime_text) : '—') + '</span>' +
          '<span class="num mono small">:' + it.port + '</span>' +
          '<span class="dense-acts">' +
            (it.running
              ? '<button class="btn xs" data-act="stop" data-id="' + it.id + '">停止</button>' +
                '<button class="btn xs" data-act="restart" data-id="' + it.id + '">重启</button>'
              : '<button class="btn xs" data-act="start" data-id="' + it.id + '">启动</button>') +
            '<button class="btn xs ghost" data-act="detail" data-id="' + it.id + '">详情</button>' +
          '</span>' +
        '</div>';
      }).join('') + '</div>';
  }

  /* ---------------------------------------------------------------- 分组视图
     按**运行状态**归堆：异常 / 运行中 / 启动中 / 已停止。
     顺序刻意把"异常"放最前 —— 该管的排在上面。空组不渲染，避免一堆空标题。 */
  var GROUPS = [
    { key: 'crashed', label: '异常', hint: '崩溃或异常退出，需要处理', cls: 'err' },
    { key: 'running', label: '运行中', hint: '正在提供服务', cls: 'ok' },
    { key: 'transition', label: '启动中 / 停止中', hint: '状态正在变化', cls: '' },
    { key: 'stopped', label: '已停止', hint: '未运行', cls: '' }
  ];

  function groupOf(it) {
    if (it.status === 'crashed') return 'crashed';
    if (it.running || it.status === 'running') return 'running';
    if (it.status === 'starting' || it.status === 'stopping') return 'transition';
    return 'stopped';
  }

  function groupedHtml(list) {
    var buckets = {};
    list.forEach(function (it) {
      var g = groupOf(it);
      (buckets[g] = buckets[g] || []).push(it);
    });
    var out = GROUPS.filter(function (g) { return (buckets[g.key] || []).length; }).map(function (g) {
      var items = buckets[g.key];
      var cpu = items.reduce(function (a, i) { return a + (i.running ? (Number(i.cpu) || 0) : 0); }, 0);
      var mem = items.reduce(function (a, i) { return a + (i.running ? (i.mem_mb || 0) : 0); }, 0);
      return '<div class="group-block" data-group="' + g.key + '">' +
        '<div class="group-head" data-toggle="' + g.key + '">' +
          '<span class="chev">' + Icon.svg('chevronDown', 14) + '</span>' +
          '<h3>' + U.esc(g.label) + '</h3>' +
          '<span class="badge ' + g.cls + '">' + items.length + '</span>' +
          '<span class="small muted">' + U.esc(g.hint) + '</span>' +
          '<span class="right small muted mono">' +
            (items.some(function (i) { return i.running; })
              ? 'CPU ' + cpu.toFixed(2) + '% · 内存 ' + Math.round(mem) + ' MB' : '') +
          '</span>' +
        '</div>' +
        '<div class="group-body inst-grid">' + items.map(safeCard).join('') + '</div>' +
      '</div>';
    }).join('');

    /* 折叠状态记在 localStorage，刷新后保持 */
    var collapsed = {};
    try { collapsed = JSON.parse(localStorage.getItem('mc_inst_group_collapsed') || '{}') || {}; } catch (e) {}
    out += '<script type="application/json" id="group-collapsed">' +
      JSON.stringify(collapsed).replace(/</g, '\\u003c') + '</script>';
    return out;
  }

  function bindGroups(box) {
    var state = {};
    var seed = box.querySelector('#group-collapsed');
    if (seed) { try { state = JSON.parse(seed.textContent) || {}; } catch (e) {} }
    box.querySelectorAll('[data-toggle]').forEach(function (h) {
      var key = h.getAttribute('data-toggle');
      var block = h.parentNode;
      if (state[key]) block.classList.add('collapsed');
      h.addEventListener('click', function () {
        block.classList.toggle('collapsed');
        state[key] = block.classList.contains('collapsed');
        try { localStorage.setItem('mc_inst_group_collapsed', JSON.stringify(state)); } catch (e) {}
      });
    });
  }

  function doAction(id, act, btn) {    var it = data.filter(function (x) { return String(x.id) === String(id); })[0] || {};
    var map = { start: '启动', stop: '停止', restart: '重启', kill: '强制结束' };
    var run = function () {
      C.withLoading(btn, API.post('/api/instances/' + id + '/' + act, {}).then(function (r) {
        Toast.ok('已请求' + map[act] + '：' + (it.name || id) + (r.note ? '（' + r.note + '）' : ''));
        setTimeout(function () { load(); }, 600);
      }).catch(function (e) {
        Toast.err(map[act] + '失败：' + e.message);
      }));
    };
    if (act === 'stop') {
      C.Modal.confirm('停止实例', '将向控制台发送 stop 命令优雅停止「' + (it.name || id) +
        '」，30 秒未退出则强制结束进程树。确定继续？', run);
    } else run();
  }

  load();
  // 列表页每 4 秒刷新一次状态（比 1 秒监控轻，且不到秒级抖动）
  Store.every(4000, function () {
    if (location.hash.indexOf('#/instances') !== 0) return;
    API.get('/api/instances').then(function (d) {
      data = d.instances || [];
      Store.set({ instances: data });
      render();
    }).catch(function () {});
  }, false);
};
