// src/idraa/static/js/help_hub.js — docs-hub behaviors: TOC scrollspy,
// '/' focuses help search, drawer anchor scroll (see help_trigger macro).
(function () {
  var spy = null; // Arch-N1: one live observer; disconnect before re-init so
                  // search-result swaps on an article page don't stack them.
  function initScrollspy() {
    if (spy) { spy.disconnect(); spy = null; }
    var toc = document.querySelector('[data-help-toc]');
    if (!toc || !('IntersectionObserver' in window)) return;
    var links = {};
    toc.querySelectorAll('[data-toc-link]').forEach(function (a) {
      links[a.getAttribute('data-toc-link')] = a;
    });
    spy = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) {
        if (e.isIntersecting) {
          Object.values(links).forEach(function (a) { a.classList.remove('help-toc-link-active'); });
          var a = links[e.target.id];
          if (a) a.classList.add('help-toc-link-active');
        }
      });
    }, { rootMargin: '0px 0px -70% 0px' });
    Object.keys(links).forEach(function (id) {
      var h = document.getElementById(id);
      if (h) spy.observe(h);
    });
  }
  document.addEventListener('DOMContentLoaded', initScrollspy);
  document.addEventListener('htmx:afterSwap', function (e) {
    if (e.target && e.target.id !== 'help-drawer-body') initScrollspy();
  });

  function visibleSearchBox() {
    // Two rendered instances (mobile disclosure + xl column / index) — pick
    // the one that is actually laid out.
    var boxes = document.querySelectorAll('input[data-help-search]');
    for (var i = 0; i < boxes.length; i++) {
      if (boxes[i].offsetParent !== null) return boxes[i];
    }
    return null;
  }
  document.addEventListener('keydown', function (e) {
    if (e.key !== '/' || e.metaKey || e.ctrlKey || e.altKey) return;
    var t = e.target;
    if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable)) return;
    var box = visibleSearchBox();
    if (box) { e.preventDefault(); box.focus(); }
  });
})();
