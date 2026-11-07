/* 全站交互反馈层
   ============================================================================
   两件事：

   1. **卡片光斑**：把指针位置写进被悬停卡片的 --mx / --my，交给 CSS 的径向渐变。
      用事件委托而不是逐张绑 —— 面板里的卡片每次重渲染都会重建（路由切换、
      轮询刷新），逐张绑会漏绑还会泄漏监听。

   2. **按下反馈**：任何可交互元素被按下时，在指针处放一圈涟漪 + 一次极短震动。
      为什么需要：手机上没有 hover，系统默认那层灰色高亮又被
      `-webkit-tap-highlight-color: transparent` 关掉了，不给反馈就等于"点没点上
      不知道"。键盘用户按 Enter/Space 时在元素中心补一次，保证两边一致。

   系统开了"减少动效"时：涟漪与光斑都不启用（震动保留，那不属于动效）。
   ========================================================================== */
(function () {
  'use strict';

  var REDUCED = !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);

  /* 能触发按下反馈的元素（与 aurora.css 里的选择器保持一致）。
     用 closest 向上找，所以图标、文字、内部 span 都能命中。 */
  var TARGETS = '.btn,.tab,.chip,.badge,.nav a,.core-card,.inst-card,.file-row,.drop-zone,' +
                '[data-go],[data-act],[data-tab],[data-edit],[data-del],[data-src],' +
                "a[href^='#']," +
                'input[type=checkbox],input[type=radio],select';

  function hit(el) {
    if (!el || !el.closest) return null;
    /* 文本输入区不要涟漪：会和光标、选区打架 */
    var field = el.closest('input,textarea');
    if (field && !/^(checkbox|radio)$/i.test(field.type || '')) return null;
    var t = el.closest(TARGETS);
    if (!t || t.disabled) return null;
    return t;
  }

  /* ---------------------------------------------------------------- 涟漪 */
  function ripple(el, x, y) {
    if (REDUCED) return;
    var r = el.getBoundingClientRect();
    if (!r.width && !r.height) return;
    var d = Math.max(r.width, r.height) * 1.15;
    var s = document.createElement('span');
    s.className = 'fx-ripple';
    s.style.width = d + 'px';
    s.style.height = d + 'px';
    s.style.left = Math.round(x - d / 2) + 'px';
    s.style.top = Math.round(y - d / 2) + 'px';
    document.body.appendChild(s);

    var done = false;
    function kill() {
      if (done) return;
      done = true;
      if (s.parentNode) s.parentNode.removeChild(s);
    }
    try {
      var a = s.animate(
        [{ transform: 'scale(.28)', opacity: .55 }, { transform: 'scale(1)', opacity: 0 }],
        { duration: 460, easing: 'cubic-bezier(.22,.61,.36,1)' }
      );
      a.onfinish = kill;
    } catch (e) { /* 老浏览器没有 WAAPI：直接淡出 */ }
    setTimeout(kill, 700);
  }

  function haptic() {
    try { if (navigator.vibrate) navigator.vibrate(8); } catch (e) {}
  }

  /* ---------------------------------------------------------------- 指针 */
  var raf = 0;
  var pending = null;

  function applySpot() {
    raf = 0;
    if (!pending) return;
    var el = pending.el, x = pending.x, y = pending.y;
    pending = null;
    var r = el.getBoundingClientRect();
    if (!r.width || !r.height) return;
    el.style.setProperty('--mx', Math.round(x - r.left) + 'px');
    el.style.setProperty('--my', Math.round(y - r.top) + 'px');
  }

  document.addEventListener('pointerdown', function (e) {
    if (e.button !== undefined && e.button !== 0) return;
    var el = hit(e.target);
    if (!el) return;
    haptic();
    ripple(el, e.clientX, e.clientY);
  }, { passive: true });

  document.addEventListener('pointermove', function (e) {
    var t = e.target;
    if (!t || !t.closest) return;
    var el = t.closest('.card');
    if (!el) return;
    pending = { el: el, x: e.clientX, y: e.clientY };
    if (!raf) raf = requestAnimationFrame(applySpot);
  }, { passive: true });

  /* ---------------------------------------------------------------- 键盘 */
  /* 键盘操作也要有反馈：Enter / Space 在元素中心补一次涟漪 */
  document.addEventListener('keydown', function (e) {
    if (e.key !== 'Enter' && e.key !== ' ' && e.key !== 'Spacebar') return;
    var el = hit(e.target);
    if (!el) return;
    var r = el.getBoundingClientRect();
    haptic();
    ripple(el, r.left + r.width / 2, r.top + r.height / 2);
  }, { passive: true });
})();
