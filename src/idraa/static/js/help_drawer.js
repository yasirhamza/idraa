// src/idraa/static/js/help_drawer.js
// Help slide-over drawer store. Registered via alpine:init so the store exists
// before any x-data evaluates. Non-defer load (like sub_function_combobox.js)
// guarantees this listener is attached before Alpine (defer) initializes.
// Survives hx-boost navigation: the <head> <script> is re-merged by hx-boost's
// head-merge, and the drawer opens from an explicit hx-get trigger, not a
// boosted <body> link — unlike the wizard store that was lost on boosted swap
// (see templates/scenarios/wizard/_shell.html).
document.addEventListener('alpine:init', function () {
  if (window.Alpine && !Alpine.store('helpDrawer')) {
    Alpine.store('helpDrawer', {
      open: false,
      _lastFocus: null,
      show() {
        this._lastFocus = document.activeElement;
        this.open = true;
      },
      hide() {
        this.open = false;
        if (this._lastFocus && this._lastFocus.focus) {
          this._lastFocus.focus();
        }
      },
    });
  }
});

// Esc closes the drawer from anywhere.
document.addEventListener('keydown', function (e) {
  if (e.key === 'Escape' && window.Alpine) {
    var s = Alpine.store('helpDrawer');
    if (s && s.open) s.hide();
  }
});
