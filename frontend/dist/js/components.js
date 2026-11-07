/* 复用组件：外壳布局（含抽屉侧栏）、弹窗、状态徽标（pill）、指标格、下载进度 */
window.C = (function () {
  /* 主题按钮：**跟随系统 / 暗色 / 亮色** 三态循环（单色皮肤）。
     `auto` 由 theme-mono.css 按 `prefers-color-scheme` 决定明暗；
     `dark` / `light` 是显式指定。面板设置页里仍可选旧的彩色变体，
     这里只负责顶栏这个"最常用"的循环。 */
  var THEME_CYCLE = ['auto', 'dark', 'light'];
  var THEME_LABEL = { auto: '跟随系统', dark: '暗色', light: '亮色',
                      darkgold: '暗色（金）', lightgold: '亮色纸面' };
  function themeLabel(t) { return THEME_LABEL[t] || t || '跟随系统'; }
  function themeNext(t) {
    var i = THEME_CYCLE.indexOf(t);
    return THEME_CYCLE[(i + 1) % THEME_CYCLE.length];
  }
  /* 导航项：`perm` 走权限点，`flag` 走 `/api/me/permissions` 的派生布尔。
     ⚠️ 每个入口都必须挂上**与后端一致**的门禁，否则会出现"菜单点得到、页面 403"
     的死循环（普通用户点「设置」「审计日志」→ 后端 403 → 内容区空）。
     后端对应关系：
       设置       → `system.settings`（只有超管）   → flags.system_settings
       审计日志   → `audit.view`（超管/管理员）     → flags.view_audit
       账号管理   → `user.manage`（只有超管）       → perm user.manage
       新建实例   → `instance.create`              → perm instance.create
       实例列表   → 登录即可（列表本身按归属过滤）    → 无门禁 */
  var NAV = [
    { group: '概览', items: [
      { id: 'dashboard', label: '仪表盘', icon: 'grid', hash: '#/dashboard' }
    ] },
    { group: '服务器', items: [
      { id: 'instances', label: '实例列表', icon: 'instances', hash: '#/instances' },
      { id: 'create', label: '新建实例', icon: 'plus', hash: '#/create', perm: 'instance.create' }
    ] },
    { group: '面板', items: [
      { id: 'settings', label: '设置', icon: 'settings', hash: '#/settings', flag: 'system_settings' },
      { id: 'accounts', label: '账号管理', icon: 'users', hash: '#/accounts', perm: 'user.manage' },
      { id: 'audit', label: '审计日志', icon: 'audit', hash: '#/audit', flag: 'view_audit' }
    ] }
  ];

  function navAllowed(it) {
    if (!window.Perm) return !it.perm && !it.flag;
    if (it.perm && !Perm.has(it.perm)) return false;
    if (it.flag) {
      var f = (Perm.flags && Perm.flags()) || {};
      if (!f[it.flag]) return false;
    }
    return true;
  }
  function navHtml(active) {
    var h = '';
    NAV.forEach(function (g) {
      var items = g.items.filter(navAllowed);
      if (!items.length) return;              // 整组都没权限就别留个空标题
      h += '<div class="nav-group">' + U.esc(g.group) + '</div>';
      items.forEach(function (it) {
        /* 按权限隐藏菜单项（后端仍必须兜底 403 —— 前端隐藏不算安全） */
        h += '<div class="nav-item' + (it.id === active ? ' active' : '') + '" data-nav="' + it.hash +
             '" role="link" tabindex="0">' + Icon.svg(it.icon, 17) + '<span>' + U.esc(it.label) + '</span></div>';
      });
    });
    return h;
  }

  /* 状态 pill：运行=绿(呼吸点)、启动中=黄、停止=灰、异常=红、备份=蓝 */
  function statusBadge(status, text) {
    var cls = status || 'stopped';
    return '<span class="badge ' + cls + '"><span class="dot"></span>' +
           U.esc(text || U.statusText(status)) + '</span>';
  }

  function metric(k, v, sub) {
    return '<div class="metric"><div class="k">' + U.esc(k) + '</div><div class="v">' + U.esc(v) + '</div>' +
           (sub ? '<div class="k">' + U.esc(sub) + '</div>' : '') + '</div>';
  }

  /* 运行指示环（视觉记忆点 ①）：百分比弧形 + 中间核心名缩写 */
  function ring(status, percent, core) {
    var r = 15, c = 2 * Math.PI * r;
    var pct = Math.max(0, Math.min(100, Number(percent) || 0));
    var off = c - (pct / 100) * c;
    var abbr = String(core || 'MC').slice(0, 3).toUpperCase();
    return '<div class="ring" title="' + U.esc(U.statusText(status)) + '">' +
      '<svg width="36" height="36" viewBox="0 0 36 36">' +
        '<circle class="ring-track" cx="18" cy="18" r="' + r + '" fill="none" stroke-width="2.5"/>' +
        '<circle class="ring-arc" cx="18" cy="18" r="' + r + '" fill="none" stroke-width="2.5" ' +
          'stroke-linecap="round" stroke-dasharray="' + c.toFixed(1) + '" stroke-dashoffset="' + off.toFixed(1) + '"/>' +
      '</svg><span class="core">' + U.esc(abbr) + '</span></div>';
  }

  /* 设计过的空状态：图形 + 一句人话 + 主行动 */
  function empty(icon, title, desc, action) {
    return '<div class="empty"><div class="art">' + Icon.svg(icon || 'box', 34) + '</div>' +
      '<div class="t">' + U.esc(title) + '</div>' +
      '<div class="d">' + U.esc(desc) + '</div>' + (action || '') + '</div>';
  }

  function shell(opts) {
    var crumbs = (opts.crumbs || []).map(function (c, i, arr) {
      if (i === arr.length - 1) return '<span class="cur">' + U.esc(c.text) + '</span>';
      if (c.hash) return '<a href="' + c.hash + '">' + U.esc(c.text) + '</a><span class="sep">/</span>';
      return '<span>' + U.esc(c.text) + '</span><span class="sep">/</span>';
    }).join('');
    var user = (Store.state.user || {}).username || '';
    /* 顶栏角色徽标：四色区分（超级管理员/管理员/普通用户/只读），全部走 token 体系 */
    var rm = (window.Perm && Perm.roleMeta()) || {};
    var roleBadge = rm.name
      ? '<span class="badge ' + (rm.cls || '') + '" title="' + U.esc(rm.desc || '') + '">' +
        Icon.svg('roleBadge', 12) + U.esc(rm.name) + '</span>'
      : '';
    var theme = document.documentElement.getAttribute('data-theme') || 'auto';
    var dens = document.documentElement.getAttribute('data-density') || 'comfy';
    /* 浏览器标签页标题跟着当前页面走：以前所有页面都叫「芮拓MC面板」，
       开几个标签就分不清哪个是实例详情、哪个是设置。 */
    try {
      var last = (opts.crumbs || []).filter(function (c) { return c && c.text; }).pop();
      document.title = (last ? last.text + ' · ' : '') + '芮拓MC面板';
    } catch (e) {}
    return '' +
      '<div class="shell">' +
        '<aside class="sidebar" id="mc-sidebar">' +
          '<div class="brand"><div class="brand-logo">芮拓</div>' +
            '<div><div class="brand-name">芮拓MC面板</div>' +
            '<div class="brand-sub">Server Panel</div></div></div>' +
          '<nav class="nav">' + navHtml(opts.active || '') + '</nav>' +
          '<div class="nav-foot">' +
            (user ? '<div class="row tight">' + Icon.svg('shield', 14) + '<span>' + U.esc(user) + '</span></div>' : '') +
            '<div class="mt row tight">' +
              '<button class="btn sm ghost" id="btn-theme" title="切换主题（跟随系统 / 暗色 / 亮色）">' +
                Icon.svg('spark', 14) + U.esc(themeLabel(theme)) +
              '</button>' +
              '<button class="btn sm ghost" id="btn-density" title="切换信息密度">' +
                Icon.svg('sliders', 14) + U.esc(dens === 'compact' ? '紧凑' : '舒适') + '</button>' +
            '</div>' +
            '<div class="mt"><button class="btn sm ghost block" id="btn-logout">' + Icon.svg('logout', 14) + '退出登录</button></div>' +
          '</div>' +
        '</aside>' +
        '<div class="main">' +
          '<header class="topbar">' +
            '<button class="btn ghost btn-icon drawer-btn" id="btn-drawer" aria-label="菜单">' + Icon.svg('instances', 18) + '</button>' +
            '<div class="crumbs">' + crumbs + '</div>' +
            '<div class="topbar-right">' +
              /* 服务器快速切换（顶栏在实例间跳转）—— 数据由 pages 通过
                 Store.set({instances:[...]}) 提供；没数据时整个控件不渲染，避免空壳。 */
              serverSwitch(opts.serverId) +
              '<span class="top-clock" id="mc-clock"></span>' +
              roleBadge + (opts.actions || '') + '</div>' +
          '</header>' +
          '<div class="content" id="page-body"></div>' +
        '</div>' +
      '</div>' +
      Bg.layerHtml();
  }

  /* 顶栏服务器下拉：快速在实例间跳转（跳该实例的控制台）。
     壳层渲染时实例列表往往**还没加载**，所以这里只给一个占位容器，
     数据到了由 `C.fillServerSwitch()` 填充 —— 否则控件永远不出现。 */
  function serverSwitch(currentId) {
    return '<span class="server-switch-slot" data-server-slot="' +
      U.esc(currentId === undefined || currentId === null ? '' : currentId) + '"></span>';
  }

  function fillServerSwitch(root, currentId) {
    var host = (root || document).querySelector('[data-server-slot]');
    if (!host) return;
    if (currentId === undefined || currentId === null) {
      currentId = host.getAttribute('data-server-slot') || '';
    }
    var list = Store.state.instances || [];
    if (!list.length) { host.innerHTML = ''; return; }
    var cur = list.filter(function (x) { return String(x.id) === String(currentId); })[0];
    var label = cur ? cur.name : '快速切换实例';
    host.innerHTML = '<label class="server-switch" title="快速切换到某个实例的控制台">' +
      Icon.svg('server', 14) +
      '<select id="topbar-server">' +
        '<option value="">' + U.esc(label) + '</option>' +
        list.map(function (it) {
          return '<option value="' + it.id + '"' +
            (String(it.id) === String(currentId) ? ' selected' : '') + '>' +
            U.esc(it.name) + (it.running ? ' ●' : '') + '</option>';
        }).join('') +
      '</select></label>';
    var sw = host.querySelector('#topbar-server');
    if (sw) sw.addEventListener('change', function () {
      if (!sw.value) return;
      location.hash = '#/instances/' + sw.value + '/console';
    });
  }

  function bindShell(root) {
    root.querySelectorAll('[data-nav]').forEach(function (el) {
      el.addEventListener('click', function () { location.hash = el.getAttribute('data-nav'); });
      el.addEventListener('keydown', function (e) {
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); location.hash = el.getAttribute('data-nav'); }
      });
    });
    var lo = root.querySelector('#btn-logout');
    if (lo) lo.addEventListener('click', function () {
      API.post('/api/auth/logout', {}).catch(function () {}).then(function () {
        API.clearToken(); Store.set({ user: null }); location.hash = '#/login';
      });
    });
    var th = root.querySelector('#btn-theme');
    if (th) th.addEventListener('click', function () {
      var cur = document.documentElement.getAttribute('data-theme') || 'auto';
      var nxt = themeNext(cur);
      document.documentElement.setAttribute('data-theme', nxt);
      try { localStorage.setItem('mc_theme', nxt); } catch (e) {}
      Toast.info('主题：' + themeLabel(nxt));
      Router.refresh();
    });
    var dn = root.querySelector('#btn-density');
    if (dn) dn.addEventListener('click', function () {
      var cur = document.documentElement.getAttribute('data-density') || 'comfy';
      var nxt = cur === 'compact' ? 'comfy' : 'compact';
      document.documentElement.setAttribute('data-density', nxt);
      try { localStorage.setItem('mc_density', nxt); } catch (e) {}
      dn.innerHTML = Icon.svg('sliders', 14) + (nxt === 'compact' ? '紧凑' : '舒适');
      Toast.info('信息密度：' + (nxt === 'compact' ? '紧凑' : '舒适'));
    });
    var db = root.querySelector('#btn-drawer');
    var sb = root.querySelector('#mc-sidebar');
    if (db && sb) db.addEventListener('click', function () { sb.classList.toggle('open'); });
    var sw = root.querySelector('#topbar-server');
    if (sw) sw.addEventListener('change', function () {
      if (!sw.value) return;
      location.hash = '#/instances/' + sw.value + '/console';
    });    Bg.mount();
    startClock();
  }

  /* 顶栏时钟（可在设置里关闭） */
  var _clockTimer = null;
  function startClock() {
    if (_clockTimer) clearInterval(_clockTimer);
    function tick() {
      var el = document.getElementById('mc-clock');
      if (!el) return;
      var d = new Date();
      function p(x) { return x < 10 ? '0' + x : '' + x; }
      el.textContent = p(d.getHours()) + ':' + p(d.getMinutes()) + ':' + p(d.getSeconds());
    }
    tick();
    _clockTimer = setInterval(tick, 1000);
  }

  /* ============================================================ 背景层
     多图 + 拖拽排序 + 每张独立遮罩 + 轮播（10–600s）+ 启动策略
     存储：localStorage 'mc_bg'（图片以 dataURL 存，避免额外后端接口）
     —— 后端接口按 docs/API-CONTRACT.md 第 2 节登记后再切换为服务端存储。 */
  var BG_KEY = 'mc_bg';
  var Bg = {
    cfg: null,
    timer: null,
    idx: 0,

    load: function () {
      if (this.cfg) return this.cfg;
      var d = { items: [], interval: 30, enabled: true, blur: 0, maskAlpha: 0.55,
                strategy: 'keep', vignette: 0.8, maskColor: '#000000' };
      try {
        var raw = localStorage.getItem(BG_KEY);
        if (raw) d = Object.assign(d, JSON.parse(raw));
      } catch (e) {}
      var de = document.documentElement;
      de.setAttribute('data-has-bg', d.items && d.items.length && d.enabled ? 'on' : 'off');
      de.setAttribute('data-glass-top', de.getAttribute('data-glass-top') || 'on');
      de.setAttribute('data-glass-side', de.getAttribute('data-glass-side') || 'off');
      de.setAttribute('data-glass-bottom', de.getAttribute('data-glass-bottom') || 'on');
      de.setAttribute('data-show-crumbs', de.getAttribute('data-show-crumbs') || 'on');
      de.setAttribute('data-show-clock', de.getAttribute('data-show-clock') || 'on');
      this.cfg = d;
      return d;
    },

    save: function () {
      try { localStorage.setItem(BG_KEY, JSON.stringify(this.cfg)); } catch (e) {
        Toast.err('背景保存失败（可能是图片过大超出 localStorage 配额）');
      }
      var de = document.documentElement;
      de.setAttribute('data-has-bg',
        this.cfg.items.length && this.cfg.enabled ? 'on' : 'off');
    },

    layerHtml: function () {
      var c = this.load();
      if (!c.items.length || !c.enabled) return '<div id="mc-bg"></div>';
      return '<div id="mc-bg">' + c.items.map(function (it, i) {
        return '<div class="bg-item' + (i === 0 ? ' active' : '') + '" data-i="' + i + '" ' +
          'style="background-image:url(\'' + it.data + '\');filter:blur(' + (it.blur || 0) + 'px)">' +
          '<div class="bg-mask" style="background:' + (c.maskColor || '#000') +
            ';opacity:' + (it.mask === undefined ? c.maskAlpha : it.mask) + '"></div></div>';
      }).join('') +
      '<div class="bg-vignette" style="opacity:' + c.vignette + '"></div>' +
      '<div class="bg-grain"></div></div>';
    },

    mount: function () {
      var c = this.load();
      if (this.timer) { clearInterval(this.timer); this.timer = null; }
      if (!c.items.length || !c.enabled) return;
      var self = this;
      var layers = Array.prototype.slice.call(document.querySelectorAll('#mc-bg .bg-item'));
      if (!layers.length) return;
      // 启动策略：keep=保持第一张；sequential=顺序；random=随机
      if (c.strategy === 'random') this.idx = Math.floor(Math.random() * layers.length);
      else if (c.strategy === 'sequential') this.idx = 0;
      else this.idx = 0;
      layers.forEach(function (l, i) { l.classList.toggle('active', i === self.idx); });
      if (layers.length > 1) {
        var iv = Math.min(600, Math.max(10, Number(c.interval) || 30)) * 1000;
        this.timer = setInterval(function () {
          var next = c.strategy === 'random'
            ? Math.floor(Math.random() * layers.length)
            : (self.idx + 1) % layers.length;
          if (next === self.idx) return;
          layers[self.idx].classList.remove('active');
          layers[next].classList.add('active');
          self.idx = next;
        }, iv);
      }
    },

    add: function (dataUrl, name) {
      var c = this.load();
      c.items.push({ data: dataUrl, name: name || ('背景 ' + (c.items.length + 1)), mask: undefined, blur: 0 });
      this.save();
    },

    remove: function (i) {
      var c = this.load();
      c.items.splice(i, 1);
      this.save();
    },

    move: function (from, to) {
      var c = this.load();
      if (to < 0 || to >= c.items.length || from === to) return;
      var it = c.items.splice(from, 1)[0];
      c.items.splice(to, 0, it);
      this.save();
    }
  };
  window.Bg = Bg;

  /* 弹窗
     ============================================================================
     交互上补齐了五件以前没做的事（都是实际操作里会痛的）：
     1. **焦点陷阱**：Tab / Shift+Tab 在弹窗内循环，不会跑到背后的页面上；
     2. **自动聚焦**：优先 [autofocus]，其次第一个输入框，最后才是「确定」；
     3. **锁背景滚动**：弹窗打开时 body 不再跟着滚（手机上尤其明显）；
     4. **回车提交**：焦点在单行输入框里按 Enter = 点「确定」（textarea 除外）；
     5. **焦点归位**：关闭后焦点回到打开它的那个按钮，键盘操作不会丢位置。
     ========================================================================== */
  var Modal = {
    _prevFocus: null,
    _trap: null,
    open: function (opts) {
      Modal.close(true);                        // 关掉上一个，但不要触发焦点归位
      Modal._prevFocus = document.activeElement;

      var mask = document.createElement('div');
      mask.className = 'modal-mask';
      mask.id = 'mc-modal';
      mask.innerHTML = '' +
        '<div class="modal' + (opts.wide ? ' wide' : '') + '" role="dialog" aria-modal="true">' +
          '<div class="modal-head"><h3>' + U.esc(opts.title || '') + '</h3>' +
            '<div class="spacer"></div>' +
            '<button class="btn ghost btn-icon" data-x aria-label="关闭">' + Icon.svg('kill', 15) + '</button></div>' +
          '<div class="modal-body">' + (opts.body || '') + '</div>' +
          (opts.footer === false ? '' :
            '<div class="modal-foot">' +
              '<button class="btn" data-x>' + U.esc(opts.cancelText || '取消') + '</button>' +
              '<button class="btn ' + (opts.danger ? 'danger' : 'primary') + '" data-ok>' +
                U.esc(opts.okText || '确定') + '</button></div>') +
        '</div>';
      document.body.appendChild(mask);
      document.body.classList.add('modal-open');   // 锁背景滚动

      mask.querySelectorAll('[data-x]').forEach(function (b) {
        b.addEventListener('click', function () { Modal.close(); if (opts.onCancel) opts.onCancel(); });
      });
      var ok = mask.querySelector('[data-ok]');
      if (ok) ok.addEventListener('click', function () {
        var r = opts.onOk ? opts.onOk(mask, ok) : undefined;
        if (r !== false && !(r && r.then)) Modal.close();
        if (r && r.then) {
          ok.classList.add('loading');
          r.then(function (keep) { ok.classList.remove('loading'); if (!keep) Modal.close(); })
           .catch(function () { ok.classList.remove('loading'); });
        }
      });
      mask.addEventListener('click', function (e) { if (e.target === mask) Modal.close(); });
      document.addEventListener('keydown', Modal._esc);

      /* 焦点陷阱 + 回车提交 */
      Modal._trap = function (e) {
        if (!document.getElementById('mc-modal')) return;
        if (e.key === 'Enter') {
          var t = e.target;
          if (ok && t && t.tagName === 'INPUT' && !t.disabled) {
            e.preventDefault();
            ok.click();
          }
          return;
        }
        if (e.key !== 'Tab') return;
        var nodes = mask.querySelectorAll(
          'a[href],button:not([disabled]),input:not([disabled]),select:not([disabled]),' +
          'textarea:not([disabled]),[tabindex]:not([tabindex="-1"])');
        var list = Array.prototype.filter.call(nodes, function (n) {
          return n.offsetParent !== null || n === document.activeElement;
        });
        if (!list.length) return;
        var first = list[0], last = list[list.length - 1];
        if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
        else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
      };
      document.addEventListener('keydown', Modal._trap);

      if (opts.onMount) opts.onMount(mask);
      /* 自动聚焦：把键盘用户直接送到能动手的地方 */
      var auto = mask.querySelector('[autofocus]') ||
                 mask.querySelector('.modal-body input:not([type=hidden]):not([disabled]),' +
                                    '.modal-body select:not([disabled]),.modal-body textarea:not([disabled])') ||
                 ok || mask.querySelector('[data-x]');
      if (auto) { try { auto.focus(); if (auto.select && auto.tagName === 'INPUT') auto.select(); } catch (e) {} }
      return mask;
    },
    _esc: function (e) { if (e.key === 'Escape') Modal.close(); },
    close: function (silent) {
      var m = document.getElementById('mc-modal');
      if (m) m.parentNode.removeChild(m);
      document.removeEventListener('keydown', Modal._esc);
      if (Modal._trap) { document.removeEventListener('keydown', Modal._trap); Modal._trap = null; }
      document.body.classList.remove('modal-open');
      if (silent) return;
      var back = Modal._prevFocus;
      Modal._prevFocus = null;
      if (back && back.focus && document.contains(back)) { try { back.focus(); } catch (e) {} }
    },
    confirm: function (title, text, onOk, danger) {
      Modal.open({
        title: title, danger: !!danger, okText: danger ? '确认执行' : '确定',
        body: '<div class="tip' + (danger ? ' err' : '') + '">' + U.esc(text) + '</div>',
        onOk: function () { return onOk(); }
      });
    }
  };

  /* 按钮 loading 态封装 */
  function withLoading(btn, promise) {
    if (btn) { btn.classList.add('loading'); btn.disabled = true; }
    return Promise.resolve(promise).finally(function () {
      if (btn) { btn.classList.remove('loading'); btn.disabled = false; }
    });
  }

  function trackDownload(taskId, el, onDone) {
    var timer = setInterval(function () {
      API.get('/api/downloads/' + taskId).then(function (d) {
        if (!el || !el.parentNode) { clearInterval(timer); return; }
        var pct = d.percent || 0;
        el.innerHTML = '<div class="row small between"><span>' + U.esc(d.label || '') + '</span>' +
          '<span class="mono num">' + pct + '%' + (d.speed ? ' · ' + d.speed + ' MB/s' : '') + '</span></div>' +
          '<div class="progress"><i style="width:' + pct + '%"></i></div>' +
          (d.status === 'failed' ? '<div class="small" style="color:var(--danger)">失败：' + U.esc(d.error) + '</div>' : '');
        if (d.status === 'done' || d.status === 'failed') {
          clearInterval(timer);
          if (onDone) onDone(d);
        }
      }).catch(function (e) {
        clearInterval(timer);
        if (el && el.parentNode) el.innerHTML = '<div class="small" style="color:var(--danger)">进度查询失败：' + U.esc(e.message) + '</div>';
      });
    }, 700);
    return timer;
  }

  /* 数字计数动画（200ms） */
  function countUp(el, from, to, ms) {
    if (!el) return;
    var reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    if (reduce || from === to) { el.textContent = to; return; }
    var t0 = performance.now();
    ms = ms || 200;
    function step(t) {
      var k = Math.min(1, (t - t0) / ms);
      var v = from + (to - from) * (1 - Math.pow(1 - k, 3));
      el.textContent = (Math.round(v * 10) / 10);
      if (k < 1) requestAnimationFrame(step);
      else el.textContent = to;
    }
    requestAnimationFrame(step);
  }

  return { shell: shell, bindShell: bindShell, navHtml: navHtml, statusBadge: statusBadge,
           metric: metric, ring: ring, empty: empty, Modal: Modal, trackDownload: trackDownload,
           withLoading: withLoading, countUp: countUp, NAV: NAV, Bg: Bg,
           fillServerSwitch: fillServerSwitch, themeLabel: themeLabel };
})();
