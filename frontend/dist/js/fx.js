/* 触摸反馈：只保留极短震动。
   ============================================================================
   涟漪（彩色扩散）与卡片光斑属于装饰，按设计规范移除；
   震动不改变视觉，保留下来让手指有回应。
   系统开了"减少动效"时仍然保留震动（它不属于动效）。
   ========================================================================== */
(function () {
  'use strict';
  var TARGETS = '.btn,.tab,.chip,.badge,.nav a,.core-card,.inst-card,.file-row,.drop-zone,' +
                '[data-go],[data-act],[data-tab],[data-edit],[data-del],[data-src],' +
                "a[href^='#']," +
                'input[type=checkbox],input[type=radio],select';

  function hit(el) {
    if (!el || !el.closest) return null;
    var field = el.closest('input,textarea');
    if (field && !/^(checkbox|radio)$/i.test(field.type || '')) return null;
    var t = el.closest(TARGETS);
    if (!t || t.disabled) return null;
    return t;
  }

  function haptic() {
    try { if (navigator.vibrate) navigator.vibrate(8); } catch (e) {}
  }

  document.addEventListener('pointerdown', function (e) {
    if (e.button !== undefined && e.button !== 0) return;
    if (hit(e.target)) haptic();
  }, { passive: true });

  document.addEventListener('keydown', function (e) {
    if (e.key !== 'Enter' && e.key !== ' ' && e.key !== 'Spacebar') return;
    if (hit(e.target)) haptic();
  }, { passive: true });
})();
