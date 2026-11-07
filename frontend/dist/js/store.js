/* 极简全局状态 + 提示 */
window.Store = (function () {
  var state = {
    user: null,
    overview: null,
    instances: [],
    settings: null,
    pollers: []
  };
  var subs = [];

  function set(patch) {
    Object.keys(patch).forEach(function (k) { state[k] = patch[k]; });
    subs.forEach(function (fn) { try { fn(state); } catch (e) {} });
  }

  function subscribe(fn) { subs.push(fn); }

  // 统一轮询管理：切换页面时全部清掉，避免残留定时器
  // 页面不可见（后台标签页 / 最小化）时**跳过这一轮**，回到前台立刻补一次 ——
  // 后台继续轮询纯属浪费（每 2~5 秒一串请求），而一回来又必须是最新数据。
  var pollFns = [];
  function every(ms, fn, immediate) {
    if (immediate !== false) { try { fn(); } catch (e) {} }
    pollFns.push(fn);
    var id = setInterval(function () {
      if (document.hidden) return;
      try { fn(); } catch (e) {}
    }, ms);
    state.pollers.push(id);
    return id;
  }

  function clearPollers() {
    state.pollers.forEach(function (id) { clearInterval(id); });
    state.pollers = [];
    pollFns = [];
  }

  document.addEventListener('visibilitychange', function () {
    if (document.hidden) return;
    pollFns.forEach(function (fn) { try { fn(); } catch (e) {} });
  });

  return { state: state, set: set, subscribe: subscribe, every: every, clearPollers: clearPollers };
})();

/* 提示条：同一条消息 1.5 秒内不重复弹；最多同时 4 条（超了挤掉最旧的）。
   为什么需要去重：页面里多个请求同时失败时，Toast.err 会一口气叠 5~6 条
   一模一样的红条，把右上角糊死，反而看不到真正不同的那条。 */
window.Toast = (function () {
  var box = null;
  var MAX = 4;
  var DEDUP_MS = 1500;
  var last = {};

  function ensure() {
    if (!box) {
      box = document.createElement('div');
      box.className = 'toasts';
      document.body.appendChild(box);
    }
    return box;
  }

  function show(msg, kind, ms) {
    var text = msg === null || msg === undefined ? '' : String(msg);
    if (!text) return;
    var now = Date.now();
    var key = (kind || '') + '\u0000' + text;
    if (last[key] && now - last[key] < DEDUP_MS) return;
    last[key] = now;
    /* 键表防膨胀：超过 40 条就清掉 1 分钟前的 */
    var keys = Object.keys(last);
    if (keys.length > 40) {
      keys.forEach(function (k) { if (now - last[k] > 60000) delete last[k]; });
    }

    var el = document.createElement('div');
    el.className = 'toast ' + (kind || '');
    el.textContent = text;
    var host = ensure();
    host.appendChild(el);
    while (host.children.length > MAX) host.removeChild(host.firstChild);

    setTimeout(function () {
      el.style.transition = 'opacity .2s';
      el.style.opacity = '0';
      setTimeout(function () { if (el.parentNode) el.parentNode.removeChild(el); }, 220);
    }, ms || (kind === 'err' ? 5200 : 2800));
  }

  return {
    ok: function (m) { show(m, 'ok'); },
    err: function (m) { show(m, 'err'); },
    warn: function (m) { show(m, 'warn'); },
    info: function (m) { show(m, ''); }
  };
})();
