/* 芮拓MC面板 · 官网交互
   只做四件小事：主题切换、滚动时给顶栏描边、移动端菜单、入场动画。
   全部用 IntersectionObserver / rAF，不引入任何依赖。 */
(function () {
  var root = document.documentElement;

  /* ---------------- 主题：跟随系统 / 暗色 / 亮色 ---------------- */
  var CYCLE = ['auto', 'dark', 'light'];
  var LABEL = { auto: '跟随系统', dark: '暗色', light: '亮色' };
  try {
    var saved = localStorage.getItem('mc_site_theme');
    if (saved && CYCLE.indexOf(saved) >= 0) root.setAttribute('data-theme', saved);
  } catch (e) {}
  var tbtn = document.getElementById('theme-btn');
  if (tbtn) {
    tbtn.title = '切换主题（当前：' + LABEL[root.getAttribute('data-theme') || 'auto'] + '）';
    tbtn.addEventListener('click', function () {
      var cur = root.getAttribute('data-theme') || 'auto';
      var next = CYCLE[(CYCLE.indexOf(cur) + 1) % CYCLE.length];
      root.setAttribute('data-theme', next);
      try { localStorage.setItem('mc_site_theme', next); } catch (e) {}
      tbtn.title = '切换主题（当前：' + LABEL[next] + '）';
    });
  }

  /* ---------------- 滚动：顶栏描边 ---------------- */
  var nav = document.getElementById('nav');
  var ticking = false;
  function onScroll() {
    if (ticking) return;
    ticking = true;
    requestAnimationFrame(function () {
      if (nav) nav.classList.toggle('scrolled', window.scrollY > 8);
      ticking = false;
    });
  }
  window.addEventListener('scroll', onScroll, { passive: true });
  onScroll();

  /* ---------------- 移动端菜单 ---------------- */
  var mbtn = document.getElementById('menu-btn');
  var links = document.getElementById('nav-links');
  if (mbtn && links) {
    mbtn.addEventListener('click', function () { links.classList.toggle('open'); });
    links.addEventListener('click', function (e) {
      if (e.target.tagName === 'A') links.classList.remove('open');
    });
  }

  /* ---------------- 入场动画 + 数字滚动 + 进度条 ---------------- */
  var reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  function countUp(el) {
    var target = parseFloat(el.getAttribute('data-count'));
    var suffix = el.getAttribute('data-suffix') || '';
    var dec = (String(target).split('.')[1] || '').length;
    if (isNaN(target)) return;
    if (reduce) { el.textContent = target.toFixed(dec) + suffix; return; }
    var t0 = null, DUR = 1100;
    function step(t) {
      if (t0 === null) t0 = t;
      var k = Math.min(1, (t - t0) / DUR);
      var v = target * (1 - Math.pow(1 - k, 3));
      el.textContent = v.toFixed(dec) + suffix;
      if (k < 1) requestAnimationFrame(step);
    }
    requestAnimationFrame(step);
  }

  function runMock() {
    var fill = document.querySelector('.bar-fill');
    if (fill) setTimeout(function () { fill.style.width = '100%'; }, 320);
    var nums = document.querySelectorAll('.mock .num');
    for (var i = 0; i < nums.length; i++) {
      (function (el, d) { setTimeout(function () { countUp(el); }, d); })(nums[i], i * 90);
    }
  }

  var targets = [].slice.call(document.querySelectorAll(
    '.fcard, .table-wrap, .step, .aside, .dbox, .faq details, .sec-head'));
  targets.forEach(function (el) { el.classList.add('reveal'); });

  if ('IntersectionObserver' in window) {
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) {
        if (!en.isIntersecting) return;
        en.target.classList.add('in');
        io.unobserve(en.target);
      });
    }, { rootMargin: '0px 0px -8% 0px', threshold: 0.08 });
    targets.forEach(function (el) { io.observe(el); });
  } else {
    targets.forEach(function (el) { el.classList.add('in'); });
  }

  // 首屏的界面示意直接用定时器跑（它就在视口里）
  if (document.readyState === 'complete') runMock();
  else window.addEventListener('load', runMock);
})();
