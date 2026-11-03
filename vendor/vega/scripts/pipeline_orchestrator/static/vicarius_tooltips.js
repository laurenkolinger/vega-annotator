/* VICARIUS tooltip bubble: shows each control's title on hover and on
   keyboard focus, parks the title in data-tip while open so the browser
   draws no second tooltip, Escape closes. Spec section 7. The bubble
   opens under its control, or above it when there is no room below, and
   always stays inside the window. */
(function () {
  'use strict';
  var GAP = 6;     /* pixels between the control and the bubble */
  var MARGIN = 8;  /* pixels the bubble keeps from every window edge */
  var bubble = document.createElement('div');
  bubble.className = 'bubble';
  bubble.id = 'vicarius-bubble';
  bubble.setAttribute('role', 'tooltip');
  bubble.hidden = true;
  function mount() { if (!bubble.parentNode) document.body.appendChild(bubble); }
  function place(el) {
    /* Measured at the page's top left first, so its width (and so its height) is never narrowed by where it last opened. */
    bubble.style.left = '0px';
    bubble.style.top = '0px';
    var r = el.getBoundingClientRect();
    var viewWidth = document.documentElement.clientWidth;
    var viewHeight = document.documentElement.clientHeight;
    var width = bubble.offsetWidth;
    var height = bubble.offsetHeight;
    var top = r.bottom + GAP;
    if (top + height > viewHeight - MARGIN && r.top - GAP - height >= MARGIN) top = r.top - GAP - height;
    top = Math.max(MARGIN, Math.min(top, viewHeight - MARGIN - height));
    var left = Math.max(MARGIN, Math.min(r.left, viewWidth - width - MARGIN));
    bubble.style.top = (top + window.scrollY) + 'px';
    bubble.style.left = (left + window.scrollX) + 'px';
  }
  function open(el) {
    var text = el.getAttribute('title');
    if (!text) return;
    mount();
    el.dataset.tip = text;
    el.removeAttribute('title');
    var existingDesc = el.getAttribute('aria-describedby');
    if (existingDesc) el.dataset.desc = existingDesc;
    el.setAttribute('aria-describedby', 'vicarius-bubble');
    bubble.textContent = text;
    bubble.hidden = false;
    place(el);
  }
  function close(el) {
    if (el && el.dataset && el.dataset.tip) { el.setAttribute('title', el.dataset.tip); delete el.dataset.tip; }
    if (el && el.dataset && el.dataset.desc) { el.setAttribute('aria-describedby', el.dataset.desc); delete el.dataset.desc; }
    else if (el) { el.removeAttribute('aria-describedby'); }
    bubble.hidden = true;
  }
  document.addEventListener('mouseover', function (e) { var el = e.target.closest && e.target.closest('[title]'); if (el) open(el); });
  document.addEventListener('mouseout', function (e) { var el = e.target.closest && e.target.closest('[data-tip]'); if (el) close(el); });
  document.addEventListener('focusin', function (e) { var el = e.target.closest && e.target.closest('[title]'); if (el) open(el); });
  document.addEventListener('focusout', function (e) { var el = e.target.closest && e.target.closest('[data-tip]'); if (el) close(el); });
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape') close(document.querySelector('[data-tip]')); });
  window.VicariusTooltips = { open: open, close: close };
})();
