/* Hash 路由
   ============================================================================
   已经踩过的坑（改这里之前先读）：

   1. **不能用布尔标志当"权限已装载"的闸门**（曾用 `_permReady = true` 再复位）。
      某条路径把它置位后没复位（401/网络抖动）→ 路由**再也不渲染任何页面**，
      表现为白屏或停在登录页。
   2. **异步装载期间 hash 可能已经变了**（#/accounts → 未登录被判到 #/login →
      补上 token 后又回 #/accounts）。等完再渲染时必须用**此刻**的 hash；
      并且只有最后发起的那次 resolve 允许画页面，否则先发后完成的会把新页面盖掉。
   3. **页面 handler 抛异常必须可见**：以前异常直接冒泡出 resolve()，
      页面停在上一帧（用户看到"框架在、内容区空了"）。
      现在渲染由 `View.boot()` 包住，异常一律变成可读的错误卡片。

   现在的做法：每次 resolve 领一个自增序号；异步链结束后只有"序号仍是最新"的那次才渲染。
   权限装载只是体验优化（少闪一下无权项），失败也必须渲染 —— 安全边界在后端。
   ========================================================================== */
window.Router = (function () {
  var routes = [];
  var current = null;
  var _seq = 0;

  function add(pattern, handler) {
    var keys = [];
    var rx = new RegExp('^' + pattern.replace(/:[A-Za-z_]+/g, function (m) {
      keys.push(m.slice(1));
      return '([^/]+)';
    }) + '$');
    routes.push({ rx: rx, keys: keys, handler: handler, pattern: pattern });
  }

  function parse() {
    var h = location.hash || '#/dashboard';       // 首屏 = 仪表盘
    var qidx = h.indexOf('?');
    var query = qidx >= 0 ? U.qs(h.slice(qidx + 1)) : {};
    var path = qidx >= 0 ? h.slice(0, qidx) : h;
    return { path: path, query: query };
  }

  function go(hash) { location.hash = hash; }

  /* 身份 + 权限装载：失败/超时都必须收尾，绝不允许挂住路由 */
  function loadContext() {
    var me = API.get('/api/auth/me').then(function (d) {
      Store.set({ user: d.user });
    }).catch(function (e) {
      console.error('[router] /api/auth/me', e);
    });
    var perms = (window.Perm ? Perm.load(true) : Promise.resolve(null)).catch(function (e) {
      console.error('[router] /api/me/permissions', e);
    });
    var timeout = new Promise(function (res) { setTimeout(res, 8000); });
    return Promise.race([Promise.all([me, perms]), timeout]);
  }

  function resolve() {
    var seq = ++_seq;
    var p = parse();
    var isLogin = (p.path === '#/login' || p.path === '' || p.path === '#');
    var logged = !!API.token();
    if (!logged && !isLogin) { location.hash = '#/login'; return; }
    if (logged && isLogin) { location.hash = '#/dashboard'; return; }
    Store.clearPollers();
    if (!logged) { render(seq); return; }
    loadContext().then(function () { render(seq); }, function () { render(seq); });
  }

  function render(seq) {
    if (seq !== _seq) return;                // 有更新的一次 resolve，放弃这次
    var p = parse();                         // 用此刻的 hash
    var matched = null;
    for (var i = 0; i < routes.length; i++) {
      var m = routes[i].rx.exec(p.path);
      if (m) {
        var params = {};
        routes[i].keys.forEach(function (k, idx) { params[k] = decodeURIComponent(m[idx + 1]); });
        matched = { r: routes[i], params: params };
        break;
      }
    }
    current = { path: p.path, query: p.query, params: matched ? matched.params : {} };
    var app = document.getElementById('app');
    if (!app) return;

    if (!matched) {
      app.innerHTML = C.shell({ active: '', crumbs: [{ text: '404' }] });
      C.bindShell(app);
      document.getElementById('page-body').innerHTML = C.empty('search', '页面不存在',
        '这个地址没有对应的页面，可能是链接过期或输入有误。',
        '<button class="btn primary" data-go="#/instances">返回实例列表</button>');
      View.bindGo(document.getElementById('page-body'));
      return;
    }
    /* 页面自己用 View.boot() 兜异常；这里再包一层是防"handler 连 boot 都没进就抛"。 */
    function doRender() {
      try {
        matched.r.handler(app, current);
      } catch (err) {
        console.error('[router] handler threw', err);
        var body = document.getElementById('page-body') || app;
        View.showError(body, err, { title: '页面渲染失败', showStack: true },
                       { onRetry: function () { resolve(); } });
      }
    }

    /* 路由切换的平滑过渡：浏览器支持 View Transitions 就交给它做淡入淡出，
       不支持（或用户要减少动效）就按原来的方式直接渲染 —— 行为不变，
       只是多一层渐进增强。 */
    var reduced = window.matchMedia &&
                  window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    if (!reduced && typeof document.startViewTransition === 'function') {
      try {
        document.startViewTransition(doRender);
        return;
      } catch (e) {
        console.warn('[router] startViewTransition 失败，回退直接渲染', e);
      }
    }
    doRender();
  }

  function start() {
    View.installGlobalHandlers();
    window.addEventListener('hashchange', resolve);
    resolve();
  }

  return { add: add, go: go, start: start, refresh: resolve, current: function () { return current; } };
})();
