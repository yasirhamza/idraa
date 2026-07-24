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

  // Drawer anchor scroll (see help_trigger macro's anchor param). After the
  // drawer body swap lands, scroll to + pulse the requested section.
  document.addEventListener('htmx:afterSwap', function (e) {
    if (!e.target || e.target.id !== 'help-drawer-body' || !window.Alpine) return;
    var s = Alpine.store('helpDrawer');
    var anchor = s && s.pendingAnchor;
    if (s) s.pendingAnchor = null;
    if (!anchor) return;
    var el = e.target.querySelector('#' + CSS.escape(anchor));
    if (el) {
      el.scrollIntoView({ block: 'start' });
      el.classList.add('help-anchor-pulse');
      setTimeout(function () { el.classList.remove('help-anchor-pulse'); }, 2000);
    }
  });

  // '?' opens contextual help for the current route (carried on <main
  // id="main"> — NEVER on <body>, whose attributes go stale across
  // hx-boost's innerHTML swap; see the data-help-slug comment in base.html).
  function currentHelpSlug() {
    var el = document.querySelector('[data-help-slug]');
    return el ? el.dataset.helpSlug : '';
  }
  document.addEventListener('keydown', function (e) {
    if (e.key !== '?' || e.metaKey || e.ctrlKey || e.altKey) return;
    var t = e.target;
    if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable)) return;
    var slug = currentHelpSlug();
    if (!slug) { window.location.assign('/help'); return; }
    if (window.htmx && window.Alpine) {
      htmx.ajax('GET', '/help/' + slug, { target: '#help-drawer-body', swap: 'innerHTML' });
      Alpine.store('helpDrawer').show();
    }
  });
})();
